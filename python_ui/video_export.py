"""Frame-by-frame capture of the currently open project into a sequence of
numbered PNG files, plus ffmpeg encoding of that sequence into an .mp4 —
together, the full "🎬 Export vidéo" pipeline described in ROADMAP.md.

`capture_frames` drives the GPU rendering loop; `encode_frames_to_mp4`
drives the `ffmpeg` subprocess; `run_export` is the orchestrator that
chains the two into a single call with one shared cancellation flag and a
guarantee that the temporary PNG sequence never survives the call (success,
failure, or cancellation alike). The export dialog / progress bar UI layer
(`ui/export_video_dialog.py`, `ui/export_progress_dialog.py`) is what
actually drives this module from the GUI; see those for how `on_progress`/
`should_cancel` get wired to a Cancel button and a progress bar.

Deliberately reuses the caller's already-instantiated `Engine` (same
compiled passes, same iChannels/buffers already assigned) instead of
spinning up a throwaway one like the cold-golf export does — the export
must reflect the project as currently open, not a bare recompile of the
Image pass alone.
"""
from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtGui import QImage

# Shadertoy convention: `iMouse` is (last/held xy, click xy — negative once
# released). No mouse trajectory is recorded for an export (yet), so it's
# held fixed for every frame; a shader that leans heavily on mouse
# interaction will export with a frozen position. This is a known,
# documented limitation (surfaced in the export dialog once it exists),
# not an oversight.
FIXED_MOUSE: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

FRAME_FILENAME_TEMPLATE = "frame_%06d.png"


class ExportCancelled(Exception):
    """Raised by `capture_frames`/`encode_frames_to_mp4`/`run_export` the
    moment a caller-supplied `should_cancel()` callback returns `True`.
    Not an error: `run_export` catches this itself to run its cleanup and
    re-raises it so the UI layer can tell "cancelled" apart from "failed"
    (e.g. to skip showing an error dialog for something the user asked
    for)."""


def capture_frames(
    engine,
    n_frames: int,
    fps: float,
    width: int,
    height: int,
    date: tuple[float, float, float, float],
    mouse: tuple[float, float, float, float] = FIXED_MOUSE,
    out_dir: str | Path | None = None,
    on_frame_rendered: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Renders `n_frames` deterministic frames from `engine` and writes
    each one to `frame_%06d.png` (0-indexed) inside a fresh temporary
    directory, whose path is returned.

    `engine` must already be at (`width`, `height`) resolution (call
    `engine.resize(width, height)` first if the export resolution differs
    from whatever the live viewport is currently showing) and already have
    every pass compiled and every iChannel assigned — this function only
    drives `render()`, it never touches compilation or channel state.

    Unlike the live viewport, which feeds `render()` wall-clock time and a
    frame counter measured from a `QElapsedTimer`, every frame here is
    computed from `i` alone: `iTime = i / fps`, `iFrame = i`. That's what
    makes a re-export of the same project byte-for-byte reproducible
    regardless of how fast the GPU happened to be while capturing —
    contrary to the real-time preview, nothing here depends on measured
    wall-clock speed.

    Readback-pipelining discount
    -----------------------------
    `Engine.render()` pipelines its GPU→CPU readback one frame behind
    (`renderer.rs::resolve_readback`): each call submits the frame for the
    parameters just passed in, but *returns* the pixels of the
    *previous* call's parameters (or an all-zero bootstrap frame, the very
    first time). That one-frame latency is invisible in a live preview —
    nobody notices the on-screen image lagging the wall clock by ~16ms —
    but it would be wrong here: naively pairing call `i`'s *return value*
    with call `i`'s *own* `iTime`/`iFrame` would silently save frame `i`'s
    pixels under frame `i-1`'s buffer-feedback state (or vice versa),
    shifting every multi-pass feedback effect by a frame against the
    animation clock actually used to render it.

    Rather than adding a blocking render mode on the Rust side, this
    renders `n_frames + 1` frames and discards the very first return
    value (the bootstrap frame, which corresponds to no real submitted
    frame): call `i` submits logical frame `i`'s parameters and its
    *return value* is logical frame `i - 1`'s actual pixels, so after
    the loop finishes, saved frame `k` (for `k` in `0 .. n_frames - 1`)
    holds exactly the pixels rendered from `iTime = k / fps`,
    `iFrame = k` — pixel-identical to what a hypothetical blocking
    `render_blocking(k / fps, ...)` would have returned directly.

    `should_cancel`, if given, is polled once per iteration (before each
    `render()` call); the instant it returns `True`, `ExportCancelled` is
    raised and no more frames are rendered or saved. The frames already
    written to `dest` before that point are left on disk — cleanup is
    `run_export`'s job (it owns the temp directory for the whole
    capture+encode pipeline), not this function's, so that a caller who
    passed its own `out_dir` (as the existing tests do) isn't surprised by
    files vanishing out from under it.
    """
    if n_frames <= 0:
        raise ValueError("n_frames must be positive")
    if fps <= 0:
        raise ValueError("fps must be positive")

    dest = Path(out_dir) if out_dir is not None else Path(tempfile.mkdtemp(prefix="petit_editeur_glsl_export_"))
    dest.mkdir(parents=True, exist_ok=True)
    time_delta = 1.0 / fps

    for i in range(n_frames + 1):
        # Checked *before* rendering the next frame, not after saving the
        # previous one: a shader that's expensive per-frame (heavy
        # raymarching, high resolution) must stop the GPU loop
        # immediately on Cancel, not finish one more render() call it no
        # longer needs.
        if should_cancel is not None and should_cancel():
            raise ExportCancelled()
        time = i / fps
        pixels = engine.render(time, time_delta, mouse, i, date)
        if i == 0:
            # This return value is the all-zero bootstrap frame from
            # before any real frame was submitted, not logical frame 0's
            # actual pixels — discarded to absorb the one-frame readback
            # offset (see docstring above).
            continue

        saved_frame_index = i - 1
        image = QImage(pixels, width, height, QImage.Format_RGBA8888)
        frame_path = dest / (FRAME_FILENAME_TEMPLATE % saved_frame_index)
        if not image.save(str(frame_path), "PNG"):
            raise RuntimeError(f"Échec de l'écriture de {frame_path}")

        if on_frame_rendered is not None:
            on_frame_rendered(saved_frame_index, n_frames)

    return dest


# --- ffmpeg encoding -------------------------------------------------------

_FFMPEG_EXE_NAME = "ffmpeg.exe"


def resolve_ffmpeg_path() -> Path:
    """Locates the bundled `ffmpeg.exe` — never a bare `"ffmpeg"` relying
    on the user's `PATH`, so the export works identically on a dev
    machine and on a fresh install that has never heard of ffmpeg.

    Two cases:
      - **Packaged** (`sys.frozen`, set by the PyInstaller bootloader):
        `petit_editeur_glsl.spec` copies `packaging/bin/ffmpeg.exe` to the
        root of the onedir bundle, right next to `PetitEditeurGLSL.exe` —
        so it's resolved relative to `sys.executable`'s own directory.
      - **Development** (plain `python run.py`): the same
        `packaging/bin/ffmpeg.exe` is used directly from the source tree
        rather than keeping a second copy under `python_ui/` — one ~100 MB
        binary to keep in sync, not two.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        # python_ui/video_export.py -> project root -> packaging/bin
        base = Path(__file__).resolve().parent.parent / "packaging" / "bin"
    return base / _FFMPEG_EXE_NAME


def _count_frames(frames_dir: Path) -> int:
    return sum(1 for _ in frames_dir.glob("frame_*.png"))


def encode_frames_to_mp4(
    frames_dir: str | Path,
    out_path: str | Path,
    fps: float,
    crf: int,
    ffmpeg_path: str | Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    """Encodes the `frame_%06d.png` sequence in `frames_dir` (as produced
    by `capture_frames`) into `out_path` with libx264, forcing
    `-pix_fmt yuv420p` explicitly (not libx264's own default) so the
    output plays back in essentially every player/social network,
    including ones that don't handle a 4:4:4 encode from RGBA8 source by
    default.

    Progress is read from `-progress pipe:1 -nostats`: ffmpeg writes
    plain `key=value` lines to stdout (one block per output frame, ended
    by a `progress=continue`/`progress=end` line) — far simpler and more
    stable to parse than the human-oriented status line it would
    otherwise print to a terminal.

    Cancellation: a background thread continuously drains ffmpeg's
    stdout into a queue (so a slow/quiet encode never blocks the
    should_cancel poll below it), while this function polls
    `should_cancel()` on a short timeout between queue reads. The instant
    it returns `True`, the subprocess is asked to stop via
    `Popen.terminate()`, escalated to `Popen.kill()` if it hasn't exited
    within a few seconds, any partial output file is deleted, and
    `ExportCancelled` is raised — mirroring `capture_frames`'s immediate
    stop on cancel, just one process-boundary removed.
    """
    frames_dir = Path(frames_dir)
    out_path = Path(out_path)
    resolved_ffmpeg = Path(ffmpeg_path) if ffmpeg_path is not None else resolve_ffmpeg_path()
    if not resolved_ffmpeg.is_file():
        raise FileNotFoundError(
            f"ffmpeg introuvable ({resolved_ffmpeg}). "
            "Voir COMPILATION.md, section « Export vidéo » : le binaire attendu "
            "est packaging/bin/ffmpeg.exe."
        )

    total_frames = _count_frames(frames_dir)
    cmd = [
        str(resolved_ffmpeg),
        "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / FRAME_FILENAME_TEMPLATE),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-progress", "pipe:1",
        "-nostats",
        str(out_path),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    # A plain blocking `for line in proc.stdout` in this same thread would
    # make the should_cancel poll below only run between ffmpeg progress
    # lines — fine most of the time (ffmpeg emits one per output frame),
    # but a background pump thread keeps Cancel responsive even on an
    # unusually slow/stalled encode instead of relying on that.
    line_queue: "queue.Queue[str | None]" = queue.Queue()

    def _pump_stdout() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line_queue.put(line)
        finally:
            line_queue.put(None)  # sentinel: stdout closed, process about to exit

    reader = threading.Thread(target=_pump_stdout, daemon=True)
    reader.start()

    # ffmpeg always writes a fair amount to stderr (banner, encoder
    # config, warnings) even with `-nostats` -- `-nostats` only silences
    # the periodic human-readable status line, not general logging. That
    # output was never read here on the success path (only on failure,
    # via `proc.stderr.read()` below), which is a classic subprocess
    # deadlock: once ffmpeg fills the OS pipe buffer for stderr, it
    # blocks on the next write() and never exits, so `proc.wait()` below
    # hangs forever even though `-progress` already reported 100% on
    # stdout -- exactly the "stuck at 100%, dialog never closes" symptom.
    # Draining stderr into its own buffer, continuously, on its own
    # thread, is what prevents that.
    stderr_lines: list[str] = []

    def _pump_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)

    stderr_reader = threading.Thread(target=_pump_stderr, daemon=True)
    stderr_reader.start()

    cancelled = False
    last_frame = 0
    try:
        while True:
            if should_cancel is not None and should_cancel():
                cancelled = True
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                break
            try:
                line = line_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            line = line.strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key == "frame":
                try:
                    last_frame = int(value)
                except ValueError:
                    pass
                if on_progress is not None:
                    on_progress(min(last_frame, total_frames), total_frames)
            elif key == "progress" and value == "end":
                break
    finally:
        reader.join(timeout=2)
        returncode = proc.wait()
        # stderr keeps arriving until the process actually exits, so this
        # join only makes sense (and is only guaranteed to finish
        # promptly) after `proc.wait()` above has returned.
        stderr_reader.join(timeout=2)

    if cancelled:
        if out_path.exists():
            out_path.unlink()
        raise ExportCancelled()

    if returncode != 0:
        stderr_output = "".join(stderr_lines)
        if out_path.exists():
            out_path.unlink()
        raise RuntimeError(f"ffmpeg a échoué (code {returncode}) :\n{stderr_output.strip()}")

    if on_progress is not None:
        on_progress(total_frames, total_frames)


# --- orchestration ----------------------------------------------------------

def run_export(
    engine,
    n_frames: int,
    fps: float,
    width: int,
    height: int,
    crf: int,
    date: tuple[float, float, float, float],
    out_path: str | Path,
    mouse: tuple[float, float, float, float] = FIXED_MOUSE,
    ffmpeg_path: str | Path | None = None,
    on_capture_progress: Callable[[int, int], None] | None = None,
    on_encode_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    """Runs the full capture → encode pipeline into a single call: renders
    `n_frames` into a fresh temporary directory, encodes that sequence
    with ffmpeg into `out_path`, and unconditionally removes the
    temporary PNG sequence afterwards — on success, on a raised error, or
    on cancellation alike. This is the one entry point the export dialog
    and the headless CLI (`run.py --export-mp4`) both call; neither has
    to know the frame sequence ever touched disk as an intermediate step.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="petit_editeur_glsl_export_"))
    try:
        capture_frames(
            engine,
            n_frames,
            fps,
            width,
            height,
            date,
            mouse=mouse,
            out_dir=tmp_dir,
            on_frame_rendered=on_capture_progress,
            should_cancel=should_cancel,
        )
        encode_frames_to_mp4(
            tmp_dir,
            out_path,
            fps,
            crf,
            ffmpeg_path=ffmpeg_path,
            on_progress=on_encode_progress,
            should_cancel=should_cancel,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
