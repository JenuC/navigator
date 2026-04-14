"""Main application — layout, controls panel, and event loop."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from imgui_bundle import imgui, ImVec2, ImVec4

log = logging.getLogger(__name__)

from .image_store import ImageStore
from .stage import Stage
from .viewport import Viewport


@dataclass
class PointResult:
    photon_count: int = 0
    error: str | None = None
    microtimes: np.ndarray | None = None  # uint16, shape (N,)


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

        # Sequence state (guarded by _seq_lock)
        self._seq_lock = threading.Lock()
        self._seq_stop_event = threading.Event()
        self._seq_running: bool = False
        self._seq_idx: int = -1
        self._seq_thread: threading.Thread | None = None
        self._point_results: dict[int, PointResult] = {}

        # Histogram acquisition state (guarded by _seq_lock)
        self._hist_running: bool = False
        self._hist_running_idx: int = -1
        self._hist_thread: threading.Thread | None = None

        # Object size for Fermat spiral scan ROI (pixels square)
        self._object_size: int = 50

        # Index of the point whose histogram is displayed (-1 = none)
        self._hist_point_idx: int = -1

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

    def _draw_histogram_panel(self) -> None:
        HIST_H = 180.0
        imgui.begin_child("##histpanel", ImVec2(-1, HIST_H), imgui.ChildFlags_.borders)

        idx = self._hist_point_idx
        with self._seq_lock:
            results = dict(self._point_results)

        if idx >= 0 and idx in results and results[idx].microtimes is not None:
            mt = results[idx].microtimes
            label = self._mark_points[idx][0] if idx < len(self._mark_points) else f"P{idx}"
            self._section(
                f"HISTOGRAM  {label}  ({len(mt):,} ph)", (0.9, 0.75, 0.4, 1.0)
            )
            try:
                from imgui_bundle import implot

                avail = imgui.get_content_region_avail()
                if implot.begin_plot("##mt_hist", ImVec2(avail.x, avail.y - 2)):
                    implot.setup_axes("microtime (ch)", "counts")
                    implot.setup_axis_limits(
                        implot.ImAxis_.x1, 0.0, 4095.0, implot.Cond_.always
                    )
                    implot.plot_histogram("##mt", mt.astype(np.float32), bins=256)
                    implot.end_plot()
            except Exception as exc:
                imgui.text_colored(ImVec4(1.0, 0.4, 0.4, 1.0), f"implot: {exc}")
        else:
            imgui.text_disabled("No histogram — run acquisition or click Hist")

        imgui.end_child()

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
            self._hist_point_idx = -1

        # ---- ACQUISITION ----
        self._section("ACQUISITION", (0.9, 0.75, 0.4, 1.0))

        imgui.set_next_item_width(70)
        _, self._dwell_s = imgui.input_float(
            "##dwell", self._dwell_s, 0.0, 0.0, "%.1f s"
        )
        if imgui.is_item_hovered():
            imgui.set_tooltip("Dwell time: acquisition duration per point (seconds)")
        imgui.same_line()
        imgui.text("dwell")
        imgui.same_line(spacing=16)
        imgui.set_next_item_width(55)
        _, self._settle_s = imgui.input_float(
            "##settle", self._settle_s, 0.0, 0.0, "%.1f s"
        )
        if imgui.is_item_hovered():
            imgui.set_tooltip("Settle time: wait after moving to each point before acquisition starts (seconds)")
        imgui.same_line()
        imgui.text("settle")

        imgui.set_next_item_width(w - 32 - gap)
        _, self._output_folder = imgui.input_text(
            "##folder", self._output_folder, 512
        )
        if imgui.is_item_hovered():
            imgui.set_tooltip("Output directory — .spc (raw FIFO) and .npz (microtimes) files saved here")
        imgui.same_line()
        imgui.text_disabled("dir")

        imgui.set_next_item_width(80)
        _, self._object_size = imgui.input_int("##objsize", self._object_size)
        if imgui.is_item_hovered():
            imgui.set_tooltip("Object size (px): Fermat spiral ROI = (FOV-obj, FOV-obj, obj, obj)")
        imgui.same_line()
        imgui.text_disabled("obj px")

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

        imgui.separator()

        self._draw_histogram_panel()

        # ---- Points list (takes remaining space) ----
        imgui.begin_child("##mplist", ImVec2(-1, -1), imgui.ChildFlags_.none)
        to_del = -1
        with self._seq_lock:
            results = dict(self._point_results)
            cur_idx = self._seq_idx
            hist_running = self._hist_running
            hist_running_idx = self._hist_running_idx

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
                r = results[i]
                if r.error is None:
                    imgui.text_colored(ImVec4(0.5, 1.0, 0.5, 1.0), f"{r.photon_count:,}")
                    imgui.same_line()
                    if hist_running_idx == i:
                        imgui.text_colored(ImVec4(1.0, 0.85, 0.3, 1.0), "hist…")
                    else:
                        can_hist = not running and not hist_running and self._spc is not None
                        if not can_hist:
                            imgui.begin_disabled()
                        btn_label = "Hist*" if r.microtimes is not None else "Hist"
                        if imgui.small_button(f"{btn_label}##{i}"):
                            if r.microtimes is not None:
                                # data already exists — just show it
                                self._hist_point_idx = i
                            else:
                                with self._seq_lock:
                                    self._hist_running = True
                                    self._hist_running_idx = i
                                self._hist_thread = threading.Thread(
                                    target=self._run_hist_acq, args=(i,), daemon=True
                                )
                                self._hist_thread.start()
                        if imgui.is_item_hovered():
                            tip = "Show histogram" if r.microtimes is not None else "Acquire microtimes at this point"
                            imgui.set_tooltip(tip)
                        if not can_hist:
                            imgui.end_disabled()
                else:
                    imgui.text_colored(ImVec4(1.0, 0.4, 0.4, 1.0), "ERR")
                    if imgui.is_item_hovered():
                        imgui.set_tooltip(r.error)
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
                if self._hist_running_idx == to_del:
                    self._hist_running_idx = -1
                elif self._hist_running_idx > to_del:
                    self._hist_running_idx -= 1
            if self._hist_point_idx == to_del:
                self._hist_point_idx = -1
            elif self._hist_point_idx > to_del:
                self._hist_point_idx -= 1
        imgui.end_child()

    # ------------------------------------------------------------------
    # Sequence helpers
    # ------------------------------------------------------------------

    def _run_hist_acq(self, idx: int) -> None:
        """Background thread: move to point idx, acquire microtimes, update result."""
        try:
            pt = self._mark_points[idx]
        except IndexError:
            with self._seq_lock:
                self._hist_running = False
                self._hist_running_idx = -1
            return

        label, x, y = pt[0], pt[1], pt[2]
        log.debug("hist acq [%s]: moving to (%.1f, %.1f)", label, x, y)
        self.stage.move_to(x, y)

        settle_end = time.monotonic() + self._settle_s
        while time.monotonic() < settle_end:
            time.sleep(0.05)

        safe = label.replace("/", "_").replace("\\", "_")
        out = Path(self._output_folder) / f"{safe}_x{x:.0f}_y{y:.0f}.spc"
        core = getattr(self.stage, "core", None)
        spcm = getattr(self._spc, "_spcm", None)
        mod_no = getattr(self._spc, "_mod_no", 0)
        log.debug("hist acq [%s]: core=%r  spcm=%r  mod_no=%d  dwell=%.1fs  obj=%dpx  out=%s",
                  label, core, spcm, mod_no, self._dwell_s, self._object_size, out)

        from . import flim_acquire
        mt, ph, err = flim_acquire.acquire_point(
            core, spcm, self._dwell_s, out,
            object_size=self._object_size,
            mod_no=mod_no,
        )
        photons = int(len(ph))
        microtimes = mt if err is None else None
        log.debug("hist acq [%s]: done — photons=%d  err=%r", label, photons, err)
        with self._seq_lock:
            self._point_results[idx] = PointResult(
                photon_count=photons,
                error=err,
                microtimes=microtimes,
            )
            self._hist_running = False
            self._hist_running_idx = -1

        if microtimes is not None and err is None:
            self._hist_point_idx = idx

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
                self._dwell_s,
                out,
                stop_event=self._seq_stop_event,
            )

            with self._seq_lock:
                self._point_results[i] = PointResult(
                    photon_count=photons,
                    error=err,
                )

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
