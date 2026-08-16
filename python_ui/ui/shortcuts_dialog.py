"""Keyboard-shortcut rebinding dialog: one row per entry in
`shortcuts.SHORTCUT_SPECS`, a `QKeySequenceEdit` per row to capture a new
binding, live duplicate detection, and a per-row "Réinitialiser" plus a
global "Tout réinitialiser" back to `shortcuts.py`'s built-in defaults.

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
            table.setCellWidget(row, 1, editor)
            self._editors[spec.action_id] = editor

            reset_button = QPushButton(tr("dialogs.shortcuts.reset_row"), table)
            reset_button.clicked.connect(lambda _checked=False, aid=spec.action_id: self._reset_row(aid))
            table.setCellWidget(row, 2, reset_button)

        table.resizeRowsToContents()
        layout.addWidget(table)
        self._table = table

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

    def _reset_all(self) -> None:
        for spec in SHORTCUT_SPECS:
            self._editors[spec.action_id].setKeySequence(QKeySequence(spec.default))

    def _find_conflict(self) -> tuple[str, str] | None:
        """Returns the (action_id, action_id) pair of the first two
        commands that would end up sharing the same non-empty shortcut if
        Ok were pressed now, or `None` if there's no conflict. Checked on
        accept rather than on every keystroke -- a mid-edit
        `QKeySequenceEdit` is routinely a *prefix* of another binding
        (e.g. typing "Ctrl" while "Ctrl+S" already exists elsewhere) and
        flagging that transient state as a conflict would be more
        annoying than helpful.
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
