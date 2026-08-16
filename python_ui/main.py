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
from PySide6.QtWidgets import QApplication

import i18n
from ui.main_window import MainWindow


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
    i18n.load_language(_startup_language_code())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
