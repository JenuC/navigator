"""
PycroStage — real hardware backend via pycromanager + Micro-Manager 2.

Wraps microscope_control.hardware.stage.PycromanagerStage and exposes
the same public API as the simulated Stage class so the UI works
unchanged.

Requirements
------------
* Micro-Manager 2 running with the Python bridge enabled.
* `uv run navigator --hardware` (or `--hardware --mm-settings path/to/settings.yaml`)

Connection flow
---------------
1.  pycromanager.Core() connects to MM2 over the Java bridge.
2.  PycromanagerStage wraps Core and exposes move_xy / move_z / get_xyz.
3.  PycroStage wraps PycromanagerStage, polls position at 20 Hz in a
    daemon thread, and translates the navigator's move_to / jog / stop
    API into the appropriate Core calls.
"""

from __future__ import annotations

import logging
import math
import sys
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Make the submodule importable (navigator/microscope_control/)
# ---------------------------------------------------------------------------
import importlib.util as _ilu

def _load_mm_stage():
    """
    Load microscope_control.hardware.stage directly from its file path.

    This avoids triggering the package's __init__.py, which eagerly imports
    pycromanager (and would fail if it isn't installed yet).
    """
    _stage_file = (
        Path(__file__).resolve().parent.parent.parent
        / "microscope_control"
        / "microscope_control"
        / "hardware"
        / "stage.py"
    )
    spec = _ilu.spec_from_file_location("_mc_stage", _stage_file)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_mc_stage = _load_mm_stage()
_MMStage = _mc_stage.PycromanagerStage

from .stage import Waypoint  # noqa: E402


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class PycroStage:
    """
    Drop-in replacement for the simulated Stage, backed by pycromanager Core.

    Identical public interface
    --------------------------
    position, target, is_moving, speed (read-only),
    move_to(), jog(), stop(), home(), add_waypoint(),
    waypoints, history, max_history,
    x_range, y_range, z_range.

    Implementation notes
    --------------------
    * move_to / jog fire-and-forget: they call move_xy_no_wait / move_z_no_wait
      on a daemon thread so the UI never blocks.
    * Position is polled at 20 Hz from the hardware.  is_moving is inferred
      from whether the position changed between two consecutive polls.
    * stop() snaps the target to the current position and optionally calls
      core.stop() if the device supports it.
    * speed is not settable through the generic MM Core API; the property
      returns float('nan') and the setter is a no-op (control speed via MM
      device properties instead).
    """

    _POLL_HZ = 20
    _MOVE_THRESHOLD_UM = 0.5  # smaller than this = considered stopped

    def __init__(self, core, settings: dict | None = None):
        """
        Parameters
        ----------
        core
            A connected ``pycromanager.Core()`` object.
        settings
            Optional dict matching microscope_control's settings schema, e.g.::

                {
                    "stage": {
                        "z_stage": "ZDrive",           # MM focus device name
                        "f_stage": "FocusDrive",       # secondary Z (optional)
                        "limits": {
                            "x_um": {"low": -50000, "high": 50000},
                            "y_um": {"low": -50000, "high": 50000},
                            "z_um": {"low": 0,      "high": 10000},
                        },
                    }
                }

            Omit to use wide defaults (±50 mm XY, 0–10 mm Z).
        """
        settings = settings or {}
        self._hw = _MMStage(core, settings)
        self._core = core

        # ---- Axis limits ----
        lim = settings.get("stage", {}).get("limits", {})
        self.x_range: tuple[float, float] = (
            lim.get("x_um", {}).get("low",  -10_000.0),
            lim.get("x_um", {}).get("high",  10_000.0),
        )
        self.y_range: tuple[float, float] = (
            lim.get("y_um", {}).get("low",  -10_000.0),
            lim.get("y_um", {}).get("high",  10_000.0),
        )
        self.z_range: tuple[float, float] = (
            lim.get("z_um", {}).get("low",       0.0),
            lim.get("z_um", {}).get("high",  10_000.0),
        )

        # ---- Seed position from hardware ----
        try:
            ix, iy, iz = self._hw.get_xyz()
        except Exception:
            ix, iy, iz = 0.0, 0.0, 0.0

        self._x, self._y, self._z = ix, iy, iz
        self._px, self._py, self._pz = ix, iy, iz          # previous poll values
        self._target_x, self._target_y, self._target_z = ix, iy, iz
        self._is_moving = False
        self._last_poll_ok = True
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        # ---- Shared state (same shape as Stage) ----
        self.waypoints: list[Waypoint] = []
        self.history: list[tuple[float, float]] = [(ix, iy)]
        self.max_history = 500

        # ---- Log MM2 details ----
        try:
            version = core.get_version_info()
            log.info("MM2 version: %s", version)
        except Exception:
            pass
        try:
            xy_dev = core.get_xy_stage_device()
            z_dev = core.get_focus_device()
            cam_dev = core.get_camera_device()
            log.info(
                "MM2 devices — XY stage: %r  focus: %r  camera: %r",
                xy_dev, z_dev, cam_dev,
            )
        except Exception:
            pass
        try:
            devices = list(core.get_loaded_devices())
            log.debug("MM2 loaded devices (%d): %s", len(devices), ", ".join(devices))
        except Exception:
            pass

        # ---- Start polling ----
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Motion API
    # ------------------------------------------------------------------

    def move_to(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
    ) -> None:
        """Non-blocking absolute move.  Any axis may be omitted (None)."""
        with self._lock:
            if x is not None:
                self._target_x = self._clamp(x, *self.x_range)
            if y is not None:
                self._target_y = self._clamp(y, *self.y_range)
            if z is not None:
                self._target_z = self._clamp(z, *self.z_range)
            tx, ty, tz = self._target_x, self._target_y, self._target_z

        threading.Thread(
            target=self._issue_move,
            args=(tx if x is not None else None,
                  ty if y is not None else None,
                  tz if z is not None else None),
            daemon=True,
        ).start()

    def jog(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> None:
        """Non-blocking relative move from current target."""
        with self._lock:
            move_xy = dx != 0.0 or dy != 0.0
            move_z  = dz != 0.0
            if move_xy:
                self._target_x = self._clamp(self._target_x + dx, *self.x_range)
                self._target_y = self._clamp(self._target_y + dy, *self.y_range)
            if move_z:
                self._target_z = self._clamp(self._target_z + dz, *self.z_range)
            tx, ty, tz = self._target_x, self._target_y, self._target_z

        threading.Thread(
            target=self._issue_move,
            args=(tx if move_xy else None,
                  ty if move_xy else None,
                  tz if move_z  else None),
            daemon=True,
        ).start()

    def stop(self) -> None:
        """Stop motion — snap target to current position."""
        with self._lock:
            self._target_x = self._x
            self._target_y = self._y
            self._target_z = self._z
        # Try device-level halt (not all MM adapters support stop())
        try:
            self._core.stop(self._core.get_xy_stage_device())
        except Exception:
            pass

    def home(self) -> None:
        self.move_to(0.0, 0.0, 0.0)

    def add_waypoint(self, name: str = "") -> None:
        x, y, z = self.position
        if not name:
            name = f"WP{len(self.waypoints) + 1}"
        self.waypoints.append(Waypoint(name, x, y, z))

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def position(self) -> tuple[float, float, float]:
        with self._lock:
            return self._x, self._y, self._z

    @property
    def target(self) -> tuple[float, float, float]:
        with self._lock:
            return self._target_x, self._target_y, self._target_z

    @property
    def is_moving(self) -> bool:
        with self._lock:
            return self._is_moving

    @property
    def speed(self) -> float:
        """Hardware speed is managed via MM device properties; returns NaN."""
        return float("nan")

    @speed.setter
    def speed(self, _: float) -> None:
        pass  # no-op — set speed through MM device property browser

    @property
    def core(self):
        """Raw pycromanager Core — for imaging and other device-level calls."""
        return self._core

    @property
    def backend_name(self) -> str:
        return "MM2 (pycromanager)"

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._last_poll_ok

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp(val: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, val))

    def _issue_move(
        self,
        x: Optional[float],
        y: Optional[float],
        z: Optional[float],
    ) -> None:
        """Fire-and-forget move commands (runs on a daemon thread)."""
        try:
            if x is not None and y is not None:
                self._hw.move_xy_no_wait(x, y)
            if z is not None:
                self._hw.move_z_no_wait(z)
        except Exception as exc:
            log.error("move error: %s", exc)

    def shutdown(self) -> None:
        """Signal the poll thread to stop and wait for it to exit."""
        self._stop_event.set()
        self._thread.join(timeout=2.0)

    def _poll_loop(self) -> None:
        """Read position from hardware at _POLL_HZ; update is_moving and history."""
        interval = 1.0 / self._POLL_HZ
        while not self._stop_event.is_set():
            try:
                x, y, z = self._hw.get_xyz()
                with self._lock:
                    moved = math.sqrt(
                        (x - self._px) ** 2
                        + (y - self._py) ** 2
                        + (z - self._pz) ** 2
                    )
                    self._is_moving = moved > self._MOVE_THRESHOLD_UM
                    self._px, self._py, self._pz = x, y, z
                    self._x, self._y, self._z = x, y, z
                    self._last_poll_ok = True

                    # Append to history if moved far enough
                    if math.hypot(x - self.history[-1][0], y - self.history[-1][1]) > 200:
                        self.history.append((x, y))
                        if len(self.history) > self.max_history:
                            self.history.pop(0)
            except Exception as exc:
                with self._lock:
                    self._last_poll_ok = False
                log.warning("poll error: %s", exc)
                time.sleep(1.0)   # back off on repeated errors
                continue

            self._stop_event.wait(interval)
