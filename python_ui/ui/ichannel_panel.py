"""iChannel0-3 texture slots: image file, video file, an audio file, a
webcam, a 6-face cubemap, the keyboard (`iKeyboard`), a built-in procedural
preset, empty, or one of the 4 Buffer render targets (Shadertoy-style
multi-pass). Each of
the 5 passes (Image, Buffer A-D) has its own independent set of 4 slots;
`set_active_pass` switches which set is currently displayed.
"""
from __future__ import annotations

import random

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from audio_source import AUDIO_EXTENSIONS
from i18n import tr
from video_source import VIDEO_EXTENSIONS, list_cameras

THUMB_SIZE = 64
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")
_THUMB_STYLE = "background:#222; border:1px solid #444;"
_THUMB_STYLE_DRAG_OVER = "background:#223; border:2px dashed #6cf;"
_THUMB_STYLE_BUFFER = "background:#243; border:1px solid #4a6; color:#9e9;"
_THUMB_STYLE_CUBEMAP = "background:#233; border:1px solid #46a; color:#9cf;"
_THUMB_STYLE_KEYBOARD = "background:#332; border:1px solid #a94; color:#fc9;"
_THUMB_STYLE_VIDEO = "background:#423; border:1px solid #a4a; color:#fcf;"
_THUMB_STYLE_WEBCAM = "background:#234; border:1px solid #48a; color:#9df;"
_THUMB_STYLE_AUDIO = "background:#342; border:1px solid #6a4; color:#9f9;"

# Cubemap face order matches Shadertoy/WebGPU convention: this is exactly
# the array-layer order a `Cube`-dimension texture view interprets its 6
# layers as (see `ChannelTexture::from_cubemap_files` on the Rust side).
# Axis names, not language text -- kept as-is regardless of UI language.
_CUBE_FACE_LABELS = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]

# Built-in procedural presets: (identifier passed to the engine / stored in
# the project file, i18n key for its display label). Order here is the
# order shown in the combo box.
_PROCEDURAL_PRESETS = [
    ("checker", "ichannel_panel.procedural_checker"),
    ("white_noise", "ichannel_panel.procedural_white_noise"),
    ("value_noise", "ichannel_panel.procedural_value_noise"),
]


def _video_filter() -> str:
    return tr("ichannel_panel.video_filter")


def _audio_filter() -> str:
    return tr("ichannel_panel.audio_filter")


def _image_filter() -> str:
    return tr("ichannel_panel.image_filter")


def _source_labels() -> list[str]:
    """Combo box entries, resolved through `tr()` at call time (from
    `_ChannelSlot.__init__`, well after `main.py` has called
    `i18n.load_language()`) rather than baked into a module-level
    constant at import time -- this module is imported (via
    `ui.main_window`) before that call happens."""
    return (
        [
            tr("ichannel_panel.source_empty"),
            tr("ichannel_panel.source_image"),
            tr("ichannel_panel.source_video"),
            tr("ichannel_panel.source_audio"),
            tr("ichannel_panel.source_webcam"),
            tr("ichannel_panel.source_cubemap"),
            tr("ichannel_panel.source_keyboard"),
        ]
        + [tr("ichannel_panel.procedural_suffix", label=tr(label_key)) for _, label_key in _PROCEDURAL_PRESETS]
        + [tr("tabs.buffer_a"), tr("tabs.buffer_b"), tr("tabs.buffer_c"), tr("tabs.buffer_d")]
    )


_VIDEO_INDEX = 2
# Between "Vidéo (fichier)…" and "Webcam", matching the roadmap plan for
# this feature.
_AUDIO_INDEX = 3
_WEBCAM_INDEX = 4
_CUBEMAP_INDEX = 5
_KEYBOARD_INDEX = 6
_PROCEDURAL_OFFSET = 7
_BUFFER_OFFSET = _PROCEDURAL_OFFSET + len(_PROCEDURAL_PRESETS)


def _combo_index_for(kind: str, value) -> int:
    if kind == "image":
        return 1
    if kind == "video":
        return _VIDEO_INDEX
    if kind == "audio":
        return _AUDIO_INDEX
    if kind == "webcam":
        return _WEBCAM_INDEX
    if kind == "cubemap":
        return _CUBEMAP_INDEX
    if kind == "keyboard":
        return _KEYBOARD_INDEX
    if kind == "procedural":
        for i, (key, _label) in enumerate(_PROCEDURAL_PRESETS):
            if key == value:
                return _PROCEDURAL_OFFSET + i
        return 0
    if kind == "buffer":
        return _BUFFER_OFFSET + int(value)
    return 0


def _kind_value_for(combo_index: int):
    if combo_index == 0:
        return "empty", None
    if combo_index == 1:
        return "image", None
    if combo_index == _VIDEO_INDEX:
        return "video", None
    if combo_index == _AUDIO_INDEX:
        return "audio", None
    if combo_index == _WEBCAM_INDEX:
        return "webcam", None
    if combo_index == _CUBEMAP_INDEX:
        return "cubemap", None
    if combo_index == _KEYBOARD_INDEX:
        return "keyboard", None
    if combo_index < _BUFFER_OFFSET:
        key, _label = _PROCEDURAL_PRESETS[combo_index - _PROCEDURAL_OFFSET]
        return "procedural", key
    return "buffer", combo_index - _BUFFER_OFFSET


class _CubemapDialog(QDialog):
    """Picks the 6 face images for a cubemap iChannel slot, one explicit
    file picker per face (rather than a single multi-select dialog, whose
    result order isn't guaranteed to match face order and would silently
    scramble the cubemap)."""

    def __init__(self, channel_index: int, initial: list | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dialogs.cubemap.title", index=channel_index))
        self._edits: list[QLineEdit] = []

        form = QFormLayout(self)
        values = list(initial) if initial else [""] * 6
        values += [""] * (6 - len(values))
        for label, path in zip(_CUBE_FACE_LABELS, values):
            row = QHBoxLayout()
            edit = QLineEdit(path)
            edit.setReadOnly(True)
            edit.setMinimumWidth(260)
            browse = QPushButton(tr("dialogs.cubemap.browse"))
            browse.clicked.connect(lambda _checked=False, e=edit: self._browse(e))
            row.addWidget(edit)
            row.addWidget(browse)
            form.addRow(tr("dialogs.cubemap.face_row", face=label), row)
            self._edits.append(edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("dialogs.cubemap.choose_face_image"), "", _image_filter(),
        )
        if path:
            edit.setText(path)

    def _on_accept(self) -> None:
        if any(not e.text() for e in self._edits):
            QMessageBox.warning(self, tr("dialogs.cubemap.missing_faces_title"), tr("dialogs.cubemap.missing_faces_body"))
            return
        self.accept()

    def paths(self) -> list[str]:
        return [e.text() for e in self._edits]


def _procedural_preview(key: str) -> QPixmap:
    """A small preview thumbnail for a procedural preset. This is a cheap
    UI-only approximation (not pixel-identical to the engine's actual
    generated texture, which is regenerated in Rust from its own fixed
    seed) — just enough for the user to tell presets apart at a glance."""
    if key == "checker":
        pix = QPixmap(THUMB_SIZE, THUMB_SIZE)
        pix.fill(QColor(35, 35, 35))
        painter = QPainter(pix)
        cells = 8
        cell = THUMB_SIZE // cells
        for cy in range(cells):
            for cx in range(cells):
                if (cx + cy) % 2 == 0:
                    painter.fillRect(cx * cell, cy * cell, cell, cell, QColor(235, 235, 235))
        painter.end()
        return pix
    if key == "white_noise":
        img = QImage(THUMB_SIZE, THUMB_SIZE, QImage.Format_RGB32)
        rng = random.Random(key)
        for y in range(THUMB_SIZE):
            for x in range(THUMB_SIZE):
                v = rng.randint(0, 255)
                img.setPixelColor(x, y, QColor(v, v, v))
        return QPixmap.fromImage(img)
    if key == "value_noise":
        # A coarse random grid scaled up with smooth interpolation reads as
        # soft blobs, close enough to preview the real bilinear/smoothstep
        # value noise the engine generates.
        small = 8
        img = QImage(small, small, QImage.Format_RGB32)
        rng = random.Random(key)
        for y in range(small):
            for x in range(small):
                v = rng.randint(0, 255)
                img.setPixelColor(x, y, QColor(v, v, v))
        return QPixmap.fromImage(img).scaled(
            THUMB_SIZE, THUMB_SIZE, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )
    pix = QPixmap(THUMB_SIZE, THUMB_SIZE)
    pix.fill(QColor(35, 35, 35))
    return pix


class _ChannelSlot(QWidget):
    # index, kind ("empty" | "image" | "video" | "audio" | "webcam" |
    # "cubemap" | "keyboard" | "procedural" | "buffer"), value (None | image
    # path | video file path | audio file path | webcam device id
    # ("" = system default) | list of 6 face paths | None | procedural
    # preset key | buffer_index)
    assignmentChanged = Signal(int, str, object)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self._kind = "empty"
        self._value = None
        self.setAcceptDrops(True)

        box = QGroupBox(f"iChannel{index}")
        layout = QVBoxLayout(box)

        self._thumb = QLabel()
        self._thumb.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setStyleSheet(_THUMB_STYLE)
        self._thumb.setText(tr("ichannel_panel.thumb_empty"))
        layout.addWidget(self._thumb, alignment=Qt.AlignHCenter)

        self._combo = QComboBox()
        self._combo.addItems(_source_labels())
        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        layout.addWidget(self._combo)

        btn_row = QHBoxLayout()
        self._browse_btn = QPushButton(tr("ichannel_panel.browse"))
        self._browse_btn.clicked.connect(self._on_browse)
        btn_row.addWidget(self._browse_btn)
        layout.addLayout(btn_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)

    # ---- external state sync (switching the active pass) ------------------

    def set_state(self, kind: str, value) -> None:
        """Updates the displayed state without emitting `assignmentChanged`
        (used when switching which pass's slots are shown)."""
        self._kind = kind
        self._value = value
        self._combo.blockSignals(True)
        self._combo.setCurrentIndex(_combo_index_for(kind, value))
        self._combo.blockSignals(False)
        if kind == "image" and value:
            pixmap = QPixmap(value).scaled(
                THUMB_SIZE, THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._thumb.setStyleSheet(_THUMB_STYLE)
            self._thumb.setPixmap(pixmap)
            self._thumb.setText("")
        elif kind == "cubemap" and value:
            # Preview the +X face (first in the list) as a stand-in — not
            # meant to look like an actual cube, just enough to confirm
            # something's assigned and glance at one face.
            self._thumb.setStyleSheet(_THUMB_STYLE_CUBEMAP)
            first_face = value[0] if value else ""
            if first_face:
                pixmap = QPixmap(first_face).scaled(
                    THUMB_SIZE, THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self._thumb.setPixmap(pixmap)
                self._thumb.setText("")
            else:
                self._thumb.setPixmap(QPixmap())
                self._thumb.setText(tr("ichannel_panel.thumb_cube"))
        elif kind == "procedural" and value:
            self._thumb.setStyleSheet(_THUMB_STYLE)
            self._thumb.setPixmap(_procedural_preview(value))
            self._thumb.setText("")
        elif kind == "video":
            # No live preview thumbnail here (unlike a static image): the
            # first decoded frame only exists once `VideoChannelSource`
            # actually starts playing, well after this call — an icon plus
            # the filename as a tooltip is what's available up front.
            self._thumb.setStyleSheet(_THUMB_STYLE_VIDEO)
            self._thumb.setPixmap(QPixmap())
            self._thumb.setText("🎬")
            self._thumb.setToolTip(value or "")
        elif kind == "audio":
            # Same treatment as "video": no live-updating mini-spectrum in
            # the thumbnail itself (the real preview is the shader running
            # in the viewport once the channel is actually bound), just an
            # icon plus the filename as a tooltip.
            self._thumb.setStyleSheet(_THUMB_STYLE_AUDIO)
            self._thumb.setPixmap(QPixmap())
            self._thumb.setText("🎵")
            self._thumb.setToolTip(value or "")
        elif kind == "webcam":
            self._thumb.setStyleSheet(_THUMB_STYLE_WEBCAM)
            self._thumb.setPixmap(QPixmap())
            self._thumb.setText("📷")
            label = next((desc for cam_id, desc in list_cameras() if cam_id == value), None)
            self._thumb.setToolTip(label or tr("ichannel_panel.default_webcam_tooltip"))
        elif kind == "buffer":
            self._thumb.setStyleSheet(_THUMB_STYLE_BUFFER)
            self._thumb.setPixmap(QPixmap())
            self._thumb.setText("ABCD"[value])
        elif kind == "keyboard":
            self._thumb.setStyleSheet(_THUMB_STYLE_KEYBOARD)
            self._thumb.setPixmap(QPixmap())
            self._thumb.setText("⌨")
        else:
            self._thumb.setStyleSheet(_THUMB_STYLE)
            self._thumb.setPixmap(QPixmap())
            self._thumb.setText(tr("ichannel_panel.thumb_empty"))

        # The browse button doubles as "pick a different file/camera" for
        # every multi-step source (cubemap faces, a new video file, a
        # different webcam), since re-selecting the same combo entry
        # doesn't fire currentIndexChanged and so wouldn't otherwise offer
        # a way back into that source's picker.
        self._browse_btn.setText({
            "cubemap": tr("ichannel_panel.change_cubemap"),
            "video": tr("ichannel_panel.change_video"),
            "audio": tr("ichannel_panel.change_audio"),
            "webcam": tr("ichannel_panel.change_webcam"),
        }.get(kind, tr("ichannel_panel.browse")))

    def state(self) -> tuple[str, object]:
        return self._kind, self._value

    # ---- user interaction ---------------------------------------------

    @staticmethod
    def _is_image_path(path: str) -> bool:
        return path.lower().endswith(IMAGE_EXTENSIONS)

    def _apply_image(self, path: str) -> None:
        self.set_state("image", path)
        self.assignmentChanged.emit(self.index, "image", path)

    def _apply_video(self, path: str) -> None:
        self.set_state("video", path)
        self.assignmentChanged.emit(self.index, "video", path)

    def _apply_audio(self, path: str) -> None:
        self.set_state("audio", path)
        self.assignmentChanged.emit(self.index, "audio", path)

    def _revert_combo(self) -> None:
        """Snaps the combo box back to whatever this slot is actually
        assigned to, after a picker dialog (image/video/webcam/cubemap) is
        cancelled — the combo has already visually jumped to the new entry
        by the time `currentIndexChanged` fires."""
        self._combo.blockSignals(True)
        self._combo.setCurrentIndex(_combo_index_for(self._kind, self._value))
        self._combo.blockSignals(False)

    def _open_cubemap_dialog(self) -> bool:
        """Opens the 6-face picker, applying the result if accepted.
        Returns whether it was accepted (so callers can revert UI state
        that assumed it would be, e.g. the combo box selection)."""
        initial = self._value if self._kind == "cubemap" else None
        dialog = _CubemapDialog(self.index, initial, self)
        if dialog.exec() != QDialog.Accepted:
            return False
        paths = dialog.paths()
        self.set_state("cubemap", paths)
        self.assignmentChanged.emit(self.index, "cubemap", paths)
        return True

    def _pick_video_file(self) -> bool:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("ichannel_panel.choose_video", index=self.index), "", _video_filter(),
        )
        if not path:
            return False
        self._apply_video(path)
        return True

    def _pick_audio_file(self) -> bool:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("ichannel_panel.choose_audio", index=self.index), "", _audio_filter(),
        )
        if not path:
            return False
        self._apply_audio(path)
        return True

    def _pick_webcam(self) -> bool:
        cameras = list_cameras()
        if not cameras:
            QMessageBox.warning(self, tr("dialogs.webcam_error.title"), tr("dialogs.webcam_error.no_webcam"))
            return False
        if len(cameras) == 1:
            device_id = cameras[0][0]
        else:
            labels = [desc for _cam_id, desc in cameras]
            label, ok = QInputDialog.getItem(
                self, tr("dialogs.webcam_error.title"),
                tr("dialogs.webcam_error.camera_prompt", index=self.index), labels, 0, False,
            )
            if not ok:
                return False
            device_id = next(cid for cid, desc in cameras if desc == label)
        self.set_state("webcam", device_id)
        self.assignmentChanged.emit(self.index, "webcam", device_id)
        return True

    def _on_combo_changed(self, combo_index: int) -> None:
        kind, value = _kind_value_for(combo_index)
        if kind == "image":
            path, _ = QFileDialog.getOpenFileName(
                self, f"Choisir une image pour iChannel{self.index}", "",
                "Images (*.png *.jpg *.jpeg *.bmp)",
            )
            if not path:
                self._revert_combo()
                return
            self._apply_image(path)
        elif kind == "video":
            if not self._pick_video_file():
                self._revert_combo()
        elif kind == "audio":
            if not self._pick_audio_file():
                self._revert_combo()
        elif kind == "webcam":
            if not self._pick_webcam():
                self._revert_combo()
        elif kind == "cubemap":
            if not self._open_cubemap_dialog():
                self._revert_combo()
        elif kind == "procedural":
            self.set_state("procedural", value)
            self.assignmentChanged.emit(self.index, "procedural", value)
        elif kind == "buffer":
            self.set_state("buffer", value)
            self.assignmentChanged.emit(self.index, "buffer", value)
        elif kind == "keyboard":
            self.set_state("keyboard", None)
            self.assignmentChanged.emit(self.index, "keyboard", None)
        else:
            self.set_state("empty", None)
            self.assignmentChanged.emit(self.index, "empty", None)

    def _on_browse(self) -> None:
        if self._kind == "cubemap":
            self._open_cubemap_dialog()
            return
        if self._kind == "video":
            self._pick_video_file()
            return
        if self._kind == "audio":
            self._pick_audio_file()
            return
        if self._kind == "webcam":
            self._pick_webcam()
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Choisir une image pour iChannel{self.index}", "",
            "Images (*.png *.jpg *.jpeg *.bmp)",
        )
        if not path:
            return
        self._apply_image(path)

    # ---- drag & drop --------------------------------------------------

    @staticmethod
    def _is_video_path(path: str) -> bool:
        return path.lower().endswith(VIDEO_EXTENSIONS)

    @staticmethod
    def _is_audio_path(path: str) -> bool:
        return path.lower().endswith(AUDIO_EXTENSIONS)

    def _first_droppable_path(self, mime_data) -> tuple[str, str] | None:
        """The first dropped local file that's an image, video, or audio
        file, as `(path, kind)`. Anything else (a directory, a URL, an
        unsupported extension) is ignored rather than guessed at."""
        if not mime_data.hasUrls():
            return None
        for url in mime_data.urls():
            path = url.toLocalFile()
            if not path:
                continue
            if self._is_image_path(path):
                return path, "image"
            if self._is_video_path(path):
                return path, "video"
            if self._is_audio_path(path):
                return path, "audio"
        return None

    def dragEnterEvent(self, event) -> None:
        if self._first_droppable_path(event.mimeData()) is not None:
            event.acceptProposedAction()
            self._thumb.setStyleSheet(_THUMB_STYLE_DRAG_OVER)

    def dragLeaveEvent(self, event) -> None:
        self.set_state(self._kind, self._value)

    def dropEvent(self, event) -> None:
        found = self._first_droppable_path(event.mimeData())
        if found is None:
            return
        path, kind = found
        if kind == "video":
            self._apply_video(path)
        elif kind == "audio":
            self._apply_audio(path)
        else:
            self._apply_image(path)
        event.acceptProposedAction()


class IChannelPanel(QWidget):
    # pass_index, channel_index, kind, value
    assignmentChanged = Signal(int, int, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._slots: list[_ChannelSlot] = []
        for i in range(4):
            slot = _ChannelSlot(i)
            slot.assignmentChanged.connect(self._on_slot_changed)
            layout.addWidget(slot)
            self._slots.append(slot)

        self._active_pass = 0
        # pass_index -> [(kind, value), ...] x4
        self._assignments: dict[int, list[tuple[str, object]]] = {}

    def _states_for(self, pass_index: int) -> list[tuple[str, object]]:
        return self._assignments.setdefault(pass_index, [("empty", None)] * 4)

    def set_active_pass(self, pass_index: int) -> None:
        self._active_pass = pass_index
        states = self._states_for(pass_index)
        for slot, (kind, value) in zip(self._slots, states):
            slot.set_state(kind, value)

    def _on_slot_changed(self, channel_index: int, kind: str, value) -> None:
        states = self._states_for(self._active_pass)
        states[channel_index] = (kind, value)
        self.assignmentChanged.emit(self._active_pass, channel_index, kind, value)

    def project_data(self) -> dict:
        """Serializable snapshot of every pass's channel assignments."""
        return {
            str(pass_index): [{"kind": k, "value": v} for k, v in states]
            for pass_index, states in self._assignments.items()
        }

    def load_project_data(self, data: dict) -> None:
        self._assignments = {
            int(pass_index): [(item["kind"], item.get("value")) for item in items]
            for pass_index, items in data.items()
        }
        self.set_active_pass(self._active_pass)

    def all_assignments(self):
        """Every (pass_index, channel_index, kind, value) currently set,
        e.g. to push a freshly loaded project into the render engine."""
        result = []
        for pass_index, states in self._assignments.items():
            for channel_index, (kind, value) in enumerate(states):
                result.append((pass_index, channel_index, kind, value))
        return result
