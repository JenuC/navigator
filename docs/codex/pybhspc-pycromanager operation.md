# `pycro-manager` and `pybhspc` operation notes

## `pycro-manager`: start and stop live collection

`pycro-manager` can control live acquisition through either the MMCore layer or the Micro-Manager GUI live-mode layer.

### MMCore continuous acquisition

`Core()` exposes the Micro-Manager Core API. The relevant MMCore methods are:

- `startContinuousSequenceAcquisition(double intervalMs)`
- `stopSequenceAcquisition()`
- `isSequenceRunning()`

In `pycro-manager`, Java camelCase methods are typically available in snake_case:

```python
from pycromanager import Core

core = Core()
core.start_continuous_sequence_acquisition(0)  # 0 = as fast as possible

# ... read images from the circular buffer ...

core.stop_sequence_acquisition()
```

### Micro-Manager GUI Live mode

If the goal is specifically the GUI's Live button behavior, `Studio.live()` exposes `SnapLiveManager`, including `setLiveModeOn(boolean)` and `isLiveModeOn()`.

Typical `pycro-manager` usage:

```python
from pycromanager import Studio

studio = Studio()
studio.live().set_live_mode_on(True)   # start Live
studio.live().set_live_mode_on(False)  # stop Live
```

### References

- `pycro-manager` API docs: <https://pycro-manager.readthedocs.io/en/latest/apis.html>
- Micro-Manager `CMMCore` API: <https://micro-manager.org/apidoc/mmcorej/latest/mmcorej/CMMCore.html>
- Micro-Manager `Studio` API: <https://micro-manager.org/apidoc/mmstudio/2.0.0/org/micromanager/Studio.html>
- Micro-Manager `SnapLiveManager` API: <https://micro-manager.org/apidoc/mmstudio/2.0.0/org/micromanager/SnapLiveManager.html>

## `pybhspc`: start FIFO output and save to `<current_time>.spc`

`pybhspc` provides the low-level FIFO access needed to start measurement, read FIFO data, and stop measurement. It does not appear to provide a higher-level helper for writing a Becker & Hickl `.spc` container format. The straightforward approach is:

1. Initialize the SPC module.
2. Set FIFO mode.
3. Start measurement.
4. Repeatedly read FIFO words with `read_fifo_to_array(...)`.
5. Write those raw FIFO words to a timestamped file.
6. Stop measurement and drain any remaining FIFO data.

Example:

```python
from datetime import datetime
from pathlib import Path
import time

import bh_spc
from bh_spc import spcm

mod_no = 0
buf_size = 32768
duration_s = 5.0

with bh_spc.ini_file(
    bh_spc.minimal_spcm_ini(spcm.DLLOperationMode.HARDWARE)
) as ini:
    spcm.init(ini)

spcm.set_parameter(mod_no, spcm.ParID.MODE, 1)  # FIFO mode for most modern boards

filename = Path(datetime.now().strftime("%Y%m%d_%H%M%S.spc"))

with filename.open("wb") as f:
    spcm.start_measurement(mod_no)
    start = time.monotonic()

    try:
        while time.monotonic() - start < duration_s:
            buf = spcm.read_fifo_to_array(mod_no, buf_size)
            if len(buf):
                buf.tofile(f)  # writes raw uint16 FIFO words
            if len(buf) < buf_size:
                time.sleep(0.001)
    finally:
        spcm.stop_measurement(mod_no)

        # drain anything that arrived just before/after stop
        while True:
            buf = spcm.read_fifo_to_array(mod_no, buf_size)
            if not len(buf):
                break
            buf.tofile(f)

print(f"Saved raw FIFO stream to {filename}")
```

### Caveats

- This writes the raw FIFO stream to a file with a `.spc` extension.
- It may not match a Becker & Hickl `.spc` container format expected by other BH tools.
- If another tool requires a formal BH file structure, `pybhspc` likely does not implement that writer directly.

### References

- `pybhspc` getting started: <https://marktsuchida.github.io/pybhspc/getting_started/>
- `pybhspc` API reference: <https://marktsuchida.github.io/pybhspc/api_spcm/>
- `pybhspc` project docs: <https://marktsuchida.github.io/pybhspc/>
