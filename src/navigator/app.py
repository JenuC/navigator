"""Main application — layout, controls panel, and event loop."""

from __future__ import annotations

from imgui_bundle import imgui, ImVec2, ImVec4

from .image_store import ImageStore
from .stage import Stage
from .viewport import Viewport


class App:
    PANEL_W = 290.0

    def __init__(self, stage: object | None = None) -> None:
        self.stage = stage if stage is not None else Stage()
        self.viewport = Viewport()

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

        # Imaging
        self._image_store = ImageStore()
        self._pixel_size_um = 1.468

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
        imgui.same_line(spacing=12)
        zoom_pct = self.viewport.zoom * 1000
        imgui.text(f"Zoom {zoom_pct:.2f}x")

        imgui.separator()

    # ------------------------------------------------------------------
    # Left controls panel
    # ------------------------------------------------------------------

    def _draw_controls(self) -> None:
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _section(label: str, color: tuple) -> None:
        imgui.push_style_color(imgui.Col_.text, ImVec4(*color))
        imgui.text(label)
        imgui.pop_style_color()
        imgui.separator()
