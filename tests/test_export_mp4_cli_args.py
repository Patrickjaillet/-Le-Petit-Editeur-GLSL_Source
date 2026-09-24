"""RESTE.md: `python run.py --export-mp4 ...` had never been exercised
end-to-end in any session (no ffmpeg binary available, `MainWindow`
non-instantiable). Running it for real (real ffmpeg, real GPU via
lavapipe) surfaced a genuine bug in `run.py::_parse_export_mp4_args`: it
collected "every argument not starting with `--`" as positional, which
also swept up each value-bearing flag's own value (`--duration 10` ->
`10` looked exactly like a bare positional token) -- breaking the CLI for
any invocation that actually uses `--duration`/`--fps`/`--crf`/`--width`/
`--height`, i.e. the exact usage documented in this module's own
docstring. `--golf`'s sibling CLI never hit this because its own flags
(`--no-rename`/`--no-dead-code`) are boolean, carrying no value to be
confused with a positional.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from run import _parse_export_mp4_args  # noqa: E402

# ---- 1. The documented usage must parse correctly -------------------------

parsed = _parse_export_mp4_args(
    ["projet.json", "sortie.mp4", "--duration", "10", "--fps", "30", "--crf", "23", "--width", "1920", "--height", "1080"]
)
assert parsed is not None, "documented usage must not be rejected"
assert parsed["project"] == "projet.json"
assert parsed["out"] == "sortie.mp4"
assert parsed["duration"] == 10.0
assert parsed["fps"] == 30.0
assert parsed["crf"] == 23
assert parsed["width"] == "1920"
assert parsed["height"] == "1080"
print("documented usage (duration/fps/crf/width/height) parses correctly: ok")

# ---- 2. Minimal usage (defaults only) --------------------------------------

parsed = _parse_export_mp4_args(["projet.json", "sortie.mp4"])
assert parsed is not None
assert parsed["duration"] == 5.0  # documented default
assert parsed["fps"] == 30.0
assert parsed["crf"] == 23
assert parsed["width"] is None
assert parsed["height"] is None
print("minimal usage (no optional flags) parses with documented defaults: ok")

# ---- 3. Audio flags (also value-bearing, same failure mode) ---------------

parsed = _parse_export_mp4_args(
    ["projet.json", "sortie.mp4", "--audio", "musique.mp3", "--audio-volume", "-6", "--audio-start", "1.5", "--audio-loop", "0", "--audio-bitrate", "128"]
)
assert parsed is not None
assert parsed["audio"] == "musique.mp3"
assert parsed["audio_volume"] == -6.0
assert parsed["audio_start"] == 1.5
assert parsed["audio_loop"] is False
assert parsed["audio_bitrate"] == 128
print("audio flags parse correctly: ok")

# ---- 4. Wrong number of positional arguments is still rejected ------------

assert _parse_export_mp4_args(["only_one.json"]) is None
assert _parse_export_mp4_args(["a.json", "b.mp4", "c_extra"]) is None
assert _parse_export_mp4_args([]) is None
print("wrong positional argument count still rejected: ok")

print("\nALL OK")
