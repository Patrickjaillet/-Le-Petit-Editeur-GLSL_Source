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

from PySide6.QtCore import QObject, QUrl, Signal
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
    thread at however fast the decoder/camera can go.

    `sourceLost(str)` fires when an already-*started* source (webcam or
    video file) fails or disappears mid-use -- device unplugged, external
    drive holding the file disconnected, driver-level camera error -- as
    opposed to `start_file`/`start_webcam` raising synchronously for a
    source that was never usable in the first place. RM10.md section 1,
    item 6: this must never crash or silently go blank, and must surface an
    explicit message in the textures panel (see
    `MainWindow._on_video_source_lost`) rather than nothing at all. Backed
    by `QCamera.errorOccurred`/`QMediaPlayer.errorOccurred`, which Qt fires
    only for genuine error conditions -- never for our own programmatic
    `stop()` -- so this can't misfire on ordinary reassignment/close.
    """

    sourceLost = Signal(str)

    def __init__(self, on_frame: Callable[[int, int, bytes, float], None], parent=None):
        super().__init__(parent)
        self._on_frame = on_frame
        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_video_frame)
        self._player: QMediaPlayer | None = None
        self._camera: QCamera | None = None
        self._capture_session: QMediaCaptureSession | None = None
        # RM10.md section 5: the `device_id` this source is currently a
        # webcam for -- `None` while not a webcam at all (empty/video-file/
        # never started), `""` for "system default" (see `start_webcam`),
        # otherwise a specific device id the user explicitly picked. Only
        # the `""` case reacts to `_on_video_inputs_changed` below; an
        # explicit pick is left alone even if the system default changes
        # around it.
        self._webcam_device_id: str | None = None
        # A `QMediaDevices` *instance* (not just the static helpers this
        # module already uses elsewhere) is what actually emits
        # `videoInputsChanged` -- Qt Multimedia fires it on that instance
        # whenever the OS-reported video input list (including which one
        # is flagged default) changes, e.g. a camera is plugged in/out or
        # the user changes their default camera in Windows Settings while
        # this source is actively using "system default".
        self._media_devices = QMediaDevices(self)
        self._media_devices.videoInputsChanged.connect(self._on_video_inputs_changed)

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
        self._player.errorOccurred.connect(lambda _error, message: self.sourceLost.emit(message))
        # See `AudioChannelSource.start`'s identical fix for why this must
        # be `QUrl.fromLocalFile(path)`, not a bare `str`: PySide6 coerces
        # a plain string into `QUrl(str)`, which on Windows misreads the
        # drive letter as a URL scheme and produces a non-local-file URL
        # `QMediaPlayer` silently fails to open.
        self._player.setSource(QUrl.fromLocalFile(path))
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
        self._camera.errorOccurred.connect(lambda _error, message: self.sourceLost.emit(message))
        self._capture_session = QMediaCaptureSession(self)
        self._capture_session.setCamera(self._camera)
        self._capture_session.setVideoSink(self._sink)
        self._camera.start()
        # Recorded *after* `self.stop()` above (which itself clears this
        # back to `None`), and after every failure path has already
        # returned via `raise` -- only a genuinely opened webcam counts.
        self._webcam_device_id = device_id

    def _on_video_inputs_changed(self) -> None:
        """RM10.md section 5: reopens the camera when this source is on
        "system default" (`device_id == ""`) and the OS's default video
        input changes while in use -- e.g. the user changes their default
        camera in Windows Settings, or a higher-priority camera is plugged
        in. The previous default may still be perfectly healthy (no
        `errorOccurred`, so `sourceLost` never fires on its own for this
        case), it's simply no longer the one this slot should be watching.
        A no-op for a source that isn't a webcam, or one pinned to a
        specific device the user explicitly picked -- and for a
        default-list change that doesn't actually change which physical
        device the default resolves to."""
        if self._webcam_device_id != "":
            return
        new_default = QMediaDevices.defaultVideoInput()
        if new_default is None or new_default.isNull():
            return
        if self._camera is not None and self._camera.cameraDevice().id() == new_default.id():
            return
        self.start_webcam("")

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
        self._webcam_device_id = None

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
