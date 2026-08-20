"""RM10.md section 2, item 1 ("coloration syntaxique correcte à 100% pour
les trois styles") -- real functional coverage: builds a real `MainWindow`
with its real `QWebEngineView`-backed Monaco editor, drives dialect
switches through the actual UI code paths (`_goto_tab`,
`_recompile_current_tab`), and reads back the *actual* Monaco model
language via a real `page().runJavaScript()` round-trip -- not just "the
Python call didn't raise".

Needs the native module built (`engine_bridge` import) -- absent, SKIPs
cleanly, same convention as the other native-module-dependent tests here.
"""
import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# `ui.main_window` imports `app_version` (repo root, next to `run.py`) as a
# bare top-level module -- must be on sys.path too, not just `python_ui/`.
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python_ui"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import i18n
i18n.load_language(i18n.FALLBACK_LANGUAGE_CODE)

try:
    import engine_bridge  # noqa: F401
except ImportError as exc:
    print(f"SKIPPED: native module not built ({exc}); "
          f"run 'cd rust_engine && maturin develop --release' first.")
    sys.exit(0)

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([sys.argv[0]])
app.setOrganizationName("PetitEditeurGLSL")
app.setApplicationName("PetitEditeurGLSL")
QStandardPaths.setTestModeEnabled(True)
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp(prefix="peg_test_settings_"))

from ui.main_window import MainWindow, COMMON_TAB  # noqa: E402

window = MainWindow()

# Wait for the real Monaco editor (QWebEngineView, async page load + AMD
# require()) to actually be ready before driving anything through it.
if not window.editor._ready:
    from PySide6.QtCore import QEventLoop
    loop = QEventLoop()
    window.editor.editorReady.connect(loop.quit)
    loop.exec()
assert window.editor._ready


def current_model_language() -> str:
    """Round-trips through the real page's JS, reading Monaco's own model
    language back -- not a Python-side assumption about what was sent."""
    result = {}
    loop_done = []

    def _cb(value):
        result["language"] = value
        loop_done.append(True)

    window.editor.page().runJavaScript("editor.getModel().getLanguageId();", _cb)
    while not loop_done:
        app.processEvents()
    return result["language"]


# ---- 1. Default shader (Shadertoy) -> glsl tokenizer ----------------------

assert current_model_language() == "glsl"
print("default Shadertoy shader: Monaco model language is 'glsl': ok")

# ---- 2. Typing/compiling a WGSL pass -> wgsl tokenizer --------------------

wgsl_src = (
    "@fragment\n"
    "fn main() -> @location(0) vec4<f32> {\n"
    "    return vec4<f32>(0.0, 0.0, 1.0, 1.0);\n"
    "}\n"
)
window._on_text_changed(wgsl_src)
window._recompile_current_tab()
assert current_model_language() == "wgsl", "a WGSL pass should switch Monaco's tokenizer to 'wgsl'"
print("WGSL pass compiled: Monaco model language switches to 'wgsl': ok")

# ---- 3. Switching back to a GLSL/Shadertoy pass -> glsl tokenizer ---------

glsl_src = "void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }"
window._on_text_changed(glsl_src)
window._recompile_current_tab()
assert current_model_language() == "glsl", "switching back to a Shadertoy pass should restore 'glsl'"
print("switching back to a Shadertoy pass: Monaco model language is 'glsl' again: ok")

# ---- 4. Switching tabs applies the target tab's own language immediately -

window._pass_sources[engine_bridge.PASS_BUFFER_A] = wgsl_src
window._goto_tab(engine_bridge.PASS_BUFFER_A)
assert current_model_language() == "wgsl", "_goto_tab must apply the new tab's language immediately, not lazily"
window._goto_tab(engine_bridge.PASS_IMAGE)
assert current_model_language() == "glsl"
print("_goto_tab switches Monaco's language immediately on tab change: ok")

# ---- 5. Common tab can itself contain WGSL and gets colored accordingly --

window._goto_tab(COMMON_TAB)
window._on_text_changed("fn helper(x: f32) -> f32 { return x + 1.0; }\n")
window._recompile_current_tab()
assert current_model_language() == "wgsl", "Common written in WGSL (helper fn, no entry point) should still highlight as WGSL"
print("Common tab written in WGSL gets the 'wgsl' tokenizer too: ok")

window._on_text_changed("float helper(float x) { return x + 1.0; }\n")
window._recompile_current_tab()
assert current_model_language() == "glsl"
print("Common tab written in GLSL gets the 'glsl' tokenizer: ok")

window._autosave_timer.stop()
print("\nALL OK")
