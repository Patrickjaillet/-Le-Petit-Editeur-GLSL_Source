"""Cancelable progress dialog for the video-export pipeline
(`video_export.run_export`): capture and encoding are shown as two
separate phases with their own "N/total" counters (see ROADMAP.md, "🎬
Export vidéo" — capture and encoding have very different durations
depending on shader complexity vs resolution/CRF, so collapsing them into
a single bar would be misleading).

The whole capture+encode run happens on the GUI thread, inside `run()`'s
call to `video_export.run_export` -- deliberately *not* on a background
`QThread`. `Engine.render()` is a blocking native (Rust/wgpu) call, and
the live viewport (`ui/viewport.py`) always drives it from the GUI thread
too; there is nothing elsewhere in this codebase that calls it from any
other thread, and wgpu's GPU-driver-facing calls are not guaranteed safe
or non-blocking when made from a thread other than the one the rest of
the app already renders from. An earlier version of this dialog ran
`Engine.render()` on a worker `QThread` instead, and that reliably hung
the very first `render()` call of an export on Windows (0/N frames,
0%, and a Cancel click that could never be observed because the stuck
call held the Python GIL and the worker thread's event loop never got a
chance to notice the cancellation flag) — this file must not reintroduce
that.

Responsiveness without a worker thread comes from `QCoreApplication.
processEvents()`, called once per frame from the `on_capture_progress`/
`on_encode_progress` callbacks that `video_export.run_export` already
invokes after every frame/encode-progress tick: that's what lets the
Cancel button's click actually get processed and repaints happen between
`render()` calls, at the same per-frame cadence the live viewport already
updates at. `should_cancel` is a plain flag flipped by the click handler
-- no `threading.Event` needed now that everything runs on one thread.
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

import video_export
from i18n import tr

# No module-level constants for these two labels: this module is imported
# (via `ui.main_window`) before `main.py` calls `i18n.load_language()`, so
# anything resolved through `tr()` at import time would freeze on an
# unloaded language. `tr("dialogs.export_progress.capture_label")` /
# `tr("dialogs.export_progress.encode_label")` are called lazily instead,
# from `__init__`/the progress callbacks below, well after startup.


class ExportProgressDialog(QDialog):
    """Owns the whole capture+encode run for one export. `run()` blocks
    (modal `exec()`, pumped by our own progress callbacks -- see module
    docstring) until the pipeline finishes, is cancelled, or fails, and
    returns one of `"ok"`, `"cancelled"`, `"error"` — `error_message()`
    holds the detail in the last case.
    """

    def __init__(
        self, engine, n_frames: int, fps: float, width: int, height: int, crf: int, date: tuple, out_path: str,
        audio_path: str | None = None, audio_volume_db: float = 0.0, audio_start_offset: float = 0.0,
        audio_loop: bool = True, audio_bitrate_kbps: int = video_export.DEFAULT_AUDIO_BITRATE_KBPS, parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("dialogs.export_progress.title"))
        self.setModal(True)

        self._engine = engine
        self._n_frames = n_frames
        self._fps = fps
        self._width = width
        self._height = height
        self._crf = crf
        self._date = date
        self._out_path = out_path
        self._audio_path = audio_path
        self._audio_volume_db = audio_volume_db
        self._audio_start_offset = audio_start_offset
        self._audio_loop = audio_loop
        self._audio_bitrate_kbps = audio_bitrate_kbps

        self._result: str | None = None
        self._error_message = ""
        self._cancel_requested = False

        layout = QVBoxLayout(self)
        self._phase_label = QLabel(tr("dialogs.export_progress.capture_progress", done=0, total=n_frames))
        layout.addWidget(self._phase_label)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, max(n_frames, 1))
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self._on_cancel_clicked)
        layout.addWidget(buttons)
        self._cancel_button = buttons.button(QDialogButtonBox.Cancel)

    # ---- Cancel ------------------------------------------------------------
    def _on_cancel_clicked(self) -> None:
        # Disabled rather than hidden: still visible feedback that the
        # click registered, while preventing a second terminate()/kill()
        # race if the user clicks it again before the next should_cancel()
        # poll notices the flag.
        self._cancel_button.setEnabled(False)
        self._cancel_button.setText(tr("dialogs.export_progress.cancelling"))
        self._cancel_requested = True

    def reject(self) -> None:
        # Qt calls this for Esc / a close button, if any ever appears —
        # route it through the same single cancellation path rather than
        # letting the dialog close out from under a still-running export.
        self._on_cancel_clicked()

    def _should_cancel(self) -> bool:
        return self._cancel_requested

    # ---- progress ------------------------------------------------------------
    def _on_capture_progress(self, done: int, total: int) -> None:
        self._phase_label.setText(tr("dialogs.export_progress.capture_progress", done=done, total=total))
        self._progress_bar.setRange(0, max(total, 1))
        self._progress_bar.setValue(done)
        # Nothing runs this dialog's event loop while `run_export` is
        # inside a blocking `render()`/ffmpeg-progress-poll call below --
        # this is what lets the Cancel button, the progress bar repaint,
        # and the rest of the UI stay responsive despite everything
        # happening on the GUI thread. See module docstring for why this
        # runs here instead of on a worker QThread.
        QCoreApplication.processEvents()

    def _on_encode_progress(self, done: int, total: int) -> None:
        self._phase_label.setText(tr("dialogs.export_progress.encode_label"))
        self._progress_bar.setRange(0, max(total, 1))
        self._progress_bar.setValue(done)
        QCoreApplication.processEvents()

    def error_message(self) -> str:
        return self._error_message

    # ---- entry point ------------------------------------------------------------
    def run(self) -> str:
        """Shows the dialog (non-blocking `show()`, not `exec()` --
        nothing re-enters the Qt event loop here; `processEvents()` calls
        from the progress callbacks above are what keep the UI alive
        while `run_export` itself blocks), runs the capture+encode
        pipeline synchronously on this (GUI) thread, and returns `"ok"` /
        `"cancelled"` / `"error"`.
        """
        self.show()
        QCoreApplication.processEvents()
        try:
            video_export.run_export(
                self._engine,
                self._n_frames,
                self._fps,
                self._width,
                self._height,
                self._crf,
                self._date,
                self._out_path,
                audio_path=self._audio_path,
                audio_volume_db=self._audio_volume_db,
                audio_start_offset=self._audio_start_offset,
                audio_loop=self._audio_loop,
                audio_bitrate_kbps=self._audio_bitrate_kbps,
                on_capture_progress=self._on_capture_progress,
                on_encode_progress=self._on_encode_progress,
                should_cancel=self._should_cancel,
            )
        except video_export.ExportCancelled:
            self._result = "cancelled"
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the caller
            self._result = "error"
            self._error_message = str(exc)
        else:
            self._result = "ok"
        finally:
            # `self.close()` would go through `closeEvent` -> the default
            # `QDialog.closeEvent`, which calls `reject()` -- and our
            # `reject()` override (see above) only flips the cancellation
            # flag, it never actually hides the window. That combination
            # is exactly why this dialog used to stay on screen forever
            # once an export finished: `hide()` sidesteps that whole path
            # and unconditionally takes the window off screen, regardless
            # of whether we got here via success, cancellation, or error.
            self.hide()
            QCoreApplication.processEvents()
        return self._result or "error"
