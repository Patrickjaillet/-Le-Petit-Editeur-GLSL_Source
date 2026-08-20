"""Audio-file source for iChannel slots, Shadertoy-style.

On shadertoy.com an audio channel exposes a 512x2 texture: row 0 is the
frequency spectrum (FFT magnitude, one band per column), row 1 is the
waveform (raw amplitude, downsampled to 512 points) -- see
`renderer::ChannelInput::Audio` and `texture::ChannelTexture::audio` on the
Rust side, which just re-uploads whatever two 512-byte rows this module
hands it every tick.

Same decoding choice as `video_source.py` and for the same reason:
`rust_engine` has no audio-decoding dependency today (ffmpeg/gstreamer/
symphonia) and gaining one just to duplicate what Qt already does isn't
worth it. Unlike a video channel, though, this one is meant to be heard --
a Shadertoy audio channel plays out loud while it drives the shader, that's
the whole point of a music-reactive shader -- so `AudioChannelSource` wires
up a real `QAudioOutput`, not a muted sink.

FFT is done with numpy (`requirements.txt`), not in pure Python: a radix-2
FFT on a 1024-sample window at ~60 Hz would likely be too slow on a modest
machine, and numpy is the simplest way to avoid writing/maintaining one.

Caveat carried over from the rest of this codebase's Qt Multimedia code
(`video_source.py`, `shadertoy_import.py`): this module was written without
a working PySide6 install available in this development environment (no
network egress to PyPI for it here), so the exact `QAudioBuffer` sample-data
access below is based on the documented Qt Multimedia API rather than a
locally-run smoke test -- worth double-checking against a real decoded file
once this lands somewhere PySide6 is actually installed. The spectrum's
dB-to-[0,1] normalization curve is a second, separately-documented
approximation (see the ROADMAP.md entry for this feature): shadertoy.com
doesn't publish its exact scaling formula, so this is calibrated by eye
against known audio-reactive shaders rather than guaranteed bit-exact.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import (
    QAudioBuffer,
    QAudioBufferOutput,
    QAudioFormat,
    QAudioOutput,
    QMediaPlayer,
)

# Formats `QMediaPlayer` decodes natively on most platforms, no extra codec
# to embed -- same reasoning as `video_source.VIDEO_EXTENSIONS`.
AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg", ".flac")

# Samples analyzed per FFT window. `np.fft.rfft` on a real-valued window of
# this size returns 513 bins (0..N/2 inclusive); dropping the Nyquist bin
# lands exactly on the 512-wide texture Shadertoy's audio channel uses, so
# no extra bin-regrouping step is needed downstream.
_FFT_SIZE = 1024
# Width of the iChannel audio texture (both rows) -- must match
# `texture::AUDIO_TEXTURE_WIDTH` on the Rust side.
_TEXTURE_WIDTH = 512

# Empirical dB range mapped to the [0,1] spectrum output -- see the module
# and ROADMAP.md caveats above: no published shadertoy.com formula to match
# bit-exactly, this is a plausible working range for typical music/game
# audio rather than a verified reproduction.
_DB_FLOOR = -60.0
_DB_CEIL = 0.0


class AudioChannelSource(QObject):
    """One live iChannel audio source, bound to a single (pass, channel)
    slot by whoever constructs it -- same one-source-per-slot model as
    `video_source.VideoChannelSource`. Unlike that class, this one has no
    `on_frame` callback fired per decoded chunk: raw samples just
    accumulate into a rolling buffer as they arrive, and `compute_frame()`
    is meant to be called once per UI tick (from `MainWindow`, driven off
    `Viewport.timeUpdated` -- the same ~60fps cadence every other dynamic
    iChannel source ticks on) to FFT/downsample whatever's accumulated
    since the last call and hand back the two texture rows to upload.

    `sourceLost(str)` mirrors `VideoChannelSource.sourceLost` (RM10.md
    section 1, item 6): fires via `QMediaPlayer.errorOccurred` if playback
    fails mid-use (e.g. the file's drive was disconnected), never on our
    own `stop()`."""

    sourceLost = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player: QMediaPlayer | None = None
        self._audio_output: QAudioOutput | None = None
        self._buffer_output: QAudioBufferOutput | None = None
        self._ring = np.zeros(_FFT_SIZE, dtype=np.float32)
        # Precomputed once: same window every call, only the samples
        # inside it change.
        self._hann = np.hanning(_FFT_SIZE).astype(np.float32)
        # RM10.md section 5: independent of the system output volume (see
        # `start`'s docstring) -- kept here, not just on `_audio_output`
        # directly, because `start()` tears down and rebuilds a brand new
        # `QAudioOutput` every time (a different file, or the same file
        # re-picked), which would otherwise silently reset volume/mute
        # back to 100%/unmuted on every reassignment.
        self._volume = 1.0
        self._muted = False

    # ---- volume (independent of the system output volume) --------------

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume))
        if self._audio_output is not None:
            self._audio_output.setVolume(self._volume)

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        if self._audio_output is not None:
            self._audio_output.setMuted(muted)

    # ---- playback -----------------------------------------------------

    def start(self, path: str) -> None:
        """Plays `path` on loop, audibly (at `self._volume`, independent of
        the system output volume) -- deliberately the opposite choice from
        `VideoChannelSource.start_file`, which stays silent: hearing the
        audio while it drives the shader is the entire point of an
        audio-reactive iChannel."""
        self.stop()
        self._ring[:] = 0.0
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(self._volume)
        self._audio_output.setMuted(self._muted)
        self._player.setAudioOutput(self._audio_output)
        self._buffer_output = QAudioBufferOutput(self)
        self._buffer_output.audioBufferReceived.connect(self._on_audio_buffer)
        self._player.setAudioBufferOutput(self._buffer_output)
        self._player.setLoops(QMediaPlayer.Infinite)
        self._player.errorOccurred.connect(lambda _error, message: self.sourceLost.emit(message))
        # `setSource` takes a `QUrl`, and PySide6 will silently coerce a
        # bare Python `str` into one via `QUrl(str)` -- which parses it as
        # a generic URL, not a local path. On Windows that's actively
        # wrong: `QUrl("C:\\...\\track.mp3")` (or even the forward-slash
        # form) reads the drive letter "C" as the URL *scheme*, produces
        # an `isLocalFile() == False` URL, and `QMediaPlayer` then fails
        # to open it with a bare "Could not open file" -- silently, since
        # nothing here ever surfaced that as anything other than "the
        # audio channel just stays silent". `QUrl.fromLocalFile` is the
        # one that actually means "this is a path on disk".
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

    def stop(self) -> None:
        """Releases the player/output/buffer-sink if currently active. Safe
        to call unconditionally, same contract as
        `VideoChannelSource.stop`."""
        if self._player is not None:
            self._player.stop()
            self._player.deleteLater()
            self._player = None
        if self._buffer_output is not None:
            self._buffer_output.deleteLater()
            self._buffer_output = None
        if self._audio_output is not None:
            self._audio_output.deleteLater()
            self._audio_output = None

    def is_active(self) -> bool:
        return self._player is not None

    def position_seconds(self) -> float:
        """Current playback position, for `iChannelTime` -- same idea as
        `QVideoFrame.startTime()` on the video side, just read from the
        player directly since a decoded `QAudioBuffer` doesn't carry its
        own absolute timestamp the way a video frame does."""
        if self._player is None:
            return 0.0
        return self._player.position() / 1000.0

    # ---- decoding -------------------------------------------------------

    def _on_audio_buffer(self, buffer: QAudioBuffer) -> None:
        """Appends one newly decoded chunk to the rolling analysis buffer.
        Fires on the Qt main thread, same as `VideoChannelSource._on_video_frame`
        -- kept cheap (no FFT here), the actual analysis happens on demand
        in `compute_frame()` instead of on every single decoded chunk."""
        if not buffer.isValid():
            return
        fmt = buffer.format()
        channels = fmt.channelCount()
        if channels <= 0 or buffer.sampleCount() <= 0:
            return
        raw = bytes(buffer.constData())
        sample_format = fmt.sampleFormat()
        if sample_format == QAudioFormat.SampleFormat.UInt8:
            samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif sample_format == QAudioFormat.SampleFormat.Int16:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif sample_format == QAudioFormat.SampleFormat.Int32:
            samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif sample_format == QAudioFormat.SampleFormat.Float:
            samples = np.frombuffer(raw, dtype=np.float32).copy()
        else:
            return
        if channels > 1:
            usable = (samples.size // channels) * channels
            samples = samples[:usable].reshape(-1, channels).mean(axis=1)
        if samples.size == 0:
            return
        if samples.size >= _FFT_SIZE:
            self._ring = samples[-_FFT_SIZE:].astype(np.float32, copy=True)
        else:
            self._ring = np.concatenate([self._ring[samples.size:], samples]).astype(np.float32)

    # ---- analysis -------------------------------------------------------

    def compute_frame(self) -> tuple[bytes, bytes]:
        """FFTs the last `_FFT_SIZE` decoded samples (Hann-windowed to
        limit spectral leakage) and returns `(spectrum_bytes, waveform_bytes)`,
        each exactly 512 bytes -- ready to push straight into
        `Engine.update_ichannel_audio_frame`. Returns silence (all zeros /
        all mid-value) before any audio has actually been decoded yet,
        same "defined data before the first real frame" convention as the
        engine's own zero-filled `ChannelTexture::audio`."""
        windowed = self._ring * self._hann
        spectrum_full = np.abs(np.fft.rfft(windowed))  # _FFT_SIZE//2 + 1 bins
        db = 20.0 * np.log10(spectrum_full + 1e-9)
        normalized = np.clip((db - _DB_FLOOR) / (_DB_CEIL - _DB_FLOOR), 0.0, 1.0)
        # Drop the Nyquist bin (index _FFT_SIZE//2) so exactly 512 bands
        # remain, one per texture column, low frequencies on the left --
        # same layout Shadertoy uses.
        spectrum_bytes = (normalized[:_TEXTURE_WIDTH] * 255.0).astype(np.uint8).tobytes()

        # Waveform: the same analysis window, downsampled by a factor of 2
        # (1024 samples -> 512 points), amplitude mapped from [-1,1] to
        # [0,255] around a silence midpoint of 128 -- the same "R=G=B,
        # 0-255" convention as the spectrum row and every other
        # single-channel texture in this engine (iKeyboard included).
        waveform = np.clip(self._ring[::2], -1.0, 1.0)
        waveform_bytes = ((waveform * 127.0) + 128.0).astype(np.uint8).tobytes()
        return spectrum_bytes, waveform_bytes
