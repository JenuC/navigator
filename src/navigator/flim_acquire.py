"""
FLIM point acquisition — mirrors test_modules_hooks.ipynb exactly.

Usage:
    mt, ph, err = acquire_point(core, duration, output_path, object_size=50, mod_no=0)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

_BUF_SIZE = 2**15  # 32768 uint16 words — matches notebook buf_size


def _get_microtimes_mto(
    duration: float,
    mod_no: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Verbatim translation of notebook get_microtimes_MTO.

    Returns (raw_words_uint16, microtimes_uint16, photon_records_uint32).
    raw_words is the full FIFO stream including gap/marker records.
    """
    from bh_spc import spcm

    spcm.start_measurement(mod_no)
    start_time = time.monotonic()
    data = []

    while True:
        elapsed = time.monotonic() - start_time
        if elapsed >= duration:
            spcm.stop_measurement(mod_no)
            break
        buf = spcm.read_fifo_to_array(mod_no, _BUF_SIZE)
        if len(buf):
            data.append(buf)
        if len(buf) < _BUF_SIZE:
            time.sleep(0.001)

    # drain remaining data after stop
    while True:
        buf = spcm.read_fifo_to_array(mod_no, _BUF_SIZE)
        if not len(buf):
            break
        data.append(buf)

    if not data:
        return (
            np.array([], dtype=np.uint16),
            np.array([], dtype=np.uint16),
            np.array([], dtype=np.uint32),
        )

    raw_words = np.concatenate(data)          # uint16
    records = raw_words.view(np.uint32)

    had_gap = bool(np.any(records & (1 << 29)))
    log.debug("FIFO gap: %s  total records: %d", had_gap, len(records))

    photons = records[(records & (0b1001 << 28)) == 0]
    max_12bit = (1 << 12) - 1                 # 4095
    microtimes = max_12bit - ((photons >> 16) & max_12bit)

    return raw_words, microtimes.astype(np.uint16), photons.astype(np.uint32)


def acquire_point(
    core,
    duration: float,
    output_path,
    object_size: int = 50,
    mod_no: int = 0,
) -> tuple[np.ndarray, np.ndarray, str | None]:
    """
    Acquire FLIM data at a single point — identical to the notebook cell.

    Sequence:
      1. EnableChannel1=Yes, EnableChannel0=No, PMTSignalSwitch=TCSPC PreAmp
      2. Fermat Spiral Scan = Yes
      3. set_roi(W-obj, H-obj, obj, obj)          ← bottom-right corner
      4. stop scanner if already running
      5. start_continuous_sequence_acquisition(0)
      6. _get_microtimes_mto(duration, mod_no)
      7. [finally] stop scanner, Fermat=No, restore roi

    Saves:
      <output_path>       raw uint16 FIFO words (.spc)
      <output_path>.npz   mt (uint16 microtimes), ph (uint32 photon records)
      <output_path>.csv   histogram: bin_center, count  (256 bins)

    Returns (mt, ph, error_or_None).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    orig_roi = core.get_roi()
    W, H = orig_roi.width, orig_roi.height
    log.debug(
        "acquire_point: FOV %dx%d  obj=%d  dur=%.1fs  mod=%d  out=%s",
        W, H, object_size, duration, mod_no, output_path,
    )

    scanner_started = False
    try:
        # ---- setup (matches notebook) ----
        core.set_property("OSc-LSM", "Dev1-EnableChannel1", "Yes")
        core.set_property("OSc-LSM", "Dev1-EnableChannel0", "No")
        core.set_property("PMTSignalSwitch", "Label", "TCSPC PreAmp")
        core.set_property("OSc-LSM", "Dev1-Fermat Spiral Scan", "Yes")
        core.set_roi(W - object_size, H - object_size, object_size, object_size)
        log.debug(
            "acquire_point: roi=(%d,%d,%d,%d)",
            W - object_size, H - object_size, object_size, object_size,
        )

        if core.is_sequence_running():
            log.debug("acquire_point: stopping existing scanner first")
            core.stop_sequence_acquisition()

        log.debug("acquire_point: starting scanner")
        core.start_continuous_sequence_acquisition(0)
        scanner_started = True
        log.debug("acquire_point: scanner running — starting SPC FIFO")

        raw_words, mt, ph = _get_microtimes_mto(duration, mod_no)
        log.debug("acquire_point: %d photons collected", len(ph))

        # ---- save files ----
        raw_words.tofile(str(output_path))
        np.savez(output_path.with_suffix(".npz"), mt=mt, ph=ph)

        if len(mt):
            counts, edges = np.histogram(mt, bins=256, range=(0, 4095))
            centers = ((edges[:-1] + edges[1:]) / 2).astype(int)
            with open(output_path.with_suffix(".csv"), "w") as f:
                f.write("bin_center,count\n")
                for c, n in zip(centers, counts):
                    f.write(f"{c},{n}\n")
            log.debug("acquire_point: histogram CSV saved")

        return mt, ph, None

    except Exception as exc:
        log.error("acquire_point failed: %s", exc, exc_info=True)
        return np.array([], dtype=np.uint16), np.array([], dtype=np.uint32), str(exc)

    finally:
        # ---- teardown (matches notebook) ----
        if scanner_started:
            try:
                core.stop_sequence_acquisition()
            except Exception:
                pass
        try:
            core.set_property("OSc-LSM", "Dev1-Fermat Spiral Scan", "No")
        except Exception:
            pass
        try:
            core.set_roi(orig_roi.x, orig_roi.y, orig_roi.width, orig_roi.height)
        except Exception:
            pass
