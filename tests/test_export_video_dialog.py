import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python_ui"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import i18n
i18n.load_language(i18n.FALLBACK_LANGUAGE_CODE)

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from ui.export_video_dialog import (  # noqa: E402
    CRF_PRESETS,
    ExportVideoDialog,
    ExportVideoSettings,
    estimated_file_size_bytes,
    record_actual_export_size,
)

_ini_fd, _ini_path = tempfile.mkstemp(suffix=".ini")
os.close(_ini_fd)


def fresh_settings() -> QSettings:
    return QSettings(_ini_path, QSettings.IniFormat)


# ---- fresh dialog: sensible defaults ------------------------------------
dlg = ExportVideoDialog(800, 450, fresh_settings())
assert dlg._width_box.value() == 800 and dlg._height_box.value() == 450
assert dlg._current_fps() == 30.0
assert dlg._crf_spin.value() == CRF_PRESETS["balanced"]
initial = dlg._current_settings()
assert initial.frames == round(5.0 * 30.0), initial.frames  # 5s default @ 30fps

# ---- seconds -> frames sync, anchored on current fps ----------------------
dlg._seconds_box.setValue(10.0)
assert dlg._frames_box.value() == 300, dlg._frames_box.value()

# ---- frames -> seconds sync ------------------------------------------------
dlg._frames_box.setValue(60)
assert abs(dlg._seconds_box.value() - 2.0) < 1e-9, dlg._seconds_box.value()

# ---- switching fps preset keeps frames fixed, recomputes seconds ----------
dlg._fps_radios[60].setChecked(True)
assert dlg._current_fps() == 60.0
assert dlg._frames_box.value() == 60, "frames must stay the anchor across an fps change"
assert abs(dlg._seconds_box.value() - 1.0) < 1e-9, dlg._seconds_box.value()

# ---- custom fps ------------------------------------------------------------
dlg._fps_custom_radio.setChecked(True)
dlg._fps_custom_box.setValue(50.0)
assert dlg._current_fps() == 50.0
assert dlg._frames_box.value() == 60  # still the anchor
assert abs(dlg._seconds_box.value() - 1.2) < 1e-9, dlg._seconds_box.value()

# ---- CRF presets vs advanced -----------------------------------------------
dlg._crf_radios["quality"].setChecked(True)
assert dlg._crf_spin.value() == 18
assert not dlg._crf_spin.isEnabled()

dlg._crf_advanced_box.setChecked(True)
assert dlg._crf_spin.isEnabled()
dlg._crf_spin.setValue(40)
assert dlg._current_settings().crf == 40

dlg._crf_advanced_box.setChecked(False)
# 40 is closest to "Taille minimale" (30) among the three presets
assert dlg._crf_radios["smallest"].isChecked()
assert dlg._crf_spin.value() == 30

print("SYNC OK")

# ---- persistence round-trip ------------------------------------------------
settings_store = fresh_settings()
dlg2 = ExportVideoDialog(800, 450, settings_store)
dlg2._width_box.setValue(1920)
dlg2._height_box.setValue(1080)
dlg2._frames_box.setValue(150)
dlg2._fps_radios[24].setChecked(True)
dlg2._crf_radios["quality"].setChecked(True)
result = dlg2.settings()
assert result == ExportVideoSettings(frames=150, fps=24.0, width=1920, height=1080, crf=18), result

dlg3 = ExportVideoDialog(800, 450, settings_store)  # reopen: should start from what was just saved
assert dlg3._width_box.value() == 1920
assert dlg3._height_box.value() == 1080
assert dlg3._frames_box.value() == 150
assert dlg3._current_fps() == 24.0
assert dlg3._crf_spin.value() == 18
assert dlg3._crf_radios["quality"].isChecked()

print("PERSISTENCE OK")

# ---- file-size estimate: matches width*height*fps*duration*factor --------
s = ExportVideoSettings(frames=90, fps=30.0, width=640, height=360, crf=23)
expected = round(640 * 360 * 30.0 * 3.0 * 0.025)  # 90 frames @ 30fps = 3s, default factor for CRF 23
assert estimated_file_size_bytes(s) == expected, (estimated_file_size_bytes(s), expected)

# Interpolates between presets for an in-between CRF (advanced slider).
s_mid = ExportVideoSettings(frames=30, fps=30.0, width=100, height=100, crf=20)  # halfway 18<->23
low = estimated_file_size_bytes(ExportVideoSettings(30, 30.0, 100, 100, 18))
high = estimated_file_size_bytes(ExportVideoSettings(30, 30.0, 100, 100, 23))
mid = estimated_file_size_bytes(s_mid)
assert min(low, high) <= mid <= max(low, high), (low, mid, high)

# Held flat outside the table's own range.
below = estimated_file_size_bytes(ExportVideoSettings(30, 30.0, 100, 100, 0))
at_min = estimated_file_size_bytes(ExportVideoSettings(30, 30.0, 100, 100, 18))
assert below == at_min

print("ESTIMATE OK")

# ---- calibration: a real export narrows the estimate toward reality -------
calib_settings = fresh_settings()
export_settings = ExportVideoSettings(frames=30, fps=30.0, width=200, height=100, crf=23)
before = estimated_file_size_bytes(export_settings)
record_actual_export_size(calib_settings, export_settings, actual_bytes=before * 2)

from ui.export_video_dialog import _load_calibration  # noqa: E402

calibration = _load_calibration(calib_settings)
after = estimated_file_size_bytes(export_settings, calibration)
assert abs(after - before * 2) < 2, (after, before)  # rounding tolerance only

print("CALIBRATION OK")

os.unlink(_ini_path)
print("ALL OK")
