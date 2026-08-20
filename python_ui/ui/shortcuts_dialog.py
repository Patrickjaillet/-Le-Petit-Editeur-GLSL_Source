"""Keyboard-shortcut rebinding dialog: one row per entry in
`shortcuts.SHORTCUT_SPECS`, a `QKeySequenceEdit` per row to capture a new
binding, live duplicate detection (RM10.md section 10 -- a conflict is
flagged the moment a row finishes capturing a new binding, via an inline
warning label naming the command it collides with, not only when Ok is
pressed), and a per-row "Réinitialiser" plus a global "Tout réinitialiser"
back to `shortcuts.py`'s built-in defaults.

Opened from `MainWindow`'s Edition menu ("Raccourcis clavier…"); changes
are only written to `QSettings` (via `ShortcutRegistry.apply_many`) if the
user confirms with Ok, mirroring the existing Préférences dialog's
Ok/Cancel-commits-nothing-until-accepted pattern.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from shortcuts import SHORTCUT_SPECS, ShortcutRegistry, default_shortcut


class ShortcutsDialog(QDialog):
    def __init__(self, registry: ShortcutRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("dialogs.shortcuts.title"))
        self.setMinimumSize(520, 480)
        self._registry = registry
        self._editors: dict[str, QKeySequenceEdit] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("dialogs.shortcuts.hint")))

        table = QTableWidget(len(SHORTCUT_SPECS), 3, self)
        table.setHorizontalHeaderLabels([
            tr("dialogs.shortcuts.column_command"), tr("dialogs.shortcuts.column_shortcut"), "",
        ])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)

        for row, spec in enumerate(SHORTCUT_SPECS):
            label_item = QTableWidgetItem(tr(spec.label_key))
            label_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            table.setItem(row, 0, label_item)

            editor = QKeySequenceEdit(QKeySequence(registry.current_sequence(spec.action_id)), table)
            editor.editingFinished.connect(self._check_live_conflict)
            table.setCellWidget(row, 1, editor)
            self._editors[spec.action_id] = editor

            reset_button = QPushButton(tr("dialogs.shortcuts.reset_row"), table)
            reset_button.clicked.connect(lambda _checked=False, aid=spec.action_id: self._reset_row(aid))
            table.setCellWidget(row, 2, reset_button)

        table.resizeRowsToContents()
        layout.addWidget(table)
        self._table = table

        # RM10.md section 10: flagged the instant a row finishes capturing
        # a binding (see `editingFinished` connections above/below), not
        # only when Ok is pressed -- text-based (not color-only, see the
        # accessibility item in the same roadmap), hidden while there's
        # nothing to report.
        self._conflict_label = QLabel("")
        self._conflict_label.setStyleSheet("color: #d9534f; font-weight: 600;")
        self._conflict_label.setWordWrap(True)
        self._conflict_label.hide()
        layout.addWidget(self._conflict_label)

        button_row = QHBoxLayout()
        reset_all_button = QPushButton(tr("dialogs.shortcuts.reset_all"))
        reset_all_button.clicked.connect(self._reset_all)
        button_row.addWidget(reset_all_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _reset_row(self, action_id: str) -> None:
        self._editors[action_id].setKeySequence(QKeySequence(default_shortcut(action_id)))
        self._check_live_conflict()

    def _reset_all(self) -> None:
        for spec in SHORTCUT_SPECS:
            self._editors[spec.action_id].setKeySequence(QKeySequence(spec.default))
        self._check_live_conflict()

    def _find_conflict(self) -> tuple[str, str] | None:
        """Returns the (action_id, action_id) pair of the first two
        commands that would end up sharing the same non-empty shortcut if
        Ok were pressed now, or `None` if there's no conflict. Not hooked
        to every keystroke -- a mid-edit `QKeySequenceEdit` is routinely a
        *prefix* of another binding (e.g. typing "Ctrl" while "Ctrl+S"
        already exists elsewhere) and flagging that transient state as a
        conflict would be more annoying than helpful -- but *is* re-run
        every time a row finishes capturing a binding (`editingFinished`,
        see `_check_live_conflict`) as well as on accept, not only then.
        """
        seen: dict[str, str] = {}
        for spec in SHORTCUT_SPECS:
            text = self._editors[spec.action_id].keySequence().toString()
            if not text:
                continue
            if text in seen:
                return (seen[text], spec.action_id)
            seen[text] = spec.action_id

        return None

    def _check_live_conflict(self) -> None:
        """RM10.md section 10: shows/hides the inline warning label the
        instant a row finishes capturing a new binding (wired to every
        `QKeySequenceEdit.editingFinished` above, plus both reset
        actions) -- named after the two colliding commands, exactly like
        the modal accept-time check below, just surfaced immediately
        instead of only once Ok is pressed.
        """
        conflict = self._find_conflict()
        if conflict is None:
            self._conflict_label.hide()
            self._conflict_label.setText("")
            return
        first_id, second_id = conflict
        first = tr(f"actions.{first_id}")
        second = tr(f"actions.{second_id}")
        self._conflict_label.setText(tr("dialogs.shortcuts.duplicate_body", first=first, second=second))
        self._conflict_label.show()

    def _on_accept(self) -> None:
        conflict = self._find_conflict()
        if conflict is not None:
            first_id, second_id = conflict
            first = tr(f"actions.{first_id}")
            second = tr(f"actions.{second_id}")
            QMessageBox.warning(
                self, tr("dialogs.shortcuts.duplicate_title"),
                tr("dialogs.shortcuts.duplicate_body", first=first, second=second),
            )
            return

        updates = {
            spec.action_id: self._editors[spec.action_id].keySequence().toString()
            for spec in SHORTCUT_SPECS
        }
        self._registry.apply_many(updates)
        self.accept()
