"""Tiny local HTTP server for the assets/ directory.

Monaco Editor loads its language workers via `new Worker(...)`, which
Chromium (and therefore QtWebEngine) refuses to do for `file://` pages.
Serving the assets over `http://127.0.0.1` instead sidesteps that
restriction entirely.
"""
from __future__ import annotations

import functools
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

_server: HTTPServer | None = None
_thread: threading.Thread | None = None


class _QuietRequestHandler(SimpleHTTPRequestHandler):
    """Same as SimpleHTTPRequestHandler, minus the access-log line per
    request. `log_message` writes to `sys.stderr` by default, which is
    `None` in a PyInstaller build with `console=False` (no console attached
    to inherit) — logging would then raise *before* the response is ever
    sent, turning every request into ERR_EMPTY_RESPONSE in the embedded
    Monaco view. Silencing it outright also just avoids console spam for
    every asset request in the normal (non-frozen) case.
    """

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def start() -> int:
    """Starts the server (once) and returns the port it is listening on."""
    global _server, _thread
    if _server is not None:
        return _server.server_address[1]

    handler = functools.partial(_QuietRequestHandler, directory=str(ASSETS_DIR))
    _server = HTTPServer(("127.0.0.1", 0), handler)
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    return _server.server_address[1]
