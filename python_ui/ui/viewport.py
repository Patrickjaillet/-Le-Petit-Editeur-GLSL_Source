"""Resizable render viewport, driven by the Rust engine at ~60fps."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from ui.keymap import qt_key_to_js_keycode

VIEWPORT_WIDTH = 800
VIEWPORT_HEIGHT = 450
MIN_VIEWPORT_SIZE = 160
RESIZE_DEBOUNCE_MS = 150


class Viewport(QWidget):
    fpsUpdated = Signal(float)
    renderError = Signal(str)
    frameRendered = Signal(float)  # wall-clock time spent in engine.render(), in ms
    timeUpdated = Signal(float)  # current iTime (seconds), every tick, paused or not

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self._engine = engine
        self.setMinimumSize(MIN_VIEWPORT_SIZE, MIN_VIEWPORT_SIZE)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        # `iKeyboard` needs actual key events, which Qt only delivers to
        # whichever widget currently has focus - click the viewport first,
        # exactly like clicking a Shadertoy canvas to give it keyboard focus.
        self.setFocusPolicy(Qt.StrongFocus)

        self._render_width = VIEWPORT_WIDTH
        self._render_height = VIEWPORT_HEIGHT

        self._pixmap: QPixmap | None = None
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self._last_time_s = 0.0
        self._frame = 0
        self._paused = False
        self._mouse = (0.0, 0.0, 0.0, 0.0)
        self._click_pos = (0.0, 0.0)

        # `iKeyboard` state (Shadertoy layout: down / pressed-this-frame /
        # toggled, one byte per JS-style legacy keyCode, see `ui.keymap`
        # and `ChannelTexture::write_keyboard_state` on the Rust side).
        # `_key_pressed` accumulates between ticks and is cleared right
        # after each `update_keyboard` upload, so it's a true one-frame
        # pulse no matter how the tick rate relates to key event timing.
        self._key_down = bytearray(256)
        self._key_pressed = bytearray(256)
        self._key_toggled = bytearray(256)

        self._fps_frame_count = 0
        self._fps_accum_ms = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

        # The engine reallocates GPU textures on resize, so that's
        # debounced to the tail end of a drag rather than done per pixel.
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._apply_resize)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    def render_size(self) -> tuple[int, int]:
        """Current (width, height) the engine is actually rendering at --
        what `Engine.resize` was last called with, not necessarily this
        widget's own on-screen size while a resize is still debouncing."""
        return self._render_width, self._render_height

    def suspend_for_external_render(self) -> None:
        """Stops the live ~60fps tick loop and its resize-debounce timer,
        without touching the engine itself, so a caller (the video-export
        flow) can safely drive the shared `Engine` directly -- resize it
        to an export resolution, run a batch of blocking `render()` calls,
        resize it back -- without a live tick landing in between and
        reading pixels sized for the wrong resolution, or a pending
        debounced resize firing mid-export and undoing the export
        resolution. Always pair with `resume_after_external_render`,
        including on the error path (the export may fail partway through)."""
        self._timer.stop()
        self._resize_timer.stop()

    def resume_after_external_render(self) -> None:
        """Restores live ticking after `suspend_for_external_render`.
        Assumes the caller has already put the engine back at
        `render_size()` (this widget's own last-requested resolution)
        before calling this -- otherwise the very next tick renders at a
        stale resolution."""
        self._timer.start(16)

    def reset_time(self) -> None:
        self._elapsed.restart()
        self._last_time_s = 0.0
        self._frame = 0
        self.timeUpdated.emit(0.0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_timer.start(RESIZE_DEBOUNCE_MS)

    def _apply_resize(self) -> None:
        new_width = max(MIN_VIEWPORT_SIZE, self.width())
        new_height = max(MIN_VIEWPORT_SIZE, self.height())
        if (new_width, new_height) == (self._render_width, self._render_height):
            return
        self._engine.resize(new_width, new_height)
        self._render_width = new_width
        self._render_height = new_height

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position()
        x, y = pos.x(), self._render_height - pos.y()
        # Shadertoy convention: iMouse.zw holds the position of the last
        # click, positive while the button is held down.
        self._click_pos = (x, y)
        self._mouse = (x, y, x, y)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            pos = event.position()
            cx, cy = self._click_pos
            self._mouse = (pos.x(), self._render_height - pos.y(), cx, cy)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        # Shadertoy convention: iMouse.zw goes negative once the button is
        # released (xy keeps the last dragged position, per spec).
        cx, cy = self._click_pos
        self._mouse = (self._mouse[0], self._mouse[1], -abs(cx), -abs(cy))

    def keyPressEvent(self, event) -> None:
        code = qt_key_to_js_keycode(event.key())
        if code is None:
            super().keyPressEvent(event)
            return
        self._key_down[code] = 1
        self._key_pressed[code] = 1
        # Matches Shadertoy's own "toggle" row: flips every time the key
        # goes down, including on OS key-repeat (see `ui.keymap` module
        # docstring / the Shadertoy keyboard-texture help panel).
        self._key_toggled[code] ^= 1
        event.accept()

    def keyReleaseEvent(self, event) -> None:
        # X11/Wayland auto-repeat delivers a release+press pair for every
        # repeated keystroke; treating that synthetic release as a real
        # key-up would make `_key_down` flicker off between repeats.
        if event.isAutoRepeat():
            return
        code = qt_key_to_js_keycode(event.key())
        if code is None:
            super().keyReleaseEvent(event)
            return
        self._key_down[code] = 0
        event.accept()

    def _tick(self) -> None:
        now_s = self._elapsed.elapsed() / 1000.0
        dt = 0.0 if self._paused else max(0.0, now_s - self._last_time_s)
        if not self._paused:
            self._last_time_s = now_s
        self.timeUpdated.emit(self._last_time_s)

        self._engine.update_keyboard(bytes(self._key_down), bytes(self._key_pressed), bytes(self._key_toggled))
        # "Pressed this frame" is a one-frame pulse: clear it right after
        # this tick's upload so it doesn't stay true for every following
        # frame until the next key event.
        self._key_pressed = bytearray(256)

        frame_start = QElapsedTimer()
        frame_start.start()
        try:
            raw = self._engine.render(
                self._last_time_s, dt, self._mouse, self._frame, self.current_date()
            )
        except RuntimeError as exc:
            self.renderError.emit(str(exc))
            return
        self.frameRendered.emit(float(frame_start.elapsed()))

        image = QImage(raw, self._render_width, self._render_height, QImage.Format_RGBA8888)
        self._pixmap = QPixmap.fromImage(image)
        self._frame += 1
        self.update()

        self._fps_frame_count += 1
        self._fps_accum_ms += 16.0
        if self._fps_accum_ms >= 500.0:
            fps = self._fps_frame_count / (self._fps_accum_ms / 1000.0)
            self.fpsUpdated.emit(fps)
            self._fps_frame_count = 0
            self._fps_accum_ms = 0.0

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
            if self._pixmap.size() != self.size():
                # letterbox the remainder while a resize is still debouncing
                painter.fillRect(self._pixmap.width(), 0, self.width(), self.height(), Qt.black)
                painter.fillRect(0, self._pixmap.height(), self.width(), self.height(), Qt.black)
        else:
            painter.fillRect(self.rect(), Qt.black)

    @staticmethod
    def current_date() -> tuple[float, float, float, float]:
        """Matches the Shadertoy `iDate` convention: (year, month, day,
        seconds since local midnight). Shadertoy's own implementation uses
        JS `Date.getMonth()`, which is 0-indexed (0 = January) — matched
        here so month arithmetic in pasted shaders behaves identically."""
        now = datetime.now()
        seconds_since_midnight = now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1e6
        return (float(now.year), float(now.month - 1), float(now.day), seconds_since_midnight)

    def export_png(self, path: str) -> bool:
        """Saves the currently displayed frame as a PNG. Returns False if
        nothing has been rendered yet."""
        if self._pixmap is None:
            return False
        return self._pixmap.save(path, "PNG")
