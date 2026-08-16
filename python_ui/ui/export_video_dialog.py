"""Export-video settings dialog (`Fichier → Exporter une vidéo (MP4)…`).

Pure UI + a rough file-size estimate: collects duration/fps/resolution/
compression and hands them back as an `ExportVideoSettings`. Actually
driving the capture loop (`video_export.capture_frames`, see the "🎬 Export
vidéo" section of ROADMAP.md) and encoding with ffmpeg are left to the
caller — this module only needs `Engine.resize`'d dimensions and a frame
count/fps pair, it never touches the engine or the filesystem itself.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from i18n import tr

# --- Compression -------------------------------------------------------
# Exposed as an ffmpeg/libx264 CRF (0 = quasi sans perte, ~51 = très
# compressé) rather than a target bitrate: shader output is typically full
# of high-frequency procedural noise/dithering, where a fixed bitrate
# would give very inconsistent visual quality from one shader to the next
# -- CRF targets a roughly constant *quality* instead, which is what
# actually matters for "does this look right", not file size.
#
# Keyed by neutral internal identifiers rather than the display text
# itself (previously the French labels doubled as the dict keys) so the
# radio buttons can show a translated label (`_CRF_PRESET_LABEL_KEYS`)
# without the lookup breaking whenever the active language changes.
CRF_PRESETS = {"quality": 18, "balanced": 23, "smallest": 30}
_CRF_PRESET_LABEL_KEYS = {
    "quality": "dialogs.export_video.crf_preset_quality",
    "balanced": "dialogs.export_video.crf_preset_balanced",
    "smallest": "dialogs.export_video.crf_preset_smallest",
}
CRF_MIN, CRF_MAX = 0, 51
DEFAULT_CRF_PRESET = "balanced"

FPS_PRESETS = (24, 30, 60)
DEFAULT_FPS = 30
MIN_FPS, MAX_FPS = 1.0, 240.0

MIN_DURATION_S, MAX_DURATION_S = 0.1, 3600.0
MIN_FRAMES, MAX_FRAMES = 1, 3600 * 240  # consistent with the ranges above
DEFAULT_DURATION_S = 5.0

MIN_DIMENSION, MAX_DIMENSION = 16, 7680  # 7680 = a generous 8K-wide ceiling

# Rough bytes-per-(pixel·frame) for libx264 `-pix_fmt yuv420p` output, one
# entry per CRF preset. Deliberately crude -- an actual encode's bitrate
# depends heavily on the shader's own content (fine procedural noise
# compresses far worse than smooth gradients) -- this exists only to keep
# the estimate in this dialog responsive without ever running a real
# encode. `record_actual_export_size` narrows a project-specific copy of
# this table toward what this project's shader actually produces, the
# first time a real export finishes (not wired up yet: ffmpeg invocation
# is a separate, not-yet-implemented roadmap item).
_DEFAULT_BYTES_PER_PIXEL_FRAME = {18: 0.06, 23: 0.025, 30: 0.01}

_SETTINGS_KEY_CALIBRATION = "videoExportCalibration"


@dataclass
class ExportVideoSettings:
    frames: int
    fps: float
    width: int
    height: int
    crf: int

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.fps if self.fps > 0 else 0.0


def _interpolated_factor(crf: int, table: dict[int, float]) -> float:
    """Piecewise-linear interpolation of `table` (keyed by CRF) at an
    arbitrary CRF the "avancé" spinbox might land on between presets --
    held flat past either end, same shape as the slider keyframe
    interpolation already used elsewhere in the app
    (`sliders_panel._interpolate_keyframes`)."""
    points = sorted(table.items())
    if crf <= points[0][0]:
        return points[0][1]
    if crf >= points[-1][0]:
        return points[-1][1]
    for (c0, v0), (c1, v1) in zip(points, points[1:]):
        if c0 <= crf <= c1:
            if c1 == c0:
                return v0
            t = (crf - c0) / (c1 - c0)
            return v0 + t * (v1 - v0)
    return points[-1][1]  # unreachable, keeps this total for any input


def estimated_file_size_bytes(settings: ExportVideoSettings, calibration: dict[int, float] | None = None) -> int:
    """`résolution × durée × fps × un facteur empirique par CRF`, exactly
    as crude as that sounds -- see `_DEFAULT_BYTES_PER_PIXEL_FRAME`."""
    table = calibration or _DEFAULT_BYTES_PER_PIXEL_FRAME
    factor = _interpolated_factor(settings.crf, table)
    return round(settings.width * settings.height * settings.fps * settings.duration_seconds * factor)


def _load_calibration(qsettings: QSettings) -> dict[int, float] | None:
    raw = qsettings.value(_SETTINGS_KEY_CALIBRATION, "", type=str)
    if not raw:
        return None
    try:
        return {int(c): float(v) for c, v in (pair.split(":") for pair in raw.split(",") if pair)}
    except ValueError:
        return None  # corrupt/foreign value -- fall back to the defaults rather than crash


def _save_calibration(qsettings: QSettings, calibration: dict[int, float]) -> None:
    qsettings.setValue(_SETTINGS_KEY_CALIBRATION, ",".join(f"{c}:{v}" for c, v in sorted(calibration.items())))


def record_actual_export_size(qsettings: QSettings, settings: ExportVideoSettings, actual_bytes: int) -> None:
    """Call once a real export (ffmpeg encode) finishes, so the next time
    this dialog opens, its file-size estimate is calibrated toward what
    this project's own shader actually produces at that CRF instead of
    the generic default table. Nothing calls this yet -- ffmpeg invocation
    is a separate, not-yet-implemented roadmap item -- but the storage
    format lives here so that item only has to call this function, not
    reinvent it."""
    pixel_frames = settings.width * settings.height * settings.fps * settings.duration_seconds
    if pixel_frames <= 0:
        return
    calibration = _load_calibration(qsettings) or dict(_DEFAULT_BYTES_PER_PIXEL_FRAME)
    calibration[settings.crf] = actual_bytes / pixel_frames
    _save_calibration(qsettings, calibration)


def _format_bytes(n: int) -> str:
    """Decimal (Mo/Go) units, matching what a file manager or ffmpeg's own
    `-progress` output would show -- not binary MiB/GiB. These unit
    letters (Go/Mo/Ko/o) are metric-system abbreviations, not language
    text -- kept as-is regardless of the active UI language, same as
    "MB"/"GB" would be in an English-only build."""
    for unit, factor in (("Go", 1_000_000_000), ("Mo", 1_000_000), ("Ko", 1_000)):
        if n >= factor:
            return f"{n / factor:.1f} {unit}"
    return f"{n} o"


class ExportVideoDialog(QDialog):
    """Collects the settings for one video export. `settings()` is only
    meaningful after `exec() == QDialog.Accepted`; calling it also
    persists the chosen values via `QSettings` so reopening the dialog
    later starts from the last export instead of hardcoded defaults."""

    def __init__(self, current_width: int, current_height: int, qsettings: QSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dialogs.export_video.title"))
        self._qsettings = qsettings

        layout = QVBoxLayout(self)

        # --- Durée -----------------------------------------------------
        duration_group = QGroupBox(tr("dialogs.export_video.duration_group"))
        duration_form = QFormLayout(duration_group)
        self._seconds_box = QDoubleSpinBox()
        self._seconds_box.setRange(MIN_DURATION_S, MAX_DURATION_S)
        self._seconds_box.setDecimals(2)
        self._seconds_box.setSuffix(" s")
        self._frames_box = QSpinBox()
        self._frames_box.setRange(MIN_FRAMES, MAX_FRAMES)
        duration_form.addRow(tr("dialogs.export_video.duration_seconds"), self._seconds_box)
        duration_form.addRow(tr("dialogs.export_video.duration_frames"), self._frames_box)
        layout.addWidget(duration_group)

        # --- Images par seconde ------------------------------------------
        fps_group = QGroupBox(tr("dialogs.export_video.fps_group"))
        fps_row = QHBoxLayout(fps_group)
        self._fps_buttons = QButtonGroup(self)
        self._fps_radios: dict[int, QRadioButton] = {}
        for fps in FPS_PRESETS:
            radio = QRadioButton(str(fps))
            self._fps_buttons.addButton(radio)
            self._fps_radios[fps] = radio
            fps_row.addWidget(radio)
        self._fps_custom_radio = QRadioButton(tr("dialogs.export_video.fps_custom"))
        self._fps_buttons.addButton(self._fps_custom_radio)
        fps_row.addWidget(self._fps_custom_radio)
        self._fps_custom_box = QDoubleSpinBox()
        self._fps_custom_box.setRange(MIN_FPS, MAX_FPS)
        self._fps_custom_box.setDecimals(2)
        fps_row.addWidget(self._fps_custom_box)
        layout.addWidget(fps_group)

        # --- Résolution ----------------------------------------------------
        resolution_group = QGroupBox(tr("dialogs.export_video.resolution_group"))
        resolution_form = QFormLayout(resolution_group)
        self._width_box = QSpinBox()
        self._width_box.setRange(MIN_DIMENSION, MAX_DIMENSION)
        self._height_box = QSpinBox()
        self._height_box.setRange(MIN_DIMENSION, MAX_DIMENSION)
        resolution_form.addRow(tr("dialogs.export_video.resolution_width"), self._width_box)
        resolution_form.addRow(tr("dialogs.export_video.resolution_height"), self._height_box)
        layout.addWidget(resolution_group)

        # --- Compression (CRF) ---------------------------------------------
        compression_group = QGroupBox(tr("dialogs.export_video.compression_group"))
        compression_layout = QVBoxLayout(compression_group)
        preset_row = QHBoxLayout()
        self._crf_buttons = QButtonGroup(self)
        self._crf_radios: dict[str, QRadioButton] = {}
        for name in CRF_PRESETS:
            radio = QRadioButton(tr(_CRF_PRESET_LABEL_KEYS[name]))
            self._crf_buttons.addButton(radio)
            self._crf_radios[name] = radio
            radio.setProperty("presetId", name)
            preset_row.addWidget(radio)
        compression_layout.addLayout(preset_row)
        advanced_row = QHBoxLayout()
        self._crf_advanced_box = QCheckBox(tr("dialogs.export_video.compression_advanced"))
        self._crf_spin = QSpinBox()
        self._crf_spin.setRange(CRF_MIN, CRF_MAX)
        self._crf_spin.setEnabled(False)
        advanced_row.addWidget(self._crf_advanced_box)
        advanced_row.addWidget(self._crf_spin)
        compression_layout.addLayout(advanced_row)
        layout.addWidget(compression_group)

        # --- Estimation de taille (en direct) -------------------------------
        self._estimate_label = QLabel()
        self._estimate_label.setAlignment(Qt.AlignRight)
        layout.addWidget(self._estimate_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Guards the seconds<->frames sync below against feeding its own
        # `valueChanged` signal back into itself.
        self._syncing_duration = False

        self._seconds_box.valueChanged.connect(self._on_seconds_changed)
        self._frames_box.valueChanged.connect(self._on_frames_changed)
        self._fps_buttons.buttonToggled.connect(self._on_fps_toggled)
        self._fps_custom_box.valueChanged.connect(self._on_fps_custom_changed)
        self._crf_buttons.buttonToggled.connect(self._on_crf_preset_toggled)
        self._crf_advanced_box.toggled.connect(self._on_crf_advanced_toggled)
        self._crf_spin.valueChanged.connect(lambda _: self._update_estimate())
        self._width_box.valueChanged.connect(lambda _: self._update_estimate())
        self._height_box.valueChanged.connect(lambda _: self._update_estimate())

        self._load_initial_values(current_width, current_height)
        self._update_estimate()

    # ---- initial values: last export's settings, or sensible defaults ----
    def _load_initial_values(self, current_width: int, current_height: int) -> None:
        s = self._qsettings
        fps = s.value("videoExportFps", float(DEFAULT_FPS), type=float)
        if int(fps) in self._fps_radios and float(int(fps)) == fps:
            self._fps_radios[int(fps)].setChecked(True)
        else:
            self._fps_custom_radio.setChecked(True)
        self._fps_custom_box.setValue(fps)
        self._fps_custom_box.setEnabled(self._fps_custom_radio.isChecked())

        default_frames = round(DEFAULT_DURATION_S * fps)
        frames = s.value("videoExportFrames", default_frames, type=int)
        self._syncing_duration = True
        self._frames_box.setValue(max(MIN_FRAMES, frames))
        self._seconds_box.setValue(self._frames_box.value() / fps if fps > 0 else MIN_DURATION_S)
        self._syncing_duration = False

        self._width_box.setValue(s.value("videoExportWidth", current_width, type=int))
        self._height_box.setValue(s.value("videoExportHeight", current_height, type=int))

        crf = s.value("videoExportCrf", CRF_PRESETS[DEFAULT_CRF_PRESET], type=int)
        preset_name = next((name for name, value in CRF_PRESETS.items() if value == crf), None)
        self._crf_advanced_box.setChecked(preset_name is None)
        if preset_name is not None:
            self._crf_radios[preset_name].setChecked(True)
        self._crf_spin.setEnabled(preset_name is None)
        self._crf_spin.setValue(crf)

    # ---- duration sync: seconds <-> frames, anchored on the current fps ----
    def _current_fps(self) -> float:
        if self._fps_custom_radio.isChecked():
            return self._fps_custom_box.value()
        for fps, radio in self._fps_radios.items():
            if radio.isChecked():
                return float(fps)
        return float(DEFAULT_FPS)

    def _on_seconds_changed(self, value: float) -> None:
        if self._syncing_duration:
            return
        self._syncing_duration = True
        self._frames_box.setValue(max(MIN_FRAMES, round(value * self._current_fps())))
        self._syncing_duration = False
        self._update_estimate()

    def _on_frames_changed(self, value: int) -> None:
        if self._syncing_duration:
            return
        self._syncing_duration = True
        fps = self._current_fps()
        self._seconds_box.setValue(value / fps if fps > 0 else MIN_DURATION_S)
        self._syncing_duration = False
        self._update_estimate()

    def _resync_seconds_from_frames(self) -> None:
        """An fps change redefines what "the same duration" means; frames
        is the value actually fed to the capture loop later on, so it's
        kept as the anchor and seconds is recomputed to match -- same
        convention a direct seconds/frames edit uses via `_current_fps`."""
        self._syncing_duration = True
        fps = self._current_fps()
        self._seconds_box.setValue(self._frames_box.value() / fps if fps > 0 else MIN_DURATION_S)
        self._syncing_duration = False

    def _on_fps_toggled(self, button, checked: bool) -> None:
        if not checked:
            return
        self._fps_custom_box.setEnabled(button is self._fps_custom_radio)
        self._resync_seconds_from_frames()
        self._update_estimate()

    def _on_fps_custom_changed(self, _value: float) -> None:
        if self._fps_custom_radio.isChecked():
            self._resync_seconds_from_frames()
            self._update_estimate()

    # ---- CRF sync: presets <-> advanced exact value ------------------------
    def _on_crf_preset_toggled(self, button, checked: bool) -> None:
        if not checked:
            return
        self._crf_spin.setValue(CRF_PRESETS[button.property("presetId")])
        self._update_estimate()

    def _on_crf_advanced_toggled(self, checked: bool) -> None:
        self._crf_spin.setEnabled(checked)
        for radio in self._crf_radios.values():
            radio.setEnabled(not checked)
        if not checked:
            # Leaving "avancé" with a typed value that matches no preset
            # exactly should still land somewhere, not leave every radio
            # unchecked -- snap to whichever preset is visually closest.
            closest_name = min(CRF_PRESETS, key=lambda name: abs(CRF_PRESETS[name] - self._crf_spin.value()))
            self._crf_radios[closest_name].setChecked(True)
        self._update_estimate()

    # ---- live file-size estimate --------------------------------------------
    def _update_estimate(self) -> None:
        size = estimated_file_size_bytes(self._current_settings(), _load_calibration(self._qsettings))
        self._estimate_label.setText(tr("dialogs.export_video.estimated_size", size=_format_bytes(size)))

    def _current_settings(self) -> ExportVideoSettings:
        return ExportVideoSettings(
            frames=self._frames_box.value(),
            fps=self._current_fps(),
            width=self._width_box.value(),
            height=self._height_box.value(),
            crf=self._crf_spin.value(),
        )

    # ---- result --------------------------------------------------------------
    def settings(self) -> ExportVideoSettings:
        result = self._current_settings()
        s = self._qsettings
        s.setValue("videoExportFps", result.fps)
        s.setValue("videoExportFrames", result.frames)
        s.setValue("videoExportWidth", result.width)
        s.setValue("videoExportHeight", result.height)
        s.setValue("videoExportCrf", result.crf)
        return result
