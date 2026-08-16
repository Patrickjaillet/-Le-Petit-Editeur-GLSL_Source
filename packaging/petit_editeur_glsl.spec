# PyInstaller spec — build autonome (onedir) de Petit Editeur GLSL.
#
# Usage (depuis la racine du projet, venv active) :
#   pyinstaller packaging/petit_editeur_glsl.spec --noconfirm
#
# Produit dist/PetitEditeurGLSL/ : un dossier autonome contenant
# PetitEditeurGLSL.exe + toutes ses dependances (Python, PySide6/QtWebEngine,
# le module natif shadertoy_engine, les assets Monaco). C'est ce dossier que
# packaging/installer.iss empaquette ensuite en installeur.
#
# Pourquoi onedir et pas onefile : QtWebEngine lance un sous-processus
# (QtWebEngineProcess.exe) qui doit retrouver ses ressources (icudtl.dat,
# *.pak, translations/) a cote de l'executable principal. Un onefile
# re-extrairait tout ca dans un dossier temporaire a chaque lancement
# (demarrage plus lent, et plus fragile avec WebEngine) sans benefice reel
# pour une appli qui est de toute facon installee via un installeur.

import sys
from pathlib import Path

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.building.datastruct import Tree
from PyInstaller.utils.hooks import collect_all

block_cipher = None

PROJECT_ROOT = Path(SPECPATH).resolve().parent
PYTHON_UI_DIR = PROJECT_ROOT / "python_ui"
ICON_PATH = PROJECT_ROOT / "packaging" / "icon.ico"

APP_NAME = "PetitEditeurGLSL"

# Le code n'importe que 6 sous-modules PySide6 : QtCore, QtGui, QtWidgets,
# QtMultimedia, QtWebChannel et QtWebEngineWidgets (cf. `grep -rho "from
# PySide6\.\w+" python_ui/`). collect_all("PySide6") embarquait *tout*
# PySide6 (QtSql, QtBluetooth, Qt3D, QtCharts, QtDesigner, QtQml/Quick,
# les traductions de modules non utilises...), d'ou un dossier de sortie
# de plusieurs centaines de Mo. On ne collecte donc plus que ces 6
# sous-modules : chacun a son propre hook PyInstaller qui sait deja
# embarquer ce dont il a besoin -- notamment celui de QtWebEngineWidgets,
# qui recupere seul QtWebEngineProcess.exe, icudtl.dat, *.pak et les
# traductions necessaires. Les DLL dont ces modules dependent en interne
# (ex. Qt6Network.dll, requis par QtWebEngineCore) restent embarquees
# quoi qu'il arrive : PyInstaller les detecte par analyse des dependances
# binaires de chaque .dll/.so, independamment de cette liste Python.
PYSIDE6_SUBMODULES_USED = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineWidgets",
]

pyside6_datas: list = []
pyside6_binaries: list = []
pyside6_hiddenimports: list = []
for _mod in PYSIDE6_SUBMODULES_USED:
    _datas, _binaries, _hiddenimports = collect_all(_mod)
    pyside6_datas += _datas
    pyside6_binaries += _binaries
    pyside6_hiddenimports += _hiddenimports

# Filet de securite : exclut explicitement les grandes familles Qt non
# utilisees par le code (3D, IoT/capteurs, base de donnees, outils de
# design, QML/Quick, PDF...) au cas ou l'un des hooks ci-dessus tenterait
# d'en ajouter une en hiddenimport. Ne touche pas aux DLL C++ (cf. ci-
# dessus), seulement aux wrappers Python correspondants.
PYSIDE6_SUBMODULES_EXCLUDED = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtGraphs", "PySide6.QtGraphsWidgets",
    "PySide6.QtHelp", "PySide6.QtNetworkAuth", "PySide6.QtNfc",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSpatialAudio",
    "PySide6.QtSql", "PySide6.QtStateMachine", "PySide6.QtSvg",
    "PySide6.QtSvgWidgets", "PySide6.QtTest", "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools", "PySide6.QtWebSockets", "PySide6.QtXml",
]

a = Analysis(
    [str(PROJECT_ROOT / "run.py")],
    pathex=[str(PYTHON_UI_DIR)],
    binaries=pyside6_binaries,
    datas=pyside6_datas,
    hiddenimports=[
        *pyside6_hiddenimports,
        "shadertoy_engine",
    ],
    excludes=PYSIDE6_SUBMODULES_EXCLUDED,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    noarchive=False,
    cipher=block_cipher,
)

# --- Locales QtWebEngine (Chromium) -------------------------------------
# QtWebEngineCore embarque ses propres traductions cote Chromium (menu
# contextuel, chaines internes du moteur web) pour une cinquantaine de
# langues, sous forme de fichiers .pak dans un dossier
# "qtwebengine_locales" -- independamment de l'i18n de l'app elle-meme
# (lngs/*.json, 12 langues). Sans filtrage, ces paks pesent plusieurs
# dizaines de Mo pour des langues jamais exposees dans l'UI de l'app. On
# ne garde donc que les paks correspondant aux langues supportees par
# lngs/ (mappees vers leurs codes locale Chromium, qui ne correspondent
# pas toujours aux codes de lngs/ -- ex. "no" -> "nb", "zh" -> "zh-CN"/
# "zh-TW") plus "en-US", locale de repli integree a Chromium.
#
# NON TESTE contre un vrai build PySide6/QtWebEngine (indisponible dans
# cet environnement) : a valider avant release -- lancer l'app dans
# chacune des 12 langues de lngs/, ouvrir le menu contextuel de l'editeur
# Monaco (clic droit) dans chacune, et verifier l'absence d'avertissement
# "locale introuvable" dans les logs au demarrage. En cas de doute,
# supprimer entierement ce bloc pour revenir au comportement precedent
# (toutes les locales embarquees).
APP_LANGUAGE_TO_CHROMIUM_LOCALES = {
    "de": ["de"],
    "en": ["en-US", "en-GB"],
    "es": ["es", "es-419"],
    "fr": ["fr"],
    "hi": ["hi"],
    "it": ["it"],
    "ja": ["ja"],
    "ko": ["ko"],
    "no": ["nb", "nn"],  # Chromium n'a pas de code locale "no" generique
    "pt": ["pt-BR", "pt-PT"],
    "sv": ["sv"],
    "zh": ["zh-CN", "zh-TW", "zh-HK"],
}
KEPT_WEBENGINE_LOCALES = {"en-US"}  # fallback Chromium, toujours conserve
for _codes in APP_LANGUAGE_TO_CHROMIUM_LOCALES.values():
    KEPT_WEBENGINE_LOCALES.update(_codes)


def _is_unwanted_webengine_locale_pak(dest_path: str) -> bool:
    normalized = dest_path.replace("\\", "/")
    if "qtwebengine_locales/" not in normalized:
        return False
    filename = normalized.rsplit("/", 1)[-1]
    stem = filename[: -len(".pak")] if filename.endswith(".pak") else filename
    return stem not in KEPT_WEBENGINE_LOCALES


a.datas = [
    entry for entry in a.datas
    if not _is_unwanted_webengine_locale_pak(entry[0])
]

# Les assets (Monaco vendorise, page hote, shader par defaut) sont lus au
# runtime via Path(__file__).parent / "assets" depuis des modules qui
# deviennent top-level dans le bundle (cf. pathex ci-dessus, meme logique
# que le sys.path.insert(python_ui/) de run.py en developpement) : ils
# doivent donc atterrir a la racine du bundle sous "assets/", pas sous
# "python_ui/assets/".
a.datas += Tree(str(PYTHON_UI_DIR / "assets"), prefix="assets")

# lngs/*.json (interface translations, cf. python_ui/i18n.py) are copied
# the same way and for the same reason as assets/ just above: read at
# runtime via a path resolved relative to sys.executable in sys.frozen
# mode (i18n.lngs_dir()), so they must land at the bundle root as
# "lngs/", not nested under "python_ui/". Unlike assets/, this directory
# lives at the project root (not under python_ui/) since it's also read
# directly from the source tree in development -- see lngs_dir()'s
# docstring for both cases.
a.datas += Tree(str(PROJECT_ROOT / "lngs"), prefix="lngs")

# ffmpeg.exe (encodage de l'export video, cf. video_export.py) est copie
# tel quel a la racine du bundle -- a cote de PetitEditeurGLSL.exe, pas
# sous python_ui/ -- pour correspondre a ce que
# video_export.resolve_ffmpeg_path() attend en mode sys.frozen. Ajoute
# directement a a.datas (et pas comme binaire PyInstaller) car c'est un
# executable autonome qu'on veut copier a l'identique, sans que
# PyInstaller tente d'en analyser les dependances comme il le ferait pour
# un binaire lie a l'appli.
FFMPEG_EXE = PROJECT_ROOT / "packaging" / "bin" / "ffmpeg.exe"
FFMPEG_LICENSE = PROJECT_ROOT / "packaging" / "bin" / "ffmpeg-LICENSE.txt"
# ffmpeg.exe n'est pas versionne dans le depot (~100 Mo, cf.
# COMPILATION.md section 3bis) : il doit etre telecharge une fois par
# poste de build. On echoue bruyamment ici plutot que de laisser
# PyInstaller continuer silencieusement sans lui -- sinon le build et
# l'installeur se produisent "sans erreur" mais l'export video ne
# fonctionnera pour personne une fois l'app installee (cf. issue :
# ffmpeg absent de l'installeur).
if not FFMPEG_EXE.is_file():
    raise SystemExit(
        "ERREUR : packaging/bin/ffmpeg.exe introuvable -- l'installeur "
        "serait genere SANS ffmpeg et l'export video serait casse chez "
        "tous les utilisateurs. Telechargez-le d'abord (COMPILATION.md, "
        "section 3bis) avant de relancer le build."
    )
if not FFMPEG_LICENSE.is_file():
    raise SystemExit(
        "ERREUR : packaging/bin/ffmpeg-LICENSE.txt introuvable -- "
        "recuperez-le en meme temps que ffmpeg.exe (COMPILATION.md, "
        "section 3bis)."
    )
a.datas += [(FFMPEG_EXE.name, str(FFMPEG_EXE), "DATA")]
a.datas += [(FFMPEG_LICENSE.name, str(FFMPEG_LICENSE), "DATA")]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON_PATH) if ICON_PATH.is_file() else None,
    # Depuis PyInstaller 6.0, le mode onedir place par defaut tout ce qui
    # n'est pas l'exe principal (DLLs, module natif shadertoy_engine,
    # assets Monaco, ffmpeg.exe...) dans un sous-dossier "_internal/" a
    # cote de l'exe, plutot qu'a plat comme avant. Or tout le reste du
    # code (video_export.resolve_ffmpeg_path() qui cherche ffmpeg.exe a
    # cote de sys.executable, les modules qui lisent leurs assets via
    # Path(__file__).parent / "assets") suppose l'ancien layout a plat.
    # contents_directory="." desactive ce sous-dossier "_internal/" et
    # restaure ce layout -- sans ca, ffmpeg.exe (et les assets) finissent
    # bien copies dans le build mais au mauvais endroit, introuvables au
    # runtime (cause du bug "ffmpeg absent de l'installeur").
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
