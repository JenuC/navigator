"""Main application — layout, controls panel, and event loop."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from imgui_bundle import imgui, ImVec2, ImVec4

from .image_store import ImageStore
from .stage import Stage
from .viewport import Viewport


class App:
    PANEL_W = 290.0

    def __init__(self, stage: object | None = None, spc: object | None = None) -> None:
        self.stage = stage if stage is not None else Stage()
        self.viewport = Viewport()
        self._spc = spc

        # Go-to inputs
        self._goto_x = 0.0
        self._goto_y = 0.0
        self._goto_z = 0.0

        # Jog
        STEP_SIZES = [1, 10, 100, 500, 1_000, 5_000, 10_000]
        self._step_sizes = STEP_SIZES
        self._step_idx = 4  # default 1000 µm

        # Waypoints
        self._wp_name_buf = ""

        # Mark points
        self._mark_points: list[list] = []  # each: [label, x, y]

        # Acquisition settings
        self._dwell_s: float = 10.0
        self._settle_s: float = 1.0
        self._output_folder: str = str(Path.cwd() / "data")

        # MM hook: list of [device, property, value] rows applied before each acquisition
        self._mm_hook_rows: list[list[str]] = []
        self._hook_dev_buf: str = ""
        self._hook_prop_buf: str = ""
        self._hook_val_buf: str = ""

        # Sequence state (guarded by _seq_lock)
        self._seq_lock = threading.Lock()
        self._seq_stop_event = threading.Event()
        self._seq_running: bool = False
        self._seq_idx: int = -1
        self._seq_thread: threading.Thread | None = None
        self._point_results: dict[int, tuple[int, str | None]] = {}

        # Imaging
        self._image_store = ImageStore()
        self._pixel_size_um = 1.468
        self._img_invert_x = False
        self._img_invert_y = False

        self._first_frame = True

    # ------------------------------------------------------------------
    # Top-level render — called every frame by immapp
    # ------------------------------------------------------------------

    def render(self) -> None:
        self._image_store.upload_pending()   # GPU upload must happen on the GL thread

        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(vp.work_pos)
        imgui.set_next_window_size(vp.work_size)
        flags = (
            imgui.WindowFlags_.no_title_bar
            | imgui.WindowFlags_.no_resize
            | imgui.WindowFlags_.no_move
            | imgui.WindowFlags_.no_scrollbar
            | imgui.WindowFlags_.no_scroll_with_mouse
            | imgui.WindowFlags_.no_collapse
            | imgui.WindowFlags_.no_saved_settings
        )
        imgui.begin("##root", None, flags)

        avail = imgui.get_content_region_avail()
        total_h = avail.y

        # ---- Left controls panel ----
        imgui.begin_child(
            "##ctrl",
            ImVec2(self.PANEL_W, total_h),
            imgui.ChildFlags_.borders,
        )
        self._draw_controls()
        imgui.end_child()

        imgui.same_line()

        # ---- Viewport area ----
        stage_w = avail.x - self.PANEL_W - imgui.get_style().item_spacing.x
        imgui.begin_child(
            "##vp_area",
            ImVec2(stage_w, total_h),
            imgui.ChildFlags_.borders,
            imgui.WindowFlags_.no_scrollbar,
        )
        self._draw_viewport_toolbar()

        if self._first_frame:
            avail2 = imgui.get_content_region_avail()
            self.viewport.fit_to_stage(self.stage, avail2.x, avail2.y)
            self._first_frame = False

        cursor = imgui.get_cursor_screen_pos()
        avail2 = imgui.get_content_region_avail()
        clicked = self.viewport.draw(
            self.stage, (cursor.x, cursor.y), (avail2.x, avail2.y),
            self._image_store,
            self._img_invert_x,
            self._img_invert_y,
            self._mark_points,
        )
        if clicked is not None:
            sx, sy = clicked
            self.stage.move_to(sx, sy)
            self._goto_x, self._goto_y = sx, sy

        imgui.end_child()
        imgui.end()

    # ------------------------------------------------------------------
    # Viewport toolbar (above the viewport)
    # ------------------------------------------------------------------

    def _draw_viewport_toolbar(self) -> None:
        x, y, z = self.stage.position

        moving = self.stage.is_moving
        status_col = ImVec4(0.25, 1.0, 0.35, 1.0) if not moving else ImVec4(1.0, 0.75, 0.1, 1.0)
        imgui.text_colored(status_col, "●")
        imgui.same_line()
        imgui.text(f"{'MOVING' if moving else 'IDLE ':6s}  "
                   f"X {x:+10.1f}  Y {y:+10.1f}  Z {z:+8.2f}  µm")

        imgui.same_line(spacing=16)
        if imgui.small_button("Fit"):
            avail = imgui.get_content_region_avail()
            self.viewport.fit_to_stage(self.stage, avail.x, avail.y)
        imgui.same_line()
        if imgui.small_button("Center"):
            px, py, _ = self.stage.position
            self.viewport.center_on(px, py)
        imgui.same_line()
        if imgui.small_button("Home view"):
            self.viewport.center_on(0.0, 0.0)
        imgui.same_line(spacing=12)
        _, self.viewport.show_history = imgui.checkbox("Trail", self.viewport.show_history)
        imgui.same_line()
        _, self.viewport.show_waypoints = imgui.checkbox("Waypoints", self.viewport.show_waypoints)
        imgui.same_line()
        _, self.viewport.show_mark_points = imgui.checkbox("Points", self.viewport.show_mark_points)
        imgui.same_line(spacing=12)
        zoom_pct = self.viewport.zoom * 1000
        imgui.text(f"Zoom {zoom_pct:.2f}x")

        imgui.separator()

    # ------------------------------------------------------------------
    # Left controls panel
    # ------------------------------------------------------------------

    def _draw_controls(self) -> None:
        if not imgui.begin_tab_bar("##ctrl_tabs"):
            return
        if imgui.begin_tab_item("Stage")[0]:
            self._draw_stage_tab()
            imgui.end_tab_item()
        if imgui.begin_tab_item("Points")[0]:
            self._draw_points_tab()
            imgui.end_tab_item()
        imgui.end_tab_bar()

    def _draw_stage_tab(self) -> None:
        backend = getattr(self.stage, "backend_name", "Simulated")
        connected = getattr(self.stage, "connected", True)
        conn_col = ImVec4(0.25, 1.0, 0.35, 1.0) if connected else ImVec4(1.0, 0.3, 0.3, 1.0)
        conn_label = "CONNECTED" if connected else "DISCONNECTED"
        self._section("BACKEND", (0.7, 0.7, 0.7, 1.0))
        imgui.text(backend)
        imgui.same_line()
        imgui.text_colored(conn_col, f"● {conn_label}")
        imgui.spacing()

        self._section("POSITION", (0.35, 1.0, 0.55, 1.0))
        x, y, z = self.stage.position
        imgui.text(f"X  {x:+12.2f} µm")
        imgui.text(f"Y  {y:+12.2f} µm")
        imgui.text(f"Z  {z:+12.2f} µm")

        imgui.spacing()
        self._section("GO TO", (1.0, 0.85, 0.3, 1.0))

        w = imgui.get_content_region_avail().x
        imgui.set_next_item_width(w)
        _, self._goto_x = imgui.drag_float(
            "##gx", self._goto_x, 10.0,
            self.stage.x_range[0], self.stage.x_range[1], "X  %+.1f µm"
        )
        imgui.set_next_item_width(w)
        _, self._goto_y = imgui.drag_float(
            "##gy", self._goto_y, 10.0,
            self.stage.y_range[0], self.stage.y_range[1], "Y  %+.1f µm"
        )
        imgui.set_next_item_width(w)
        _, self._goto_z = imgui.drag_float(
            "##gz", self._goto_z, 1.0,
            self.stage.z_range[0], self.stage.z_range[1], "Z  %+.2f µm"
        )
        if imgui.button("Go##goto", ImVec2(w, 28)):
            self.stage.move_to(self._goto_x, self._goto_y, self._goto_z)

        # Sync goto fields with live position (double-click)
        if imgui.is_item_hovered() and imgui.is_mouse_double_clicked(imgui.MouseButton_.left):
            px, py, pz = self.stage.position
            self._goto_x, self._goto_y, self._goto_z = px, py, pz

        imgui.spacing()
        self._section("JOG", (0.5, 0.85, 1.0, 1.0))

        imgui.text("Step:")
        imgui.same_line()
        imgui.set_next_item_width(w - imgui.get_cursor_pos_x() + imgui.get_window_pos().x)
        if imgui.begin_combo("##step_sz", f"{self._step_sizes[self._step_idx]} µm"):
            for i, s in enumerate(self._step_sizes):
                sel = i == self._step_idx
                clicked, _ = imgui.selectable(f"{s} µm", sel)
                if clicked:
                    self._step_idx = i
                if sel:
                    imgui.set_item_default_focus()
            imgui.end_combo()

        step = float(self._step_sizes[self._step_idx])
        btn = 52.0
        gap = imgui.get_style().item_spacing.x
        indent = (w - btn * 3 - gap * 2) * 0.5

        # Row: Y+
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + indent + btn + gap)
        if imgui.button("Y+", ImVec2(btn, 28)):
            self.stage.jog(dy=step)
        imgui.spacing()

        # Row: X-  ·  X+
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + indent)
        if imgui.button("X-", ImVec2(btn, 28)):
            self.stage.jog(dx=-step)
        imgui.same_line()
        imgui.push_style_color(imgui.Col_.button, ImVec4(0.25, 0.25, 0.28, 1.0))
        if imgui.button("·##ctr", ImVec2(btn, 28)):
            pass  # centre noop — could home XY
        imgui.pop_style_color()
        imgui.same_line()
        if imgui.button("X+", ImVec2(btn, 28)):
            self.stage.jog(dx=step)
        imgui.spacing()

        # Row: Y-
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + indent + btn + gap)
        if imgui.button("Y-", ImVec2(btn, 28)):
            self.stage.jog(dy=-step)

        imgui.spacing()
        if imgui.button("Z+##z", ImVec2(w * 0.5 - gap * 0.5, 24)):
            self.stage.jog(dz=step)
        imgui.same_line()
        if imgui.button("Z-##z", ImVec2(-1, 24)):
            self.stage.jog(dz=-step)

        imgui.spacing()
        self._section("SPEED", (1.0, 0.6, 0.4, 1.0))
        import math as _math
        spd = self.stage.speed
        if _math.isnan(spd):
            imgui.text_disabled("Controlled by MM device props")
        else:
            imgui.set_next_item_width(w)
            changed, spd = imgui.slider_float(
                "##spd", spd, 100.0, 50_000.0, "%.0f µm/s",
                imgui.SliderFlags_.logarithmic,
            )
            if changed:
                self.stage.speed = spd

        imgui.spacing()
        self._section("ACTIONS", (0.9, 0.9, 0.9, 1.0))
        half = (w - gap) * 0.5
        if imgui.button("Home##act", ImVec2(half, 30)):
            self.stage.home()
        imgui.same_line()
        imgui.push_style_color(imgui.Col_.button, ImVec4(0.65, 0.1, 0.1, 1.0))
        imgui.push_style_color(imgui.Col_.button_hovered, ImVec4(0.85, 0.15, 0.15, 1.0))
        imgui.push_style_color(imgui.Col_.button_active, ImVec4(1.0, 0.2, 0.2, 1.0))
        if imgui.button("STOP##act", ImVec2(-1, 30)):
            self.stage.stop()
        imgui.pop_style_color(3)

        imgui.spacing()
        self._section("IMAGING", (0.5, 0.85, 1.0, 1.0))

        core = getattr(self.stage, "core", None)
        has_gl = True
        try:
            import OpenGL.GL  # noqa: F401
        except ImportError:
            has_gl = False

        # Pixel size input
        imgui.set_next_item_width(w)
        changed, self._pixel_size_um = imgui.input_float(
            "##px_size", self._pixel_size_um, 0.0, 0.0, "Pixel size %.4f µm"
        )
        if changed:
            self._image_store.pixel_size_um = self._pixel_size_um

        can_snap = core is not None and has_gl
        snap_label = (
            "Snapping…" if self._image_store.snapping
            else "Snap" if can_snap
            else "Snap (sim)"
        )
        if not can_snap:
            imgui.begin_disabled()
        if imgui.button(snap_label, ImVec2(w - gap - 60, 28)) and not self._image_store.snapping:
            sx, sy, _ = self.stage.position
            self._image_store.snap_async(core, sx, sy)
        if not can_snap:
            imgui.end_disabled()

        imgui.same_line()
        n_imgs = len(self._image_store.images)
        if imgui.button(f"Clear ({n_imgs})", ImVec2(-1, 28)):
            self._image_store.clear()

        if self._image_store.last_error:
            imgui.text_colored(ImVec4(1.0, 0.3, 0.3, 1.0), "Err:")
            imgui.same_line()
            imgui.text_wrapped(self._image_store.last_error)

        _, self._img_invert_x = imgui.checkbox("Invert X##img", self._img_invert_x)
        imgui.same_line()
        _, self._img_invert_y = imgui.checkbox("Invert Y##img", self._img_invert_y)

        imgui.spacing()
        self._section("WAYPOINTS", (1.0, 0.65, 0.9, 1.0))
        imgui.set_next_item_width(w - 50 - gap)
        _, self._wp_name_buf = imgui.input_text("##wpn", self._wp_name_buf, 64)
        imgui.same_line()
        if imgui.button("Add##wp", ImVec2(-1, 0)):
            self.stage.add_waypoint(self._wp_name_buf.strip())
            self._wp_name_buf = ""

        imgui.begin_child("##wplist", ImVec2(-1, -1), imgui.ChildFlags_.none)
        to_del = -1
        for i, wp in enumerate(self.stage.waypoints):
            imgui.push_id(i)
            if imgui.small_button("Go"):
                self.stage.move_to(wp.x, wp.y, wp.z)
            imgui.same_line()
            if imgui.small_button("X"):
                to_del = i
            imgui.same_line()
            imgui.text_colored(
                ImVec4(1.0, 0.75, 0.93, 1.0),
                f"{wp.name}",
            )
            imgui.same_line()
            imgui.text_disabled(f"({wp.x:.0f}, {wp.y:.0f})")
            imgui.pop_id()
        if to_del >= 0:
            del self.stage.waypoints[to_del]
        imgui.end_child()

    def _draw_points_tab(self) -> None:
        w = imgui.get_content_region_avail().x
        gap = imgui.get_style().item_spacing.x

        # ---- Mark / Clear ----
        half = (w - gap) * 0.5
        if imgui.button("Mark current##mp", ImVec2(half, 28)):
            x, y, _ = self.stage.position
            n = len(self._mark_points) + 1
            self._mark_points.append([f"P{n}", x, y])
        imgui.same_line()
        n_pts = len(self._mark_points)
        if imgui.button(f"Clear ({n_pts})##mpclear", ImVec2(-1, 28)):
            self._mark_points.clear()
            with self._seq_lock:
                self._point_results.clear()

        # ---- ACQUISITION ----
        self._section("ACQUISITION", (0.9, 0.75, 0.4, 1.0))

        imgui.set_next_item_width(70)
        _, self._dwell_s = imgui.input_float(
            "##dwell", self._dwell_s, 0.0, 0.0, "%.1f s"
        )
        imgui.same_line()
        imgui.text("dwell")
        imgui.same_line(spacing=16)
        imgui.set_next_item_width(55)
        _, self._settle_s = imgui.input_float(
            "##settle", self._settle_s, 0.0, 0.0, "%.1f s"
        )
        imgui.same_line()
        imgui.text("settle")

        imgui.set_next_item_width(w - 32 - gap)
        _, self._output_folder = imgui.input_text(
            "##folder", self._output_folder, 512
        )
        imgui.same_line()
        imgui.text_disabled("dir")

        with self._seq_lock:
            running = self._seq_running
            seq_idx = self._seq_idx

        can_run = not running and self._spc is not None and len(self._mark_points) > 0
        if not can_run:
            imgui.begin_disabled()
        if imgui.button("Run##seq", ImVec2(half, 28)):
            self._seq_stop_event.clear()
            with self._seq_lock:
                self._point_results.clear()
                self._seq_running = True
                self._seq_idx = -1
            self._seq_thread = threading.Thread(
                target=self._run_sequence, daemon=True
            )
            self._seq_thread.start()
        if not can_run:
            imgui.end_disabled()

        imgui.same_line()
        if not running:
            imgui.begin_disabled()
        if imgui.button("Stop##seq", ImVec2(-1, 28)):
            self._seq_stop_event.set()
        if not running:
            imgui.end_disabled()

        if running and self._spc is not None:
            elapsed = self._spc.elapsed
            total = len(self._mark_points)
            imgui.text_colored(
                ImVec4(1.0, 0.85, 0.3, 1.0),
                f"Point {seq_idx + 1}/{total}  {elapsed:.1f} s",
            )
        elif self._spc is None:
            imgui.text_colored(
                ImVec4(0.5, 0.5, 0.5, 1.0), "No SPC  (use --spc or --spc-sim)"
            )

        # ---- MM HOOK ----
        self._section("MM HOOK", (0.7, 0.5, 1.0, 1.0))
        imgui.text_disabled("Applied to pycromanager before each acquisition:")

        col_w = (w - gap * 3) / 3
        imgui.set_next_item_width(col_w)
        _, self._hook_dev_buf = imgui.input_text(
            "##hdev", self._hook_dev_buf, 64
        )
        imgui.same_line()
        imgui.set_next_item_width(col_w)
        _, self._hook_prop_buf = imgui.input_text(
            "##hprop", self._hook_prop_buf, 64
        )
        imgui.same_line()
        imgui.set_next_item_width(col_w - 26 - gap)
        _, self._hook_val_buf = imgui.input_text(
            "##hval", self._hook_val_buf, 64
        )
        imgui.same_line()
        if imgui.button("+##hadd", ImVec2(-1, 0)) and self._hook_dev_buf.strip():
            self._mm_hook_rows.append([
                self._hook_dev_buf.strip(),
                self._hook_prop_buf.strip(),
                self._hook_val_buf.strip(),
            ])
            self._hook_dev_buf = self._hook_prop_buf = self._hook_val_buf = ""

        hook_h = max(min(len(self._mm_hook_rows) * 20 + 6, 80), 24)
        imgui.begin_child(
            "##hookrows", ImVec2(-1, hook_h), imgui.ChildFlags_.borders
        )
        hdel = -1
        for hi, row in enumerate(self._mm_hook_rows):
            imgui.push_id(hi)
            if imgui.small_button("X##hdel"):
                hdel = hi
            imgui.same_line()
            imgui.text(f"{row[0]}  |  {row[1]}  =  {row[2]}")
            imgui.pop_id()
        if hdel >= 0:
            del self._mm_hook_rows[hdel]
        imgui.end_child()

        imgui.separator()

        # ---- Points list (takes remaining space) ----
        imgui.begin_child("##mplist", ImVec2(-1, -1), imgui.ChildFlags_.none)
        to_del = -1
        with self._seq_lock:
            results = dict(self._point_results)
            cur_idx = self._seq_idx

        for i, pt in enumerate(self._mark_points):
            imgui.push_id(i)
            if imgui.small_button("Go##mpgo"):
                self.stage.move_to(pt[1], pt[2])
            imgui.same_line()
            if imgui.small_button("X##mpdel"):
                to_del = i
            imgui.same_line()
            imgui.text_colored(ImVec4(0.4, 1.0, 0.9, 1.0), pt[0])
            imgui.same_line()
            imgui.text_disabled(f"({pt[1]:.0f}, {pt[2]:.0f})")
            imgui.same_line()
            if i in results:
                photons, err = results[i]
                if err is None or err == "":
                    imgui.text_colored(
                        ImVec4(0.5, 1.0, 0.5, 1.0), f"{photons:,}"
                    )
                else:
                    imgui.text_colored(ImVec4(1.0, 0.4, 0.4, 1.0), "ERR")
                    if imgui.is_item_hovered():
                        imgui.set_tooltip(err)
            elif i == cur_idx:
                imgui.text_colored(ImVec4(1.0, 0.85, 0.3, 1.0), "acq…")
            else:
                imgui.text_disabled("-")
            imgui.pop_id()

        if to_del >= 0:
            del self._mark_points[to_del]
            with self._seq_lock:
                self._point_results = {
                    (k if k < to_del else k - 1): v
                    for k, v in self._point_results.items()
                    if k != to_del
                }
        imgui.end_child()

    # ------------------------------------------------------------------
    # Sequence helpers
    # ------------------------------------------------------------------

    def _make_pre_hook(self):
        """Return a callable that applies all MM hook rows, or None."""
        core = getattr(self.stage, "core", None)
        rows = [list(r) for r in self._mm_hook_rows if r[0] and r[1]]
        if not rows or core is None:
            return None

        def hook():
            for device, prop, value in rows:
                core.set_property(device, prop, value)

        return hook

    def _run_sequence(self) -> None:
        """Background thread: move → settle → acquire at each marked point."""
        points = [(i, list(pt)) for i, pt in enumerate(self._mark_points)]

        for i, pt in points:
            with self._seq_lock:
                if self._seq_stop_event.is_set():
                    break
                self._seq_idx = i

            label, x, y = pt[0], pt[1], pt[2]
            self.stage.move_to(x, y)

            # Settle delay (interruptible)
            settle_end = time.monotonic() + self._settle_s
            while time.monotonic() < settle_end:
                if self._seq_stop_event.is_set():
                    break
                time.sleep(0.05)

            if self._seq_stop_event.is_set():
                break

            safe = label.replace("/", "_").replace("\\", "_")
            out = Path(self._output_folder) / f"{safe}_x{x:.0f}_y{y:.0f}.spc"

            photons, err = self._spc.acquire(
                self._dwell_s, out, self._make_pre_hook(), self._seq_stop_event
            )

            with self._seq_lock:
                self._point_results[i] = (photons, err)

        with self._seq_lock:
            self._seq_running = False
            self._seq_idx = -1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _section(label: str, color: tuple) -> None:
        imgui.push_style_color(imgui.Col_.text, ImVec4(*color))
        imgui.text(label)
        imgui.pop_style_color()
        imgui.separator()
