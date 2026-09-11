"""Bottom status bar: FPS, frame-time graph, compile status, golf sizes."""
from __future__ import annotations

import gzip
import json
from collections import deque

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QComboBox, QLabel, QStatusBar, QWidget

import engine_bridge
from i18n import tr

FRAME_GRAPH_SAMPLES = 90
FRAME_GRAPH_WIDTH = 120
FRAME_GRAPH_HEIGHT = 22
FRAME_GRAPH_MAX_MS = 33.0  # ~30 fps floor; bars clip above this

# RM10.md section 4 "résolution de la prévisualisation indépendante de la
# fenêtre" : lets a heavy shader stay smooth by rendering at a fraction of
# the viewport's own pixel size (then upscaled to fill it, see
# `Viewport.paintEvent`) instead of the window's full resolution. Ordered
# 100% first so index 0 (the combo's default before any setting is
# restored) is always full resolution -- never a silent, unexplained
# downscale on first launch.
RENDER_SCALE_OPTIONS = (1.0, 0.75, 0.5, 0.25)

# Icône + couleur + clé i18n du libellé par id de dialecte (voir
# `engine_bridge.DIALECT_SHADERTOY`/`DIALECT_GLSL`), pour un repérage
# instantané au premier coup d'oeil sans avoir à lire le texte. Une entrée
# par dialecte reconnu par le moteur — un futur langage ajouté côté Rust
# (voir roadmap1.md, "Architecture extensible pour de futurs langages")
# n'a qu'à ajouter sa propre entrée ici, pas à modifier `set_dialect`.
_DIALECT_DISPLAY = {
    engine_bridge.DIALECT_SHADERTOY: ("🌈", "#64b5f6", "footer.dialect_shadertoy"),
    engine_bridge.DIALECT_GLSL: ("📄", "#ffb74d", "footer.dialect_glsl"),
    engine_bridge.DIALECT_WGSL: ("🟪", "#ba68c8", "footer.dialect_wgsl"),
}

# Traditional demoscene compo size classes (4k/8k), checked against the
# *golfed* byte count (not gzip) since that's the figure the golf score is
# actually about. Ordered ascending: the first limit the size fits under
# wins. Above the last one, there's simply no landmark to show. Just the
# byte limits here (no display text) — `golf_size_tier_label` resolves
# `footer.size_tier` through `tr()` lazily, at call time, rather than this
# module baking translated text into a constant at import time (before
# `i18n.load_language()` has run).
GOLF_SIZE_TIERS = (2 * 1024, 4 * 1024, 8 * 1024)

# RM10.md section 7: the tier landmark must be "mis en avant (couleur,
# icône)" the instant it's approached or crossed, not just spelled out as
# plain text easy to skim past. One (icon, color) pair per tier, tightest
# limit first -- gold/silver/bronze reads instantly as "how good a score is
# this" to the demoscene-compo crowd this feature is aimed at, without
# needing to do the Ko math themselves.
_GOLF_SIZE_TIER_STYLE = {
    2 * 1024: ("🥇", "#4caf50"),
    4 * 1024: ("🥈", "#8bc34a"),
    8 * 1024: ("🥉", "#ff9800"),
}
# "Approaching" a tier from above (not yet under it, but close enough that
# golfing a little further would cross it) gets its own, more urgent
# marker -- red rather than the tier's own color, since the shader is not
# actually under that limit yet.
_APPROACHING_COLOR = "#f44336"
_APPROACHING_MARGIN = 0.10  # within the last 10% above a limit counts as "approaching" it


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


def golf_size_tier_html(after_bytes: int) -> str:
    """Rich-text version of `golf_size_tier_label`: the same landmark, but
    color+icon coded (green/gold going down to orange for 2K/4K/8K once
    under a tier, red while still just above the nearest one within
    `_APPROACHING_MARGIN` of it) — see the RM10.md note above. Returns an
    empty string when there's nothing to show (not close to and not under
    any known tier), same "no landmark" convention as the plain version.
    """
    for limit in GOLF_SIZE_TIERS:
        if after_bytes <= limit:
            icon, color = _GOLF_SIZE_TIER_STYLE[limit]
            text = tr("footer.size_tier", kb=limit // 1024)
            return f' | <span style="color:{color}; font-weight:600;">{icon} {text}</span>'
        if after_bytes <= limit * (1.0 + _APPROACHING_MARGIN):
            text = tr("footer.size_tier_approaching", kb=limit // 1024)
            return f' | <span style="color:{_APPROACHING_COLOR}; font-weight:600;">⚠ {text}</span>'
    return ""


# ROADMAP.md, section "Golfing" — "comparaison en ligne avec un score
# externe" was explicitly left out ("hors périmètre, aucune API de
# leaderboard fiable et stable à intégrer") because this app is a
# standalone desktop editor with no backend service of its own to talk to:
# inventing one here would mean shipping a call to a service that doesn't
# exist. What follows instead is a **local** personal-best tracker — this
# project's own golf history, compared against itself — which gives the
# footer's live readout the "how does this compare" landmark the ticket
# was actually about, without any network dependency. Persisted the same
# way `export_video_dialog._load_calibration`/`_save_calibration` persist
# their own per-CRF table: one `QSettings` string value.
_SETTINGS_KEY_GOLF_BEST = "golfPersonalBests"


def _load_golf_bests(qsettings: QSettings) -> dict[str, dict[str, int]]:
    """`{project_path: {str(tab_id): best_bytes}}` — nested by project path
    *and* tab/pass id, never just the project path alone: different passes
    of the same project (Image, BufferA, Common, ...) have wildly different
    achievable sizes, so a single flattened "best" would silently compare a
    pass against a completely different pass's record the moment the user
    switched tabs. Stored as JSON rather than the flat `key:value,...`
    scheme `export_video_dialog`'s CRF table uses, because a real
    filesystem path can itself contain `:`/`,` (a Windows drive letter, a
    folder name with a comma) that flat scheme would silently corrupt.
    """
    raw = qsettings.value(_SETTINGS_KEY_GOLF_BEST, "", type=str)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}  # corrupt/foreign value -- fall back to "no history" rather than crash
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, int]] = {}
    for path, per_tab in data.items():
        if not isinstance(path, str) or not isinstance(per_tab, dict):
            continue
        clean: dict[str, int] = {
            tab_id: size for tab_id, size in per_tab.items()
            if isinstance(tab_id, str) and isinstance(size, int) and size > 0
        }
        if clean:
            out[path] = clean
    return out


def _save_golf_bests(qsettings: QSettings, bests: dict[str, dict[str, int]]) -> None:
    qsettings.setValue(_SETTINGS_KEY_GOLF_BEST, json.dumps(bests))


def record_golf_score(
    qsettings: QSettings, project_path: str | None, tab_id: int, after_bytes: int,
) -> tuple[int | None, bool]:
    """Call once a golfed byte count is known for the currently displayed
    pass of the currently *saved* project (`project_path` is
    `MainWindow._current_project_path`, `tab_id` is `MainWindow._current_tab`
    — `COMMON_TAB` included, it's just another int key here), so the footer
    can show how this run compares to the best ever recorded for this exact
    project+pass combination.

    Returns `(previous_best_or_None, is_new_best)`: the best recorded
    *before* this call (`None` the very first time this project+pass is
    ever golfed — nothing to compare against yet), and whether
    `after_bytes` itself just beat that previous record. `is_new_best` is
    always `False` on that very first call — there is nothing yet to
    *beat*, so it is never itself counted as "new best" (distinct from a
    tie, which is also `False`, for the same "matching a record is not an
    improvement" reason — the two cases share the same boolean on purpose,
    only `previous_best is None` tells them apart for a caller that needs
    to). The byte count is still recorded either way, exactly like a tie
    or an improvement, so the *next* call has something to compare against.

    Does nothing and returns `(None, False)` for an unsaved project
    (`project_path is None`): there's no stable key to record a best
    under yet, same "no stable identity, no tracking" choice
    `sliders_panel`'s own per-project layout keys already make — a project
    only starts building golf history once it has a file path.
    """
    if project_path is None or after_bytes <= 0:
        return None, False
    bests = _load_golf_bests(qsettings)
    per_tab = bests.get(project_path, {})
    previous_best = per_tab.get(str(tab_id))
    should_store = previous_best is None or after_bytes < previous_best
    is_new_best = previous_best is not None and after_bytes < previous_best
    if should_store:
        per_tab = dict(per_tab)
        per_tab[str(tab_id)] = after_bytes
        bests = dict(bests)
        bests[project_path] = per_tab
        _save_golf_bests(qsettings, bests)
    return previous_best, is_new_best


def golf_personal_best_html(after_bytes: int, previous_best: int | None, is_new_best: bool) -> str:
    """Small HTML suffix comparing the just-golfed size to this
    project+pass's own local history (see `record_golf_score`) — the local
    "leaderboard" ROADMAP.md's golfing section asks for. Three states:
    nothing yet to compare against (`previous_best is None`, the very
    first time this project+pass is golfed) shows nothing at all, rather
    than a misleading "0% better than a record that doesn't exist"; beating
    the previous best gets the same celebratory gold styling
    `golf_size_tier_html` already uses for crossing a demoscene size tier,
    with the exact byte improvement spelled out; matching or landing above
    it is shown plainly in a neutral grey, since staying within a few
    bytes of your own best golf on unrelated edits is not a regression
    worth flagging in the tier-warning red.
    """
    if previous_best is None:
        return ""
    if is_new_best:
        delta = previous_best - after_bytes
        text = tr("footer.golf_best_new", delta=delta)
        return f' | <span style="color:#4caf50; font-weight:600;">🏆 {text}</span>'
    text = tr("footer.golf_best_existing", best=previous_best)
    return f' | <span style="color:#9e9e9e;">{text}</span>'


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
    # Emitted with the newly chosen scale factor (one of `RENDER_SCALE_OPTIONS`)
    # whenever the user picks a different entry in the render-scale combo box.
    renderScaleChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._fps_label = QLabel(tr("footer.fps", fps="--"))
        self._status_label = QLabel(tr("footer.ready"))
        self._size_label = QLabel("")
        # RM10.md section 7: the demoscene-tier landmark appended to this
        # label's text is color+icon coded HTML (`golf_size_tier_html`) --
        # explicit here rather than relying on QLabel's Qt.AutoText
        # sniffing to notice the embedded <span>.
        self._size_label.setTextFormat(Qt.RichText)
        self._dialect_label = QLabel("")
        self._frame_graph = FrameTimeGraph()

        # RM10.md section 4: the actual render resolution used by the
        # engine, always shown plainly next to the combo box that controls
        # it -- so a downscaled preview (chosen for fluidity on a heavy
        # shader) is never a silent, easy-to-forget state.
        self._resolution_label = QLabel("")
        self._resolution_label.setToolTip(tr("footer.resolution_tooltip"))

        self._scale_combo = QComboBox()
        self._scale_combo.addItems([f"{int(round(s * 100))}%" for s in RENDER_SCALE_OPTIONS])
        self._scale_combo.setToolTip(tr("footer.render_scale_tooltip"))
        self._scale_combo.currentIndexChanged.connect(self._on_scale_index_changed)

        self.addWidget(self._status_label, 1)
        self.addPermanentWidget(self._size_label)
        self.addPermanentWidget(self._dialect_label)
        self.addPermanentWidget(self._frame_graph)
        self.addPermanentWidget(self._resolution_label)
        self.addPermanentWidget(self._scale_combo)
        self.addPermanentWidget(self._fps_label)

    def _on_scale_index_changed(self, index: int) -> None:
        if 0 <= index < len(RENDER_SCALE_OPTIONS):
            self.renderScaleChanged.emit(RENDER_SCALE_OPTIONS[index])

    def set_render_scale_silent(self, scale: float) -> None:
        """Restores the combo box to `scale` (a persisted setting, or the
        default at startup) without re-emitting `renderScaleChanged` --
        the caller already knows the value it's restoring, and re-emitting
        would just bounce it straight back through `MainWindow` for no
        reason."""
        try:
            index = RENDER_SCALE_OPTIONS.index(scale)
        except ValueError:
            index = 0
        self._scale_combo.blockSignals(True)
        self._scale_combo.setCurrentIndex(index)
        self._scale_combo.blockSignals(False)

    def set_resolution(self, width: int, height: int, scale: float) -> None:
        self._resolution_label.setText(tr(
            "footer.resolution", width=width, height=height, percent=int(round(scale * 100)),
        ))

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

    def set_dialect(self, dialect_id: str, signal_i18n_key: str) -> None:
        """Affiche le mode détecté (`engine_bridge.DIALECT_SHADERTOY`/
        `DIALECT_GLSL`) avec une icône/couleur distincte par dialecte.
        `signal_i18n_key` (une des clés `footer.dialect_signal_*` — voir
        `dialect::DialectSignal::i18n_key` côté Rust) alimente le tooltip
        au survol, pour que le mode affiché ne soit jamais une boîte noire
        si l'utilisateur ne comprend pas pourquoi son code est classé
        d'une façon ou d'une autre. Un id de dialecte inconnu (futur
        langage pas encore doté d'un affichage dédié, voir
        `_DIALECT_DISPLAY`) retombe silencieusement sur `clear_dialect()`
        plutôt que de planter le footer.
        """
        display = _DIALECT_DISPLAY.get(dialect_id)
        if display is None:
            self.clear_dialect()
            return
        icon, color, label_key = display
        self._dialect_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        self._dialect_label.setText(f"{icon} {tr(label_key)}")
        self._dialect_label.setToolTip(tr(
            "footer.dialect_tooltip", mode=tr(label_key), signal=tr(signal_i18n_key)
        ))

    def clear_dialect(self) -> None:
        """État neutre avant toute compilation (tout premier affichage) —
        un label vide plutôt qu'un mode par défaut trompeur, cohérent avec
        `footer.ready` déjà utilisé pour le statut de compilation avant la
        première frame."""
        self._dialect_label.setText("")
        self._dialect_label.setToolTip("")

    def set_golf_sizes(
        self,
        before_text: str,
        after_text: str,
        previous_best: int | None = None,
        is_new_best: bool = False,
    ) -> None:
        if not before_text:
            self._size_label.setText("")
            return
        before = len(before_text.encode("utf-8"))
        after = len(after_text.encode("utf-8"))
        before_gz = len(gzip.compress(before_text.encode("utf-8")))
        after_gz = len(gzip.compress(after_text.encode("utf-8")))
        pct = 100.0 * (before - after) / before if before else 0.0
        # `previous_best`/`is_new_best` default to a no-comparison state
        # (`golf_personal_best_html` then renders nothing) so callers that
        # never golfed a *saved* project yet — or that don't care about
        # this ticket's personal-best tracking at all — keep working
        # exactly as before this feature existed.
        tier_suffix = golf_size_tier_html(after) + golf_personal_best_html(after, previous_best, is_new_best)
        self._size_label.setText(tr(
            "footer.size_format",
            before=before, after=after, percent=f"{pct:.0f}",
            before_gz=before_gz, after_gz=after_gz, tier_suffix=tier_suffix,
        ))
        self._size_label.setToolTip(tr("footer.size_tooltip"))
