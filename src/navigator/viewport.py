"""2D stage viewport widget — pan, zoom, click-to-move."""

from __future__ import annotations

import math
from typing import Optional

from imgui_bundle import imgui, ImVec2, ImVec4

from .stage import Stage


def _rgba(r: float, g: float, b: float, a: float = 1.0) -> int:
    return imgui.color_convert_float4_to_u32(ImVec4(r, g, b, a))


class Viewport:
    """
    Renders the stage top-down view using ImDrawList.

    Coordinate convention
    ---------------------
    Stage X increases rightward on screen.
    Stage Y increases *upward* on screen (flipped from screen Y).

    Controls
    --------
    - Left-click          → send stage to that position
    - Scroll wheel        → zoom centred on cursor
    - Middle-click + drag → pan
    - Right-click menu    → add waypoint at cursor
    """

    ZOOM_MIN = 0.0001
    ZOOM_MAX = 1.0
    ZOOM_STEP = 1.15

    def __init__(self) -> None:
        self.zoom: float = 0.004  # px / µm
        self.pan_x: float = 0.0  # stage coord at viewport centre
        self.pan_y: float = 0.0
        self.show_history: bool = True
        self.show_waypoints: bool = True

        self._dragging: bool = False
        self._drag_start_m: tuple[float, float] = (0.0, 0.0)
        self._drag_start_p: tuple[float, float] = (0.0, 0.0)
        self._ctx_menu_pos: Optional[tuple[float, float]] = None  # stage coords

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def stage_to_screen(
        self, sx: float, sy: float, cx: float, cy: float
    ) -> tuple[float, float]:
        """Stage µm → screen px (cx/cy = viewport centre in screen px)."""
        return (
            cx + (sx - self.pan_x) * self.zoom,
            cy - (sy - self.pan_y) * self.zoom,
        )

    def screen_to_stage(
        self, px: float, py: float, cx: float, cy: float
    ) -> tuple[float, float]:
        """Screen px → stage µm."""
        return (
            (px - cx) / self.zoom + self.pan_x,
            -((py - cy) / self.zoom) + self.pan_y,
        )

    def zoom_at(
        self, px: float, py: float, cx: float, cy: float, factor: float
    ) -> None:
        """Zoom keeping the stage point under (px, py) fixed on screen."""
        sx, sy = self.screen_to_stage(px, py, cx, cy)
        self.zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, self.zoom * factor))
        self.pan_x = sx - (px - cx) / self.zoom
        self.pan_y = sy + (py - cy) / self.zoom

    def center_on(self, sx: float, sy: float) -> None:
        self.pan_x = sx
        self.pan_y = sy

    def fit_to_stage(self, stage: Stage, vp_w: float, vp_h: float) -> None:
        """Fit entire stage limits into the viewport with 5 % margin."""
        stage_w = stage.x_range[1] - stage.x_range[0]
        stage_h = stage.y_range[1] - stage.y_range[0]
        self.zoom = min(vp_w / stage_w, vp_h / stage_h) * 0.92
        self.pan_x = (stage.x_range[0] + stage.x_range[1]) / 2.0
        self.pan_y = (stage.y_range[0] + stage.y_range[1]) / 2.0

    # ------------------------------------------------------------------
    # Grid helpers
    # ------------------------------------------------------------------

    def _nice_grid(self) -> tuple[float, float]:
        """Return (minor_step, major_step) in stage µm for current zoom."""
        target_px = 50.0  # desired minor spacing in pixels
        raw = target_px / self.zoom
        mag = 10 ** math.floor(math.log10(max(raw, 1e-9)))
        for mult in (1, 2, 5, 10):
            step = mag * mult
            if step * self.zoom >= target_px:
                minor = step
                break
        else:
            minor = mag * 10
        return minor, minor * 5

    # ------------------------------------------------------------------
    # Main draw
    # ------------------------------------------------------------------

    def draw(
        self,
        stage: Stage,
        vp_pos: tuple[float, float],
        vp_size: tuple[float, float],
        image_store=None,
        img_invert_x: bool = False,
        img_invert_y: bool = False,
    ) -> Optional[tuple[float, float]]:
        """
        Render the viewport and return a clicked stage position, or None.

        Call this immediately after `imgui.begin_child(...)`.
        """
        dl = imgui.get_window_draw_list()
        vp_x, vp_y = vp_pos
        vp_w, vp_h = vp_size
        cx = vp_x + vp_w * 0.5
        cy = vp_y + vp_h * 0.5

        clicked_pos: Optional[tuple[float, float]] = None

        # Invisible interaction surface
        imgui.set_cursor_screen_pos(ImVec2(vp_x, vp_y))
        imgui.invisible_button("##vp_surface", ImVec2(vp_w, vp_h))
        is_hovered = imgui.is_item_hovered()
        io = imgui.get_io()
        mx, my = io.mouse_pos.x, io.mouse_pos.y

        # --- Scroll zoom ---
        if is_hovered and io.mouse_wheel != 0:
            factor = self.ZOOM_STEP if io.mouse_wheel > 0 else 1.0 / self.ZOOM_STEP
            self.zoom_at(mx, my, cx, cy, factor)

        # --- Middle-drag pan ---
        if is_hovered and imgui.is_mouse_clicked(imgui.MouseButton_.middle):
            self._dragging = True
            self._drag_start_m = (mx, my)
            self._drag_start_p = (self.pan_x, self.pan_y)

        if self._dragging:
            if imgui.is_mouse_down(imgui.MouseButton_.middle):
                self.pan_x = (
                    self._drag_start_p[0] - (mx - self._drag_start_m[0]) / self.zoom
                )
                self.pan_y = (
                    self._drag_start_p[1] + (my - self._drag_start_m[1]) / self.zoom
                )
            else:
                self._dragging = False

        # --- Left-click → move ---
        if is_hovered and imgui.is_mouse_clicked(imgui.MouseButton_.left):
            clicked_pos = self.screen_to_stage(mx, my, cx, cy)

        # --- Right-click context menu ---
        if is_hovered and imgui.is_mouse_clicked(imgui.MouseButton_.right):
            self._ctx_menu_pos = self.screen_to_stage(mx, my, cx, cy)
            imgui.open_popup("##vp_ctx")

        ctx_wp = None
        if imgui.begin_popup("##vp_ctx"):
            if self._ctx_menu_pos is not None:
                sx, sy = self._ctx_menu_pos
                imgui.text(f"({sx:.1f}, {sy:.1f}) µm")
                imgui.separator()
                if imgui.menu_item("Move here")[0]:
                    clicked_pos = (sx, sy)
                if imgui.menu_item("Add waypoint here")[0]:
                    ctx_wp = (sx, sy)
                if imgui.menu_item("Center view here")[0]:
                    self.center_on(sx, sy)
            imgui.end_popup()

        if ctx_wp is not None:
            stage.move_to(*ctx_wp)
            # Temporarily move to that position to save a waypoint
            import threading, time  # noqa: PLC0415

            def _snap():
                time.sleep(0.05)
                stage.add_waypoint()

            threading.Thread(target=_snap, daemon=True).start()

        # ---------------------------------------------------------------
        # Rendering
        # ---------------------------------------------------------------
        dl.push_clip_rect(ImVec2(vp_x, vp_y), ImVec2(vp_x + vp_w, vp_y + vp_h), True)

        # Background
        dl.add_rect_filled(
            ImVec2(vp_x, vp_y),
            ImVec2(vp_x + vp_w, vp_y + vp_h),
            _rgba(0.07, 0.07, 0.09),
        )

        # --- Snapped images (drawn before grid so grid overlays them) ---
        if image_store is not None:
            with image_store._lock:
                imgs = list(image_store.images)
            uv_min = ImVec2(1.0 if img_invert_x else 0.0, 1.0 if img_invert_y else 0.0)
            uv_max = ImVec2(0.0 if img_invert_x else 1.0, 0.0 if img_invert_y else 1.0)
            for img in imgs:
                # stage_x/y is the top-left corner of the image in stage space.
                # Stage Y increases upward, so top-left in stage = top-left on screen.
                scr_x, scr_y = self.stage_to_screen(img.stage_x, img.stage_y, cx, cy)
                w_px = img.width_um  * self.zoom
                h_px = img.height_um * self.zoom
                p_min = ImVec2(scr_x, scr_y)
                p_max = ImVec2(scr_x + w_px, scr_y + h_px)
                dl.add_image(imgui.ImTextureRef(img.texture_id), p_min, p_max, uv_min, uv_max)
                # Thin border so image boundary is visible
                dl.add_rect(p_min, p_max, _rgba(0.6, 0.6, 0.6, 0.4), 0.0, 0, 1.0)

        # --- Grid ---
        minor, major = self._nice_grid()
        # Visible stage extents
        ls, ts = self.screen_to_stage(
            vp_x, vp_y, cx, cy
        )  # left, top-of-screen (high Y)
        rs, bs = self.screen_to_stage(
            vp_x + vp_w, vp_y + vp_h, cx, cy
        )  # right, bottom-of-screen (low Y)

        x = math.floor(ls / minor) * minor
        while x <= rs + minor:
            spx, _ = self.stage_to_screen(x, 0, cx, cy)
            on_major = abs(round(x / major) * major - x) < 0.1
            on_axis = abs(x) < 0.1
            col = (
                _rgba(0.55, 0.55, 0.55)
                if on_axis
                else _rgba(0.28, 0.28, 0.30) if on_major else _rgba(0.14, 0.14, 0.16)
            )
            thick = 1.5 if on_axis else (1.0 if on_major else 0.5)
            dl.add_line(ImVec2(spx, vp_y), ImVec2(spx, vp_y + vp_h), col, thick)
            x += minor

        y = math.floor(bs / minor) * minor
        while y <= ts + minor:
            _, spy = self.stage_to_screen(0, y, cx, cy)
            on_major = abs(round(y / major) * major - y) < 0.1
            on_axis = abs(y) < 0.1
            col = (
                _rgba(0.55, 0.55, 0.55)
                if on_axis
                else _rgba(0.28, 0.28, 0.30) if on_major else _rgba(0.14, 0.14, 0.16)
            )
            thick = 1.5 if on_axis else (1.0 if on_major else 0.5)
            dl.add_line(ImVec2(vp_x, spy), ImVec2(vp_x + vp_w, spy), col, thick)
            y += minor

        # --- Stage boundary ---
        bx0, by0 = self.stage_to_screen(stage.x_range[0], stage.y_range[0], cx, cy)
        bx1, by1 = self.stage_to_screen(stage.x_range[1], stage.y_range[1], cx, cy)
        # by0 > by1 because Y is flipped
        dl.add_rect(
            ImVec2(bx0, by1), ImVec2(bx1, by0), _rgba(0.8, 0.2, 0.2, 0.5), 0.0, 0, 2.0
        )

        # --- History trail ---
        if self.show_history and len(stage.history) >= 2:
            for i in range(1, len(stage.history)):
                p0 = stage.history[i - 1]
                p1 = stage.history[i]
                hx0, hy0 = self.stage_to_screen(p0[0], p0[1], cx, cy)
                hx1, hy1 = self.stage_to_screen(p1[0], p1[1], cx, cy)
                alpha = 0.25 + 0.5 * (i / len(stage.history))
                dl.add_line(
                    ImVec2(hx0, hy0), ImVec2(hx1, hy1), _rgba(0.3, 0.6, 1.0, alpha), 1.5
                )

        # --- Waypoints ---
        if self.show_waypoints:
            for wp in stage.waypoints:
                wpx, wpy = self.stage_to_screen(wp.x, wp.y, cx, cy)
                dl.add_circle_filled(ImVec2(wpx, wpy), 6.0, _rgba(1.0, 0.4, 0.8))
                dl.add_circle(ImVec2(wpx, wpy), 7.0, _rgba(1.0, 0.75, 0.93), 0, 1.5)
                dl.add_text(ImVec2(wpx + 9, wpy - 10), _rgba(1.0, 0.75, 0.93), wp.name)

        # --- Target marker (diamond) ---
        tx, ty, _ = stage.target
        tpx, tpy = self.stage_to_screen(tx, ty, cx, cy)
        r = 9.0
        col_tgt = _rgba(1.0, 0.75, 0.0, 0.9)
        dl.add_line(ImVec2(tpx - r, tpy), ImVec2(tpx, tpy - r), col_tgt, 1.5)
        dl.add_line(ImVec2(tpx, tpy - r), ImVec2(tpx + r, tpy), col_tgt, 1.5)
        dl.add_line(ImVec2(tpx + r, tpy), ImVec2(tpx, tpy + r), col_tgt, 1.5)
        dl.add_line(ImVec2(tpx, tpy + r), ImVec2(tpx - r, tpy), col_tgt, 1.5)

        # --- Current position crosshair ---
        px_x, px_y, _ = stage.position
        spx, spy = self.stage_to_screen(px_x, px_y, cx, cy)
        arm = 14.0
        gap = 4.0
        col_pos = _rgba(0.2, 1.0, 0.35)
        # Horizontal arms
        dl.add_line(ImVec2(spx - arm, spy), ImVec2(spx - gap, spy), col_pos, 2.0)
        dl.add_line(ImVec2(spx + gap, spy), ImVec2(spx + arm, spy), col_pos, 2.0)
        # Vertical arms
        dl.add_line(ImVec2(spx, spy - arm), ImVec2(spx, spy - gap), col_pos, 2.0)
        dl.add_line(ImVec2(spx, spy + gap), ImVec2(spx, spy + arm), col_pos, 2.0)
        # Centre ring
        dl.add_circle(ImVec2(spx, spy), gap + 1, col_pos, 0, 1.5)

        # --- Scale bar (bottom-left) ---
        scale_stage = major
        scale_px = scale_stage * self.zoom
        sb_x = vp_x + 18
        sb_y = vp_y + vp_h - 22
        dl.add_line(
            ImVec2(sb_x, sb_y), ImVec2(sb_x + scale_px, sb_y), _rgba(0.8, 0.8, 0.8), 1.5
        )
        dl.add_line(
            ImVec2(sb_x, sb_y - 4), ImVec2(sb_x, sb_y + 4), _rgba(0.8, 0.8, 0.8), 1.5
        )
        dl.add_line(
            ImVec2(sb_x + scale_px, sb_y - 4),
            ImVec2(sb_x + scale_px, sb_y + 4),
            _rgba(0.8, 0.8, 0.8),
            1.5,
        )
        lbl = (
            f"{scale_stage:.0f} µm"
            if scale_stage < 1000
            else f"{scale_stage / 1000:.1f} mm"
        )
        dl.add_text(ImVec2(sb_x, sb_y - 16), _rgba(0.8, 0.8, 0.8), lbl)

        # --- Mouse coordinate readout ---
        if is_hovered:
            ms_x, ms_y = self.screen_to_stage(mx, my, cx, cy)
            coord_str = f"  ({ms_x:+.1f},  {ms_y:+.1f}) µm"
            dl.add_text(ImVec2(vp_x + 4, vp_y + 4), _rgba(0.5, 0.5, 0.55), coord_str)

        dl.pop_clip_rect()

        return clicked_pos
