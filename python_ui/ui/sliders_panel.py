"""Tabbed sliders panel, driven by literals auto-detected in the shader's
own code (no `@slider` annotation, no custom uniform: the code stays 100%
Shadertoy-compatible). Four kinds are supported:

- float literals (`1.5`)              -> slider + spinbox
- bare int literals (`4`)             -> integer-stepped slider + spinbox
- `true`/`false` literals             -> checkbox toggle
- `vec2(a, b)` / `vec3(a, b, c)` calls whose args are all plain literals
  -> a single grouped control (X/Y spinboxes, or a color swatch + R/G/B
  spinboxes for vec3) instead of 2-3 separate float sliders.

Moving any of them rewrites the literal (or, for vec2/vec3, the whole
`vecN(...)` call) directly in the editor via
`literalEdited(start, end, new_text)`.

Scalar (float/int) sliders additionally support basic keyframing: the "🎬"
button on a row records the slider's current value at the shared animation
clock's current time (`set_time`, driven by `Viewport.timeUpdated`, i.e.
`iTime` — there's no separate transport, playback rides the same
play/pause/reset-time controls already in the toolbar). Once a slider has
2+ keyframes, `set_time` interpolates its value linearly between the two
keyframes bracketing the current time (held constant before the first and
after the last, never extrapolated) on every tick, letting a shader
sequence be previewed by just hitting Play.

BUG FIX: `set_time()` used to re-apply keyframe interpolation on *every*
call, even when `t` hadn't actually moved since the previous call. Since
`Viewport.timeUpdated` fires on every render tick "paused or not"
(~60/s), any slider carrying keyframes had its value silently reasserted
~60 times a second regardless of playback state. That undid manual
interaction (drag, typed value, "reset", "randomize") on such a slider
within a single frame, and — because each reassertion goes through
`_emit_edit` -> `literalEdited` -> a fresh compile-debounce restart in
`MainWindow` — starved the compile debounce indefinitely while a
keyframed slider was mid-interpolation, so the rendered shader stopped
following the values shown in the panel. `set_time` now only
re-evaluates keyframes when `t` has genuinely changed.
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from i18n import tr

SLIDER_STEPS = 1000

# How long is_drag_active() stays armed after the *last* valueChanged tick
# that didn't go through sliderPressed/sliderReleased (mouse wheel, or
# keyboard arrows on a focused QSlider — see C2 in AUDIT.md). Kept above
# main_window.SLIDER_COMPILE_DEBOUNCE_MS (100ms) so a burst of wheel/key
# ticks faster than that debounce is still fully covered: the guard only
# lapses once ticks actually stop arriving, not on a fixed schedule.
_INTERACTION_QUIESCENCE_MS = 200
SHIFT_STEP_MULTIPLIER = 10
# Clicking "add keyframe" again within this many seconds of an existing
# keyframe updates it in place instead of creating a near-duplicate.
KEYFRAME_MERGE_EPS = 0.05


# RM10.md section 3: the interpolation shape between two keyframes, not
# just the raw (time, value) pairs themselves. "linear" is the historical
# behaviour (unchanged default, and what every layout saved before this
# existed implicitly used); "ease" eases in/out of each segment
# (smoothstep, `3t²-2t³`) instead of moving at a constant rate; "step"
# (paliers) holds the earlier keyframe's value for the whole segment and
# jumps discretely to the next one, for parameters that should snap
# rather than glide (palette swaps, discrete states, ...).
KEYFRAME_CURVE_LINEAR = "linear"
KEYFRAME_CURVE_EASE = "ease"
KEYFRAME_CURVE_STEP = "step"
KEYFRAME_CURVES = (KEYFRAME_CURVE_LINEAR, KEYFRAME_CURVE_EASE, KEYFRAME_CURVE_STEP)


def _interpolate_keyframes(
    keyframes: list[tuple[float, float]], t: float, curve: str = KEYFRAME_CURVE_LINEAR
) -> float:
    """Interpolates between `(time, value)` pairs already sorted by time,
    shaped by `curve` (see `KEYFRAME_CURVES`). Held constant outside the
    recorded range — a preview should never guess past the last keyframe
    the user actually set."""
    if len(keyframes) == 1:
        return keyframes[0][1]
    if t <= keyframes[0][0]:
        return keyframes[0][1]
    if t >= keyframes[-1][0]:
        return keyframes[-1][1]
    for (t0, v0), (t1, v1) in zip(keyframes, keyframes[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return v1
            if curve == KEYFRAME_CURVE_STEP:
                return v0
            ratio = (t - t0) / (t1 - t0)
            if curve == KEYFRAME_CURVE_EASE:
                ratio = ratio * ratio * (3.0 - 2.0 * ratio)
            return v0 + (v1 - v0) * ratio
    return keyframes[-1][1]  # unreachable, defensive


def _parse_keyframes(raw) -> list[tuple[float, float]]:
    """Validates the `"keyframes"` field of a saved layout entry (see
    `SlidersPanel.export_layout`) — malformed or foreign entries are
    dropped rather than raising, same best-effort spirit as the rest of
    layout loading."""
    if not isinstance(raw, list):
        return []
    out: list[tuple[float, float]] = []
    for item in raw:
        try:
            t, v = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        out.append((t, v))
    out.sort(key=lambda pair: pair[0])
    return out




class _SliderSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox where Shift+Up/Down steps by a larger increment
    (10x the normal step) instead of the default single step."""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Up, Qt.Key_Down) and event.modifiers() & Qt.ShiftModifier:
            original_step = self.singleStep()
            self.setSingleStep(original_step * SHIFT_STEP_MULTIPLIER)
            super().keyPressEvent(event)
            self.setSingleStep(original_step)
        else:
            super().keyPressEvent(event)


def format_glsl_float(value: float, decimals: int = 6) -> str:
    """Formats a value as a valid GLSL float literal (always has a `.`),
    trimmed to at most `decimals` fractional digits (matches the slider's
    configured precision)."""
    text = f"{value:.{max(decimals, 1)}f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return text


def _default_decimals_for(value: float, text: str) -> int:
    """Picks the initial spinbox precision for a float slider so a
    small-magnitude literal (`0.00003`, `1e-5`, ...) isn't silently
    rounded down to `0.0`/`-0.0` the very first time its slider moves
    (see AUDIT.md, C1). A fixed `4` (the previous default) is fine for
    everyday values but destroys anything smaller than ~5e-5.

    Combines two signals, whichever asks for more digits:
    - how many fractional digits the literal's own source text already
      uses (handles `0.00003` directly);
    - how many decimals are needed for the value's magnitude itself to
      not round to zero, with a couple of digits of headroom
      (handles exponent notation like `1e-5`, which has no fractional
      digits in its text at all).

    Always at least 4 (unchanged default for ordinary values) and at
    most 8 (same ceiling as the right-click bounds dialog's "decimals"
    field).
    """
    if value == 0.0:
        return 4
    mantissa = text.split("e")[0].split("E")[0]
    frac_digits = len(mantissa.split(".", 1)[1]) if "." in mantissa else 0
    magnitude_decimals = max(0, -math.floor(math.log10(abs(value)))) + 2
    return max(4, min(max(frac_digits, magnitude_decimals), 8))


def _default_float_range(value: float) -> tuple[float, float]:
    # Kept in sync by hand with `default_float_range` in
    # `rust_engine/src/literals.rs` — that one computes the range for
    # ordinary scalar sliders (via the Rust binding), this one for each
    # component of a `VecSlider` (which has no min/max of its own on the
    # Rust side). See m3 in AUDIT.md: no test currently guards the two
    # against drifting apart if the heuristic ever changes on one side
    # only — if you touch this rule, update both.
    if value == 0.0:
        return (-1.0, 1.0)
    if value > 0.0:
        return (0.0, value * 2.0)
    return (value * 2.0, 0.0)


def _combine_sliders(sliders) -> list[tuple[str, object]]:
    """Merges the 4 kind-specific lists `detect_all_sliders` returns into
    one (kind, literal) sequence ordered by source position — the order
    edits need to be in for offset-shifting to work (see `_emit_edit`)."""
    floats, ints, bools, vecs = sliders
    tagged = (
        [("float", lit) for lit in floats]
        + [("int", lit) for lit in ints]
        + [("bool", lit) for lit in bools]
        + [(f"vec{lit.size}", lit) for lit in vecs]
    )
    tagged.sort(key=lambda pair: pair[1].start)
    return tagged


class _CurvePreviewWidget(QWidget):
    """RM10.md section 3: plots the actual interpolation curve between a
    slider's own recorded keyframes -- not a schematic, the real function
    `set_time` evaluates -- so choosing "ease" vs. "linear" vs. "paliers"
    in `_edit_keyframe_curve`'s dialog is something the user can *see*
    change, not just a label in a combo box."""

    _MARGIN = 12
    _SAMPLES = 200

    def __init__(self, keyframes: list[tuple[float, float]], curve: str, parent=None):
        super().__init__(parent)
        self._keyframes = keyframes
        self._curve = curve
        self.setMinimumSize(260, 120)

    def set_curve(self, curve: str) -> None:
        self._curve = curve
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1e1e1e"))
        m = self._MARGIN
        w, h = self.width() - 2 * m, self.height() - 2 * m
        if w <= 0 or h <= 0 or len(self._keyframes) < 2:
            return

        t0, t1 = self._keyframes[0][0], self._keyframes[-1][0]
        values = [v for _, v in self._keyframes]
        v_lo, v_hi = min(values), max(values)
        if v_hi == v_lo:
            v_lo, v_hi = v_lo - 1.0, v_hi + 1.0  # a flat curve still needs a visible span

        def to_point(t: float, v: float) -> tuple[float, float]:
            x = m + (t - t0) / (t1 - t0) * w if t1 > t0 else m
            y = m + h - (v - v_lo) / (v_hi - v_lo) * h
            return x, y

        painter.setPen(QPen(QColor("#555555"), 1))
        painter.drawRect(m, m, w, h)

        painter.setPen(QPen(QColor("#64b5f6"), 2))
        prev = None
        for i in range(self._SAMPLES + 1):
            t = t0 + (t1 - t0) * (i / self._SAMPLES)
            value = _interpolate_keyframes(self._keyframes, t, self._curve)
            point = to_point(t, value)
            if prev is not None:
                painter.drawLine(int(prev[0]), int(prev[1]), int(point[0]), int(point[1]))
            prev = point

        painter.setPen(QPen(QColor("#ffb74d"), 1))
        painter.setBrush(QColor("#ffb74d"))
        for t, v in self._keyframes:
            x, y = to_point(t, v)
            painter.drawEllipse(int(x) - 3, int(y) - 3, 6, 6)


class _LiteralState:
    """Mutable, editor-local mirror of a detected literal (of any kind).

    Kept separate from the Rust object (whose fields are read-only) so
    offsets can be optimistically updated between recompiles: dragging a
    slider fires many edits per second, faster than the debounced
    recompile, so each tick must account for the text-length delta of the
    previous tick before the engine has had a chance to re-detect literals.
    """

    __slots__ = ("kind", "start", "end", "value", "min", "max", "category", "initial_value", "keyframes", "curve")

    def __init__(self, kind: str, lit, initial_value=None, keyframes=None, curve=None):
        self.kind = kind
        self.start = lit.start
        self.end = lit.end
        self.category = lit.category
        if kind in ("float", "int"):
            self.value = lit.value
            self.min = lit.min
            self.max = lit.max
        elif kind == "bool":
            self.value = lit.value
            self.min = None
            self.max = None
        else:  # vec2 / vec3
            self.value = list(lit.values)
            self.min = None
            self.max = None
        # The value as first seen at rebuild() time (a fresh widget), used
        # by the "reset" buttons. Preserved across refresh() calls (which
        # re-detect literals on every recompile) instead of being
        # recomputed, otherwise "reset" would have nothing to reset to.
        if initial_value is not None:
            self.initial_value = initial_value
        elif kind.startswith("vec"):
            self.initial_value = list(self.value)
        else:
            self.initial_value = self.value
        # (time, value) pairs, sorted by time, scalar (float/int) sliders
        # only — see the module docstring's "keyframing" paragraph. Empty
        # for every other kind and for a scalar slider nobody keyframed.
        self.keyframes: list[tuple[float, float]] = list(keyframes) if keyframes else []
        self.curve: str = curve if curve in KEYFRAME_CURVES else KEYFRAME_CURVE_LINEAR


class SlidersPanel(QTabWidget):
    literalEdited = Signal(int, int, str)  # start, end, new_text
    # Fired once a slider drag that was in progress finishes (mouse
    # released), so the caller can do the one resync it skipped while
    # `is_drag_active()` was true (see that method's docstring).
    dragFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._literals: list[_LiteralState] = []
        self._rows: list[tuple | None] = []
        self._keyframe_buttons: list[QToolButton | None] = []
        # Shared animation clock, pushed in from `Viewport.timeUpdated`
        # (mirrors `iTime`) via `set_time`. Recording a keyframe stamps it
        # with whatever this currently holds.
        self._time = 0.0
        # (row widget, form layout, searchable text, tab index) for the
        # name filter — QFormLayout.setRowVisible needs both the row
        # widget and the layout it belongs to.
        self._filter_targets: list[tuple[QWidget, QFormLayout, str]] = []
        # Counts currently-pressed sliders (almost always 0 or 1, but a
        # plain counter needs no special-casing). See is_drag_active().
        self._drag_depth = 0
        # Quiescence timer covering the interactions _drag_depth can't see
        # (mouse wheel, keyboard arrows on a focused slider): both fire
        # valueChanged repeatedly without ever emitting sliderPressed/
        # sliderReleased. Re-armed on every such tick by
        # _arm_interaction_quiescence(); is_drag_active() stays true while
        # it's running, and it fires dragFinished once ticks stop arriving
        # (see is_drag_active()'s docstring and C2 in AUDIT.md).
        self._interaction_timer = QTimer(self)
        self._interaction_timer.setSingleShot(True)
        self._interaction_timer.timeout.connect(self._on_interaction_quiescent)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText(tr("sliders_panel.filter_placeholder"))
        self._filter_edit.setMaximumWidth(140)
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._apply_filter)
        self.setCornerWidget(self._filter_edit, Qt.TopRightCorner)

    def is_drag_active(self) -> bool:
        """True while a QSlider in this panel is being actively dragged.

        The caller (`MainWindow._refresh_sliders_for`) must skip
        rebuild()/refresh() entirely while this is true. Re-detecting
        literals from the editor's current text and resyncing
        `_literals[ordinal].start/end` to match is exactly what recompile
        normally does — safe on its own, but dangerous mid-drag: a fast
        drag fires many `replace_range` edits before Monaco's content-
        changed event for any single one of them round-trips back, so at
        any instant the editor text this method would see reflects only
        *some prefix* of the edits `_on_slider_moved` has already
        optimistically applied to `_literals` (see `_emit_edit`). Blindly
        resyncing to that stale, partial snapshot regresses the dragged
        literal's tracked `end` backward; the next tick's `replace_range`
        then targets a range that's now too short, leaving a leftover
        fragment of the previous value's text (typically its sign, hence
        runs of stray `-` accumulating) behind on every subsequent tick.
        Skipping the resync entirely until the drag actually ends (see
        `dragFinished`) avoids ever reading that half-applied state.

        Note this covers more than a literal mouse-drag: mouse-wheel scrolls
        and keyboard arrow presses on a focused QSlider fire the exact same
        `valueChanged` -> `_on_slider_moved` -> `_emit_edit` sequence, often
        faster than the compile debounce, without ever emitting
        `sliderPressed`/`sliderReleased` — see `_arm_interaction_quiescence`.
        """
        return self._drag_depth > 0 or self._interaction_timer.isActive()

    def _on_slider_pressed(self) -> None:
        self._drag_depth += 1
        self._interaction_timer.stop()

    def _on_slider_released(self) -> None:
        self._drag_depth = max(0, self._drag_depth - 1)
        if self._drag_depth == 0 and not self._interaction_timer.isActive():
            self.dragFinished.emit()

    def _arm_interaction_quiescence(self) -> None:
        """Re-arms the quiescence guard described in `is_drag_active()`.

        Called from `_on_slider_moved` on *every* tick, regardless of what
        triggered it (mouse drag, wheel, or keyboard) — mouse-drag ticks
        are already covered by `_drag_depth`, so re-arming here for them
        too is redundant but harmless. What matters is the wheel/keyboard
        case, which has no other signal to hook: as long as ticks keep
        arriving faster than `_INTERACTION_QUIESCENCE_MS`, the timer never
        fires and `is_drag_active()` stays true; once they stop, the timer
        fires `_on_interaction_quiescent` shortly after, which — mirroring
        `_on_slider_released` — emits `dragFinished` so the caller performs
        the one resync it skipped while the guard was armed.
        """
        self._interaction_timer.start(_INTERACTION_QUIESCENCE_MS)

    def _on_interaction_quiescent(self) -> None:
        if self._drag_depth == 0:
            self.dragFinished.emit()

    def current_signature(self) -> list[tuple[str, str]]:
        """Fingerprint used to decide rebuild vs. in-place refresh."""
        return [(lit.kind, lit.category) for lit in self._literals]

    @staticmethod
    def signature_of(sliders) -> list[tuple[str, str]]:
        return [(kind, lit.category) for kind, lit in _combine_sliders(sliders)]

    # ---- layout persistence (min/max/decimals overrides + keyframes) ----
    #
    # A slider has no identity beyond its position in the source, which is
    # meaningless across a save/reload round-trip (or even a same-session
    # structural rebuild — see `_edit_range`'s docstring). The best
    # available identity is (category, kind, index-within-that-category-
    # and-kind, in source-position order): stable as long as the user
    # hasn't added/removed/reordered literals of that kind within that
    # category since the layout was captured. When it has, entries past
    # the point of divergence simply find nothing to match in
    # `apply_layout` and are dropped silently — best-effort, not a
    # guarantee, exactly as the roadmap entry describes.
    #
    # Only float/int (scalar) sliders carry an overridable min/max/
    # decimals or keyframes today; bool/vec sliders have nothing to export.

    def export_layout(self) -> list[dict]:
        """Snapshot of every scalar slider's current min/max(/decimals)
        and keyframes (if any)."""
        counts: dict[tuple[str, str], int] = {}
        layout: list[dict] = []
        for state, widgets in zip(self._literals, self._rows):
            if widgets is None or state.kind not in ("float", "int"):
                continue
            key = (state.category, state.kind)
            index = counts.get(key, 0)
            counts[key] = index + 1
            _, spin = widgets
            entry: dict = {
                "category": state.category,
                "kind": state.kind,
                "index": index,
                "min": spin.minimum(),
                "max": spin.maximum(),
            }
            if state.kind == "float":
                entry["decimals"] = spin.decimals()
            if state.keyframes:
                entry["keyframes"] = [[t, v] for t, v in state.keyframes]
                if state.curve != KEYFRAME_CURVE_LINEAR:
                    entry["curve"] = state.curve
            layout.append(entry)
        return layout

    def apply_layout(self, layout: list[dict]) -> None:
        """Reapplies a previously-exported layout onto the rows just built
        by `rebuild()`. Silently skips any entry that no longer matches
        (see class-level note above)."""
        if not layout:
            return
        by_key: dict[tuple, dict] = {}
        for entry in layout:
            try:
                by_key[(entry["category"], entry["kind"], entry["index"])] = entry
            except (KeyError, TypeError):
                continue

        counts: dict[tuple[str, str], int] = {}
        for ordinal, (state, widgets) in enumerate(zip(self._literals, self._rows)):
            if widgets is None or state.kind not in ("float", "int"):
                continue
            key = (state.category, state.kind)
            index = counts.get(key, 0)
            counts[key] = index + 1
            entry = by_key.get((state.category, state.kind, index))
            if entry is None:
                continue

            # Keyframes are applied independently of min/max validity
            # below — a layout entry with a bad/missing range shouldn't
            # also lose its keyframes.
            state.keyframes = _parse_keyframes(entry.get("keyframes"))
            entry_curve = entry.get("curve")
            state.curve = entry_curve if entry_curve in KEYFRAME_CURVES else KEYFRAME_CURVE_LINEAR
            self._refresh_keyframe_button(ordinal)

            try:
                new_min = float(entry["min"])
                new_max = float(entry["max"])
            except (KeyError, TypeError, ValueError):
                continue
            # `json.loads` accepts the non-standard `NaN`/`Infinity`/
            # `-Infinity` literals, so a hand-edited or corrupted project
            # file can smuggle non-finite bounds past the checks above
            # (float(nan) raises nothing, and every comparison against
            # NaN is False, so `new_max <= new_min` would silently pass
            # through instead of filtering the entry out).
            if not (math.isfinite(new_min) and math.isfinite(new_max)):
                continue
            if new_max <= new_min:
                continue

            slider, spin = widgets
            value = min(max(spin.value(), new_min), new_max)
            spin.blockSignals(True)
            if state.kind == "int":
                spin.setMinimum(int(round(new_min)))
                spin.setMaximum(int(round(new_max)))
                spin.setValue(int(round(value)))
            else:
                decimals = entry.get("decimals")
                if isinstance(decimals, int) and 0 <= decimals <= 8:
                    spin.setDecimals(decimals)
                spin.setMinimum(new_min)
                spin.setMaximum(new_max)
                spin.setSingleStep((new_max - new_min) / SLIDER_STEPS)
                spin.setValue(value)
            spin.blockSignals(False)
            self._set_slider_from_value(slider, new_min, new_max, value)

    def rebuild(self, source: str, sliders) -> None:
        # RM10.md section 1, item 10: `QTabWidget.clear()` (Qt's own,
        # inherited here since `SlidersPanel` is a `QTabWidget`) removes
        # every page from the tab bar but, per Qt's own documentation,
        # deliberately does **not** delete the page widgets themselves --
        # every previous `rebuild()` call's category pages (each holding a
        # handful of buttons/spinboxes/rows) would otherwise leak
        # permanently on every single tab switch or edit that triggers a
        # rebuild. Verified: a 3000-iteration tab-switch/recompile stress
        # test (`test_perf_stress_rm10.py`) showed steady, roughly linear
        # growth (~+116% over 2700 post-warmup iterations) before this fix,
        # and no meaningful growth after it.
        for i in range(self.count()):
            self.widget(i).deleteLater()
        self.clear()
        tagged = _combine_sliders(sliders)
        self._literals = [_LiteralState(kind, lit) for kind, lit in tagged]
        self._rows = [None] * len(self._literals)
        self._keyframe_buttons = [None] * len(self._literals)
        self._filter_targets = []

        categories: dict[str, list[int]] = {}
        for ordinal, lit in enumerate(self._literals):
            categories.setdefault(lit.category, []).append(ordinal)

        # `ordinals` below is every literal in the category at rebuild()
        # time, independent of the search filter's current state — the
        # category-wide reset/randomize/clear-keyframes buttons therefore
        # also act on rows currently hidden by the filter. Confirmed as
        # the intended behaviour (the filter is a search aid, not a
        # selection mechanism) — see m5 in AUDIT.md — and now called out
        # explicitly via each button's tooltip instead of only being
        # discoverable by testing it.
        for category, ordinals in categories.items():
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(4, 4, 4, 4)

            header_row = QHBoxLayout()
            reset_category_btn = QPushButton(tr("sliders_panel.reset_category"))
            reset_category_btn.setToolTip(tr("sliders_panel.reset_category_tooltip"))
            reset_category_btn.clicked.connect(
                lambda checked=False, ords=list(ordinals): self._reset_ordinals(ords)
            )
            randomize_category_btn = QPushButton(tr("sliders_panel.randomize_category"))
            randomize_category_btn.setToolTip(tr("sliders_panel.randomize_category_tooltip"))
            randomize_category_btn.clicked.connect(
                lambda checked=False, ords=list(ordinals): self._randomize_ordinals(ords)
            )
            clear_keyframes_btn = QPushButton(tr("sliders_panel.clear_category_keyframes"))
            clear_keyframes_btn.setToolTip(
                tr("sliders_panel.clear_category_keyframes_tooltip")
            )
            clear_keyframes_btn.clicked.connect(
                lambda checked=False, ords=list(ordinals): self._clear_keyframes_ordinals(ords)
            )
            header_row.addWidget(reset_category_btn)
            header_row.addWidget(randomize_category_btn)
            header_row.addWidget(clear_keyframes_btn)
            page_layout.addLayout(header_row)

            form_container = QWidget()
            form = QFormLayout(form_container)
            page_layout.addWidget(form_container)
            page_layout.addStretch(1)

            for ordinal in ordinals:
                lit = self._literals[ordinal]
                line = source.count("\n", 0, lit.start) + 1
                label = f"L{line}"

                if lit.kind == "float":
                    original_text = source[lit.start:lit.end]
                    decimals = _default_decimals_for(lit.value, original_text)
                    row, widgets = self._build_scalar_row(ordinal, lit, integer=False, decimals=decimals)
                elif lit.kind == "int":
                    row, widgets = self._build_scalar_row(ordinal, lit, integer=True)
                elif lit.kind == "bool":
                    row, widgets = self._build_bool_row(ordinal, lit)
                else:  # vec2 / vec3
                    row, widgets = self._build_vec_row(ordinal, lit)

                form.addRow(label, row)
                self._filter_targets.append((row, form, f"{label} {category}".lower()))
                self._rows[ordinal] = widgets

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.addTab(scroll, category)

        self._apply_filter(self._filter_edit.text())

    # ---- row construction, per kind -----------------------------------

    def _build_scalar_row(self, ordinal: int, lit: _LiteralState, integer: bool, decimals: int = 4):
        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(SLIDER_STEPS)
        slider.setContextMenuPolicy(Qt.CustomContextMenu)

        if integer:
            spin = QSpinBox()
            spin.setMinimum(int(lit.min))
            spin.setMaximum(int(lit.max))
            spin.setSingleStep(1)
            # RM10.md section 3: the slider's own 0..SLIDER_STEPS internal
            # scale is far finer than most int ranges (e.g. 1000 raw
            # positions for an 0..16 iteration-count range), so Qt's default
            # singleStep of 1 *raw* unit moves the mapped int value by a
            # fraction that rounds right back to where it started -- mouse
            # wheel or keyboard arrows focused on the slider itself would
            # visibly do nothing for many ticks in a row, unlike the exact
            # 1-per-tick response of the paired QSpinBox right next to it.
            # Scaled so one notch reliably moves the rounded int by (at
            # least) 1, matching the spinbox -- except when the int range
            # itself exceeds SLIDER_STEPS, where 1 raw unit already covers
            # more than 1 int value and further scaling would only overshoot.
            span = max(1, int(lit.max) - int(lit.min))
            slider.setSingleStep(max(1, round(SLIDER_STEPS / span)))
            slider.setPageStep(slider.singleStep())
        else:
            spin = _SliderSpinBox()
            spin.setDecimals(decimals)
            spin.setMinimum(lit.min)
            spin.setMaximum(lit.max)
            spin.setSingleStep((lit.max - lit.min) / SLIDER_STEPS if lit.max > lit.min else 0.01)

        self._set_slider_from_value(slider, lit.min, lit.max, lit.value)
        spin.setValue(lit.value)

        slider.valueChanged.connect(
            lambda v, s=spin, o=ordinal, integer=integer: self._on_slider_moved(v, s, o, integer)
        )
        slider.sliderPressed.connect(self._on_slider_pressed)
        slider.sliderReleased.connect(self._on_slider_released)
        spin.valueChanged.connect(
            lambda v, sl=slider, o=ordinal, sp=spin, integer=integer: self._on_spin_changed(v, sl, o, sp, integer)
        )
        if not integer:
            slider.customContextMenuRequested.connect(
                lambda pos, sl=slider, sp=spin: self._show_range_menu(sl, sp, sl.mapToGlobal(pos))
            )

        reset_btn = QToolButton()
        reset_btn.setText("↺")
        reset_btn.setToolTip(tr("sliders_panel.reset_value_tooltip"))
        reset_btn.clicked.connect(lambda checked=False, o=ordinal: self._reset_ordinals([o]))

        randomize_btn = QToolButton()
        randomize_btn.setText("🎲")
        randomize_btn.setToolTip(tr("sliders_panel.randomize_value_tooltip"))
        randomize_btn.clicked.connect(lambda checked=False, o=ordinal: self._randomize_ordinals([o]))

        keyframe_btn = QToolButton()
        keyframe_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        keyframe_btn.clicked.connect(lambda checked=False, o=ordinal: self.add_keyframe(o))
        keyframe_btn.customContextMenuRequested.connect(
            lambda pos, o=ordinal, btn=keyframe_btn: self._show_keyframe_menu(o, btn.mapToGlobal(pos))
        )
        self._keyframe_buttons[ordinal] = keyframe_btn
        self._refresh_keyframe_button(ordinal)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(slider)
        row_layout.addWidget(spin)
        row_layout.addWidget(reset_btn)
        row_layout.addWidget(randomize_btn)
        row_layout.addWidget(keyframe_btn)
        return row, (slider, spin)

    def _build_bool_row(self, ordinal: int, lit: _LiteralState):
        checkbox = QCheckBox()
        checkbox.setChecked(lit.value)
        checkbox.toggled.connect(lambda checked, o=ordinal: self._on_bool_toggled(checked, o))

        reset_btn = QToolButton()
        reset_btn.setText("↺")
        reset_btn.setToolTip(tr("sliders_panel.reset_value_tooltip"))
        reset_btn.clicked.connect(lambda checked=False, o=ordinal: self._reset_ordinals([o]))

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(checkbox)
        row_layout.addWidget(reset_btn)
        row_layout.addStretch(1)
        return row, (checkbox,)

    def _build_vec_row(self, ordinal: int, lit: _LiteralState):
        size = len(lit.value)
        if size == 4:
            component_labels = tr("sliders_panel.component_labels_rgba")
        elif size == 3:
            component_labels = tr("sliders_panel.component_labels_rgb")
        else:
            component_labels = tr("sliders_panel.component_labels_xy")
        spins: list[QDoubleSpinBox] = []
        for i, comp_label in enumerate(component_labels):
            comp_value = lit.value[i]
            lo, hi = _default_float_range(comp_value)
            spin = _SliderSpinBox()
            spin.setPrefix(f"{comp_label} ")
            spin.setDecimals(4)
            spin.setMinimum(min(lo, comp_value))
            spin.setMaximum(max(hi, comp_value))
            spin.setSingleStep(0.01)
            spin.setValue(comp_value)
            spin.setContextMenuPolicy(Qt.CustomContextMenu)
            spin.customContextMenuRequested.connect(
                lambda pos, sp=spin: self._show_vec_component_range_menu(sp, sp.mapToGlobal(pos))
            )
            spins.append(spin)

        swatch = None
        if size in (3, 4):
            # vec4 groups a color-and-alpha constant (`vec4(r, g, b, a)`,
            # a very common `mainImage` tail pattern — see m1 in
            # AUDIT.md); the swatch itself only ever previews/edits the
            # RGB components (`_update_swatch_color`/`_on_swatch_clicked`
            # both slice to the first 3), the 4th spinbox (alpha) is left
            # to the ordinary numeric editing every vec component gets.
            swatch = QPushButton()
            swatch.setFixedSize(28, 22)
            self._update_swatch_color(swatch, lit.value)
            swatch.clicked.connect(lambda checked=False, o=ordinal: self._on_swatch_clicked(o))

        for i, spin in enumerate(spins):
            spin.valueChanged.connect(
                lambda v, o=ordinal, idx=i: self._on_vec_component_changed(o, idx, v)
            )

        reset_btn = QToolButton()
        reset_btn.setText("↺")
        reset_btn.setToolTip(tr("sliders_panel.reset_value_tooltip"))
        reset_btn.clicked.connect(lambda checked=False, o=ordinal: self._reset_ordinals([o]))

        randomize_btn = QToolButton()
        randomize_btn.setText("🎲")
        randomize_btn.setToolTip(tr("sliders_panel.randomize_value_tooltip"))
        randomize_btn.clicked.connect(lambda checked=False, o=ordinal: self._randomize_ordinals([o]))

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        if swatch is not None:
            row_layout.addWidget(swatch)
        for spin in spins:
            row_layout.addWidget(spin)
        row_layout.addWidget(reset_btn)
        row_layout.addWidget(randomize_btn)
        return row, (swatch, *spins)

    def refresh(self, sliders) -> None:
        """Same literal count/categories as before: resync offsets/values
        in place without recreating widgets, so an active drag survives
        the recompile it just triggered."""
        tagged = _combine_sliders(sliders)
        old_initial_values = [state.initial_value for state in self._literals]
        old_keyframes = [state.keyframes for state in self._literals]
        old_curves = [state.curve for state in self._literals]
        self._literals = [
            _LiteralState(
                kind, lit,
                initial_value=old_initial_values[i] if i < len(old_initial_values) else None,
                keyframes=old_keyframes[i] if i < len(old_keyframes) else None,
                curve=old_curves[i] if i < len(old_curves) else None,
            )
            for i, (kind, lit) in enumerate(tagged)
        ]
        for ordinal, lit in enumerate(self._literals):
            if ordinal >= len(self._rows) or self._rows[ordinal] is None:
                continue
            widgets = self._rows[ordinal]

            if lit.kind in ("float", "int"):
                slider, spin = widgets
                if abs(spin.value() - lit.value) > 1e-6:
                    spin.blockSignals(True)
                    spin.setValue(lit.value)
                    spin.blockSignals(False)
                    self._set_slider_from_value(slider, lit.min, lit.max, lit.value)
            elif lit.kind == "bool":
                (checkbox,) = widgets
                if checkbox.isChecked() != lit.value:
                    checkbox.blockSignals(True)
                    checkbox.setChecked(lit.value)
                    checkbox.blockSignals(False)
            else:  # vec2 / vec3
                swatch, *spins = widgets
                for spin, value in zip(spins, lit.value):
                    if abs(spin.value() - value) > 1e-6:
                        spin.blockSignals(True)
                        spin.setValue(value)
                        spin.blockSignals(False)
                if swatch is not None:
                    self._update_swatch_color(swatch, lit.value)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        tab_has_visible_row = [False] * self.count()
        for row, form, searchable in self._filter_targets:
            visible = not needle or needle in searchable
            form.setRowVisible(row, visible)
            if visible:
                # row's ancestor chain goes through the scroll area's
                # viewport; walk up to find which tab page it belongs to.
                widget = row
                while widget is not None and self.indexOf(widget) == -1:
                    widget = widget.parentWidget()
                tab_index = self.indexOf(widget) if widget is not None else -1
                if 0 <= tab_index < len(tab_has_visible_row):
                    tab_has_visible_row[tab_index] = True
        for i in range(self.count()):
            self.setTabVisible(i, tab_has_visible_row[i] if needle else True)

    @staticmethod
    def _set_slider_from_value(slider: QSlider, lo: float, hi: float, value: float) -> None:
        ratio = 0.0 if hi <= lo else (value - lo) / (hi - lo)
        ratio = max(0.0, min(1.0, ratio))
        slider.blockSignals(True)
        slider.setValue(int(ratio * SLIDER_STEPS))
        slider.blockSignals(False)

    @staticmethod
    def _update_swatch_color(swatch: QPushButton, values: list[float]) -> None:
        # `values` has 3 components for a vec3 and 4 (RGB + alpha) for a
        # vec4 (see m1 in AUDIT.md) — the swatch preview always shows just
        # the RGB part, so slice rather than unpack to stay correct for
        # both sizes.
        r, g, b = (int(max(0.0, min(1.0, v)) * 255) for v in values[:3])
        swatch.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid #444;")

    # ---- scalar (float/int) interaction --------------------------------

    def _on_slider_moved(self, raw: int, spin, ordinal: int, integer: bool) -> None:
        # Re-arm the guard on every tick: covers mouse-wheel/keyboard bursts
        # that never touch _drag_depth (see is_drag_active() and C2 in
        # AUDIT.md). Cheap no-op for genuine mouse drags, which are already
        # covered by sliderPressed/sliderReleased.
        self._arm_interaction_quiescence()
        lo, hi = spin.minimum(), spin.maximum()
        value = lo + (raw / SLIDER_STEPS) * (hi - lo)
        if integer:
            value = round(value)
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)
        if integer:
            self._emit_int_edit(ordinal, int(value))
        else:
            self._emit_float_edit(ordinal, value, spin.decimals())

    def _on_spin_changed(self, value, slider: QSlider, ordinal: int, spin, integer: bool) -> None:
        self._set_slider_from_value(slider, spin.minimum(), spin.maximum(), value)
        if integer:
            self._emit_int_edit(ordinal, int(value))
        else:
            self._emit_float_edit(ordinal, value, spin.decimals())

    def _show_range_menu(self, slider: QSlider, spin: QDoubleSpinBox, global_pos) -> None:
        menu = QMenu(self)
        edit_action = menu.addAction(tr("dialogs.slider_bounds.menu_edit"))
        chosen = menu.exec(global_pos)
        if chosen != edit_action:
            return
        self._edit_range(slider, spin)

    def _edit_range(self, slider: QSlider, spin: QDoubleSpinBox) -> None:
        """Overrides a slider's min/max locally (UI-only, does not touch the
        code): the auto-detected `[0, 2×valeur]` heuristic isn't always the
        right range for a given literal."""
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("dialogs.slider_bounds.title"))
        form = QFormLayout(dialog)

        min_box = QDoubleSpinBox()
        min_box.setRange(-1e9, 1e9)
        min_box.setDecimals(4)
        min_box.setValue(spin.minimum())
        max_box = QDoubleSpinBox()
        max_box.setRange(-1e9, 1e9)
        max_box.setDecimals(4)
        max_box.setValue(spin.maximum())
        decimals_box = QSpinBox()
        decimals_box.setRange(0, 8)
        decimals_box.setValue(spin.decimals())

        form.addRow(tr("dialogs.slider_bounds.min"), min_box)
        form.addRow(tr("dialogs.slider_bounds.max"), max_box)
        form.addRow(tr("dialogs.slider_bounds.decimals"), decimals_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        new_min, new_max = min_box.value(), max_box.value()
        if new_max <= new_min:
            return

        value = min(max(spin.value(), new_min), new_max)
        spin.blockSignals(True)
        spin.setDecimals(decimals_box.value())
        spin.setMinimum(new_min)
        spin.setMaximum(new_max)
        spin.setSingleStep((new_max - new_min) / SLIDER_STEPS)
        spin.setValue(value)
        spin.blockSignals(False)
        self._set_slider_from_value(slider, new_min, new_max, value)

    def _show_vec_component_range_menu(self, spin: QDoubleSpinBox, global_pos) -> None:
        menu = QMenu(self)
        edit_action = menu.addAction(tr("dialogs.slider_bounds.menu_edit"))
        chosen = menu.exec(global_pos)
        if chosen != edit_action:
            return
        self._edit_vec_component_range(spin)

    def _edit_vec_component_range(self, spin: QDoubleSpinBox) -> None:
        """Same UI-only min/max/decimals override as `_edit_range`, adapted
        for a single vec2/vec3/vec4 component spinbox — which, unlike a
        scalar slider, has no separate `QSlider` companion widget to
        rebuild (vec rows are spinbox-only, see `_build_vec_row`).

        Before this, vec components were wired to a hardcoded 4 decimals
        with no way to raise it (see m2 in AUDIT.md): this mechanically
        worsened C1 for any vector with a small-magnitude component (a
        normal, a fine offset) with no UI escape hatch at all, not even a
        right-click, unlike scalar sliders which already had this dialog.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("dialogs.slider_bounds.title"))
        form = QFormLayout(dialog)

        min_box = QDoubleSpinBox()
        min_box.setRange(-1e9, 1e9)
        min_box.setDecimals(4)
        min_box.setValue(spin.minimum())
        max_box = QDoubleSpinBox()
        max_box.setRange(-1e9, 1e9)
        max_box.setDecimals(4)
        max_box.setValue(spin.maximum())
        decimals_box = QSpinBox()
        decimals_box.setRange(0, 8)
        decimals_box.setValue(spin.decimals())

        form.addRow(tr("dialogs.slider_bounds.min"), min_box)
        form.addRow(tr("dialogs.slider_bounds.max"), max_box)
        form.addRow(tr("dialogs.slider_bounds.decimals"), decimals_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        new_min, new_max = min_box.value(), max_box.value()
        if new_max <= new_min:
            return

        value = min(max(spin.value(), new_min), new_max)
        spin.blockSignals(True)
        spin.setDecimals(decimals_box.value())
        spin.setMinimum(new_min)
        spin.setMaximum(new_max)
        spin.setSingleStep(0.01)
        spin.setValue(value)
        spin.blockSignals(False)

    # ---- bool interaction -----------------------------------------------

    def _on_bool_toggled(self, checked: bool, ordinal: int) -> None:
        self._emit_edit(ordinal, "true" if checked else "false", checked)

    # ---- vec2/vec3 interaction --------------------------------------------

    def _on_vec_component_changed(self, ordinal: int, index: int, value: float) -> None:
        if ordinal >= len(self._literals):
            return
        state = self._literals[ordinal]
        new_values = list(state.value)
        new_values[index] = value
        widgets = self._rows[ordinal]
        swatch = widgets[0] if widgets else None
        if swatch is not None:
            self._update_swatch_color(swatch, new_values)
        self._emit_vec_edit(ordinal, new_values)

    def _on_swatch_clicked(self, ordinal: int) -> None:
        if ordinal >= len(self._literals):
            return
        state = self._literals[ordinal]
        # Only the first 3 components are ever color (RGB); a vec4 (see
        # m1 in AUDIT.md) carries a 4th alpha component that this dialog
        # doesn't touch — it's carried through unchanged below via
        # `state.value[3:]`, so it round-trips through `picked_values`
        # exactly like the existing per-component spin edits do.
        r, g, b = (int(max(0.0, min(1.0, v)) * 255) for v in state.value[:3])
        chosen = QColorDialog.getColor(QColor(r, g, b), self, tr("dialogs.color_picker.title"))
        if not chosen.isValid():
            return
        picked_values = [chosen.redF(), chosen.greenF(), chosen.blueF(), *state.value[3:]]
        widgets = self._rows[ordinal]
        # What actually gets written to the code (see _emit_vec_edit below)
        # must always match what the spinboxes display. `picked_values` is
        # the color dialog's raw 0..1 output, unbounded by each component
        # spinbox's own [min, max] (set once at row-build time from the
        # literal's original magnitude — see `_default_float_range` — so a
        # bright color picked for an originally dark/narrow-ranged vec3
        # commonly falls outside it). `spin.setValue()` below silently
        # clamps to that range; reading `spin.value()` back afterward is
        # what the row now genuinely displays, so that's what gets emitted
        # — never the unclamped `picked_values` (see C3 in AUDIT.md: writing
        # the unclamped value there left the code and the UI permanently
        # out of sync, since a later refresh() reclamps identically instead
        # of ever reconciling the two).
        new_values = picked_values
        if widgets is not None:
            swatch, *spins = widgets
            new_values = []
            for spin, value in zip(spins, picked_values):
                spin.blockSignals(True)
                spin.setValue(value)
                new_values.append(spin.value())
                spin.blockSignals(False)
            if swatch is not None:
                self._update_swatch_color(swatch, new_values)
        self._emit_vec_edit(ordinal, new_values)

    # ---- shared reset / randomize / filter ------------------------------

    def _reset_ordinals(self, ordinals: list[int]) -> None:
        """Resets one or more literals back to the value they had when their
        row was created (rebuild() time) — there's no annotated `default`
        anymore, so "reset" means "undo my edits since this session"."""
        for ordinal in ordinals:
            if ordinal >= len(self._literals) or ordinal >= len(self._rows):
                continue
            widgets = self._rows[ordinal]
            if widgets is None:
                continue
            state = self._literals[ordinal]
            if state.kind in ("float", "int"):
                _, spin = widgets
                target = state.initial_value
                target = min(max(target, spin.minimum()), spin.maximum())
                spin.setValue(target)  # triggers _on_spin_changed -> emits the edit
            elif state.kind == "bool":
                (checkbox,) = widgets
                checkbox.setChecked(state.initial_value)  # triggers _on_bool_toggled
            else:  # vec2 / vec3
                swatch, *spins = widgets
                for spin, value in zip(spins, state.initial_value):
                    spin.setValue(value)  # triggers _on_vec_component_changed per spin

    def _randomize_ordinals(self, ordinals: list[int]) -> None:
        """Randomizes one or more literals within their current bounds
        (bools flip randomly, vec components stay in their own spinbox
        range, same as reset does per-component)."""
        for ordinal in ordinals:
            if ordinal >= len(self._rows) or self._rows[ordinal] is None:
                continue
            state = self._literals[ordinal]
            widgets = self._rows[ordinal]
            if state.kind in ("float", "int"):
                _, spin = widgets
                if state.kind == "int":
                    spin.setValue(random.randint(spin.minimum(), spin.maximum()))
                else:
                    spin.setValue(random.uniform(spin.minimum(), spin.maximum()))
            elif state.kind == "bool":
                (checkbox,) = widgets
                checkbox.setChecked(random.random() < 0.5)
            else:  # vec2 / vec3
                swatch, *spins = widgets
                for spin in spins:
                    spin.setValue(random.uniform(spin.minimum(), spin.maximum()))

    # ---- keyframing (scalar sliders only) --------------------------------
    #
    # Playback rides the shared animation clock (`iTime`, pushed in via
    # `set_time` from `Viewport.timeUpdated`) rather than a dedicated
    # transport: the toolbar's existing Play/Pause and "Reinitialiser le
    # temps" already start/stop/rewind it, so recording a couple of
    # keyframes and hitting Play is enough to preview a sequence.

    def set_time(self, t: float) -> None:
        """Advances the shared animation clock and snaps every keyframed
        scalar slider to its interpolated value at `t` (see
        `_interpolate_keyframes`). A slider with no keyframes is untouched
        — the common case, so this stays cheap. Skips the edit entirely
        when the interpolated value hasn't meaningfully moved since the
        last tick, so a paused/idle clock doesn't spam edits into the
        editor 60 times a second for nothing.

        Also skips the whole pass when `t` itself hasn't changed since the
        previous call. `Viewport.timeUpdated` fires on *every* render tick
        whether the clock is playing or paused, so without this guard a
        paused clock (constant `t`, called ~60x/s) would keep re-imposing
        the keyframe-interpolated value on top of anything the user just
        did to that slider by hand (drag, typed value, reset, randomize):
        the manual edit gets silently overwritten within a single frame,
        every time, and never actually sticks — see the module docstring's
        "BUG FIX" paragraph.

        RM10.md section 3: `setUpdatesEnabled(False)` around the per-slider
        widget updates below coalesces their repaints into one, instead of
        each `spin.setValue()` triggering its own immediate layout/paint
        pass. With only a handful of simultaneously keyframed sliders the
        difference is negligible, but a shader with several dozen of them
        animating at once (measured: ~50) could otherwise spike a single
        `set_time()` call past 40ms on some ticks — an entire render frame
        (or several) spent just updating sliders, before the GPU render
        call for that frame has even started. Confirmed empirically: with
        this guard, the same 50-simultaneously-keyframed-sliders case never
        exceeds ~10ms."""
        if t == self._time:
            return
        self._time = t
        self.setUpdatesEnabled(False)
        try:
            for ordinal, (state, widgets) in enumerate(zip(self._literals, self._rows)):
                if widgets is None or not state.keyframes:
                    continue
                value = _interpolate_keyframes(state.keyframes, t, state.curve)
                if state.kind == "int":
                    value = round(value)
                if abs(value - state.value) < 1e-6:
                    continue
                _, spin = widgets
                spin.setValue(value)  # triggers _on_spin_changed -> emits the edit
        finally:
            self.setUpdatesEnabled(True)

    def add_keyframe(self, ordinal: int) -> None:
        """Records this slider's current value as a keyframe at the
        animation clock's current time, replacing any existing keyframe
        within `KEYFRAME_MERGE_EPS` seconds of it rather than piling up
        near-duplicates from repeated clicks at roughly the same moment."""
        if ordinal >= len(self._literals) or ordinal >= len(self._rows):
            return
        if self._rows[ordinal] is None:
            return
        state = self._literals[ordinal]
        if state.kind not in ("float", "int"):
            return
        t = self._time
        state.keyframes = [kf for kf in state.keyframes if abs(kf[0] - t) > KEYFRAME_MERGE_EPS]
        state.keyframes.append((t, float(state.value)))
        state.keyframes.sort(key=lambda kf: kf[0])
        self._refresh_keyframe_button(ordinal)

    def clear_keyframes(self, ordinal: int) -> None:
        if ordinal >= len(self._literals):
            return
        self._literals[ordinal].keyframes = []
        self._refresh_keyframe_button(ordinal)

    def _clear_keyframes_ordinals(self, ordinals: list[int]) -> None:
        for ordinal in ordinals:
            self.clear_keyframes(ordinal)

    def _show_keyframe_menu(self, ordinal: int, global_pos) -> None:
        if ordinal >= len(self._literals):
            return
        state = self._literals[ordinal]
        menu = QMenu(self)
        add_action = menu.addAction(tr("sliders_panel.add_keyframe_menu", time=f"{self._time:.2f}"))
        clear_action = menu.addAction(tr("sliders_panel.clear_keyframes_menu"))
        clear_action.setEnabled(bool(state.keyframes))
        curve_action = menu.addAction(tr("sliders_panel.edit_curve_menu"))
        # RM10.md section 3: a curve shape only means anything once there
        # are (at least) two keyframes to interpolate *between* -- offered
        # but disabled rather than hidden, same convention as
        # `clear_action`, so the option stays discoverable either way.
        curve_action.setEnabled(len(state.keyframes) >= 2)
        chosen = menu.exec(global_pos)
        if chosen == add_action:
            self.add_keyframe(ordinal)
        elif chosen == clear_action:
            self.clear_keyframes(ordinal)
        elif chosen == curve_action:
            self._edit_keyframe_curve(ordinal)

    def _edit_keyframe_curve(self, ordinal: int) -> None:
        """RM10.md section 3: lets the interpolation *shape* between this
        slider's keyframes be chosen and actually seen -- not just a bare
        list of (time, value) pairs. `_CurvePreviewWidget` plots the real
        keyframes with the currently-selected curve live, so switching the
        combo box shows exactly what playback will do before committing to
        it."""
        if ordinal >= len(self._literals):
            return
        state = self._literals[ordinal]
        if state.kind not in ("float", "int") or len(state.keyframes) < 2:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(tr("dialogs.keyframe_curve.title"))
        layout = QVBoxLayout(dialog)

        combo = QComboBox()
        combo.addItem(tr("dialogs.keyframe_curve.linear"), KEYFRAME_CURVE_LINEAR)
        combo.addItem(tr("dialogs.keyframe_curve.ease"), KEYFRAME_CURVE_EASE)
        combo.addItem(tr("dialogs.keyframe_curve.step"), KEYFRAME_CURVE_STEP)
        combo.setCurrentIndex(KEYFRAME_CURVES.index(state.curve))
        layout.addWidget(combo)

        preview = _CurvePreviewWidget(state.keyframes, state.curve)
        layout.addWidget(preview)
        combo.currentIndexChanged.connect(lambda i: preview.set_curve(combo.itemData(i)))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return
        state.curve = combo.currentData()
        # Forces set_time's re-evaluation on the next tick even if the
        # clock's own `t` hasn't moved: only the curve *shape* changed,
        # which set_time's own `t == self._time` guard has no way to know
        # about on its own, so the currently-displayed value could
        # otherwise keep showing the old curve's result until the clock
        # itself advances again.
        current_t = self._time
        self._time = None
        self.set_time(current_t)

    def _refresh_keyframe_button(self, ordinal: int) -> None:
        if ordinal >= len(self._keyframe_buttons):
            return
        btn = self._keyframe_buttons[ordinal]
        if btn is None:
            return
        state = self._literals[ordinal]
        count = len(state.keyframes)
        btn.setText("🎬" if count == 0 else f"🎬{count}")
        if count == 0:
            btn.setToolTip(tr("sliders_panel.add_keyframe_tooltip", time=f"{self._time:.2f}"))
        else:
            times = ", ".join(f"{t:.2f}s" for t, _ in state.keyframes)
            btn.setToolTip(
                tr("sliders_panel.keyframe_times_tooltip", count=count, times=times)
                + tr("sliders_panel.keyframe_button_tooltip", time=f"{self._time:.2f}")
            )

    # ---- edit emission (offset bookkeeping shared by every kind) --------

    def _emit_edit(self, ordinal: int, text: str, new_value) -> None:
        if ordinal >= len(self._literals):
            return
        state = self._literals[ordinal]
        old_len = state.end - state.start
        delta = len(text) - old_len

        self.literalEdited.emit(state.start, state.end, text)

        state.value = new_value
        state.end = state.start + len(text)
        if delta != 0:
            for other in self._literals[ordinal + 1 :]:
                other.start += delta
                other.end += delta

    def _emit_float_edit(self, ordinal: int, value: float, decimals: int = 6) -> None:
        self._emit_edit(ordinal, format_glsl_float(value, decimals), value)

    def _emit_int_edit(self, ordinal: int, value: int) -> None:
        self._emit_edit(ordinal, str(value), value)

    def _emit_vec_edit(self, ordinal: int, values: list[float]) -> None:
        if ordinal >= len(self._literals):
            return
        size = len(values)
        # Each component can carry its own decimals override now (see m2
        # in AUDIT.md — right-click on a vec component spinbox), so read
        # it back per-spin instead of the previous hardcoded 4, or the
        # override would be visible in the UI but silently ignored when
        # writing the code.
        widgets = self._rows[ordinal] if ordinal < len(self._rows) else None
        if widgets is not None:
            decimals = [spin.decimals() for spin in widgets[1:]]
        else:
            decimals = [4] * size
        text = f"vec{size}(" + ", ".join(
            format_glsl_float(v, d) for v, d in zip(values, decimals)
        ) + ")"
        self._emit_edit(ordinal, text, values)
