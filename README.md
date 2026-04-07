# Stage Navigator

A lightweight XY(Z) stage control GUI built with [Dear ImGui](https://github.com/ocornut/imgui) via [imgui-bundle](https://github.com/pthom/imgui_bundle). Designed for microscopy and precision positioning workflows — fast, GPU-rendered, and dependency-light compared to Qt-based alternatives.

---

## Quick Start

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone <this-repo>
cd navigator
uv run navigator
```

`uv` will create a virtualenv and install `imgui-bundle` automatically on first run.

---

## How It Works

### Architecture

```
src/navigator/
├── stage.py      # Stage model — thread-safe position, movement, waypoints
├── viewport.py   # 2D ImDrawList viewport — pan, zoom, click-to-move
├── app.py        # Full UI layout — left panel + toolbar + viewport
└── __main__.py   # immapp entry point
```

### Stage model (`stage.py`)

`Stage` runs a **100 Hz background thread** that smoothly interpolates the current position toward the target at a configurable speed (µm/s). All position reads and writes are mutex-protected.

Key API:

| Method | Description |
|---|---|
| `stage.move_to(x, y, z)` | Set absolute target (any axis optional) |
| `stage.jog(dx, dy, dz)` | Relative move from current target |
| `stage.stop()` | Snap target to current position |
| `stage.home()` | Move to (0, 0, 0) |
| `stage.position` | Read current (x, y, z) in µm |
| `stage.target` | Read current target (x, y, z) |
| `stage.is_moving` | Boolean |
| `stage.speed` | µm/s — settable property |
| `stage.add_waypoint(name)` | Save current position |

The stage is **simulated** by default. To connect real hardware, replace the `_update_loop` internals in `stage.py` — the public API stays the same.

### Viewport (`viewport.py`)

A pure-ImDrawList 2D canvas with:

- **Coordinate system**: stage X right, stage Y up (flipped from screen), origin at (0, 0)
- **Pan**: middle-click + drag
- **Zoom**: scroll wheel, centred on cursor
- **Click to move**: left-click → sends `stage.move_to()` to clicked stage coords
- **Right-click menu**: move here / add waypoint / centre view

Rendered elements:

| Element | Description |
|---|---|
| Grid | Auto-scales minor/major lines to zoom level |
| Scale bar | Shows current grid major step in µm or mm |
| Stage boundary | Red rectangle — soft limits |
| Crosshair (green) | Current stage position |
| Diamond (yellow) | Current move target |
| Trail (blue) | Position history (last 500 points, 200 µm min spacing) |
| Waypoints (pink) | Named saved positions |

### Controls panel (`app.py`)

| Section | Controls |
|---|---|
| **POSITION** | Live X/Y/Z readout (µm) |
| **GO TO** | Drag-to-set X/Y/Z inputs + "Go" button |
| **JOG** | Step size selector + arrow pad (X/Y) + Z+/Z- buttons |
| **SPEED** | Logarithmic slider (100 – 50 000 µm/s) |
| **ACTIONS** | Home (go to origin) + STOP (halt immediately) |
| **WAYPOINTS** | Named save/go/delete per position |

Viewport toolbar shows live position, focus/fit controls, and trail/waypoint toggles.

---

## Coordinate Units

All positions are in **micrometres (µm)**. Default stage limits:

| Axis | Range |
|---|---|
| X | −50 000 to +50 000 µm (±50 mm) |
| Y | −50 000 to +50 000 µm (±50 mm) |
| Z | 0 to 10 000 µm (10 mm) |

---

## Connecting Real Hardware

Edit `src/navigator/stage.py`:

1. Add your SDK import at the top.
2. In `__init__`, open the connection to your controller.
3. Replace the `_update_loop` body: poll the controller for actual position and issue motion commands instead of the simulated integrator.

The `move_to` / `jog` / `stop` / `home` methods already call `self._target_*` — redirect those to your controller's motion API.

---

## Dependencies

| Package | Purpose |
|---|---|
| `imgui-bundle` | Dear ImGui + GLFW + OpenGL bindings (all bundled) |

No Qt, no wx, no Tk — just one package.
