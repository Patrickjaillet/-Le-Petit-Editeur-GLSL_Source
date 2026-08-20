"""Main application window: menubar, toolbar, splitters, footer."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSpinBox,
    QSplitter,
    QTabBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

import engine_bridge
import shadertoy_import
import video_export
import workspace_dirs
from app_version import APP_VERSION
import i18n
from i18n import tr
from shortcuts import ShortcutRegistry
from ui.export_progress_dialog import ExportProgressDialog
from ui.export_video_dialog import ExportVideoDialog, record_actual_export_size
from ui.footer import RENDER_SCALE_OPTIONS, Footer
from ui.ichannel_panel import THUMB_SIZE, IChannelPanel
from ui.monaco_editor import MonacoEditor
from ui.shortcuts_dialog import ShortcutsDialog
from ui.sliders_panel import SlidersPanel
from ui.viewport import VIEWPORT_HEIGHT, VIEWPORT_WIDTH, Viewport
from audio_source import AudioChannelSource
from video_source import VideoChannelSource

DEFAULT_SHADER_PATH = Path(__file__).resolve().parent.parent / "assets" / "shaders" / "default.frag"
COMPILE_DEBOUNCE_MS = 350
SLIDER_COMPILE_DEBOUNCE_MS = 100
# Hard cap on how long `_on_text_changed` may keep postponing a recompile
# by restarting the single-shot `_compile_timer`. Needed because a
# continuous edit stream (a playing keyframed slider, see
# `_compile_burst_started_at`) never leaves a `delay`-sized gap for the
# debounce to actually fire — without this cap the shader would simply
# stop recompiling for as long as the stream continues.
MAX_COMPILE_DEBOUNCE_MS = 250

# RM10.md section 5: minimum wall-clock gap between live thumbnail
# refreshes for one video/webcam/audio iChannel slot -- see
# `_thumb_last_update`.
_THUMBNAIL_MIN_INTERVAL_S = 0.2

_LINE_COL_RE = re.compile(r":(\d+):(\d+)")
MAX_RECENT_FILES = 8

# `_build_project_dict`'s own `"format"` field. RM10.md section 9: opening
# a project written by a *newer* version of this software (a higher
# number here than this build knows about) must warn explicitly rather
# than silently loading whatever of it happens to still make sense --
# see `_load_project`.
PROJECT_FORMAT_VERSION = 3

# The "Common" tab isn't a real render pass, just plain GLSL prepended to
# every pass before compilation — it needs a slot in the tab bar but no
# valid engine_bridge.PASS_* index of its own.
COMMON_TAB = 5

_TAB_ORDER = [
    engine_bridge.PASS_IMAGE,
    engine_bridge.PASS_BUFFER_A,
    engine_bridge.PASS_BUFFER_B,
    engine_bridge.PASS_BUFFER_C,
    engine_bridge.PASS_BUFFER_D,
    COMMON_TAB,
]
# Maps to `tabs.*` keys rather than baking translated text into a
# module-level constant at import time (before `i18n.load_language()` has
# run) — resolved through `tr()` lazily, when the tab bar is actually built.
_TAB_LABEL_KEYS = {
    engine_bridge.PASS_IMAGE: "tabs.image",
    engine_bridge.PASS_BUFFER_A: "tabs.buffer_a",
    engine_bridge.PASS_BUFFER_B: "tabs.buffer_b",
    engine_bridge.PASS_BUFFER_C: "tabs.buffer_c",
    engine_bridge.PASS_BUFFER_D: "tabs.buffer_d",
    COMMON_TAB: "tabs.common",
}

_BUFFER_STUB = (
    "void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n"
    "    fragColor = vec4(0.0, 0.0, 0.0, 1.0);\n"
    "}\n"
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("app.window_title", version=APP_VERSION))

        self._engine = engine_bridge.Engine(VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
        self._pending_edit_from_slider = False
        # Wall-clock time (`time.monotonic()`) of the first edit in the
        # current uninterrupted burst of `_compile_timer` restarts, or
        # `None` when the timer is idle. `_on_text_changed` restarts a
        # *single-shot* timer on every edit, which is a correct debounce
        # for human typing/dragging (edits stop, the gap exceeds `delay`,
        # the timer finally fires) but starves completely under a
        # continuous edit stream that never leaves a `delay`-sized gap —
        # exactly what a playing keyframed slider produces, since
        # `Viewport.timeUpdated`/`set_time` can emit a fresh
        # `literalEdited` on every ~16ms render tick. Without a cap, the
        # shader would then never recompile again for as long as the
        # animation keeps interpolating, even though the sliders panel
        # keeps showing updated values. See `MAX_COMPILE_DEBOUNCE_MS`.
        self._compile_burst_started_at: float | None = None
        self._pre_golf_source: str | None = None
        self._golf_options: tuple[bool, bool, bool] | None = (True, True, True)
        # Path of the `.json` project (or bare `.frag`, display-only in that
        # case -- see `_quick_save_current_project`'s suffix guard) most
        # recently opened/saved/exported in this session, or `None` before
        # anything has ever been. Drives the window title (`_update_window_title`)
        # and is what a "Save" choice in the unsaved-changes dialog writes to
        # when it's a real project path (RM10.md section 1, items 4/5).
        self._current_project_path: str | None = None
        self._is_dirty = False

        # One live `VideoChannelSource` per (pass_idx, channel_idx) slot
        # currently assigned to a video file or webcam — see
        # `_apply_ichannel_assignment`/`_stop_video_channel`. Kept here
        # rather than inside `IChannelPanel` since it's the render-engine
        # side of the assignment (an open file handle / camera device),
        # not UI display state, and must be torn down from `closeEvent`
        # regardless of which pass/tab happens to be showing at the time.
        self._video_sources: dict[tuple[int, int], VideoChannelSource] = {}

        # One live `AudioChannelSource` per (pass_idx, channel_idx) slot
        # currently assigned to an audio file — same lifecycle/ownership
        # split as `_video_sources` above (render-engine-adjacent state,
        # not UI display state; must be torn down from `closeEvent`
        # regardless of which tab is showing). Unlike video, there's no
        # per-decoded-chunk push into the engine: `_on_audio_tick` (wired
        # to `viewport.timeUpdated` below) polls every active source once
        # per UI tick instead, since the spectrum/waveform only make sense
        # computed over a rolling window, not frame-by-frame as chunks
        # arrive.
        self._audio_sources: dict[tuple[int, int], AudioChannelSource] = {}

        # RM10.md section 5: wall-clock time (`time.monotonic()`) each
        # (pass, channel) video/webcam/audio slot's live thumbnail was last
        # refreshed. Video/webcam frames and audio ticks both arrive far
        # faster (~30-60fps / ~60/s) than a thumbnail glanced at by eye
        # needs to update — this throttles refreshes to
        # `_THUMBNAIL_MIN_INTERVAL_S` apart per slot instead of converting
        # to a `QPixmap` and repainting on every single tick.
        self._thumb_last_update: dict[tuple[int, int], float] = {}

        self._current_tab = engine_bridge.PASS_IMAGE
        self._pass_sources: dict[int, str] = {
            engine_bridge.PASS_BUFFER_A: "",
            engine_bridge.PASS_BUFFER_B: "",
            engine_bridge.PASS_BUFFER_C: "",
            engine_bridge.PASS_BUFFER_D: "",
            engine_bridge.PASS_IMAGE: "",
        }
        self._common_source = ""

        # Per-pass (keyed by `str(tab_id)`, `COMMON_TAB` included) snapshots
        # of slider min/max/decimals overrides — see `_refresh_sliders_for`
        # and `SlidersPanel.export_layout`/`apply_layout`. `_slider_panel_tab`
        # tracks which tab's data is *currently* live in `sliders_panel`
        # (which can briefly lag `_current_tab` right after a tab switch,
        # before the panel has actually been rebuilt for it) — `None` means
        # "whatever's in the panel right now is stale/unrelated, don't
        # bother snapshotting it" (used when loading a project/new file).
        self._slider_layouts: dict[str, list[dict]] = {}
        self._slider_panel_tab: int | None = self._current_tab

        # Dernier dialecte détecté (`engine_bridge.DIALECT_SHADERTOY`/
        # `DIALECT_GLSL`) par pass, clé `str(pass_idx)` — même convention
        # que `_slider_layouts`. N'inclut jamais `COMMON_TAB` : le Common
        # n'est pas lui-même un dialecte, voir `_update_dialect_indicator`.
        # Sert de "previous" à `engine_bridge.detect_dialect` pour qu'un
        # texte sans aucun signal (ex. un helper pur) garde le mode déjà
        # affiché plutôt que de retomber sur une valeur par défaut à
        # chaque frappe.
        self._pass_dialects: dict[str, str] = {}

        self._settings = QSettings("PetitEditeurGLSL", "PetitEditeurGLSL")
        raw_recent = self._settings.value("recentFiles", [])
        if isinstance(raw_recent, str):
            raw_recent = [raw_recent] if raw_recent else []
        self._recent_files: list[str] = [p for p in raw_recent if p and Path(p).exists()]
        self._compile_debounce_ms = self._settings.value(
            "compileDebounceMs", COMPILE_DEBOUNCE_MS, type=int
        )

        # Built before _build_menu()/_build_toolbar() so every QAction they
        # create can call registry.register(...) inline as it's built,
        # instead of wiring shortcuts in a separate pass afterwards.
        self._shortcuts = ShortcutRegistry(self._settings)

        self._build_menu()
        self._build_toolbar()
        self._build_central_widget()
        self.setStatusBar(self.footer)
        self._restore_layout()

        self._compile_timer = QTimer(self)
        self._compile_timer.setSingleShot(True)
        self._compile_timer.timeout.connect(self._recompile_current_tab)
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.editorReady.connect(self._apply_editor_preferences)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._write_autosave)
        self._apply_autosave_settings()

        if not self._try_crash_recovery():
            self._load_default_shader()

    # ---- UI construction -------------------------------------------------

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        reg = self._shortcuts.register

        file_menu = menu_bar.addMenu(tr("menu.file.title"))
        new_action = reg("file.new", QAction(tr("menu.file.new"), self))
        new_action.triggered.connect(self._on_new)
        open_action = reg("file.open", QAction(tr("menu.file.open"), self))
        open_action.triggered.connect(self._on_open)
        self._recent_menu = QMenu(tr("menu.file.recent_files"), self)
        open_project_action = reg("file.open_project", QAction(tr("menu.file.open_project"), self))
        open_project_action.triggered.connect(self._on_open_project)
        import_shadertoy_action = reg("file.import_shadertoy", QAction(tr("menu.file.import_shadertoy"), self))
        import_shadertoy_action.triggered.connect(self._on_import_shadertoy)
        save_action = reg("file.save_as", QAction(tr("menu.file.save_as"), self))
        save_action.triggered.connect(self._on_save_as)
        save_project_action = reg("file.save_project", QAction(tr("menu.file.save_project"), self))
        save_project_action.triggered.connect(self._on_save_project)
        export_action = reg("file.export_golfed", QAction(tr("menu.file.export_golfed"), self))
        export_action.triggered.connect(self._on_export_golfed)
        # Compiled-shader export (HLSL/MSL): a one-off translation of the
        # pass currently shown in the editor, never a new tab/dialect --
        # see RMLG.md section 2. Deliberately a submenu next to the golfed
        # export rather than its own top-level menu entry, since it's the
        # same family of "write this pass out to a file" actions.
        export_compiled_menu = QMenu(tr("menu.file.export_compiled_menu"), self)
        export_hlsl_action = reg("file.export_hlsl", QAction(tr("menu.file.export_hlsl"), self))
        export_hlsl_action.triggered.connect(lambda: self._on_export_compiled_shader("hlsl"))
        export_msl_action = reg("file.export_msl", QAction(tr("menu.file.export_msl"), self))
        export_msl_action.triggered.connect(lambda: self._on_export_compiled_shader("msl"))
        export_compiled_menu.addAction(export_hlsl_action)
        export_compiled_menu.addAction(export_msl_action)
        export_png_action = reg("file.export_png", QAction(tr("menu.file.export_png"), self))
        export_png_action.triggered.connect(self._on_export_png)
        export_video_action = reg("file.export_video", QAction(tr("menu.file.export_video"), self))
        export_video_action.triggered.connect(self._on_export_video)
        preferences_action = reg("file.preferences", QAction(tr("menu.file.preferences"), self))
        preferences_action.triggered.connect(self._on_preferences)
        quit_action = reg("file.quit", QAction(tr("menu.file.quit"), self))
        quit_action.triggered.connect(self.close)
        for a in (new_action, open_action, open_project_action, import_shadertoy_action):
            file_menu.addAction(a)
        file_menu.addMenu(self._recent_menu)
        for a in (save_action, save_project_action, export_action):
            file_menu.addAction(a)
        file_menu.addMenu(export_compiled_menu)
        for a in (export_png_action, export_video_action):
            file_menu.addAction(a)
        file_menu.addSeparator()
        file_menu.addAction(preferences_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)
        self._rebuild_recent_menu()

        edit_menu = menu_bar.addMenu(tr("menu.edit.title"))
        undo_action = reg("edit.undo", QAction(tr("menu.edit.undo"), self))
        undo_action.triggered.connect(lambda: self.editor.undo())
        redo_action = reg("edit.redo", QAction(tr("menu.edit.redo"), self))
        redo_action.triggered.connect(lambda: self.editor.redo())
        golf_action = reg("edit.golf", QAction(tr("menu.edit.golf"), self))
        golf_action.triggered.connect(self._on_golf)
        undo_golf_action = reg("edit.undo_golf", QAction(tr("menu.edit.undo_golf"), self))
        undo_golf_action.triggered.connect(self._on_undo_golf)
        degolf_action = reg("edit.degolf", QAction(tr("menu.edit.degolf"), self))
        degolf_action.triggered.connect(self._on_degolf)
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)
        golf_all_action = reg("edit.golf_all", QAction(tr("menu.edit.golf_all"), self))
        golf_all_action.triggered.connect(self._on_golf_all)
        edit_menu.addSeparator()
        edit_menu.addAction(golf_action)
        edit_menu.addAction(golf_all_action)
        edit_menu.addAction(undo_golf_action)
        edit_menu.addAction(degolf_action)
        edit_menu.addSeparator()
        shortcuts_action = QAction(tr("menu.edit.shortcuts"), self)
        shortcuts_action.triggered.connect(self._on_edit_shortcuts)
        edit_menu.addAction(shortcuts_action)

        help_menu = menu_bar.addMenu(tr("menu.help.title"))
        about_action = QAction(tr("menu.help.about"), self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _build_toolbar(self) -> None:
        reg = self._shortcuts.register

        toolbar = QToolBar(tr("app.toolbar_name"), self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        run_action = reg("toolbar.compile", QAction(tr("toolbar.compile"), self))
        run_action.triggered.connect(self._recompile_current_tab)
        toolbar.addAction(run_action)

        toolbar.addSeparator()

        self._play_action = reg("toolbar.play_pause", QAction(tr("toolbar.pause"), self))
        self._play_action.setCheckable(True)
        self._play_action.setToolTip(tr("toolbar.play_pause_tooltip"))
        self._play_action.toggled.connect(self._on_play_toggled)
        toolbar.addAction(self._play_action)

        reset_time_action = reg("toolbar.reset_time", QAction(tr("toolbar.reset_time"), self))
        reset_time_action.setToolTip(tr("toolbar.reset_time_tooltip"))
        reset_time_action.triggered.connect(lambda: self.viewport.reset_time())
        toolbar.addAction(reset_time_action)

        toolbar.addSeparator()

        golf_action = reg("toolbar.golf", QAction(tr("toolbar.golf"), self))
        golf_action.triggered.connect(self._on_golf)
        toolbar.addAction(golf_action)

        degolf_action = reg("toolbar.degolf", QAction(tr("toolbar.degolf"), self))
        degolf_action.setToolTip(tr("toolbar.degolf_tooltip"))
        degolf_action.triggered.connect(self._on_degolf)
        toolbar.addAction(degolf_action)

    def _build_central_widget(self) -> None:
        self.footer = Footer(self)

        self.viewport = Viewport(self._engine, self)
        self.ichannel_panel = IChannelPanel(self)
        self.sliders_panel = SlidersPanel(self)
        self.editor = MonacoEditor(self)

        self.pass_tab_bar = QTabBar()
        for tab_id in _TAB_ORDER:
            self.pass_tab_bar.addTab(tr(_TAB_LABEL_KEYS[tab_id]))
        self.pass_tab_bar.setCurrentIndex(_TAB_ORDER.index(engine_bridge.PASS_IMAGE))
        self.pass_tab_bar.currentChanged.connect(self._on_pass_tab_changed)
        # RM10.md section 5: `IChannelPanel` defaults its own
        # `_active_pass` to 0 (== `engine_bridge.PASS_BUFFER_A`), and
        # `setCurrentIndex` just above never fires `currentChanged` (Qt
        # only emits on an actual value change, and the tab bar is already
        # at index 0 right after the `addTab` loop -- Image happens to be
        # first in `_TAB_ORDER`) -- so without this, `ichannel_panel`
        # would silently stay tracking Buffer A's slots while the editor/
        # viewport are actually showing Image, until the user's first
        # manual tab switch. Any iChannel assigned before that first
        # switch would attach to the wrong pass in the engine, with no
        # error and nothing visibly wrong in the UI to notice it by.
        self.ichannel_panel.set_active_pass(engine_bridge.PASS_IMAGE)

        self.viewport.fpsUpdated.connect(self.footer.set_fps)
        self.viewport.renderError.connect(self._on_render_error)
        self.viewport.resizeError.connect(self._on_viewport_resize_error)
        self.viewport.frameRendered.connect(self.footer.add_frame_time_sample)
        self.viewport.timeUpdated.connect(self.sliders_panel.set_time)
        self.viewport.timeUpdated.connect(self._on_audio_tick)
        self.viewport.resolutionChanged.connect(self.footer.set_resolution)
        self.footer.renderScaleChanged.connect(self._on_render_scale_changed)
        self.footer.set_resolution(*self.viewport.render_size(), 1.0)
        # RM10.md section 4: restores the user's last-chosen preview
        # resolution across sessions (persisted below, in
        # `_on_render_scale_changed`) rather than always silently reverting
        # to 100% -- a shader heavy enough to need downscaling for fluidity
        # is exactly the kind that would otherwise stutter again on every
        # single launch until re-lowered by hand.
        saved_scale = self._settings.value("renderScale", 1.0, type=float)
        if saved_scale in RENDER_SCALE_OPTIONS and saved_scale != 1.0:
            self.footer.set_render_scale_silent(saved_scale)
            self.viewport.set_render_scale(saved_scale)
        self.ichannel_panel.assignmentChanged.connect(self._on_ichannel_assignment_changed)
        self.ichannel_panel.audioSettingsChanged.connect(self._on_ichannel_audio_settings_changed)
        self.ichannel_panel.proceduralSettingsChanged.connect(self._on_ichannel_procedural_settings_changed)
        self.sliders_panel.literalEdited.connect(self._on_literal_edited)
        self.sliders_panel.dragFinished.connect(self._on_slider_drag_finished)

        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.viewport)

        self.left_splitter = QSplitter(Qt.Vertical)
        self.left_splitter.addWidget(left_container)
        self.left_splitter.addWidget(self.sliders_panel)
        self.left_splitter.setStretchFactor(0, 1)
        self.left_splitter.setStretchFactor(1, 1)

        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self.pass_tab_bar)
        right_layout.addWidget(self.editor, 1)
        right_layout.addWidget(self.ichannel_panel)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.addWidget(self.left_splitter)
        self.main_splitter.addWidget(right_container)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)

        self.setCentralWidget(self.main_splitter)

    def _restore_layout(self) -> None:
        """Restores window geometry and splitter positions saved by the
        previous session (`_save_layout`, called from `closeEvent`). Falls
        back to `main.py`'s default `resize(1400, 900)` and the splitters'
        natural (stretch-factor-driven) sizing the first time the app runs,
        or if the saved values ever become unreadable (e.g. after a layout
        code change), rather than raising or leaving the window at (0, 0).
        """
        geometry = self._settings.value("windowGeometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1400, 900)
        main_state = self._settings.value("mainSplitterState")
        if main_state is not None:
            self.main_splitter.restoreState(main_state)
        left_state = self._settings.value("leftSplitterState")
        if left_state is not None:
            self.left_splitter.restoreState(left_state)

    def _save_layout(self) -> None:
        self._settings.setValue("windowGeometry", self.saveGeometry())
        self._settings.setValue("mainSplitterState", self.main_splitter.saveState())
        self._settings.setValue("leftSplitterState", self.left_splitter.saveState())

    # ---- multi-pass tab switching -------------------------------------

    def _on_pass_tab_changed(self, tab_bar_index: int) -> None:
        new_tab = _TAB_ORDER[tab_bar_index]
        if new_tab == self._current_tab:
            return
        self._current_tab = new_tab
        text = self._common_source if new_tab == COMMON_TAB else self._pass_sources[new_tab]
        self.editor.set_value(text)
        self.editor.clear_error_marker()
        self._update_editor_language(new_tab, text)
        if new_tab == COMMON_TAB:
            self.ichannel_panel.setEnabled(False)
        else:
            self.ichannel_panel.setEnabled(True)
            self.ichannel_panel.set_active_pass(new_tab)
            # Rend le mode immédiatement visible au changement d'onglet,
            # sans attendre une frappe/recompile — l'indicateur reflète
            # déjà le dernier texte compilé de cet onglet.
            self._update_dialect_indicator(new_tab, text)
        self._refresh_sliders_for(text)

    def _refresh_sliders_for(self, source: str) -> None:
        if self.sliders_panel.is_drag_active():
            # A recompile landing mid-drag (the debounce timer can still
            # fire between ticks of a real, human-paced drag) must never
            # resync tracked literal offsets against the editor's current
            # text — see `SlidersPanel.is_drag_active` for why that
            # corrupts the value being dragged. `dragFinished` (connected
            # below) catches this back up the moment the drag ends.
            return
        sliders = engine_bridge.detect_all_sliders(source)
        if self.sliders_panel.signature_of(sliders) != self.sliders_panel.current_signature():
            # The panel currently displays `_slider_panel_tab`'s sliders —
            # not necessarily `_current_tab` yet, e.g. right after a tab
            # switch the pass tab bar has already flipped but this panel
            # hasn't been rebuilt for the new tab. Snapshot whatever
            # overrides are live there before `rebuild()` discards the
            # widgets, then restore this (new) tab's own saved overrides,
            # if any, onto the freshly built rows.
            if self._slider_panel_tab is not None:
                exported = self.sliders_panel.export_layout()
                if exported:
                    self._slider_layouts[str(self._slider_panel_tab)] = exported
            self.sliders_panel.rebuild(source, sliders)
            self._slider_panel_tab = self._current_tab
            self.sliders_panel.apply_layout(self._slider_layouts.get(str(self._current_tab), []))
        else:
            self.sliders_panel.refresh(sliders)

    # ---- shader lifecycle --------------------------------------------------

    def _load_default_shader(self) -> None:
        image_source = DEFAULT_SHADER_PATH.read_text(encoding="utf-8")
        self._pass_sources[engine_bridge.PASS_IMAGE] = image_source
        for buf in engine_bridge.BUFFER_PASSES:
            self._pass_sources[buf] = _BUFFER_STUB

        def _on_ready() -> None:
            self.editor.set_value(image_source)
            QTimer.singleShot(50, self._recompile_current_tab)

        self.editor.editorReady.connect(_on_ready)

    def _on_text_changed(self, text: str) -> None:
        self._is_dirty = True
        if self._current_tab == COMMON_TAB:
            self._common_source = text
        else:
            self._pass_sources[self._current_tab] = text
        delay = SLIDER_COMPILE_DEBOUNCE_MS if self._pending_edit_from_slider else self._compile_debounce_ms
        self._pending_edit_from_slider = False

        now = time.monotonic()
        if self._compile_burst_started_at is None:
            self._compile_burst_started_at = now
        elif (now - self._compile_burst_started_at) * 1000.0 >= MAX_COMPILE_DEBOUNCE_MS:
            # The debounce has been continuously postponed for longer than
            # the cap (a burst of edits with no gap ever reaching `delay`)
            # — force the recompile now instead of restarting the timer
            # again, or a playing keyframed slider would starve it forever.
            self._compile_timer.stop()
            self._compile_burst_started_at = None
            self._recompile_current_tab()
            return
        self._compile_timer.start(delay)

    def _recompile_current_tab(self) -> None:
        self._compile_burst_started_at = None
        self._engine.set_common(self._common_source)
        if self._current_tab == COMMON_TAB:
            # Common changed: repropagate to every pass that has real
            # content, so buffers/Image pick up the new shared code too.
            for pass_idx, src in self._pass_sources.items():
                if src:
                    self._compile_one_pass(pass_idx, src, show_marker=False)
            self.footer.set_compile_ok()
            self._refresh_sliders_for(self._common_source)
            # Le Common lui-même n'est pas un dialecte (pas de
            # mainImage/main() attendu) : l'indicateur garde le mode du
            # dernier onglet de pass affiché plutôt que d'être recalculé
            # sur du texte qui ne le concerne pas. La coloration syntaxique
            # de l'éditeur, elle, est une préoccupation séparée (RM10.md
            # section 2, item 1) : Common peut légitimement contenir du
            # WGSL (fonctions utilitaires sans point d'entrée) et mérite
            # sa propre coloration correcte.
            self._update_editor_language(COMMON_TAB, self._common_source)
            return
        source = self._pass_sources[self._current_tab]
        self._refresh_sliders_for(source)
        self._update_dialect_indicator(self._current_tab, source)
        self._update_editor_language(self._current_tab, source)
        self._compile_one_pass(self._current_tab, source, show_marker=True)

    def _update_editor_language(self, pass_idx: int, source: str) -> None:
        """RM10.md section 2, item 1: switches Monaco's tokenizer
        (`glsl`/`wgsl`, `index.html`) to match the *actually detected*
        dialect of the text currently shown, so WGSL keywords stop being
        highlighted as plain identifiers under the GLSL tokenizer. Kept
        deliberately separate from `_update_dialect_indicator` (the footer
        indicator, which only ever reflects a real pass's dialect, never
        Common's -- see that function's own comment): Common's own text
        can itself be written in WGSL (helper `fn`s with no entry point,
        see `dialect::DialectSignal::WgslUniformOrGeneric`) and still
        deserves correct highlighting, without pretending Common has a
        "detected dialect" in the footer's sense.
        """
        if not source:
            self.editor.set_language("glsl")
            return
        previous = self._pass_dialects.get(str(pass_idx), "")
        dialect_id, _ = engine_bridge.detect_dialect(source, previous)
        self.editor.set_language("wgsl" if dialect_id == engine_bridge.DIALECT_WGSL else "glsl")

    def _update_dialect_indicator(self, pass_idx: int, source: str) -> None:
        """Redétecte le dialecte (Shadertoy `mainImage` vs GLSL standalone
        `main`) de la source d'un pass et met à jour le footer — réévalué
        au même déclencheur que la compilation live (recompile debouncée,
        ou changement d'onglet), jamais à chaque frappe individuelle, pour
        ne pas faire clignoter l'indicateur en cours de frappe.
        `previous_dialect` (le mode déjà affiché pour ce pass) est passé
        au détecteur Rust pour qu'un texte sans aucun signal garde ce
        mode plutôt que de retomber sur une valeur par défaut arbitraire.
        """
        if not source:
            self.footer.clear_dialect()
            return
        previous = self._pass_dialects.get(str(pass_idx), "")
        dialect_id, signal_key = engine_bridge.detect_dialect(source, previous)
        self._pass_dialects[str(pass_idx)] = dialect_id
        self.footer.set_dialect(dialect_id, signal_key)

    def _compile_one_pass(self, pass_idx: int, source: str, show_marker: bool) -> None:
        if not source:
            # RM10.md section 4: an emptied pass tab (a Buffer nobody's
            # using anymore, typically) must not just stop being
            # *recompiled* — the last successfully-compiled pipeline was
            # otherwise left running forever, still rendered (and costing
            # GPU time) every single frame. `clear_pass` actually tears
            # that pipeline down so `submit_frame` goes back to skipping
            # this pass entirely, like one that was never compiled.
            try:
                self._engine.clear_pass(pass_idx)
            except RuntimeError:
                pass  # pass_idx is always one of this engine's own indices here; never expected
            return
        try:
            self._engine.compile_pass(pass_idx, source)
        except RuntimeError as exc:
            label = engine_bridge.PASS_LABELS[pass_idx]
            self.footer.set_compile_error(f"[{label}] {exc}")
            if show_marker:
                self._show_error_marker(str(exc), source, pass_idx)
            return
        if show_marker:
            self.editor.clear_error_marker()
        self.footer.set_compile_ok()

    def _on_literal_edited(self, start: int, end: int, text: str) -> None:
        if self._slider_panel_tab != self._current_tab:
            # The panel can still be showing widgets built from a *previous*
            # tab's source (see `_refresh_sliders_for`'s docstring on
            # `_slider_panel_tab` lagging `_current_tab`) — this happens
            # when a tab switch fires while a slider drag is active, since
            # `_refresh_sliders_for` bails out early via `is_drag_active()`
            # and never rebuilds the panel for the new tab. `start`/`end`
            # here are offsets into that stale tab's text, not the text now
            # loaded in the editor for `_current_tab` — applying them would
            # silently corrupt an unrelated range of the wrong pass. Drop
            # the edit; the pending drag will resync (or get discarded)
            # through `_on_slider_drag_finished` once it actually ends.
            return
        self._pending_edit_from_slider = True
        self.editor.replace_range(start, end, text)

    def _on_slider_drag_finished(self) -> None:
        # The drag's last tick may still be in flight (its replace_range
        # hasn't round-tripped back into `_pass_sources` yet) the instant
        # the mouse comes up, so `_refresh_sliders_for` — now unblocked
        # since `is_drag_active()` just went false — would still risk
        # reading a one-edit-stale snapshot if run synchronously here.
        # This delay only needs to clear that one last round-trip, not
        # the whole SLIDER_COMPILE_DEBOUNCE_MS window.
        QTimer.singleShot(150, self._recompile_current_tab)

    def _show_error_marker(self, message: str, source: str, pass_idx: int) -> None:
        line = 1
        match = _LINE_COL_RE.search(message)
        if match:
            wrapped_line = int(match.group(1))
            # Le nombre de lignes de harness qui précèdent le code de
            # l'utilisateur dépend du dialecte compilé pour ce pass (le
            # mode GLSL standalone n'injecte le bloc Globals/iChannel* que
            # s'ils sont réellement référencés, voir
            # `shader::build_fragment_source_standalone`) — on réutilise
            # donc le dernier dialecte détecté pour ce pass plutôt que de
            # supposer Shadertoy comme avant ce chantier.
            dialect_id = self._pass_dialects.get(str(pass_idx), engine_bridge.DIALECT_SHADERTOY)
            try:
                offset = engine_bridge.fragment_header_line_count_for_dialect(
                    self._common_source, source, dialect_id
                )
            except RuntimeError:
                offset = 0
            line = max(1, wrapped_line - offset)
        self.editor.set_error_marker(line, message)

    # ---- toolbar / menu callbacks ------------------------------------------

    def _on_play_toggled(self, paused: bool) -> None:
        self.viewport.set_paused(paused)
        self._play_action.setText(tr("toolbar.play") if paused else tr("toolbar.pause"))

    @staticmethod
    def _add_transform_row(
        group_layout: QVBoxLayout,
        checkbox: QCheckBox,
        description: str,
        *,
        tooltip: str | None = None,
    ) -> None:
        """Adds one "transform row" (a checkbox with a smaller, indented,
        muted description line below it explaining exactly what that
        transform does) to `group_layout`. Shared by every row in
        `_prompt_golf_options`'s dialog, whether the checkbox ends up
        interactive (the three optional transforms) or checked-and-disabled
        (the always-on transforms, listed for transparency rather than
        choice)."""
        if tooltip:
            checkbox.setToolTip(tooltip)
        group_layout.addWidget(checkbox)

        desc_label = QLabel(description)
        desc_label.setWordWrap(True)
        desc_label.setContentsMargins(22, 0, 0, 6)
        desc_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        group_layout.addWidget(desc_label)

    def _prompt_golf_options(self) -> tuple[bool, bool, bool] | None:
        """Dialog listing every golf transform the engine applies, each with
        its own description: the three "aggressive" ones a user can opt out
        of independently (identifier renaming, dead-code elimination,
        algebraic simplification), grouped under real checkboxes, plus the
        always-on ones (comment/whitespace/literal/semicolon cleanup,
        syntactic simplifications, macro extraction) grouped underneath as
        checked-and-disabled rows -- shown for transparency, since they can
        never actually be turned off engine-side (see `golf.rs`'s own
        pipeline doc comment). Choices persist via QSettings. Returns None
        if the user cancelled."""
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("dialogs.golf_options.title"))
        dialog.setMinimumWidth(460)
        dialog.setMaximumWidth(460)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        intro_label = QLabel(tr("dialogs.golf_options.intro"))
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        # ---- Optional transforms (real, persisted checkboxes) -----------
        optional_group = QGroupBox(tr("dialogs.golf_options.section_optional_title"))
        optional_layout = QVBoxLayout(optional_group)
        optional_layout.setSpacing(2)

        optional_desc = QLabel(tr("dialogs.golf_options.section_optional_desc"))
        optional_desc.setWordWrap(True)
        optional_desc.setStyleSheet("color: palette(mid); font-size: 11px;")
        optional_layout.addWidget(optional_desc)

        rename_box = QCheckBox(tr("dialogs.golf_options.rename"))
        rename_box.setChecked(self._settings.value("golfRenameIdentifiers", True, type=bool))
        self._add_transform_row(optional_layout, rename_box, tr("dialogs.golf_options.rename_desc"))

        dce_box = QCheckBox(tr("dialogs.golf_options.dead_code"))
        dce_box.setChecked(self._settings.value("golfRemoveDeadCode", True, type=bool))
        self._add_transform_row(optional_layout, dce_box, tr("dialogs.golf_options.dead_code_desc"))

        algebra_box = QCheckBox(tr("dialogs.golf_options.algebra"))
        algebra_box.setChecked(self._settings.value("golfSimplifyAlgebra", True, type=bool))
        self._add_transform_row(
            optional_layout,
            algebra_box,
            tr("dialogs.golf_options.algebra_desc"),
            tooltip=tr("dialogs.golf_options.algebra_tooltip"),
        )
        layout.addWidget(optional_group)

        # ---- Always-on transforms (informational, checked & disabled) ---
        always_group = QGroupBox(tr("dialogs.golf_options.section_always_title"))
        always_layout = QVBoxLayout(always_group)
        always_layout.setSpacing(2)

        always_desc = QLabel(tr("dialogs.golf_options.section_always_desc"))
        always_desc.setWordWrap(True)
        always_desc.setStyleSheet("color: palette(mid); font-size: 11px;")
        always_layout.addWidget(always_desc)

        for title_key, desc_key in (
            ("always_cleanup_title", "always_cleanup_desc"),
            ("always_structure_title", "always_structure_desc"),
            ("always_macros_title", "always_macros_desc"),
        ):
            always_box = QCheckBox(tr(f"dialogs.golf_options.{title_key}"))
            always_box.setChecked(True)
            always_box.setEnabled(False)
            self._add_transform_row(always_layout, always_box, tr(f"dialogs.golf_options.{desc_key}"))
        layout.addWidget(always_group)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return None

        rename, dead_code, algebra = rename_box.isChecked(), dce_box.isChecked(), algebra_box.isChecked()
        self._settings.setValue("golfRenameIdentifiers", rename)
        self._settings.setValue("golfRemoveDeadCode", dead_code)
        self._settings.setValue("golfSimplifyAlgebra", algebra)
        return rename, dead_code, algebra

    def _on_golf(self) -> None:
        if self._current_tab == COMMON_TAB:
            answer = QMessageBox.question(
                self,
                tr("dialogs.golf_common_confirm.title"),
                tr("dialogs.golf_common_confirm.body"),
            )
            if answer != QMessageBox.Yes:
                return
            self._golf_options = None
        else:
            options = self._prompt_golf_options()
            if options is None:
                return
            self._golf_options = options
        self.editor.get_value(self._do_golf)

    def _do_golf(self, source: str) -> None:
        if not source:
            return
        # If this pass references a name Common declares, golfing it in
        # isolation could rename that reference differently than whatever
        # Common's own (never-renaming) golf would keep it as — protect
        # Common's names here too. See `_do_golf_all` for the full story.
        if self._current_tab == COMMON_TAB:
            golfed = engine_bridge.golf_common(source)
        else:
            rename, dead_code, algebra = self._golf_options
            golfed = engine_bridge.golf_shader_ex(source, self._common_source, rename, dead_code, algebra)

        # Golf-à-froid: verify the golfed code still compiles before ever
        # showing it in the editor. A golfing bug should never silently
        # hand the user broken code. Not applicable to the Common tab,
        # which has no `mainImage` of its own to compile standalone.
        if self._current_tab != COMMON_TAB:
            self._engine.set_common(self._common_source)
            try:
                self._engine.compile_pass(self._current_tab, golfed)
            except RuntimeError as exc:
                QMessageBox.warning(
                    self,
                    tr("dialogs.golf_cancelled.title"),
                    tr("dialogs.golf_cancelled.body", error=exc),
                )
                try:
                    self._engine.compile_pass(self._current_tab, source)  # restore
                except RuntimeError:
                    pass
                return
            self.editor.clear_error_marker()
            self.footer.set_compile_ok()

        self.footer.set_golf_sizes(source, golfed)
        self._pre_golf_source = source
        self.editor.replace_value(golfed)

    def _on_undo_golf(self) -> None:
        if self._pre_golf_source is None:
            QMessageBox.information(self, tr("dialogs.undo_golf.title"), tr("dialogs.undo_golf.body"))
            return
        self.editor.replace_value(self._pre_golf_source)
        self._pre_golf_source = None

    def _on_degolf(self) -> None:
        self.editor.get_value(self._do_degolf)

    def _do_degolf(self, source: str) -> None:
        """Dé-golf (`engine_bridge.beautify_shader`): reformats the
        current tab's source -- reindented, one statement per line, spaced
        operators -- without renaming anything or otherwise changing what
        it compiles to (see `beautify::beautify_shader`'s doc comment on
        the Rust side for the exact guarantee). Unlike `_on_undo_golf`,
        which only replays whatever this session's own last golf pass
        produced, this works on any source -- including a shader pasted in
        already golfed, that this session never golfed itself.

        Same "never hand back code that doesn't compile" guarantee as golf
        (`_do_golf`) -- not applicable to Common, which has no `mainImage`
        of its own to compile standalone.
        """
        if not source:
            return
        beautified = engine_bridge.beautify_shader(source)
        if self._current_tab != COMMON_TAB:
            self._engine.set_common(self._common_source)
            try:
                self._engine.compile_pass(self._current_tab, beautified)
            except RuntimeError as exc:
                QMessageBox.warning(
                    self,
                    tr("dialogs.degolf_cancelled.title"),
                    tr("dialogs.degolf_cancelled.body", error=exc),
                )
                try:
                    self._engine.compile_pass(self._current_tab, source)  # restore
                except RuntimeError:
                    pass
                return
            self.editor.clear_error_marker()
            self.footer.set_compile_ok()

        self.editor.replace_value(beautified)

    def _on_golf_all(self) -> None:
        options = self._prompt_golf_options()
        if options is None:
            return
        self._golf_options = options
        self.editor.get_value(self._do_golf_all)

    def _do_golf_all(self, current_text: str) -> None:
        rename, dead_code, algebra = self._golf_options
        # Make sure the pass currently on screen is up to date before golfing.
        if self._current_tab == COMMON_TAB:
            self._common_source = current_text
        else:
            self._pass_sources[self._current_tab] = current_text

        # Common is textually prepended to every pass before compilation, so
        # golfing it and each pass as fully independent units could rename
        # a name Common declares (e.g. a helper function) differently in
        # each place, breaking any pass that calls it. `golf_common` never
        # renames anything, and `golf_shader_with_common` protects every
        # name declared in the *original* Common text from renaming inside
        # each pass, so both sides keep agreeing on the same spelling.
        original_common = self._common_source
        golfed_common = engine_bridge.golf_common(original_common) if original_common else ""
        self._engine.set_common(golfed_common)

        golfed_sources: dict[int, str] = {}
        total_before = len(original_common.encode("utf-8"))
        total_after = len(golfed_common.encode("utf-8"))
        # RM10.md section 7: "Golfer tout le projet" must summarize each
        # pass's own before/after, not just a single project-wide total —
        # collected alongside the totals below rather than recomputed
        # afterwards from `golfed_sources` (which loses the Common row).
        breakdown_rows: list[tuple[str, int, int]] = []
        if original_common:
            breakdown_rows.append((tr("tabs.common"), total_before, total_after))

        def _rollback() -> None:
            self._engine.set_common(original_common)
            for done_idx in golfed_sources:
                try:
                    self._engine.compile_pass(done_idx, self._pass_sources[done_idx])
                except RuntimeError:
                    pass

        for pass_idx, src in self._pass_sources.items():
            if not src:
                continue
            golfed = engine_bridge.golf_shader_ex(src, original_common, rename, dead_code, algebra)
            try:
                self._engine.compile_pass(pass_idx, golfed)
            except RuntimeError as exc:
                label = engine_bridge.PASS_LABELS[pass_idx]
                QMessageBox.warning(
                    self,
                    tr("dialogs.golf_cancelled.title"),
                    tr("dialogs.golf_cancelled.body_all_or_nothing", label=label, error=exc),
                )
                _rollback()
                return
            golfed_sources[pass_idx] = golfed
            pass_before, pass_after = len(src.encode("utf-8")), len(golfed.encode("utf-8"))
            breakdown_rows.append((engine_bridge.PASS_LABELS[pass_idx], pass_before, pass_after))
            total_before += pass_before
            total_after += pass_after

        self._common_source = golfed_common
        self._pass_sources.update(golfed_sources)
        if self._current_tab == COMMON_TAB:
            self.editor.replace_value(self._common_source)
        elif self._current_tab in golfed_sources:
            self.editor.replace_value(self._pass_sources[self._current_tab])
        self.editor.clear_error_marker()
        self.footer.set_compile_ok()
        pct = 100.0 * (total_before - total_after) / total_before if total_before else 0.0

        def _row_pct(before: int, after: int) -> float:
            return 100.0 * (before - after) / before if before else 0.0

        breakdown = "\n".join(
            tr(
                "dialogs.golf_all_result.per_pass_line",
                label=label, before=before, after=after, percent=f"{_row_pct(before, after):.0f}",
            )
            for label, before, after in breakdown_rows
        )
        QMessageBox.information(
            self,
            tr("dialogs.golf_all_result.title"),
            tr(
                "dialogs.golf_all_result.body",
                pass_count=len(golfed_sources), before=total_before, after=total_after,
                percent=f"{pct:.0f}", breakdown=breakdown,
            ),
        )

    # ---- unsaved-changes guard + recent files ------------------------------

    @property
    def _is_dirty(self) -> bool:
        return self.__is_dirty

    @_is_dirty.setter
    def _is_dirty(self, value: bool) -> None:
        self.__is_dirty = value
        self._update_window_title()

    def _update_window_title(self) -> None:
        """RM10.md section 1, item 5: the title always shows, without
        opening any menu, whether the current project has unsaved changes
        -- Qt's own `[*]` convention (`setWindowModified`) drives the
        asterisk, so the literal substring `[*]` must appear in the
        translated title string in every language (see `lngs/*.json`,
        `app.window_title`/`window_title_file`) for the marker to render at
        all; its exact placement is free to vary per language.
        """
        if self._current_project_path:
            self.setWindowTitle(tr(
                "app.window_title_file",
                filename=Path(self._current_project_path).name,
                version=APP_VERSION,
            ))
        else:
            self.setWindowTitle(tr("app.window_title", version=APP_VERSION))
        self.setWindowModified(self.__is_dirty)

    def _confirm_discard_if_dirty(self) -> bool:
        """Returns True if it's OK to proceed: no unsaved changes, the
        changes were just saved (via the "Save" choice below), or the user
        explicitly confirmed discarding them. RM10.md section 1, item 4:
        three distinct choices (Save / Don't save / Cancel) rather than the
        previous Yes/Cancel, which only ever offered to discard.
        """
        if not self._is_dirty:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("dialogs.unsaved_changes.title"))
        box.setText(tr("dialogs.unsaved_changes.body"))
        save_btn = box.addButton(tr("dialogs.unsaved_changes.save_button"), QMessageBox.AcceptRole)
        discard_btn = box.addButton(tr("dialogs.unsaved_changes.discard_button"), QMessageBox.DestructiveRole)
        cancel_btn = box.addButton(tr("dialogs.unsaved_changes.cancel_button"), QMessageBox.RejectRole)
        box.setDefaultButton(save_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is discard_btn:
            return True
        if clicked is save_btn:
            return self._quick_save_current_project()
        return False  # cancel_btn, or the dialog was dismissed some other way

    def _build_project_dict(self) -> dict:
        """The project-file payload (same `.json` shape `_on_save_project`
        writes), built synchronously from `_pass_sources`/`_common_source`
        -- already mirrored live from the editor by `_on_text_changed` on
        every keystroke, so no async `editor.get_value()` round-trip is
        needed here. Shared by `_quick_save_current_project` (unsaved-changes
        "Save" choice) and `_write_autosave` (background autosave).
        """
        if self._slider_panel_tab is not None:
            self._slider_layouts[str(self._slider_panel_tab)] = self.sliders_panel.export_layout()
        return {
            "format": PROJECT_FORMAT_VERSION,
            "common": self._common_source,
            "passes": {str(k): v for k, v in self._pass_sources.items()},
            "ichannels": self.ichannel_panel.project_data(),
            "sliders": self._slider_layouts,
        }

    def _quick_save_current_project(self) -> bool:
        """Synchronous save backing the "Save" choice of the unsaved-changes
        guard: reuses `_current_project_path` if it's an actual `.json`
        project (never a bare `.frag`/`.glsl` opened via `_open_path` --
        overwriting that with project JSON would silently corrupt it),
        otherwise falls back to the same Save Project As dialog as the
        explicit menu action. Returns False (never saved) if the user
        cancels the file dialog or the write fails.
        """
        path = self._current_project_path
        if not path or not path.lower().endswith(".json"):
            path, _ = QFileDialog.getSaveFileName(
                self, tr("dialogs.save_project.title"),
                str(workspace_dirs.dir_for("projects") / "project.json"),
                tr("dialogs.save_project.filter"),
            )
            if not path:
                return False
        project = self._build_project_dict()
        try:
            Path(path).write_text(json.dumps(project, indent=2), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, tr("dialogs.save_project.title"), str(exc))
            return False
        self._current_project_path = path
        self._is_dirty = False
        self._add_recent_file(path)
        return True

    # ---- autosave + crash recovery (RM10.md section 1, items 2/3) ---------

    def _autosave_file_path(self) -> Path:
        base = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
        base.mkdir(parents=True, exist_ok=True)
        return base / "autosave.json"

    def _write_autosave(self) -> None:
        """Timer-driven (`_autosave_timer`, see `_apply_autosave_settings`).
        Purely synchronous and reuses already-live in-memory state
        (`_build_project_dict`) -- no editor round-trip, no blocking network
        or dialog -- so it never interrupts typing or the live preview, per
        RM10.md's requirement. A skipped/failed write is never surfaced to
        the user: this is a best-effort safety net, not an explicit save.
        """
        if not self._is_dirty:
            return
        project = self._build_project_dict()
        project["__autosave_source_path"] = self._current_project_path
        project["__autosave_timestamp"] = time.time()
        try:
            self._autosave_file_path().write_text(json.dumps(project), encoding="utf-8")
        except OSError:
            pass

    def _clear_autosave(self) -> None:
        """Called after any *clean* shutdown (closeEvent) or once a
        recovered/discarded autosave has been dealt with at startup -- an
        autosave file surviving into the next launch is exactly what means
        "the previous run ended abnormally" (RM10.md section 1, item 3), so
        it must never linger past a normal close."""
        try:
            self._autosave_file_path().unlink(missing_ok=True)
        except OSError:
            pass

    def _apply_autosave_settings(self) -> None:
        enabled = self._settings.value("autosaveEnabled", True, type=bool)
        interval_min = self._settings.value("autosaveIntervalMin", 2, type=int)
        self._autosave_timer.stop()
        if enabled:
            self._autosave_timer.start(max(1, interval_min) * 60_000)

    def _try_crash_recovery(self) -> bool:
        """Called once at startup, before the default shader is loaded.
        Returns True if a recovered project was actually loaded (in which
        case the caller must skip loading the default shader) -- see
        `__init__`. A present `autosave.json` means the previous run never
        reached `closeEvent`'s clean-shutdown `_clear_autosave()` call, i.e.
        an abnormal exit (crash, forced close, power loss).
        """
        autosave_path = self._autosave_file_path()
        if not autosave_path.exists():
            return False
        try:
            data = json.loads(autosave_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._clear_autosave()
            return False

        timestamp = data.get("__autosave_timestamp")
        when = datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M") if timestamp else "?"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("dialogs.crash_recovery.title"))
        box.setText(tr("dialogs.crash_recovery.body", timestamp=when))
        restore_btn = box.addButton(tr("dialogs.crash_recovery.restore_button"), QMessageBox.AcceptRole)
        discard_btn = box.addButton(tr("dialogs.crash_recovery.discard_button"), QMessageBox.DestructiveRole)
        box.setDefaultButton(restore_btn)
        box.exec()
        if box.clickedButton() is not restore_btn:
            self._clear_autosave()
            return False

        try:
            self._apply_project_dict(data)
        except Exception as exc:  # noqa: BLE001 - a malformed autosave must never block startup
            QMessageBox.warning(
                self, tr("dialogs.crash_recovery.title"),
                tr("dialogs.open_error.body", path=str(autosave_path), error=exc),
            )
            self._clear_autosave()
            return False

        self._current_project_path = data.get("__autosave_source_path")
        self._is_dirty = True
        self._clear_autosave()
        return True

    def _show_export_success(self, title: str, body: str, file_path: str) -> None:
        """RM10.md section 8/9: every save/export confirmation names the
        exact file produced *and* offers a button to jump straight to its
        containing folder — pairs naturally with `workspace_dirs`, which
        already gives each save/export dialog its own organized starting
        folder. A plain `QMessageBox` so the extra button reads as a
        secondary action next to the default Ok, not a second, equally
        weighted choice.
        """
        box = QMessageBox(QMessageBox.Information, title, body, QMessageBox.Ok, self)
        open_folder_button = box.addButton(tr("dialogs.common.open_folder_button"), QMessageBox.ActionRole)
        box.exec()
        if box.clickedButton() is open_folder_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(file_path).resolve().parent)))

    def _add_recent_file(self, path: str) -> None:
        self._recent_files = [path] + [p for p in self._recent_files if p != path]
        self._recent_files = self._recent_files[:MAX_RECENT_FILES]
        self._settings.setValue("recentFiles", self._recent_files)
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        if not self._recent_files:
            empty_action = QAction(tr("menu.file.recent_files_empty"), self)
            empty_action.setEnabled(False)
            self._recent_menu.addAction(empty_action)
            return
        for path in self._recent_files:
            action = QAction(path, self)
            action.triggered.connect(lambda checked=False, p=path: self._open_path(p))
            self._recent_menu.addAction(action)

    def _open_path(self, path: str) -> None:
        """Single entry point for loading a file (browsed, recent, or
        project): guards unsaved changes, dispatches on extension, resets
        dirty state and records the file as recently used."""
        if not self._confirm_discard_if_dirty():
            return
        try:
            if path.lower().endswith(".json"):
                self._load_project(path)
            else:
                # Plain .frag/.glsl: treat as the Image pass, clear the rest.
                self._pass_sources = {p: "" for p in engine_bridge.ALL_PASSES}
                self._pass_sources[engine_bridge.PASS_IMAGE] = Path(path).read_text(encoding="utf-8")
                self._common_source = ""
                self._slider_layouts = {}
                self._slider_panel_tab = None
                self._goto_tab(engine_bridge.PASS_IMAGE)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr("dialogs.open_error.title"), tr("dialogs.open_error.body", path=path, error=exc))
            return
        self._current_project_path = path
        self._is_dirty = False
        self._add_recent_file(path)

    def _goto_tab(self, tab_id: int) -> None:
        # setCurrentIndex() is a no-op (no signal) if already on that tab,
        # so apply the resulting state directly rather than relying on
        # _on_pass_tab_changed to fire.
        self.pass_tab_bar.setCurrentIndex(_TAB_ORDER.index(tab_id))
        self._current_tab = tab_id
        text = self._common_source if tab_id == COMMON_TAB else self._pass_sources[tab_id]
        self.editor.set_value(text)
        self.editor.clear_error_marker()
        self._update_editor_language(tab_id, text)
        if tab_id == COMMON_TAB:
            self.ichannel_panel.setEnabled(False)
        else:
            self.ichannel_panel.setEnabled(True)
            self.ichannel_panel.set_active_pass(tab_id)
        self._refresh_sliders_for(text)

    def _load_project(self, path: str) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        # RM10.md section 9: a project written by a newer version of this
        # software (a `"format"` this build doesn't recognize) must say so
        # up front -- `_apply_project_dict` below reads each field with
        # `.get(...)` and sensible defaults, so it will not crash on an
        # unrecognized future shape, but it also can't know what a higher
        # format number might have changed, so any fields it doesn't
        # understand are silently ignored rather than migrated. Loading
        # still proceeds best-effort after the warning (refusing outright
        # would be worse for a minor, mostly-compatible bump) — this is a
        # heads-up, not a hard block.
        file_format = data.get("format")
        if isinstance(file_format, int) and file_format > PROJECT_FORMAT_VERSION:
            QMessageBox.warning(
                self, tr("dialogs.open_project.title"),
                tr(
                    "dialogs.open_project.future_format_warning",
                    file_format=file_format, current_format=PROJECT_FORMAT_VERSION,
                ),
            )
        self._apply_project_dict(data)

    def _apply_project_dict(self, data: dict) -> None:
        """Loads a project dict shaped like our `.json` format
        (`{"common", "passes", "ichannels", "sliders"}`) into the live
        UI/engine state. Shared by `_load_project` (reading from disk) and
        `_on_import_shadertoy` (translated from the shadertoy.com API by
        `shadertoy_import.build_project_data`) — one code path pushes
        sources, iChannel assignments, and slider overrides into the
        engine and editor regardless of where the dict came from.
        """
        # Every video/webcam source belonging to the project being
        # replaced is stopped up front, unconditionally: a slot that had
        # one but isn't mentioned at all in the incoming `ichannels` dict
        # (rather than explicitly reassigned to something else) would
        # otherwise never go through `_apply_ichannel_assignment` and so
        # never get its source stopped — leaving a webcam open, or a video
        # file still decoding, in the background of an unrelated project.
        self._stop_all_video_sources()
        self._stop_all_audio_sources()
        self._common_source = data.get("common", "")
        sources = data.get("passes", {})
        for pass_idx in engine_bridge.ALL_PASSES:
            self._pass_sources[pass_idx] = sources.get(str(pass_idx), "")
        self.ichannel_panel.load_project_data(data.get("ichannels", {}))
        for pass_idx, channel_idx, kind, value, _volume, _muted in self.ichannel_panel.all_assignments():
            # `_apply_ichannel_assignment` -> `_start_audio_channel` reads
            # the already-loaded volume/mute back out of `ichannel_panel`
            # itself (`audio_settings_for`), so nothing further is needed
            # here for the "audio" case specifically.
            self._apply_ichannel_assignment(pass_idx, channel_idx, kind, value)
        raw_sliders = data.get("sliders", {})
        self._slider_layouts = {
            str(k): v for k, v in raw_sliders.items() if isinstance(v, list)
        } if isinstance(raw_sliders, dict) else {}
        # Whatever `sliders_panel` currently shows belongs to the project
        # being replaced — don't let it get captured under this new
        # project's tab keys (see `_refresh_sliders_for`).
        self._slider_panel_tab = None
        self._goto_tab(engine_bridge.PASS_IMAGE)

    def _on_new(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        self._stop_all_video_sources()
        self._stop_all_audio_sources()
        self._pass_sources = {p: "" for p in engine_bridge.ALL_PASSES}
        self._pass_sources[engine_bridge.PASS_IMAGE] = _BUFFER_STUB
        self._common_source = ""
        self._slider_layouts = {}
        self._slider_panel_tab = None
        self._goto_tab(engine_bridge.PASS_IMAGE)
        self._current_project_path = None
        self._is_dirty = False

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("dialogs.open_shader.title"),
            str(workspace_dirs.dir_for("shaders")), tr("dialogs.open_shader.filter"),
        )
        if not path:
            return
        self._open_path(path)

    def _on_open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("dialogs.open_project.title"),
            str(workspace_dirs.dir_for("projects")), tr("dialogs.open_project.filter"),
        )
        if not path:
            return
        self._open_path(path)

    def _on_import_shadertoy(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        text, ok = QInputDialog.getText(
            self, tr("dialogs.import_shadertoy.title"),
            tr("dialogs.import_shadertoy.prompt"),
        )
        if not ok or not text.strip():
            return
        shader_id = shadertoy_import.parse_shader_id_or_url(text)
        if shader_id is None:
            QMessageBox.warning(
                self, tr("dialogs.import_shadertoy.title"),
                tr("dialogs.import_shadertoy.invalid_id"),
            )
            return

        api_key = self._settings.value("shadertoyApiKey", "", type=str)
        if not api_key:
            api_key, ok = QInputDialog.getText(
                self, tr("dialogs.shadertoy_api_key.title"),
                tr("dialogs.shadertoy_api_key.prompt"),
            )
            if not ok or not api_key.strip():
                return
            api_key = api_key.strip()
            self._settings.setValue("shadertoyApiKey", api_key)

        try:
            shader = shadertoy_import.fetch_shader(shader_id, api_key)
        except shadertoy_import.ShadertoyImportError as exc:
            QMessageBox.warning(self, tr("dialogs.import_shadertoy.title"), str(exc))
            return

        cache_dir = Path(
            QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
        ) / "shadertoy_media"
        data, warnings = shadertoy_import.build_project_data(shader, cache_dir)
        self._apply_project_dict(data)
        # Unlike `_open_path` (backed by a file already "saved" as-is on
        # disk), an import has nowhere on disk it corresponds to yet —
        # treat it like unsaved work so closing/opening something else
        # prompts to save it first, same as any manual edit would.
        self._current_project_path = None
        self._is_dirty = True
        if warnings:
            QMessageBox.information(
                self, tr("dialogs.import_shadertoy_partial.title"),
                tr("dialogs.import_shadertoy_partial.body", warnings="\n- ".join(warnings)),
            )

    def _on_save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.save_as.title"),
            str(workspace_dirs.dir_for("shaders") / "shader.frag"),
            tr("dialogs.save_as.filter"),
        )
        if not path:
            return

        def _save(text: str) -> None:
            Path(path).write_text(text, encoding="utf-8")
            self._current_project_path = path
            self._is_dirty = False
            self._add_recent_file(path)

        self.editor.get_value(_save)

    def _on_save_project(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.save_project.title"),
            str(workspace_dirs.dir_for("projects") / "project.json"),
            tr("dialogs.save_project.filter"),
        )
        if not path:
            return

        def _save(text: str) -> None:
            if self._current_tab == COMMON_TAB:
                self._common_source = text
            else:
                self._pass_sources[self._current_tab] = text
            # `_build_project_dict` re-snapshots `_slider_layouts` itself;
            # the explicit `text` round-trip above is what this menu action
            # adds over `_quick_save_current_project` -- belt-and-braces
            # freshness in case the very last keystroke hasn't reached
            # `_pass_sources`/`_common_source` via `_on_text_changed` yet.
            project = self._build_project_dict()
            Path(path).write_text(json.dumps(project, indent=2), encoding="utf-8")
            self._current_project_path = path
            self._is_dirty = False
            self._add_recent_file(path)
            self._show_export_success(
                tr("dialogs.save_project.title"), tr("dialogs.save_project.export_success", path=path), path,
            )

        self.editor.get_value(_save)

    def closeEvent(self, event) -> None:
        if self._confirm_discard_if_dirty():
            self._autosave_timer.stop()
            self._stop_all_video_sources()
            self._stop_all_audio_sources()
            self._save_layout()
            self._clear_autosave()
            event.accept()
        else:
            event.ignore()

    def _on_export_golfed(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.export_golfed.title"),
            str(workspace_dirs.dir_for("exports") / "shader.min.frag"),
            tr("dialogs.export_golfed.filter"),
        )
        if not path:
            return

        def _export(source: str) -> None:
            rename = self._settings.value("golfRenameIdentifiers", True, type=bool)
            dead_code = self._settings.value("golfRemoveDeadCode", True, type=bool)
            algebra = self._settings.value("golfSimplifyAlgebra", True, type=bool)
            has_common = bool(self._common_source)
            if has_common:
                golfed_common = engine_bridge.golf_common(self._common_source)
                golfed = engine_bridge.golf_shader_ex(source, self._common_source, rename, dead_code, algebra)
                exported = f"{golfed_common}\n{golfed}"
            else:
                golfed = engine_bridge.golf_shader_ex(source, "", rename, dead_code, algebra)
                exported = golfed
            # Same golf-à-froid guarantee as the in-editor golf action: never
            # write out code that doesn't actually compile. Uses a throwaway
            # engine so the live viewport pipeline is left untouched. The
            # exported file inlines Common (if any) so it stays a single,
            # self-contained, paste-anywhere .frag — Common isn't a real
            # pass and has nothing of its own to verify separately.
            try:
                throwaway = engine_bridge.Engine(64, 64)
                throwaway.compile_pass(engine_bridge.PASS_IMAGE, exported)
            except RuntimeError as exc:
                QMessageBox.warning(
                    self,
                    tr("dialogs.golf_export_cancelled.title"),
                    tr("dialogs.golf_export_cancelled.body", error=exc),
                )
                return
            Path(path).write_text(exported, encoding="utf-8")
            self._show_export_success(
                tr("dialogs.export_golfed.title"), tr("dialogs.export_golfed.export_success", path=path), path,
            )

        self.editor.get_value(_export)

    def _on_export_compiled_shader(self, target: str) -> None:
        """`target` is `"hlsl"` or `"msl"`. One-off export of the pass
        currently shown in the editor, translated via `naga`'s HLSL/MSL
        backends (`Engine.export_shader_as`, see RMLG.md section 2) --
        never a new editable tab or a `ShaderDialect`: HLSL/MSL have no
        `naga` frontend, so nothing this produces can be pasted back into
        this editor to keep editing it here. That limitation is shown to
        the user up front, not just documented in this file.
        """
        if self._current_tab == COMMON_TAB:
            QMessageBox.information(
                self,
                tr("dialogs.export_shader.title"),
                tr("dialogs.export_shader.common_tab_body"),
            )
            return

        not_reeditable_key = (
            "dialogs.export_shader.not_reeditable_hlsl"
            if target == "hlsl"
            else "dialogs.export_shader.not_reeditable_msl"
        )
        bindings_caveat_key = (
            "dialogs.export_shader.bindings_caveat_hlsl"
            if target == "hlsl"
            else "dialogs.export_shader.bindings_caveat_msl"
        )
        # Three distinct caveats, always shown together: the round-trip
        # warning (this file can't be pasted back into the editor), the
        # binding-convention warning (translated iChannel/uniform bindings
        # use naga's generic register/index conventions, not necessarily
        # those of the target engine), and the pixel-fidelity warning
        # (naga's translation targets functional correctness, never a
        # contractually guaranteed bit-exact match against this software's
        # own live rendering -- RMLG.md section 2.3, "à vérifier au cas par
        # cas plutôt que supposé"). None of the three implies another, so
        # none is dropped even when the others already apply.
        QMessageBox.information(
            self,
            tr("dialogs.export_shader.title"),
            f"{tr(not_reeditable_key)}\n\n{tr(bindings_caveat_key)}\n\n"
            f"{tr('dialogs.export_shader.pixel_fidelity_caveat')}",
        )

        if target == "hlsl":
            default_name, filt = "shader.hlsl", tr("dialogs.export_shader.filter_hlsl")
        else:
            default_name, filt = "shader.metal", tr("dialogs.export_shader.filter_msl")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.export_shader.title"),
            str(workspace_dirs.dir_for("exports") / default_name), filt,
        )
        if not path:
            return

        # `Engine.export_shader_as` reuses the last successfully compiled
        # source/dialect for this pass (see `renderer::Engine::export_shader_as`)
        # -- no need to fetch the editor's live text first, and no risk of
        # exporting a stale/partial pass since that field is only populated
        # after `compile_pass` actually succeeds.
        try:
            exported = self._engine.export_shader_as(self._current_tab, target)
        except RuntimeError as exc:
            QMessageBox.warning(
                self,
                tr("dialogs.export_shader.failed_title"),
                tr("dialogs.export_shader.failed_body", error=exc),
            )
            return

        Path(path).write_text(exported, encoding="utf-8")
        self._show_export_success(
            tr("dialogs.export_shader.title"), tr("dialogs.export_shader.export_success", path=path), path,
        )

    def _on_export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.export_png.title"),
            str(workspace_dirs.dir_for("images") / "shader.png"), "PNG (*.png)",
        )
        if not path:
            return
        if not self.viewport.export_png(path):
            QMessageBox.warning(self, tr("dialogs.export_png.title"), tr("dialogs.export_png.nothing_to_export"))
            return
        self._show_export_success(
            tr("dialogs.export_png.title"), tr("dialogs.export_png.export_success", path=path), path,
        )

    def _on_export_video(self) -> None:
        """Collects export settings (`ExportVideoDialog`), then runs the
        full capture+encode pipeline (`video_export.run_export`) behind a
        cancelable progress dialog (`ExportProgressDialog`) covering both
        phases separately -- "Rendu : N/total frames" during capture, then
        "Encodage vidéo…" with ffmpeg's own `-progress pipe:1` frame count
        during encoding. On a successful export, the file-size estimate
        table is recalibrated towards the actual output size
        (`video_export.record_actual_export_size`) so this project's next
        export estimate is a little more accurate.
        """
        path, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.export_video.save_dialog_title"),
            str(workspace_dirs.dir_for("videos") / tr("dialogs.export_video.save_dialog_default_name")),
            tr("dialogs.export_video.save_dialog_filter"),
        )
        if not path:
            return
        if not path.lower().endswith(".mp4"):
            path += ".mp4"

        current_width, current_height = self.viewport.render_size()
        dialog = ExportVideoDialog(current_width, current_height, self._settings, self)
        if dialog.exec() != QDialog.Accepted:
            return
        export = dialog.settings()

        # RM10.md section 1, item 8: `export_video_dialog.py` lets the
        # resolution spinboxes go up to 7680 (8K), but the real ceiling is
        # whatever this machine's GPU/driver actually supports
        # (`Engine.max_texture_dimension`, queried from the adapter at
        # startup -- see `renderer::Engine::new`). Checked here, before
        # `resize()` is ever called with it, rather than letting an
        # oversized request reach `wgpu` and panic on a validation error.
        max_dim = self._engine.max_texture_dimension()
        if export.width > max_dim or export.height > max_dim:
            QMessageBox.warning(
                self, tr("dialogs.export_video.title"),
                tr(
                    "dialogs.export_video.resolution_too_large",
                    width=export.width, height=export.height, max=max_dim,
                ),
            )
            return

        try:
            ffmpeg_path = video_export.resolve_ffmpeg_path()
        except Exception:
            ffmpeg_path = None
        if ffmpeg_path is None or not Path(ffmpeg_path).is_file():
            QMessageBox.warning(
                self, tr("dialogs.export_video.title"),
                tr("dialogs.export_video.ffmpeg_missing", path=ffmpeg_path),
            )
            return

        # Same precaution as a live splitter/window resize
        # (`Viewport._apply_resize`), just driven explicitly instead of by
        # a debounce timer: the live ~60fps tick loop must not land a
        # frame — or a queued debounced resize — in between resizing the
        # shared `Engine` to the export resolution and resizing it back,
        # or it would read pixels sized for the wrong resolution.
        self.viewport.suspend_for_external_render()
        try:
            # RM10.md section 1, item 8: `resize` can still fail here even
            # though `export.width/height` already passed the
            # `max_texture_dimension()` check above -- a resolution within
            # that per-axis limit can still exceed available VRAM (verified
            # empirically, see `renderer::Engine::resize`'s doc comment).
            # `Engine.resize` raises a plain `RuntimeError` in that case
            # rather than crashing, and leaves the engine at its previous,
            # still-working resolution.
            try:
                self._engine.resize(export.width, export.height)
            except RuntimeError as exc:
                QMessageBox.warning(self, tr("dialogs.export_video.title"), tr("dialogs.export_video.resize_failed", error=exc))
                return
            try:
                progress = ExportProgressDialog(
                    self._engine,
                    export.frames,
                    export.fps,
                    export.width,
                    export.height,
                    export.crf,
                    self.viewport.current_date(),
                    path,
                    audio_path=export.audio_path,
                    audio_volume_db=export.audio_volume_db,
                    audio_start_offset=export.audio_start_offset,
                    audio_loop=export.audio_loop,
                    audio_bitrate_kbps=export.audio_bitrate_kbps,
                    parent=self,
                )
                result = progress.run()
            finally:
                try:
                    self._engine.resize(current_width, current_height)
                except RuntimeError as exc:
                    QMessageBox.warning(
                        self, tr("dialogs.export_video.title"),
                        tr("dialogs.export_video.restore_resolution_failed", error=exc),
                    )
        finally:
            self.viewport.resume_after_external_render()

        if result == "cancelled":
            return
        if result == "error":
            QMessageBox.warning(
                self, tr("dialogs.export_video.title"),
                tr("dialogs.export_video.export_failed", error=progress.error_message()),
            )
            return

        try:
            actual_bytes = Path(path).stat().st_size
            record_actual_export_size(self._settings, export, actual_bytes)
        except OSError:
            pass  # calibration is a nice-to-have, never worth failing the export over

        self._show_export_success(
            tr("dialogs.export_video.title"),
            tr(
                "dialogs.export_video.export_success",
                frames=export.frames, width=export.width, height=export.height,
                fps=f"{export.fps:g}", path=path,
            ),
            path,
        )

    def _apply_editor_preferences(self) -> None:
        self.editor.set_font_size(self._settings.value("editorFontSize", 13, type=int))
        self.editor.set_minimap_enabled(self._settings.value("editorMinimap", False, type=bool))

    def _on_preferences(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("dialogs.preferences.title"))
        form = QFormLayout(dialog)

        font_box = QSpinBox()
        font_box.setRange(8, 32)
        font_box.setValue(self._settings.value("editorFontSize", 13, type=int))

        minimap_box = QCheckBox()
        minimap_box.setChecked(self._settings.value("editorMinimap", False, type=bool))

        debounce_box = QSpinBox()
        debounce_box.setRange(50, 2000)
        debounce_box.setSingleStep(50)
        debounce_box.setSuffix(" ms")
        debounce_box.setValue(self._compile_debounce_ms)

        shadertoy_key_box = QLineEdit(self._settings.value("shadertoyApiKey", "", type=str))
        shadertoy_key_box.setPlaceholderText(tr("dialogs.preferences.shadertoy_api_key_placeholder"))

        # RM10.md section 1, item 2 -- disableable, adjustable interval.
        autosave_box = QCheckBox()
        autosave_box.setChecked(self._settings.value("autosaveEnabled", True, type=bool))
        autosave_interval_box = QSpinBox()
        autosave_interval_box.setRange(1, 30)
        autosave_interval_box.setSuffix(" min")
        autosave_interval_box.setValue(self._settings.value("autosaveIntervalMin", 2, type=int))
        autosave_interval_box.setEnabled(autosave_box.isChecked())
        autosave_box.toggled.connect(autosave_interval_box.setEnabled)

        # Populated from `lngs/*.json` on disk (i18n.available_languages()),
        # never a hardcoded list -- dropping in a new language file is
        # enough to make it appear here, no code change needed. Sorted by
        # display name so the order doesn't depend on filesystem ordering.
        language_box = QComboBox()
        current_language_code = i18n.active_language_code()
        for code, name in sorted(i18n.available_languages().items(), key=lambda item: item[1].lower()):
            language_box.addItem(name, code)
        index = language_box.findData(current_language_code)
        if index != -1:
            language_box.setCurrentIndex(index)

        form.addRow(tr("dialogs.preferences.editor_font_size"), font_box)
        form.addRow(tr("dialogs.preferences.minimap"), minimap_box)
        form.addRow(tr("dialogs.preferences.compile_debounce"), debounce_box)
        form.addRow(tr("dialogs.preferences.shadertoy_api_key"), shadertoy_key_box)
        form.addRow(tr("dialogs.preferences.autosave_enabled"), autosave_box)
        form.addRow(tr("dialogs.preferences.autosave_interval_minutes"), autosave_interval_box)
        form.addRow(tr("dialogs.preferences.language"), language_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        self._settings.setValue("editorFontSize", font_box.value())
        self._settings.setValue("editorMinimap", minimap_box.isChecked())
        self._compile_debounce_ms = debounce_box.value()
        self._settings.setValue("compileDebounceMs", self._compile_debounce_ms)
        self._settings.setValue("shadertoyApiKey", shadertoy_key_box.text().strip())
        self._settings.setValue("autosaveEnabled", autosave_box.isChecked())
        self._settings.setValue("autosaveIntervalMin", autosave_interval_box.value())
        self._apply_autosave_settings()

        new_language_code = language_box.currentData()
        if new_language_code and new_language_code != current_language_code:
            # Same key `main.py::_startup_language_code()` reads at startup.
            # Nothing is re-translated live here -- every widget already
            # built keeps its current text (see roadmap: no hot-retranslation
            # of the whole UI) -- so the new language only takes effect after
            # a relaunch, which the notice below makes explicit rather than
            # leaving the user to wonder why nothing changed.
            self._settings.setValue("languageCode", new_language_code)
            QMessageBox.information(
                self, tr("dialogs.preferences.title"),
                tr("dialogs.preferences.language_restart_notice"),
            )

        self._apply_editor_preferences()

    def _on_edit_shortcuts(self) -> None:
        """Opens the rebinding dialog (`ShortcutsDialog`) and lets it write
        straight back into `self._shortcuts` on Ok -- see that dialog and
        `shortcuts.ShortcutRegistry.apply_many` for how a change here ends
        up live on the exact `QAction` instances the menu/toolbar are
        already showing, no menu rebuild required.
        """
        dialog = ShortcutsDialog(self._shortcuts, self)
        dialog.exec()

    def _on_about(self) -> None:
        QMessageBox.information(
            self, tr("dialogs.about.title"),
            tr("dialogs.about.body", version=APP_VERSION),
        )

    def _on_render_error(self, message: str) -> None:
        self.footer.set_compile_error(message)

    def _on_viewport_resize_error(self, message: str) -> None:
        """RM10.md section 1, item 8: a live window/splitter resize the
        GPU couldn't keep up with (typically insufficient VRAM at the new
        size) -- a transient status-bar message rather than a blocking
        dialog, since nothing the user explicitly asked for failed
        outright: the preview keeps rendering at its previous, still-valid
        resolution, just not yet at the size the window happens to be now."""
        self.footer.showMessage(tr("dialogs.viewport_resize_error", error=message), 8000)

    def _on_render_scale_changed(self, scale: float) -> None:
        """RM10.md section 4: the footer's render-scale combo box lets the
        preview be rendered below the viewport's own on-screen size (e.g.
        50%) to stay fluid on a heavy shader, independently of the window/
        splitter size. Persisted across sessions -- see the restore next to
        where `self.footer`/`self.viewport` are wired up in
        `_build_central_widget`."""
        self.viewport.set_render_scale(scale)
        self._settings.setValue("renderScale", scale)

    def _apply_ichannel_assignment(self, pass_idx: int, channel_idx: int, kind: str, value) -> None:
        # Any video/webcam/audio source previously bound to this exact slot
        # is no longer wanted the instant its assignment changes to
        # anything else — including a *different* video file, webcam, or
        # audio file — so it's always stopped up front, before the new
        # assignment (if any) opens its own. Otherwise a stale
        # `QMediaPlayer`/`QCamera` would keep decoding (and, for audio,
        # playing out loud) in the background, and for a webcam would keep
        # the physical device (and its capture-active indicator light)
        # locked even though nothing samples its frames anymore.
        self._stop_video_channel(pass_idx, channel_idx)
        self._stop_audio_channel(pass_idx, channel_idx)
        try:
            if kind == "image":
                self._engine.set_ichannel_texture(pass_idx, channel_idx, value)
            elif kind == "video":
                self._start_video_channel(pass_idx, channel_idx, value)
            elif kind == "audio":
                self._start_audio_channel(pass_idx, channel_idx, value)
            elif kind == "webcam":
                self._start_webcam_channel(pass_idx, channel_idx, value)
            elif kind == "cubemap":
                self._engine.set_ichannel_cubemap(pass_idx, channel_idx, value)
            elif kind == "procedural":
                scale, seed = self.ichannel_panel.procedural_settings_for(pass_idx, channel_idx)
                self._engine.set_ichannel_procedural(pass_idx, channel_idx, value, scale, seed)
            elif kind == "buffer":
                self._engine.set_ichannel_buffer(pass_idx, channel_idx, value)
            elif kind == "keyboard":
                self._engine.set_ichannel_keyboard(pass_idx, channel_idx)
            else:
                self._engine.clear_ichannel(pass_idx, channel_idx)
        except RuntimeError as exc:
            QMessageBox.warning(self, tr("dialogs.ichannel_error.title"), str(exc))

    def _start_video_channel(self, pass_idx: int, channel_idx: int, path: str) -> None:
        """Allocates the engine-side placeholder for a video-file iChannel
        slot, then opens `path` with Qt and starts streaming decoded
        frames into it. Errors from either half (an invalid pass/index on
        the engine side, an unreadable/unsupported file on Qt's side) are
        reported the same way every other iChannel assignment error is —
        a warning dialog, the slot just stays without a live source."""
        try:
            self._engine.set_ichannel_video(pass_idx, channel_idx)
        except RuntimeError as exc:
            QMessageBox.warning(self, tr("dialogs.ichannel_error.title"), str(exc))
            return
        source = VideoChannelSource(self._video_frame_callback(pass_idx, channel_idx), self)
        source.sourceLost.connect(lambda msg, p=pass_idx, c=channel_idx: self._on_source_lost(p, c, msg))
        self._video_sources[(pass_idx, channel_idx)] = source
        try:
            source.start_file(path)
        except Exception as exc:  # noqa: BLE001 - Qt's own playback errors vary in type
            QMessageBox.warning(self, tr("dialogs.video_error.title"), tr("dialogs.video_error.body", path=path, error=exc))

    def _start_webcam_channel(self, pass_idx: int, channel_idx: int, device_id: str) -> None:
        """Same as `_start_video_channel`, but opens a webcam instead of a
        file. `device_id` may be empty (system default camera) — see
        `video_source.VideoChannelSource.start_webcam`."""
        try:
            self._engine.set_ichannel_video(pass_idx, channel_idx)
        except RuntimeError as exc:
            QMessageBox.warning(self, tr("dialogs.ichannel_error.title"), str(exc))
            return
        source = VideoChannelSource(self._video_frame_callback(pass_idx, channel_idx), self)
        source.sourceLost.connect(lambda msg, p=pass_idx, c=channel_idx: self._on_source_lost(p, c, msg))
        self._video_sources[(pass_idx, channel_idx)] = source
        try:
            source.start_webcam(device_id or "")
        except RuntimeError as exc:
            QMessageBox.warning(self, tr("dialogs.webcam_error.title"), str(exc))

    def _video_frame_callback(self, pass_idx: int, channel_idx: int):
        """Builds the per-slot `on_frame` callback a `VideoChannelSource`
        calls for every decoded frame. Bound to a fixed (pass, channel)
        pair at construction time rather than reading `self._video_sources`
        back at call time, so a frame decoded just before a reassignment
        can never land on the *new* source's slot by mistake."""
        def _on_frame(width: int, height: int, rgba: bytes, time_s: float) -> None:
            try:
                self._engine.update_ichannel_video_frame(
                    pass_idx, channel_idx, width, height, rgba, time_s
                )
            except RuntimeError:
                # The engine already treats a frame for a slot that's since
                # been reassigned away from Video as a harmless no-op (see
                # `renderer::Engine::update_ichannel_video_frame`); any
                # `RuntimeError` here is something else (e.g. a genuinely
                # malformed frame), not worth interrupting playback with a
                # dialog for every single tick.
                pass
            self._maybe_refresh_live_thumbnail(pass_idx, channel_idx, width, height, rgba)
        return _on_frame

    def _maybe_refresh_live_thumbnail(
        self, pass_idx: int, channel_idx: int, width: int, height: int, rgba: bytes
    ) -> None:
        """RM10.md section 5: refreshes a video/webcam slot's thumbnail
        with an actual decoded frame (throttled, see
        `_THUMBNAIL_MIN_INTERVAL_S`/`_thumb_last_update`) instead of the
        fixed 🎬/📷 icon it showed before this existed. `IChannelPanel`
        itself already discards the update if the slot has since been
        reassigned away from video/webcam, or isn't the currently
        displayed pass — this only decides *when* to bother building the
        `QPixmap` in the first place."""
        if width <= 0 or height <= 0:
            return
        now = time.monotonic()
        key = (pass_idx, channel_idx)
        if now - self._thumb_last_update.get(key, 0.0) < _THUMBNAIL_MIN_INTERVAL_S:
            return
        self._thumb_last_update[key] = now
        image = QImage(rgba, width, height, width * 4, QImage.Format_RGBA8888)
        self.ichannel_panel.update_live_thumbnail(pass_idx, channel_idx, QPixmap.fromImage(image))

    def _stop_video_channel(self, pass_idx: int, channel_idx: int) -> None:
        source = self._video_sources.pop((pass_idx, channel_idx), None)
        if source is not None:
            source.stop()
            source.deleteLater()

    def _stop_all_video_sources(self) -> None:
        """Releases every open video file / webcam, e.g. right before the
        window actually closes — a webcam left running would otherwise
        keep the physical device locked after the app is gone."""
        for pass_idx, channel_idx in list(self._video_sources.keys()):
            self._stop_video_channel(pass_idx, channel_idx)

    def _start_audio_channel(self, pass_idx: int, channel_idx: int, path: str) -> None:
        """Allocates the engine-side placeholder (silence) for an audio
        iChannel slot, then opens `path` with Qt and starts playing it back
        — audibly, unlike the video channel, see `AudioChannelSource.start`.
        Errors reported the same way every other iChannel assignment error
        is (a warning dialog, the slot just stays without a live source)."""
        try:
            self._engine.set_ichannel_audio(pass_idx, channel_idx)
        except RuntimeError as exc:
            QMessageBox.warning(self, tr("dialogs.ichannel_error.title"), str(exc))
            return
        source = AudioChannelSource(self)
        source.sourceLost.connect(lambda msg, p=pass_idx, c=channel_idx: self._on_source_lost(p, c, msg))
        # Applied before `start()` (which itself re-applies these to every
        # freshly created `QAudioOutput`, see `AudioChannelSource.start`)
        # so swapping which file this slot points to never silently resets
        # an already-adjusted volume/mute back to 100%/unmuted.
        volume, muted = self.ichannel_panel.audio_settings_for(pass_idx, channel_idx)
        source.set_volume(volume)
        source.set_muted(muted)
        self._audio_sources[(pass_idx, channel_idx)] = source
        try:
            source.start(path)
        except Exception as exc:  # noqa: BLE001 - Qt's own playback errors vary in type
            QMessageBox.warning(self, tr("dialogs.audio_error.title"), tr("dialogs.audio_error.body", path=path, error=exc))

    def _on_source_lost(self, pass_idx: int, channel_idx: int, message: str) -> None:
        """RM10.md section 1, item 6: a webcam/video/audio source that was
        already streaming failed or disconnected mid-use (device unplugged,
        drive holding the file disconnected, driver error). Stopped
        cleanly -- releasing the device/file handle exactly like an
        explicit reassignment would -- and surfaced as a non-blocking
        message in the textures panel, never a crash, a silent stall, or an
        interrupting dialog for something the user didn't just trigger."""
        if (pass_idx, channel_idx) in self._video_sources:
            self._stop_video_channel(pass_idx, channel_idx)
        elif (pass_idx, channel_idx) in self._audio_sources:
            self._stop_audio_channel(pass_idx, channel_idx)
        self.ichannel_panel.show_slot_disconnected(pass_idx, channel_idx, message)

    def _on_audio_tick(self, _time_s: float) -> None:
        """Connected to `viewport.timeUpdated` (~60fps, same cadence every
        other dynamic iChannel source ticks on): FFTs/downsamples whatever
        each active `AudioChannelSource` has decoded since the last tick
        and pushes the resulting spectrum+waveform into the engine. A
        no-op for a slot with no active audio source, same as every other
        idle iChannel kind."""
        for (pass_idx, channel_idx), source in self._audio_sources.items():
            spectrum, waveform = source.compute_frame()
            try:
                self._engine.update_ichannel_audio_frame(
                    pass_idx, channel_idx, spectrum, waveform, source.position_seconds()
                )
            except RuntimeError:
                # Same reasoning as `_video_frame_callback`: the engine
                # already no-ops a frame for a slot reassigned away from
                # Audio, not worth a dialog interrupting every tick.
                pass
            self._maybe_refresh_audio_thumbnail(pass_idx, channel_idx, waveform)

    def _maybe_refresh_audio_thumbnail(self, pass_idx: int, channel_idx: int, waveform: bytes) -> None:
        """RM10.md section 5: same idea as `_maybe_refresh_live_thumbnail`
        for video/webcam, but for audio -- a small live waveform sparkline
        instead of the fixed 🎵 icon, drawn from the same 512-byte waveform
        row already computed every tick for the engine's own iChannel
        audio texture (see `AudioChannelSource.compute_frame`), not a
        second, separate analysis."""
        now = time.monotonic()
        key = (pass_idx, channel_idx)
        if now - self._thumb_last_update.get(key, 0.0) < _THUMBNAIL_MIN_INTERVAL_S:
            return
        self._thumb_last_update[key] = now
        pixmap = QPixmap(THUMB_SIZE, THUMB_SIZE)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setPen(QPen(Qt.green, 1))
        n = len(waveform)
        if n >= 2:
            step = THUMB_SIZE / (n - 1)
            prev_x, prev_y = 0.0, THUMB_SIZE / 2.0
            for i, byte in enumerate(waveform):
                x = i * step
                y = THUMB_SIZE - (byte / 255.0) * THUMB_SIZE
                if i > 0:
                    painter.drawLine(int(prev_x), int(prev_y), int(x), int(y))
                prev_x, prev_y = x, y
        painter.end()
        self.ichannel_panel.update_live_thumbnail(pass_idx, channel_idx, pixmap)

    def _stop_audio_channel(self, pass_idx: int, channel_idx: int) -> None:
        source = self._audio_sources.pop((pass_idx, channel_idx), None)
        if source is not None:
            source.stop()
            source.deleteLater()

    def _stop_all_audio_sources(self) -> None:
        """Releases every open audio file, e.g. right before the window
        actually closes or a new/different project is loaded — an audio
        file left playing would otherwise keep making sound in the
        background of an unrelated project."""
        for pass_idx, channel_idx in list(self._audio_sources.keys()):
            self._stop_audio_channel(pass_idx, channel_idx)

    def _on_ichannel_assignment_changed(self, pass_idx: int, channel_idx: int, kind: str, value) -> None:
        self._apply_ichannel_assignment(pass_idx, channel_idx, kind, value)

    def _on_ichannel_audio_settings_changed(
        self, pass_idx: int, channel_idx: int, volume: float, muted: bool
    ) -> None:
        """RM10.md section 5: volume/mute for an audio iChannel slot,
        adjusted live from the textures panel, independent of the system
        output volume. A no-op if this slot has no active
        `AudioChannelSource` right now (e.g. the slider was touched right
        before a file was actually picked) -- the value is still recorded
        in `IChannelPanel` and applied the moment a source does start, see
        `_start_audio_channel`."""
        source = self._audio_sources.get((pass_idx, channel_idx))
        if source is not None:
            source.set_volume(volume)
            source.set_muted(muted)

    def _on_ichannel_procedural_settings_changed(
        self, pass_idx: int, channel_idx: int, scale: int, seed: int
    ) -> None:
        """RM10.md section 5: pattern size / seed for a procedural texture
        (checker/white noise/value noise), adjusted live from the textures
        panel. Unlike audio volume there's no persistent "source" object to
        update in place -- a procedural texture is just regenerated and
        re-uploaded outright, same call as a fresh assignment."""
        kind, value = self.ichannel_panel.state_for(pass_idx, channel_idx)
        if kind != "procedural":
            return
        try:
            self._engine.set_ichannel_procedural(pass_idx, channel_idx, value, scale, seed)
        except RuntimeError as exc:
            QMessageBox.warning(self, tr("dialogs.ichannel_error.title"), str(exc))
