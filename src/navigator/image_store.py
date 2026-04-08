"""Snapped-image store — up to MAX_IMAGES images, held as OpenGL textures.

Images are snapped on a daemon thread (so the UI never blocks) then
uploaded to the GPU on the next render frame.  When the store is full
the oldest image is evicted and its texture deleted.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np

try:
    import OpenGL.GL as gl
    _HAS_GL = True
except ImportError:
    _HAS_GL = False

MAX_IMAGES = 20


@dataclass
class SnappedImage:
    stage_x: float      # centre of image in stage µm
    stage_y: float
    width_um: float     # physical width  in µm
    height_um: float    # physical height in µm
    texture_id: int

    def delete(self) -> None:
        if _HAS_GL:
            gl.glDeleteTextures([self.texture_id])


class ImageStore:
    """Thread-safe store for snapped microscope images."""

    def __init__(self, pixel_size_um: float = 1.468, max_images: int = MAX_IMAGES):
        self.pixel_size_um = pixel_size_um
        self.max_images = max_images
        self.images: list[SnappedImage] = []

        self._lock = threading.Lock()
        self._pending: Optional[tuple] = None   # (sx, sy, w_um, h_um, rgba_u8)
        self.last_error: str = ""
        self.snapping: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def snap_async(self, core, stage_x: float, stage_y: float) -> None:
        """Fire-and-forget snap on a daemon thread."""
        if self.snapping:
            return
        self.snapping = True
        self.last_error = ""
        threading.Thread(
            target=self._worker, args=(core, stage_x, stage_y), daemon=True
        ).start()

    def upload_pending(self) -> None:
        """Upload any pending snap to the GPU.  Must be called from the GL thread."""
        with self._lock:
            pending = self._pending
            self._pending = None

        if pending is None or not _HAS_GL:
            return

        stage_x, stage_y, width_um, height_um, rgba = pending
        h, w = rgba.shape[:2]

        tex_id = int(gl.glGenTextures(1))
        gl.glBindTexture(gl.GL_TEXTURE_2D, tex_id)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexImage2D(
            gl.GL_TEXTURE_2D, 0, gl.GL_RGBA, w, h, 0,
            gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, rgba.tobytes(),
        )
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)

        with self._lock:
            if len(self.images) >= self.max_images:
                self.images.pop(0).delete()
            self.images.append(SnappedImage(stage_x, stage_y, width_um, height_um, tex_id))

    def clear(self) -> None:
        with self._lock:
            for img in self.images:
                img.delete()
            self.images.clear()

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _worker(self, core, stage_x: float, stage_y: float) -> None:
        try:
            core.snap_image()
            tagged = core.get_tagged_image()
            tags = tagged.tags
            w = int(tags["Width"])
            h = int(tags["Height"])
            pix_type = tags.get("PixelType", "GRAY16")

            raw = np.array(tagged.pix, copy=False)

            # Reshape flat pixel buffer using metadata dimensions
            if "RGB" in pix_type:
                raw = raw.reshape(h, w, -1)
            else:
                raw = raw.reshape(h, w)

            width_um  = w * self.pixel_size_um
            height_um = h * self.pixel_size_um

            # Collapse to 2-D grayscale
            gray = raw if raw.ndim == 2 else raw[..., :3].mean(axis=2)

            # Stretch to uint8
            gray = gray.astype(np.float32)
            lo, hi = gray.min(), gray.max()
            if hi > lo:
                gray = ((gray - lo) / (hi - lo) * 255).astype(np.uint8)
            else:
                gray = np.zeros((h, w), dtype=np.uint8)

            # Pack into RGBA (semi-transparent so the grid shows through)
            alpha = np.full((h, w), 210, dtype=np.uint8)
            rgba = np.ascontiguousarray(np.dstack([gray, gray, gray, alpha]))

            with self._lock:
                self._pending = (stage_x, stage_y, width_um, height_um, rgba)

        except Exception as exc:
            with self._lock:
                self.last_error = str(exc)
        finally:
            self.snapping = False
