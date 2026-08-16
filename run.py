"""Launcher: adds python_ui/ to sys.path and starts the application.

Also doubles as two headless batch CLIs (no GUI/Qt window needed):

    python run.py --golf shader.frag shader.min.frag [--no-rename] [--no-dead-code]

    python run.py --export-mp4 projet.json sortie.mp4 --duration 10 --fps 30 --crf 23 \\
        [--width 1920 --height 1080]

By default both aggressive golf transforms (identifier renaming,
dead-code elimination) are on, matching the editor's own default; pass
either flag to opt out, same choice the in-app golf dialog offers.

`--export-mp4` loads a `.json` project (same format as
`Fichier -> Enregistrer le projet sous...`), builds an `Engine` outside
of any GUI, and renders+encodes it exactly like
`MainWindow._on_export_video` does -- useful for batch-generating preview
videos for a whole folder of shaders without opening the interface once
per file.
"""
import sys
from pathlib import Path

PYTHON_UI_DIR = Path(__file__).resolve().parent / "python_ui"
sys.path.insert(0, str(PYTHON_UI_DIR))


def _run_golf_cli(args: list[str]) -> int:
    import engine_bridge

    rename = "--no-rename" not in args
    dead_code = "--no-dead-code" not in args
    positional = [a for a in args if not a.startswith("--")]
    if len(positional) != 2:
        print(
            "Usage: run.py --golf <entree.frag> <sortie.frag> [--no-rename] [--no-dead-code]",
            file=sys.stderr,
        )
        return 2

    input_path, output_path = Path(positional[0]), Path(positional[1])
    source = input_path.read_text(encoding="utf-8")
    golfed = engine_bridge.golf_shader_ex(source, "", rename, dead_code)

    # Golf-à-froid, same guarantee as the interactive golf button: never
    # write out code that doesn't actually compile.
    try:
        engine_bridge.Engine(64, 64).compile_pass(engine_bridge.PASS_IMAGE, golfed)
    except RuntimeError as exc:
        print(f"Erreur : le code golfé ne compile plus, rien n'a été écrit.\n{exc}", file=sys.stderr)
        return 1

    output_path.write_text(golfed, encoding="utf-8")
    before = len(source.encode("utf-8"))
    after = len(golfed.encode("utf-8"))
    pct = 100.0 * (before - after) / before if before else 0.0
    print(f"{input_path} -> {output_path}: {before} -> {after} octets (-{pct:.0f}%)")
    return 0


def _parse_export_mp4_args(args: list[str]) -> dict | None:
    positional = [a for a in args if not a.startswith("--")]
    if len(positional) != 2:
        return None
    opts: dict[str, str] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--") and i + 1 < len(args):
            opts[a[2:]] = args[i + 1]
            i += 2
        else:
            i += 1
    return {
        "project": positional[0],
        "out": positional[1],
        "duration": float(opts.get("duration", "5")),
        "fps": float(opts.get("fps", "30")),
        "crf": int(opts.get("crf", "23")),
        "width": opts.get("width"),
        "height": opts.get("height"),
    }


def _run_export_mp4_cli(args: list[str]) -> int:
    """Headless `--export-mp4`: loads a `.json` project, builds an
    `Engine` with no GUI/window involved, and runs the exact same
    capture+encode pipeline as the interactive export dialog
    (`video_export.run_export`) -- see the module docstring above.
    """
    import json as _json
    import os

    # `QImage` (used by `video_export.capture_frames` to write PNGs) needs
    # at least a `QGuiApplication` instance to exist, even with no window
    # ever shown -- `offscreen` keeps this fully headless (no X server/
    # Wayland/Windows session required), matching how the existing test
    # suite (`test_video_export.py`) already runs Qt-less.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    import engine_bridge
    import video_export

    _qapp = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])

    parsed = _parse_export_mp4_args(args)
    if parsed is None:
        print(
            "Usage: run.py --export-mp4 <projet.json> <sortie.mp4> --duration 10 --fps 30 "
            "--crf 23 [--width 1920 --height 1080]",
            file=sys.stderr,
        )
        return 2

    project_path = Path(parsed["project"])
    data = _json.loads(project_path.read_text(encoding="utf-8"))
    common_source = data.get("common", "")
    pass_sources = {int(k): v for k, v in data.get("passes", {}).items()}
    ichannels = data.get("ichannels", {})

    default_width, default_height = 1280, 720
    width = int(parsed["width"]) if parsed["width"] else default_width
    height = int(parsed["height"]) if parsed["height"] else default_height

    engine = engine_bridge.Engine(width, height)
    engine.set_common(common_source)
    for pass_idx in engine_bridge.ALL_PASSES:
        source = pass_sources.get(pass_idx, "")
        if source:
            engine.compile_pass(pass_idx, source)

    # Only the iChannel kinds that don't need a live Qt widget behind them
    # (a decoded video frame stream, an open webcam device) are supported
    # headless -- video/webcam sources are skipped with a warning rather
    # than silently rendering black or crashing on a missing QApplication.
    skipped_video_slots = 0
    for pass_index_str, states in ichannels.items():
        pass_index = int(pass_index_str)
        for channel_index, item in enumerate(states):
            kind, value = item.get("kind"), item.get("value")
            try:
                if kind == "image" and value:
                    engine.set_ichannel_texture(pass_index, channel_index, value)
                elif kind == "cubemap" and value:
                    engine.set_ichannel_cubemap(pass_index, channel_index, value)
                elif kind == "procedural" and value:
                    engine.set_ichannel_procedural(pass_index, channel_index, value)
                elif kind == "buffer" and value is not None:
                    engine.set_ichannel_buffer(pass_index, channel_index, value)
                elif kind == "keyboard":
                    engine.set_ichannel_keyboard(pass_index, channel_index)
                elif kind in ("video", "webcam"):
                    skipped_video_slots += 1
            except RuntimeError as exc:
                print(f"Avertissement iChannel [{pass_index}][{channel_index}] : {exc}", file=sys.stderr)
    if skipped_video_slots:
        print(
            f"Avertissement : {skipped_video_slots} iChannel(s) vidéo/webcam ignoré(s) "
            "(non disponibles en export CLI headless).",
            file=sys.stderr,
        )

    n_frames = max(1, round(parsed["duration"] * parsed["fps"]))
    date = (2026.0, 1.0, 1.0, 0.0)

    def _on_capture(done: int, total: int) -> None:
        print(f"\rRendu : {done}/{total} frames", end="", file=sys.stderr, flush=True)

    def _on_encode(done: int, total: int) -> None:
        print(f"\rEncodage : {done}/{total} frames", end="", file=sys.stderr, flush=True)

    try:
        video_export.run_export(
            engine,
            n_frames,
            parsed["fps"],
            width,
            height,
            parsed["crf"],
            date,
            parsed["out"],
            on_capture_progress=_on_capture,
            on_encode_progress=_on_encode,
        )
    except Exception as exc:  # noqa: BLE001 - reported verbatim to the CLI caller
        print(f"\nErreur : {exc}", file=sys.stderr)
        return 1

    print(f"\n{parsed['project']} -> {parsed['out']} ({n_frames} frames, {width}x{height} @ {parsed['fps']:g} fps)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--golf":
        sys.exit(_run_golf_cli(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--export-mp4":
        sys.exit(_run_export_mp4_cli(sys.argv[2:]))

    from main import main  # noqa: E402

    sys.exit(main())
