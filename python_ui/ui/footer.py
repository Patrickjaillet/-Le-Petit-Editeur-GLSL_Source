"""Bottom status bar: FPS, frame-time graph, compile status, golf sizes."""
from __future__ import annotations

import gzip
from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QStatusBar, QWidget

from i18n import tr

FRAME_GRAPH_SAMPLES = 90
FRAME_GRAPH_WIDTH = 120
FRAME_GRAPH_HEIGHT = 22
FRAME_GRAPH_MAX_MS = 33.0  # ~30 fps floor; bars clip above this

# Traditional demoscene compo size classes (4k/8k), checked against the
# *golfed* byte count (not gzip) since that's the figure the golf score is
# actually about. Ordered ascending: the first limit the size fits under
# wins. Above the last one, there's simply no landmark to show. Just the
# byte limits here (no display text) — `golf_size_tier_label` resolves
# `footer.size_tier` through `tr()` lazily, at call time, rather than this
# module baking translated text into a constant at import time (before
# `i18n.load_language()` has run).
GOLF_SIZE_TIERS = (2 * 1024, 4 * 1024, 8 * 1024)


def golf_size_tier_label(after_bytes: int) -> str | None:
    """Qualitative landmark for a golfed byte count, e.g. "< 4 Ko" for a
    3500-byte shader — gives the golf-à-froid readout something to compare
    against besides a raw number, using the size classes the demoscene
    4k/8k-compo crowd already thinks in. Returns None once the shader is
    past every known tier (no landmark to show, rather than a misleading
    one) — this stays purely local (no online leaderboard comparison, no
    reliable/stable API to integrate)."""
    for limit in GOLF_SIZE_TIERS:
        if after_bytes <= limit:
            return tr("footer.size_tier", kb=limit // 1024)
    return None


class FrameTimeGraph(QWidget):
    """Small rolling sparkline of recent `Engine.render()` wall-clock times,
    so a slow shader (or a heavy resize) is visible at a glance instead of
    just a smoothed-out average FPS number."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(FRAME_GRAPH_WIDTH, FRAME_GRAPH_HEIGHT)
        self._samples: deque[float] = deque(maxlen=FRAME_GRAPH_SAMPLES)
        self.setToolTip(tr("footer.frame_time_tooltip"))

    def add_sample(self, ms: float) -> None:
        self._samples.append(ms)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if not self._samples:
            return
        bar_width = max(1.0, FRAME_GRAPH_WIDTH / FRAME_GRAPH_SAMPLES)
        for i, ms in enumerate(self._samples):
            ratio = min(1.0, ms / FRAME_GRAPH_MAX_MS)
            bar_height = max(1, int(ratio * FRAME_GRAPH_HEIGHT))
            if ms <= 16.7:
                color = Qt.green
            elif ms <= FRAME_GRAPH_MAX_MS:
                color = Qt.yellow
            else:
                color = Qt.red
            x = int(i * bar_width)
            painter.fillRect(x, FRAME_GRAPH_HEIGHT - bar_height, max(1, int(bar_width)), bar_height, color)


class Footer(QStatusBar):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._fps_label = QLabel(tr("footer.fps", fps="--"))
        self._status_label = QLabel(tr("footer.ready"))
        self._size_label = QLabel("")
        self._frame_graph = FrameTimeGraph()

        self.addWidget(self._status_label, 1)
        self.addPermanentWidget(self._size_label)
        self.addPermanentWidget(self._frame_graph)
        self.addPermanentWidget(self._fps_label)

    def set_fps(self, fps: float) -> None:
        self._fps_label.setText(tr("footer.fps", fps=f"{fps:.0f}"))

    def add_frame_time_sample(self, ms: float) -> None:
        self._frame_graph.add_sample(ms)

    def set_compile_ok(self) -> None:
        self._status_label.setStyleSheet("color: #4caf50;")
        self._status_label.setText(tr("footer.compile_ok"))

    def set_compile_error(self, message: str) -> None:
        self._status_label.setStyleSheet("color: #f44336;")
        first_line = message.strip().splitlines()[0] if message.strip() else message
        self._status_label.setText(tr("footer.compile_error", message=first_line))
        self._status_label.setToolTip(message)

    def set_golf_sizes(self, before_text: str, after_text: str) -> None:
        if not before_text:
            self._size_label.setText("")
            return
        before = len(before_text.encode("utf-8"))
        after = len(after_text.encode("utf-8"))
        before_gz = len(gzip.compress(before_text.encode("utf-8")))
        after_gz = len(gzip.compress(after_text.encode("utf-8")))
        pct = 100.0 * (before - after) / before if before else 0.0
        tier = golf_size_tier_label(after)
        tier_suffix = f" | {tier}" if tier else ""
        self._size_label.setText(tr(
            "footer.size_format",
            before=before, after=after, percent=f"{pct:.0f}",
            before_gz=before_gz, after_gz=after_gz, tier_suffix=tier_suffix,
        ))
        self._size_label.setToolTip(tr("footer.size_tooltip"))
