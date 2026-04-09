"""BH SPC-180NX acquisition wrapper (FIFO mode → .spc file).

Install pybhspc before use:
    uv pip install ./pybhspc
"""

from __future__ import annotations

import array
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np


class SPCModule:
    """
    Wraps bh_spc for per-point FIFO acquisitions on a single SPC module.

    Call acquire() from a background thread; it blocks for the full duration
    and writes a raw .spc file (32-bit event records).

    Thread-safe status properties (acquiring, elapsed, photon_count,
    last_error) may be read from any thread.
    """

    BUF_WORDS = 32768  # 16-bit words per FIFO read call

    def __init__(self, mod_no: int = 0, simulate: bool = False) -> None:
        import bh_spc
        from bh_spc import spcm

        self._spcm = spcm
        self._mod_no = mod_no
        self._lock = threading.Lock()
        self._acquiring = False
        self._elapsed = 0.0
        self._photon_count = 0
        self._last_error = ""

        mode = (
            spcm.DLLOperationMode.SIMULATE_SPC_180NX
            if simulate
            else spcm.DLLOperationMode.HARDWARE
        )
        with bh_spc.ini_file(bh_spc.minimal_spcm_ini(mode)) as ini:
            spcm.init(ini)

        # FIFO mode = 1 for SPC-180NX; disable hardware time-stop so the
        # duration is controlled in software (also required in simulation).
        spcm.set_parameter(mod_no, spcm.ParID.MODE, 1)
        params = spcm.get_parameters(mod_no)
        params.stop_on_time = 0
        spcm.set_parameters(mod_no, params)

    # ------------------------------------------------------------------
    # Thread-safe status
    # ------------------------------------------------------------------

    @property
    def acquiring(self) -> bool:
        with self._lock:
            return self._acquiring

    @property
    def elapsed(self) -> float:
        with self._lock:
            return self._elapsed

    @property
    def photon_count(self) -> int:
        with self._lock:
            return self._photon_count

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    # ------------------------------------------------------------------
    # Acquisition  (blocking — call from a background thread)
    # ------------------------------------------------------------------

    def acquire(
        self,
        duration: float,
        output_path: str | Path,
        pre_hook: Optional[Callable] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> tuple[int, Optional[str]]:
        """
        Run a FIFO acquisition for *duration* seconds, save to *output_path*.

        pre_hook() is called just before start_measurement — use it to set
        pycromanager device properties, open shutters, etc.

        stop_event: if set mid-acquisition, measurement is stopped immediately
        and (0, "Stopped by user") is returned.

        Returns (photon_count, error_string_or_None).
        """
        with self._lock:
            self._acquiring = True
            self._elapsed = 0.0
            self._photon_count = 0
            self._last_error = ""

        try:
            if pre_hook is not None:
                pre_hook()

            chunks: list[array.array] = []
            self._spcm.start_measurement(self._mod_no)
            start = time.monotonic()
            deadline = start + duration

            while True:
                if stop_event is not None and stop_event.is_set():
                    self._spcm.stop_measurement(self._mod_no)
                    return 0, "Stopped by user"

                buf = self._spcm.read_fifo_to_array(self._mod_no, self.BUF_WORDS)
                if buf:
                    chunks.append(buf)

                with self._lock:
                    self._elapsed = time.monotonic() - start

                if time.monotonic() >= deadline:
                    self._spcm.stop_measurement(self._mod_no)
                    break

                if len(buf) < self.BUF_WORDS:
                    time.sleep(0.001)  # FIFO empty, yield

            # Drain data that arrived between deadline check and stop
            while True:
                buf = self._spcm.read_fifo_to_array(self._mod_no, self.BUF_WORDS)
                if not buf:
                    break
                chunks.append(buf)

            # Count photons (bits 31 and 28 both clear = photon record)
            if chunks:
                all_words = np.concatenate(chunks)
                records = all_words.view(np.uint32)
                photons = int(np.sum((records & (0b1001 << 28)) == 0))
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                all_words.tofile(str(output_path))
            else:
                photons = 0
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(b"")

            with self._lock:
                self._photon_count = photons
                self._elapsed = duration

            return photons, None

        except Exception as exc:
            err = str(exc)
            with self._lock:
                self._last_error = err
            return 0, err
        finally:
            with self._lock:
                self._acquiring = False

    def shutdown(self) -> None:
        try:
            self._spcm.close()
        except Exception:
            pass
