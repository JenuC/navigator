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

    def acquire_microtimes(
        self,
        duration: float,
        output_path: str | Path,
        core=None,
        roi: tuple[int, int, int, int] = (0, 0, 20, 20),
        pre_hook: Optional[Callable] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> tuple[int, Optional[np.ndarray], Optional[str]]:
        """
        FIFO acquisition that also extracts per-photon microtimes.

        If *core* (pycromanager Core) is provided, sets the camera ROI and
        runs a continuous sequence acquisition around the FLIM measurement so
        the scanner is active during collection.

        Writes two output files:
          - output_path          — raw uint16 FIFO words (.spc)
          - output_path.with_suffix('.npz') — arrays 'mt' (uint16 microtimes,
            12-bit, SYNC-to-photon) and 'ph' (uint32 photon records)

        Returns (photon_count, microtimes_uint16_or_None, error_or_None).
        """
        with self._lock:
            self._acquiring = True
            self._elapsed = 0.0
            self._photon_count = 0
            self._last_error = ""

        scan_started = False
        try:
            if pre_hook is not None:
                pre_hook()

            if core is not None:
                core.set_roi(*roi)
                core.start_continuous_sequence_acquisition(0)
                scan_started = True

            chunks: list[array.array] = []
            self._spcm.start_measurement(self._mod_no)
            start = time.monotonic()
            deadline = start + duration

            while True:
                if stop_event is not None and stop_event.is_set():
                    self._spcm.stop_measurement(self._mod_no)
                    return 0, None, "Stopped by user"

                buf = self._spcm.read_fifo_to_array(self._mod_no, self.BUF_WORDS)
                if buf:
                    chunks.append(buf)

                with self._lock:
                    self._elapsed = time.monotonic() - start

                if time.monotonic() >= deadline:
                    self._spcm.stop_measurement(self._mod_no)
                    break

                if len(buf) < self.BUF_WORDS:
                    time.sleep(0.001)

            # Drain data that arrived between deadline check and stop
            while True:
                buf = self._spcm.read_fifo_to_array(self._mod_no, self.BUF_WORDS)
                if not buf:
                    break
                chunks.append(buf)

            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if chunks:
                all_words = np.concatenate(chunks)
                records = all_words.view(np.uint32)

                # Photon records: bits 31 and 28 both clear
                photon_mask = (records & (0b1001 << 28)) == 0
                photon_records = records[photon_mask]

                # 12-bit microtime is bits 27:16; invert so time runs
                # from SYNC pulse to photon (hardware measures photon→SYNC)
                max_12bit = np.uint16((1 << 12) - 1)
                raw_mt = ((photon_records >> 16) & 0x0FFF).astype(np.uint16)
                microtimes = max_12bit - raw_mt

                photons = int(len(photon_records))
                all_words.tofile(str(output_path))
                np.savez(
                    output_path.with_suffix(".npz"),
                    mt=microtimes,
                    ph=photon_records,
                )
            else:
                photons = 0
                microtimes = np.array([], dtype=np.uint16)
                output_path.write_bytes(b"")
                np.savez(
                    output_path.with_suffix(".npz"),
                    mt=microtimes,
                    ph=np.array([], dtype=np.uint32),
                )

            with self._lock:
                self._photon_count = photons
                self._elapsed = duration

            return photons, microtimes, None

        except Exception as exc:
            err = str(exc)
            with self._lock:
                self._last_error = err
            return 0, None, err
        finally:
            if scan_started:
                try:
                    core.stop_sequence_acquisition()
                except Exception:
                    pass
            with self._lock:
                self._acquiring = False

    def shutdown(self) -> None:
        try:
            self._spcm.close()
        except Exception:
            pass
