"""Monaco-based GLSL editor widget (QWebEngineView + QWebChannel bridge)."""
from __future__ import annotations

import json

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView

import local_server

READY_POLL_MS = 50


class _EditorBridge(QObject):
    """QObject exposed to the JS side as `bridge`."""

    contentChanged = Signal(str)

    @Slot(str)
    def onContentChanged(self, text: str) -> None:
        self.contentChanged.emit(text)


class MonacoEditor(QWebEngineView):
    """Embeds Monaco Editor and exposes a simple Python API around it."""

    textChanged = Signal(str)
    editorReady = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge = _EditorBridge(self)
        self._bridge.contentChanged.connect(self.textChanged)
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self._bridge)
        self.page().setWebChannel(self._channel)

        self._ready = False
        self._pending_value: str | None = None
        self._pending_language: str | None = None

        self.loadFinished.connect(self._on_load_finished)

        port = local_server.start()
        self.load(QUrl(f"http://127.0.0.1:{port}/web/index.html"))

    # ---- readiness handshake ----------------------------------------------
    # The page loads asynchronously and Monaco itself is loaded/created via
    # an async AMD `require([...])` call, so `loadFinished` alone does not
    # mean `setEditorValue`/friends exist yet. Poll `window.editor` instead
    # of racing calls against a not-yet-defined JS function.

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            self._poll_ready()

    def _poll_ready(self) -> None:
        def check(is_ready) -> None:
            if is_ready:
                self._ready = True
                if self._pending_value is not None:
                    text, self._pending_value = self._pending_value, None
                    self.set_value(text)
                if self._pending_language is not None:
                    language_id, self._pending_language = self._pending_language, None
                    self.set_language(language_id)
                self.editorReady.emit()
            else:
                QTimer.singleShot(READY_POLL_MS, self._poll_ready)

        self.page().runJavaScript("!!window.editor", check)

    def set_value(self, text: str) -> None:
        if not self._ready:
            self._pending_value = text
            return
        js = f"setEditorValue({json.dumps(text)});"
        self.page().runJavaScript(js)

    def replace_value(self, text: str) -> None:
        """Replaces content via an editable operation so Ctrl+Z can undo it."""
        js = f"replaceEditorValue({json.dumps(text)});"
        self.page().runJavaScript(js)

    def replace_range(self, start: int, end: int, text: str) -> None:
        """Rewrites a single character range in place (used by sliders)."""
        if not self._ready:
            return
        js = f"replaceRange({start}, {end}, {json.dumps(text)});"
        self.page().runJavaScript(js)

    def get_value(self, callback) -> None:
        """Asynchronously fetches the current editor text via `callback(text)`."""
        if not self._ready:
            return
        self.page().runJavaScript("getEditorValue();", callback)

    def set_language(self, language_id: str) -> None:
        """`language_id` is `"glsl"` or `"wgsl"` (both registered in
        `index.html`). RM10.md section 2, item 1."""
        if not self._ready:
            self._pending_language = language_id
            return
        js = f"setEditorLanguage({json.dumps(language_id)});"
        self.page().runJavaScript(js)

    def set_error_marker(self, line: int | None, message: str | None) -> None:
        js = f"setErrorMarker({json.dumps(line)}, {json.dumps(message)});"
        self.page().runJavaScript(js)

    def clear_error_marker(self) -> None:
        self.set_error_marker(None, None)

    def undo(self) -> None:
        self.page().runJavaScript("editor && editor.trigger('app', 'undo', null);")

    def redo(self) -> None:
        self.page().runJavaScript("editor && editor.trigger('app', 'redo', null);")

    def set_font_size(self, px: int) -> None:
        self.page().runJavaScript(f"editor && editor.updateOptions({{fontSize: {int(px)}}});")

    def set_minimap_enabled(self, enabled: bool) -> None:
        flag = "true" if enabled else "false"
        self.page().runJavaScript(f"editor && editor.updateOptions({{minimap: {{enabled: {flag}}}}});")
