"""Exercises `video_export.capture_frames` against a fake `Engine` that
reproduces the real one-frame-behind readback pipelining
(`renderer.rs::resolve_readback`) without needing the compiled Rust
extension — see COMPILATION.md for why the native module isn't built in
this environment.
"""
import shutil
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python_ui"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtGui import QGuiApplication, QImage

app = QGuiApplication.instance() or QGuiApplication([])

from video_export import capture_frames, FIXED_MOUSE  # noqa: E402

WIDTH, HEIGHT = 2, 2
DATE = (2026.0, 0.0, 1.0, 0.0)


class FakeEngine:
    """Mirrors `Engine::render`'s pipelined readback: each call submits
    the parameters it was given, but *returns* the pixels rendered for the
    *previous* call's parameters (an all-zero bootstrap frame the very
    first time, matching `pending_readback.replace(current)` returning
    `None` on the first call in `renderer.rs`)."""

    def __init__(self):
        self.calls = []  # every (time, time_delta, mouse, frame, date) submitted
        self._pending = None

    def render(self, time, time_delta, mouse, frame, date):
        self.calls.append((time, time_delta, mouse, frame, date))
        # Encode `frame` and a scaled `time` into the pixel bytes so the
        # saved PNG can be checked against exactly what was submitted.
        current = bytes([frame % 256, int(round(time * 100)) % 256, 0, 255] * (WIDTH * HEIGHT))
        previous = self._pending
        self._pending = current
        if previous is None:
            return bytes(WIDTH * HEIGHT * 4)  # bootstrap: all zero
        return previous


def pixel_frame_byte(png_path):
    img = QImage(png_path)
    assert not img.isNull(), f"failed to load {png_path}"
    px = img.pixelColor(0, 0)
    return px.red(), px.green()  # (frame % 256, round(time*100) % 256)


engine = FakeEngine()
n_frames, fps = 5, 25.0
out_dir = capture_frames(engine, n_frames, fps, WIDTH, HEIGHT, DATE)

try:
    # n_frames + 1 render() calls: one bootstrap-priming call plus one per
    # saved frame, exactly as the readback-offset discount requires.
    assert len(engine.calls) == n_frames + 1, engine.calls

    files = sorted(p.name for p in out_dir.iterdir())
    assert files == [f"frame_{i:06d}.png" for i in range(n_frames)], files

    # Frame k on disk must hold the pixels *submitted* for logical frame k
    # (iFrame=k, iTime=k/fps) -- not the parameters of whichever render()
    # call happened to return them a call later.
    for k in range(n_frames):
        red, green = pixel_frame_byte(str(out_dir / f"frame_{k:06d}.png"))
        assert red == k % 256, (k, red)
        expected_time_byte = int(round((k / fps) * 100)) % 256
        assert green == expected_time_byte, (k, green, expected_time_byte)

    # iTime/iFrame/mouse actually submitted to the engine, across the whole
    # n_frames+1 call sequence (including the discarded priming call at i=0).
    for i, (time, dt, mouse, frame, date) in enumerate(engine.calls):
        assert frame == i, (i, frame)
        assert abs(time - i / fps) < 1e-9, (i, time)
        assert abs(dt - 1.0 / fps) < 1e-9, dt
        assert mouse == FIXED_MOUSE, mouse
        assert date == DATE, date

    print("ALL OK")
finally:
    shutil.rmtree(out_dir, ignore_errors=True)

# ---- edge cases ---------------------------------------------------------

try:
    capture_frames(FakeEngine(), 0, 25.0, WIDTH, HEIGHT, DATE)
    raise AssertionError("n_frames=0 should have raised")
except ValueError:
    pass

try:
    capture_frames(FakeEngine(), 5, 0.0, WIDTH, HEIGHT, DATE)
    raise AssertionError("fps=0 should have raised")
except ValueError:
    pass

# ---- RM10.md section 8: cancelling mid-encode leaves no partial file and
# no ghost ffmpeg process --------------------------------------------------
#
# Needs the real bundled ffmpeg.exe (packaging/bin/, not committed to git,
# see COMPILATION.md) -- absent (e.g. a plain CI checkout with no release
# assets fetched), SKIPs cleanly rather than failing, same convention as
# the native-module-dependent tests elsewhere in this suite.

import subprocess  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from video_export import ExportCancelled, FRAME_FILENAME_TEMPLATE, encode_frames_to_mp4, resolve_ffmpeg_path  # noqa: E402

ffmpeg_path = resolve_ffmpeg_path()
if not ffmpeg_path.is_file():
    print(f"SKIPPED: ffmpeg.exe not found at {ffmpeg_path}; see COMPILATION.md section 3bis.")
else:
    def _count_ffmpeg_processes() -> int:
        # Windows-only app (see CLAUDE.md) -- `tasklist` is always
        # available, no extra dependency needed just for this check.
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True,
        ).stdout
        return sum(1 for line in out.splitlines() if "ffmpeg.exe" in line.lower())

    cancel_frames_dir = Path(tempfile.mkdtemp(prefix="peg_cancel_frames_"))
    try:
        cw, ch = 320, 240
        for i in range(30):
            img = QImage(cw, ch, QImage.Format_RGBA8888)
            img.fill((0xFF000000 | ((i * 8) << 8)) & 0xFFFFFFFF)
            img.save(str(cancel_frames_dir / (FRAME_FILENAME_TEMPLATE % i)), "PNG")

        cancel_out_dir = Path(tempfile.mkdtemp(prefix="peg_cancel_out_"))
        cancel_out_path = cancel_out_dir / "out.mp4"
        try:
            before = _count_ffmpeg_processes()
            # Always-true should_cancel: the encode loop checks it as its
            # very first action on every iteration (before reading any
            # ffmpeg progress line), so this reliably exercises the real
            # terminate()/kill() path on a subprocess that's still alive
            # -- not a race against how fast this particular encode
            # happens to finish.
            try:
                encode_frames_to_mp4(
                    cancel_frames_dir, cancel_out_path, fps=30, crf=30, should_cancel=lambda: True,
                )
                raise AssertionError("expected ExportCancelled")
            except ExportCancelled:
                pass
            assert not cancel_out_path.exists(), "cancelled encode must not leave a partial output file"
            after = _count_ffmpeg_processes()
            assert after <= before, (
                f"ffmpeg process count grew after a cancelled encode ({before} -> {after}) "
                "-- looks like a ghost process was left running"
            )
        finally:
            shutil.rmtree(cancel_out_dir, ignore_errors=True)
    finally:
        shutil.rmtree(cancel_frames_dir, ignore_errors=True)

    print("VIDEO EXPORT CANCELLATION OK")

print("EDGE CASES OK")
