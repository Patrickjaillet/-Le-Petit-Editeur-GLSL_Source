"""Video-file and webcam frame sources for iChannel slots.

The Rust engine does no video decoding itself (adding a video codec
dependency to `rust_engine` — ffmpeg, gstreamer, or similar — would mean
shipping and licensing a whole extra native stack just to duplicate work
Qt already does). Instead, `VideoChannelSource` wraps Qt's own decoders —
`QMediaPlayer` for a video file, `QCamera` for a webcam — and pushes every
decoded frame to the engine as a plain RGBA8 upload
(`Engine.update_ichannel_video_frame`): from the engine's point of view
this is exactly the same shape as a single loaded image, just repeated
every frame instead of uploaded once. See `renderer::ChannelInput::Video`
on the Rust side.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import (
    QCamera,
    QMediaCaptureSession,
    QMediaDevices,
    QMediaPlayer,
    QVideoFrame,
    QVideoSink,
)

# Common containers a `QMediaPlayer`/the platform's media framework can
# demux+decode; unlike images there's no universal "the app parses it
# itself" fallback; if a Windows install lacks the right codec for one of
# these it'll surface as a normal, catchable playback error via
# `QMediaPlayer.errorOccurred` (see `start_file`), not a crash.
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")


def list_cameras() -> list[tuple[str, str]]:
    """`(device_id, human-readable description)` for every camera Qt can
    currently see, for the webcam-picker dialog (`ichannel_panel.py`).
    `device_id` is exactly what gets stored in the project file and handed
    back to `start_webcam` on reload."""
    return [
        (bytes(cam.id()).decode("utf-8", "replace"), cam.description())
        for cam in QMediaDevices.videoInputs()
    ]


class VideoChannelSource(QObject):
    """One live iChannel video/webcam source, bound to a single
    (pass, channel) slot by whoever constructs it. `on_frame(width, height,
    rgba_bytes, time_seconds)` fires on the Qt main thread every time a new
    frame is decoded; the caller is expected to push it straight into the
    render engine (`Engine.update_ichannel_video_frame`) and do nothing
    slower than that from inside the callback, since it runs on the UI
    thread at however fast the decoder/camera can go."""

    def __init__(self, on_frame: Callable[[int, int, bytes, float], None], parent=None):
        super().__init__(parent)
        self._on_frame = on_frame
        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_video_frame)
        self._player: QMediaPlayer | None = None
        self._camera: QCamera | None = None
        self._capture_session: QMediaCaptureSession | None = None

    # ---- video file playback ----------------------------------------------

    def start_file(self, path: str) -> None:
        """Plays `path` on loop, silently: no `setAudioOutput` is ever
        wired up, since this engine has no audio graph to feed decoded
        audio into (it's a visual-only iChannel source, like Shadertoy's
        own "Video" option, whose frames are exactly what's sampled)."""
        self.stop()
        self._player = QMediaPlayer(self)
        self._player.setVideoSink(self._sink)
        self._player.setLoops(QMediaPlayer.Infinite)
        self._player.setSource(path)
        self._player.play()

    # ---- webcam -------------------------------------------------------------

    def start_webcam(self, device_id: str) -> None:
        """Opens the camera identified by `device_id` (as returned by
        `list_cameras`), or the system default if `device_id` is empty or
        no longer matches any currently attached camera — matching how a
        previously-saved webcam assignment degrades gracefully if the
        project is reopened on a machine with a different camera."""
        self.stop()
        chosen = None
        if device_id:
            for cam in QMediaDevices.videoInputs():
                if bytes(cam.id()).decode("utf-8", "replace") == device_id:
                    chosen = cam
                    break
        if chosen is None:
            chosen = QMediaDevices.defaultVideoInput()
        if chosen is None or chosen.isNull():
            raise RuntimeError("aucune webcam détectée sur cette machine")
        self._camera = QCamera(chosen, self)
        self._capture_session = QMediaCaptureSession(self)
        self._capture_session.setCamera(self._camera)
        self._capture_session.setVideoSink(self._sink)
        self._camera.start()

    # ---- shared ---------------------------------------------------------------

    def stop(self) -> None:
        """Releases whichever of file playback / webcam capture is
        currently active, if any. Safe to call unconditionally (including
        from `VideoChannelSource.__del__`-adjacent cleanup paths like
        `MainWindow.closeEvent`), since a webcam left open would otherwise
        keep the physical device — and, on most OSes, its capture-active
        indicator light — locked after the app has moved on."""
        if self._player is not None:
            self._player.stop()
            self._player.deleteLater()
            self._player = None
        if self._camera is not None:
            self._camera.stop()
            self._camera.deleteLater()
            self._camera = None
        if self._capture_session is not None:
            self._capture_session.deleteLater()
            self._capture_session = None

    def _on_video_frame(self, frame: QVideoFrame) -> None:
        if not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        image = image.convertToFormat(QImage.Format_RGBA8888)
        width, height = image.width(), image.height()
        if width <= 0 or height <= 0:
            return
        # QImage keeps its own internal row alignment (`bytesPerLine()` can
        # exceed `width * 4`); strip that padding out so the engine always
        # receives a tightly packed `4 * width * height` buffer with no
        # per-row gaps, matching what `ChannelTexture::write_rgba` expects
        # (the same contract `ChannelTexture::from_file` already has for a
        # loaded image, via the `image` crate's own tightly packed output).
        raw = bytes(image.constBits())
        stride = image.bytesPerLine()
        row_bytes = width * 4
        if stride == row_bytes:
            data = raw[: row_bytes * height]
        else:
            data = b"".join(raw[r * stride : r * stride + row_bytes] for r in range(height))
        # QVideoFrame::startTime() is microseconds since that source's own
        # start, or -1 if unknown (some camera backends never report one).
        time_s = frame.startTime() / 1_000_000.0 if frame.startTime() >= 0 else 0.0
        self._on_frame(width, height, data, time_s)
