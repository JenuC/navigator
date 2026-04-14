"""
FLIM acquisition function — edit this file to change how each point is acquired.

Called by the Hist button for every point:
    mt, ph, err = acquire_point(core, spcm, duration, output_path, object_size, mod_no)
"""
import time
import numpy as np
from pathlib import Path

BUF_SIZE = 2**15  # 32768 uint16 words


def acquire_point(core, spcm, duration, output_path, object_size=50, mod_no=0):
    """
    Acquire FLIM data at one stage point.  Edit freely.

    Parameters
    ----------
    core        : pycromanager Core
    spcm        : bh_spc.spcm module (already initialised)
    duration    : float  — acquisition time in seconds
    output_path : Path   — base path; .spc / .npz / .csv written here
    object_size : int    — Fermat spiral ROI size in pixels
    mod_no      : int    — SPC module number (0 for SPC-180NX)

    Returns
    -------
    mt  : np.ndarray uint16  — microtimes (SYNC→photon, 0–4095)
    ph  : np.ndarray uint32  — raw photon records
    err : str | None         — error message, or None on success
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # save full FOV so we can restore it afterwards
    orig_roi = core.get_roi()
    W, H = orig_roi.width, orig_roi.height

    try:
        # ---- setup (same as notebook) ----
        core.set_property("OSc-LSM", "Dev1-EnableChannel1", "Yes")
        core.set_property("OSc-LSM", "Dev1-EnableChannel0", "No")
        core.set_property("PMTSignalSwitch", "Label", "TCSPC PreAmp")
        core.set_property("OSc-LSM", "Dev1-Fermat Spiral Scan", "Yes")
        core.set_roi(W - object_size, H - object_size, object_size, object_size)

        if core.is_sequence_running():
            core.stop_sequence_acquisition()

        core.start_continuous_sequence_acquisition(0)

        # ---- FIFO loop (same as notebook get_microtimes_MTO) ----
        spcm.start_measurement(mod_no)
        start_time = time.monotonic()
        data = []

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= duration:
                spcm.stop_measurement(mod_no)
                break
            buf = spcm.read_fifo_to_array(mod_no, BUF_SIZE)
            if len(buf):
                data.append(buf)
            if len(buf) < BUF_SIZE:
                time.sleep(0.001)

        # drain
        while True:
            buf = spcm.read_fifo_to_array(mod_no, BUF_SIZE)
            if not len(buf):
                break
            data.append(buf)

        # ---- decode ----
        if not data:
            return np.array([], dtype=np.uint16), np.array([], dtype=np.uint32), None

        raw = np.concatenate(data)
        records = raw.view(np.uint32)
        ph = records[(records & (0b1001 << 28)) == 0].astype(np.uint32)
        max_12bit = (1 << 12) - 1
        mt = (max_12bit - ((ph >> 16) & max_12bit)).astype(np.uint16)

        # ---- save ----
        raw.tofile(str(output_path))
        np.savez(output_path.with_suffix(".npz"), mt=mt, ph=ph)
        if len(mt):
            counts, edges = np.histogram(mt, bins=256, range=(0, 4095))
            centers = ((edges[:-1] + edges[1:]) / 2).astype(int)
            with open(output_path.with_suffix(".csv"), "w") as f:
                f.write("bin_center,count\n")
                for c, n in zip(centers, counts):
                    f.write(f"{c},{n}\n")

        return mt, ph, None

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return np.array([], dtype=np.uint16), np.array([], dtype=np.uint32), str(exc)

    finally:
        # ---- teardown (same as notebook) ----
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
