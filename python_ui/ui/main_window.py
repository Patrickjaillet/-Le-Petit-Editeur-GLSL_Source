"""Main application window: menubar, toolbar, splitters, footer."""
from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
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
from app_version import APP_VERSION
import i18n
from i18n import tr
from shortcuts import ShortcutRegistry
from ui.export_progress_dialog import ExportProgressDialog
from ui.export_video_dialog import ExportVideoDialog, record_actual_export_size
from ui.footer import Footer
from ui.ichannel_panel import IChannelPanel
from ui.monaco_editor import MonacoEditor
from ui.shortcuts_dialog import ShortcutsDialog
from ui.sliders_panel import SlidersPanel
from ui.viewport import VIEWPORT_HEIGHT, VIEWPORT_WIDTH, Viewport
from audio_source import AudioChannelSource
from video_source import VideoChannelSource

DEFAULT_SHADER_PATH = Path(__file__).resolve().parent.parent / "assets" / "shaders" / "default.frag"
COMPILE_DEBOUNCE_MS = 350
SLIDER_COMPILE_DEBOUNCE_MS = 100

_LINE_COL_RE = re.compile(r":(\d+):(\d+)")
MAX_RECENT_FILES = 8

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
        self._pre_golf_source: str | None = None
        self._golf_options: tuple[bool, bool, bool] | None = (True, True, True)
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
        for a in (save_action, save_project_action, export_action, export_png_action, export_video_action):
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
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)
        golf_all_action = reg("edit.golf_all", QAction(tr("menu.edit.golf_all"), self))
        golf_all_action.triggered.connect(self._on_golf_all)
        edit_menu.addSeparator()
        edit_menu.addAction(golf_action)
        edit_menu.addAction(golf_all_action)
        edit_menu.addAction(undo_golf_action)
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

        self.viewport.fpsUpdated.connect(self.footer.set_fps)
        self.viewport.renderError.connect(self._on_render_error)
        self.viewport.frameRendered.connect(self.footer.add_frame_time_sample)
        self.viewport.timeUpdated.connect(self.sliders_panel.set_time)
        self.viewport.timeUpdated.connect(self._on_audio_tick)
        self.ichannel_panel.assignmentChanged.connect(self._on_ichannel_assignment_changed)
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
        if new_tab == COMMON_TAB:
            self.ichannel_panel.setEnabled(False)
        else:
            self.ichannel_panel.setEnabled(True)
            self.ichannel_panel.set_active_pass(new_tab)
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
        self._compile_timer.start(delay)

    def _recompile_current_tab(self) -> None:
        self._engine.set_common(self._common_source)
        if self._current_tab == COMMON_TAB:
            # Common changed: repropagate to every pass that has real
            # content, so buffers/Image pick up the new shared code too.
            for pass_idx, src in self._pass_sources.items():
                if src:
                    self._compile_one_pass(pass_idx, src, show_marker=False)
            self.footer.set_compile_ok()
            self._refresh_sliders_for(self._common_source)
            return
        source = self._pass_sources[self._current_tab]
        self._refresh_sliders_for(source)
        self._compile_one_pass(self._current_tab, source, show_marker=True)

    def _compile_one_pass(self, pass_idx: int, source: str, show_marker: bool) -> None:
        if not source:
            return
        try:
            self._engine.compile_pass(pass_idx, source)
        except RuntimeError as exc:
            label = engine_bridge.PASS_LABELS[pass_idx]
            self.footer.set_compile_error(f"[{label}] {exc}")
            if show_marker:
                self._show_error_marker(str(exc), source)
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

    def _show_error_marker(self, message: str, source: str) -> None:
        line = 1
        match = _LINE_COL_RE.search(message)
        if match:
            wrapped_line = int(match.group(1))
            try:
                offset = engine_bridge.fragment_header_line_count(self._common_source, source)
            except RuntimeError:
                offset = 0
            line = max(1, wrapped_line - offset)
        self.editor.set_error_marker(line, message)

    # ---- toolbar / menu callbacks ------------------------------------------

    def _on_play_toggled(self, paused: bool) -> None:
        self.viewport.set_paused(paused)
        self._play_action.setText(tr("toolbar.play") if paused else tr("toolbar.pause"))

    def _prompt_golf_options(self) -> tuple[bool, bool, bool] | None:
        """Small dialog for the three "aggressive" golf transforms
        (identifier renaming, dead-code elimination, algebraic
        simplification) — comments/whitespace/numeric-literal/semicolon
        minification always happen regardless, there's no real downside to
        those. Choices persist via QSettings. Returns None if the user
        cancelled."""
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("dialogs.golf_options.title"))
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(tr("dialogs.golf_options.intro")))

        rename_box = QCheckBox(tr("dialogs.golf_options.rename"))
        rename_box.setChecked(self._settings.value("golfRenameIdentifiers", True, type=bool))
        dce_box = QCheckBox(tr("dialogs.golf_options.dead_code"))
        dce_box.setChecked(self._settings.value("golfRemoveDeadCode", True, type=bool))
        algebra_box = QCheckBox(tr("dialogs.golf_options.algebra"))
        algebra_box.setToolTip(tr("dialogs.golf_options.algebra_tooltip"))
        algebra_box.setChecked(self._settings.value("golfSimplifyAlgebra", True, type=bool))
        layout.addWidget(rename_box)
        layout.addWidget(dce_box)
        layout.addWidget(algebra_box)

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
            total_before += len(src.encode("utf-8"))
            total_after += len(golfed.encode("utf-8"))

        self._common_source = golfed_common
        self._pass_sources.update(golfed_sources)
        if self._current_tab == COMMON_TAB:
            self.editor.replace_value(self._common_source)
        elif self._current_tab in golfed_sources:
            self.editor.replace_value(self._pass_sources[self._current_tab])
        self.editor.clear_error_marker()
        self.footer.set_compile_ok()
        pct = 100.0 * (total_before - total_after) / total_before if total_before else 0.0
        QMessageBox.information(
            self,
            tr("dialogs.golf_all_result.title"),
            tr(
                "dialogs.golf_all_result.body",
                pass_count=len(golfed_sources), before=total_before, after=total_after,
                percent=f"{pct:.0f}",
            ),
        )

    # ---- unsaved-changes guard + recent files ------------------------------

    def _confirm_discard_if_dirty(self) -> bool:
        """Returns True if it's OK to proceed (no unsaved changes, or the
        user confirmed discarding them)."""
        if not self._is_dirty:
            return True
        answer = QMessageBox.question(
            self,
            tr("dialogs.unsaved_changes.title"),
            tr("dialogs.unsaved_changes.body"),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return answer == QMessageBox.Yes

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
        if tab_id == COMMON_TAB:
            self.ichannel_panel.setEnabled(False)
        else:
            self.ichannel_panel.setEnabled(True)
            self.ichannel_panel.set_active_pass(tab_id)
        self._refresh_sliders_for(text)

    def _load_project(self, path: str) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
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
        for pass_idx, channel_idx, kind, value in self.ichannel_panel.all_assignments():
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
        self._is_dirty = False

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("dialogs.open_shader.title"), "", tr("dialogs.open_shader.filter")
        )
        if not path:
            return
        self._open_path(path)

    def _on_open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("dialogs.open_project.title"), "", tr("dialogs.open_project.filter")
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
        self._is_dirty = True
        if warnings:
            QMessageBox.information(
                self, tr("dialogs.import_shadertoy_partial.title"),
                tr("dialogs.import_shadertoy_partial.body", warnings="\n- ".join(warnings)),
            )

    def _on_save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.save_as.title"), "shader.frag", tr("dialogs.save_as.filter")
        )
        if not path:
            return

        def _save(text: str) -> None:
            Path(path).write_text(text, encoding="utf-8")
            self._is_dirty = False
            self._add_recent_file(path)

        self.editor.get_value(_save)

    def _on_save_project(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.save_project.title"), "project.json", tr("dialogs.save_project.filter")
        )
        if not path:
            return

        def _save(text: str) -> None:
            if self._current_tab == COMMON_TAB:
                self._common_source = text
            else:
                self._pass_sources[self._current_tab] = text
            # The panel's live widgets (right-click min/max/decimals edits
            # since the last rebuild) haven't necessarily been snapshotted
            # into `_slider_layouts` yet — only structural rebuilds trigger
            # that (see `_refresh_sliders_for`) — so capture the current
            # tab's state explicitly before serializing.
            if self._slider_panel_tab is not None:
                self._slider_layouts[str(self._slider_panel_tab)] = self.sliders_panel.export_layout()
            project = {
                "format": 3,
                "common": self._common_source,
                "passes": {str(k): v for k, v in self._pass_sources.items()},
                "ichannels": self.ichannel_panel.project_data(),
                "sliders": self._slider_layouts,
            }
            Path(path).write_text(json.dumps(project, indent=2), encoding="utf-8")
            self._is_dirty = False
            self._add_recent_file(path)

        self.editor.get_value(_save)

    def closeEvent(self, event) -> None:
        if self._confirm_discard_if_dirty():
            self._stop_all_video_sources()
            self._stop_all_audio_sources()
            self._save_layout()
            event.accept()
        else:
            event.ignore()

    def _on_export_golfed(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.export_golfed.title"), "shader.min.frag", tr("dialogs.export_golfed.filter")
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

        self.editor.get_value(_export)

    def _on_export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("dialogs.export_png.title"), "shader.png", "PNG (*.png)")
        if not path:
            return
        if not self.viewport.export_png(path):
            QMessageBox.warning(self, tr("dialogs.export_png.title"), tr("dialogs.export_png.nothing_to_export"))

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
            tr("dialogs.export_video.save_dialog_default_name"),
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
            self._engine.resize(export.width, export.height)
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
                    self,
                )
                result = progress.run()
            finally:
                self._engine.resize(current_width, current_height)
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

        QMessageBox.information(
            self, tr("dialogs.export_video.title"),
            tr(
                "dialogs.export_video.export_success",
                frames=export.frames, width=export.width, height=export.height,
                fps=f"{export.fps:g}", path=path,
            ),
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
                self._engine.set_ichannel_procedural(pass_idx, channel_idx, value)
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
        return _on_frame

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
        self._audio_sources[(pass_idx, channel_idx)] = source
        try:
            source.start(path)
        except Exception as exc:  # noqa: BLE001 - Qt's own playback errors vary in type
            QMessageBox.warning(self, tr("dialogs.audio_error.title"), tr("dialogs.audio_error.body", path=path, error=exc))

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
