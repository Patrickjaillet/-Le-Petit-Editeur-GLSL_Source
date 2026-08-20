import sys

# A PyInstaller build with console=False (this app's release build, see
# packaging/petit_editeur_glsl.spec) has no console attached, so
# sys.stdout/stderr/stdin are None rather than real streams. Anything that
# writes to them unconditionally — the default excepthook on an uncaught
# exception, print() calls, http.server's per-request access log — would
# raise AttributeError the moment it tries, which is worse than losing the
# output: it either replaces the real error with a confusing secondary one,
# or (for a request handler running in its own thread, e.g. local_server.py)
# kills the response before it's ever sent. Redirecting to devnull up front
# makes every such write a harmless no-op instead, in dev mode too (a real
# console still gets everything, since these streams are only None when
# there isn't one).
if sys.stdout is None or sys.stderr is None:
    import os

    devnull = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = devnull
    if sys.stderr is None:
        sys.stderr = devnull
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r")

from PySide6.QtCore import QLocale, QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

import i18n
from i18n import tr
from ui.main_window import MainWindow
from ui.theme import apply_glass_theme


def _startup_language_code() -> str:
    """`languageCode` from `QSettings` if the user already picked one
    (via the Préférences language picker, once that roadmap item exists);
    otherwise the system locale's language, if `lngs/` happens to have a
    file for it (`QLocale.system().name()` is like `"fr_FR"`, and only
    the `"fr"` part before the underscore is a language code the way
    `lngs/*.json` is named); otherwise `i18n.FALLBACK_LANGUAGE_CODE`
    ("fr") -- this must never raise or return a code with no matching
    file, since `i18n.load_language()` already degrades gracefully on a
    bad code, but there's no reason to hand it one on purpose.
    """
    settings = QSettings("PetitEditeurGLSL", "PetitEditeurGLSL")
    saved = settings.value("languageCode", "", type=str)
    if saved:
        return saved
    system_code = QLocale.system().name().split("_", 1)[0]
    if system_code in i18n.available_languages():
        return system_code
    return i18n.FALLBACK_LANGUAGE_CODE


def main() -> int:
    app = QApplication(sys.argv)
    # Without these, `QStandardPaths.AppDataLocation` (used by
    # `MainWindow._autosave_file_path`, RM10.md section 1) falls back to a
    # location derived from the running executable's own name rather than
    # a stable, app-specific folder -- unpredictable across dev (`python.exe`)
    # vs. a packaged build, and shared with nothing else on the machine
    # that happens to run under the same interpreter. `QSettings`'s own
    # per-call `("PetitEditeurGLSL", "PetitEditeurGLSL")` constructor args
    # already sidestep this for preferences, but `QStandardPaths` has no
    # such per-call override -- it only ever reads the global app identity.
    app.setOrganizationName("PetitEditeurGLSL")
    app.setApplicationName("PetitEditeurGLSL")
    apply_glass_theme(app)
    i18n.load_language(_startup_language_code())
    # RM10.md section 1, item 7: `renderer::Engine::new` (built inside
    # `MainWindow.__init__`) already returns a proper `RuntimeError` --
    # never panics -- when no usable graphics adapter/device can be
    # created (missing/unsupported GPU driver, no Vulkan/Metal/DX12
    # backend). What was still missing was catching it *here*: left
    # unhandled, this propagated all the way out of `main()` as a raw
    # Python traceback -- invisible in a packaged build, since
    # `console=False` (see the module docstring above) means there's no
    # console to print it to, so the app would appear to silently do
    # nothing at all rather than showing the black-window/silent-crash
    # this item explicitly asks not to happen.
    try:
        window = MainWindow()
    except RuntimeError as exc:
        QMessageBox.critical(None, tr("dialogs.gpu_error.title"), tr("dialogs.gpu_error.body", error=exc))
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
