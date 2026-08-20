"""Well-organized, user-visible target folders for every save/export
dialog and every iChannel asset picker in the app -- created under
`Documents\\Petit Editeur GLSL\\` (`QStandardPaths.DocumentsLocation`) the
first time each one is actually needed, and handed to every `QFileDialog`
call site in this app as its starting directory. That's what makes a menu
action or toolbar button open straight into the folder that matches what
it's saving or loading, every time -- instead of wherever Qt's own
per-dialog "last used directory" memory happened to leave off after some
earlier, unrelated dialog.

Deliberately *not* localized (fixed English names regardless of the active
UI language chosen via `i18n.py`): these names are written to disk and
must stay stable across a language switch, or switching languages would
leave a user with two parallel folder trees instead of one.

Distinct from `QStandardPaths.AppDataLocation`, used by
`MainWindow._autosave_file_path` for the crash-recovery autosave file --
that file is internal bookkeeping nobody is meant to browse to by hand, so
it stays out of sight in the OS's per-app data location instead of this
human-facing tree.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths

_ROOT_FOLDER_NAME = "Petit Editeur GLSL"

# Each value is a tuple of path segments under the workspace root -- a
# single-element tuple for a top-level folder, more for a nested one (every
# iChannel asset kind lives under a shared "iChannels" parent, one folder
# per kind, mirroring the categories the iChannel slot picker itself
# offers: image, video, cubemap, audio).
_SUBFOLDERS: dict[str, tuple[str, ...]] = {
    "projects": ("Projects",),
    "shaders": ("Shaders",),
    "videos": ("Videos",),
    "images": ("Images",),
    "exports": ("Exports",),
    "ichannel_textures": ("iChannels", "Textures"),
    "ichannel_videos": ("iChannels", "Videos"),
    "ichannel_cubemaps": ("iChannels", "Cubemaps"),
    "ichannel_audio": ("iChannels", "Audio"),
}


def workspace_root() -> Path:
    base = Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation))
    return base / _ROOT_FOLDER_NAME


def dir_for(category: str) -> Path:
    """Returns the target folder for `category` (one of `_SUBFOLDERS`'
    keys), creating it -- and its parents, including the workspace root
    itself -- if it doesn't exist yet. That means every call site can hand
    this straight to a `QFileDialog` as a starting directory with no
    separate existence check of its own: the folder is always there by the
    time the dialog opens.
    """
    path = workspace_root().joinpath(*_SUBFOLDERS[category])
    path.mkdir(parents=True, exist_ok=True)
    return path
