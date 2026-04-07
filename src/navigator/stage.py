"""Simulated XYZ stage with velocity-based movement."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class Waypoint:
    name: str
    x: float
    y: float
    z: float


class Stage:
    """
    Thread-safe simulated XYZ stage.

    Positions are in micrometres (µm). Movement is simulated at a
    configurable speed (µm/s) and runs in a background daemon thread.
    """

    def __init__(
        self,
        x_range: tuple[float, float] = (-50_000.0, 50_000.0),
        y_range: tuple[float, float] = (-50_000.0, 50_000.0),
        z_range: tuple[float, float] = (0.0, 10_000.0),
    ):
        self.x_range = x_range
        self.y_range = y_range
        self.z_range = z_range

        self._x = 0.0
        self._y = 0.0
        self._z = 0.0
        self._target_x = 0.0
        self._target_y = 0.0
        self._target_z = 0.0
        self._speed = 5_000.0  # µm/s
        self._is_moving = False
        self._lock = threading.Lock()

        self.waypoints: list[Waypoint] = []
        self.history: list[tuple[float, float]] = [(0.0, 0.0)]
        self.max_history = 500

        self._thread = threading.Thread(target=self._update_loop, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def move_to(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
    ) -> None:
        with self._lock:
            if x is not None:
                self._target_x = self._clamp(x, *self.x_range)
            if y is not None:
                self._target_y = self._clamp(y, *self.y_range)
            if z is not None:
                self._target_z = self._clamp(z, *self.z_range)

    def jog(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> None:
        with self._lock:
            self._target_x = self._clamp(self._target_x + dx, *self.x_range)
            self._target_y = self._clamp(self._target_y + dy, *self.y_range)
            self._target_z = self._clamp(self._target_z + dz, *self.z_range)

    def stop(self) -> None:
        with self._lock:
            self._target_x = self._x
            self._target_y = self._y
            self._target_z = self._z

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
        with self._lock:
            return self._speed

    @speed.setter
    def speed(self, val: float) -> None:
        with self._lock:
            self._speed = max(100.0, min(100_000.0, val))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp(val: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, val))

    def _update_loop(self) -> None:
        last = time.perf_counter()
        while True:
            now = time.perf_counter()
            dt = min(now - last, 0.05)  # cap dt to avoid large jumps
            last = now

            with self._lock:
                dx = self._target_x - self._x
                dy = self._target_y - self._y
                dz = self._target_z - self._z
                dist = math.sqrt(dx * dx + dy * dy + dz * dz)

                if dist > 0.5:
                    self._is_moving = True
                    step = self._speed * dt
                    if step >= dist:
                        self._x = self._target_x
                        self._y = self._target_y
                        self._z = self._target_z
                    else:
                        f = step / dist
                        self._x += dx * f
                        self._y += dy * f
                        self._z += dz * f

                    # Append to history if moved far enough
                    last_h = self.history[-1]
                    if math.hypot(self._x - last_h[0], self._y - last_h[1]) > 200:
                        self.history.append((self._x, self._y))
                        if len(self.history) > self.max_history:
                            self.history.pop(0)
                else:
                    self._is_moving = False

            time.sleep(0.01)  # 100 Hz
