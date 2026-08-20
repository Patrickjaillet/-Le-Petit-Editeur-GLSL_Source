# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Le Petit Editeur GLSL — a Windows desktop live shader editor (Shadertoy-compatible). Two-layer architecture:

- **`rust_engine/`** — a Rust crate (`shadertoy_engine`) compiled as a Python native extension via `pyo3`/`maturin`. Owns everything performance/correctness-critical: dialect detection, shader compilation (via `wgpu`/`naga`), the multi-pass renderer, texture handling, slider/literal detection, and the GLSL golfer/minifier.
- **`python_ui/`** — the PySide6 (Qt) GUI. Talks to the Rust engine only through `python_ui/engine_bridge.py`, a thin wrapper that re-exports the native module's classes/functions/constants (`Engine`, `detect_dialect`, `golf_shader_ex`, pass indices, dialect constants, etc.). The code editor itself is a vendored Monaco Editor running inside a `QtWebEngine` view, served over a local HTTP server (`python_ui/local_server.py`) — not `file://`, because QtWebEngine refuses to load Web Workers from `file://`.

Both layers are documented at length in French in `ARCHITECTURE.md`, `RMLG.md`, and `ROADMAP.md`/`roadmap1.md` — read those before making structural changes, they contain design rationale that isn't repeated here.

## Common commands

All commands below assume PowerShell from the repo root with `.venv` already created (`python -m venv .venv`, then `.venv\Scripts\python.exe -m pip install -r requirements.txt`).

### Rebuild the Rust engine after any `.rs` change

```powershell
cd rust_engine
$env:VIRTUAL_ENV = (Resolve-Path ..\.venv).Path
$env:Path = "$((Resolve-Path ..\.venv\Scripts).Path);$env:Path"
..\.venv\Scripts\python.exe -m maturin develop --release
cd ..
```

This is required before Python code will see the new Rust behavior — `python_ui` imports the compiled `shadertoy_engine` module, not the source. A `maturin develop` that finishes in well under a second (instead of ~1-5 minutes) means it silently failed to replace a locked `.pyd` — see the "cold recompile" procedure at the bottom of `COMPILATION.md` if `hasattr(engine, 'some_new_method')` comes back `False` after a rebuild.

Quick syntax-only check without building the wheel: `cargo check` (from `rust_engine/`).

### Run the app

```powershell
.venv\Scripts\python.exe run.py
```

`run.py` also exposes two headless CLIs (no GUI/Qt window): `run.py --golf <in.frag> <out.frag> [--no-rename] [--no-dead-code]` and `run.py --export-mp4 <project.json> <out.mp4> --duration 10 --fps 30 --crf 23 [--width W --height H]`.

### Tests

Rust (from `rust_engine/`):
```powershell
cargo test --release
```

Python (from repo root, every test file lives in `tests/`, each one a standalone script, not pytest-based — run individually or via the loop below):
```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python tests/test_i18n.py
python tests/test_i18n_completeness.py
python tests/test_shadertoy_import.py
python tests/test_video_export.py
python tests/test_keyframe_reset_bugfix.py
python tests/test_sliders.py
python tests/test_export_video_dialog.py
python tests/test_literals_native.py
python tests/test_dialect_detection.py
```
`tests/test_glsl_manuel.md` is a manual test checklist, not an automated test. `QT_QPA_PLATFORM=offscreen` is required since the CI runner (and often local dev) has no display attached. The exact list of test files + rationale for each lives in `.github/workflows/ci.yml`; keep both in sync when adding a new Python test file (new ones belong in `tests/` too). The native module (`shadertoy_engine`) must be built and importable before any Python test runs.

### Release build (installer)

Full procedure is in `COMPILATION.md` §5 — briefly: `packaging\build_release.ps1` runs a release Rust build + PyInstaller (`packaging\petit_editeur_glsl.spec`, onedir mode) producing `dist\PetitEditeurGLSL\`, then Inno Setup (`packaging\installer.iss`) packages that folder into `packaging\output\PetitEditeurGLSL-Setup-<version>.exe`. Needs `packaging\bin\ffmpeg.exe` (not versioned, fetch per `COMPILATION.md` §3bis) for video export to work in the packaged build.

## Architecture: adding a new input dialect

The engine currently detects and compiles three input dialects: `shadertoy` (`mainImage` wrapper), `glsl` (standalone `void main()`), and `wgsl` (`@fragment fn ...`). **`ARCHITECTURE.md` is the authoritative step-by-step procedure** for adding a fourth — read it in full before touching `dialect.rs`/`shader.rs`. Summary of the three independent places it touches, in order:

1. **Detection** (`rust_engine/src/dialect.rs`) — add a `ShaderDialect` variant + stable lowercase id, a `DialectSignal` with a unique confidence score (a test enforces strictly-ordered unique scores across the registry), and a `matches_xxx` detector function added to the `DETECTORS` registry. `detect_dialect` itself needs no changes — it already scores every registered detector generically.
2. **Compilation** (`rust_engine/src/shader.rs`) — a backend function matching `CompileBackendFn`, added to `COMPILE_BACKENDS` under the same id. `renderer.rs`/`lib.rs` need no changes — they already dispatch through `shader::compile_backend_for(dialect)` rather than a hardcoded match.
3. **i18n/display** (`lngs/*.json`, `python_ui/ui/footer.py`) — add `footer.dialect_<id>` and any new `footer.dialect_signal_<name>` keys in strict parity across **all 12** language files (de/en/es/fr/hi/it/ja/ko/no/pt/sv/zh — `test_i18n_completeness.py` enforces this), plus an entry in `footer.py::_DIALECT_DISPLAY` and a new pyo3 constant in `lib.rs` next to `DIALECT_SHADERTOY`/`DIALECT_GLSL`/`DIALECT_WGSL`.

**Input dialect vs. export target are different procedures.** A language only qualifies for the above if `naga` has a *frontend* for it (can parse it) — that's what makes it something a user can paste into the editor and have compiled/rendered live. A language with only a `naga` *backend* (write-only, e.g. HLSL/MSL) is never added to `ShaderDialect`, never appears in the footer dialect indicator, and is instead wired as a one-shot export feature (`shader::export_shader_as`/`ExportTarget`, *File → Export compiled shader to…*) — see `RMLG.md` §2 for that procedure and the feasibility matrix (`naga` frontends/backends per language) backing this split.

## Autorisation d'exécution locale (RM10 — route vers la 1.0.0)

Dans le cadre du travail sur `RM10.md`, l'utilisateur a donné son accord explicite pour que
Claude Code **lance directement le logiciel et l'ensemble des suites de tests sur sa machine**,
sans redemander confirmation à chaque fois :

- Build/rebuild du moteur (`maturin develop --release`) autant que nécessaire.
- Lancement de l'application graphique (`run.py`) pour vérifier à l'œil un comportement listé
  dans `RM10.md` (une case de ce fichier ne doit être cochée qu'après vérification réelle dans le
  logiciel lancé, jamais seulement "en théorie").
- Exécution des CLIs headless (`run.py --golf`, `run.py --export-mp4`) pour valider les exports.
- Exécution de `cargo test --release` et de l'ensemble des scripts `test_*.py` listés plus haut.
- Build complet de l'installateur (`packaging\build_release.ps1`) quand une vérification de
  RM10 §16 (installation/mise à jour/désinstallation) l'exige.

Cette autorisation vaut spécifiquement pour des actions **locales et non destructrices** vis-à-vis
du reste du système (lancer, tester, builder, lire des fichiers). Elle ne couvre pas des actions
qui sortiraient de ce périmètre (modifications système globales, envoi de données, suppression de
fichiers hors du dépôt) — dans ces cas, redemander confirmation reste la règle.

## Other things worth knowing

- The golfer/minifier (`rust_engine/src/golf.rs`, ~5000 lines — the largest file in the crate) has a safety net: if a golf pass would break compilation, the transform is discarded and the original code is returned untouched. Preserve that guarantee in any change here — `run.py --golf`'s CLI path re-compiles the golfed output before writing it out, for the same reason.
- Multi-pass rendering follows Shadertoy's model: `PASS_BUFFER_A`..`PASS_BUFFER_D` feed into each other and into `PASS_IMAGE` (the only pass actually displayed), plus a shared `Common` source concatenated into every pass. See `engine_bridge.py` for the Python-side constants.
- HLSL/MSL export is a one-time translation of whatever's currently compiled (golfed or not) — it is not a round-trip format and never re-applies golfing itself.
- Monaco Editor is vendored (not npm-fetched at runtime) into `python_ui/assets/monaco/vs`; pin to `monaco-editor@0.52.2` specifically — newer versions changed the `min/vs` build layout and break web worker loading (see `COMPILATION.md` §3).
- CI (`.github/workflows/ci.yml`) runs on `windows-latest` only — this is a Windows-targeted app (packaging, Inno Setup installer, bundled `ffmpeg.exe`), there is no cross-platform support to preserve.
