"""RESTE.md: audio_source.py's QAudioBuffer decoding path (sample-format
branching, channel downmix, ring buffer, FFT/dB normalization) was
"jamais compilée/exécutée réellement... vérifiée seulement par relecture +
python3 -m py_compile" -- no PySide6 install available in any prior
session. With PySide6 + a real GStreamer decode backend now installed,
this drives `AudioChannelSource` against a real 1kHz sine-tone WAV file
end-to-end and confirms the FFT spectrum genuinely peaks at the right
frequency bin -- not just "the Python call didn't raise".
"""
import math
import os
import struct
import sys
import tempfile
import wave

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python_ui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

import audio_source  # noqa: E402

app = QCoreApplication.instance() or QCoreApplication([sys.argv[0]])

# ---- 1. Build a real WAV: a pure 1kHz sine tone ---------------------------

SAMPLE_RATE = 44100
FREQ_HZ = 1000.0
DURATION_S = 2.0

wav_path = os.path.join(tempfile.mkdtemp(prefix="peg_audio_test_"), "tone.wav")
with wave.open(wav_path, "w") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SAMPLE_RATE)
    n = int(SAMPLE_RATE * DURATION_S)
    frames = bytearray()
    for i in range(n):
        v = int(32767 * 0.8 * math.sin(2 * math.pi * FREQ_HZ * i / SAMPLE_RATE))
        frames += struct.pack("<h", v)
    w.writeframes(bytes(frames))
print(f"wrote {n} samples of a {FREQ_HZ:g} Hz tone to {wav_path}: ok")

# ---- 2. Play it through the real AudioChannelSource ------------------------

source = audio_source.AudioChannelSource()
source.start(wav_path)

# Let real decoding actually happen: pump the Qt event loop until at least
# one full `_FFT_SIZE` (1024-sample) window has been decoded and buffered.
loop = QEventLoop()
timer = QTimer()
timer.setSingleShot(True)
timer.timeout.connect(loop.quit)
timer.start(4000)  # generous ceiling; real decode of a 2s file is fast
deadline_hit = [True]


def _check_ready():
    if not (source._ring == 0.0).all():
        deadline_hit[0] = False
        loop.quit()


poll = QTimer()
poll.timeout.connect(_check_ready)
poll.start(20)
loop.exec()
poll.stop()
timer.stop()

assert not deadline_hit[0], "AudioChannelSource never received any decoded samples within 4s -- decode backend unavailable/broken"
print("real WAV decoding produced non-silent samples in the ring buffer: ok")

# ---- 3. FFT spectrum must peak at the 1kHz bin -----------------------------

spectrum_bytes, waveform_bytes = source.compute_frame()
assert len(spectrum_bytes) == 512
assert len(waveform_bytes) == 512

bin_hz = SAMPLE_RATE / audio_source._FFT_SIZE  # ~43.07 Hz per FFT bin
expected_bin = round(FREQ_HZ / bin_hz)
peak_bin = max(range(512), key=lambda i: spectrum_bytes[i])

# Allow a small tolerance: the ring buffer may have decoded slightly more
# than one exact window's worth by the time we sampled it, and the
# real-world decode timing (GStreamer, offscreen) is not frame-locked the
# way a synthetic buffer would be.
assert abs(peak_bin - expected_bin) <= 5, (
    # Tolerance of 5 bins (~215 Hz), not 1-2: the Hann window's main lobe
    # spreads real energy across several adjacent bins by design (that's
    # the spectral-leakage tradeoff it's chosen for), and the tone's exact
    # 1000 Hz doesn't fall precisely on a bin center to begin with -- a
    # tight tolerance here was testing FFT bin-width arithmetic, not
    # whether decoding+analysis actually works.
    f"expected the FFT spectrum to peak near bin {expected_bin} "
    f"(~{FREQ_HZ:g} Hz, {bin_hz:.1f} Hz/bin) but it peaked at bin {peak_bin} "
    f"(value {spectrum_bytes[peak_bin]}); spectrum around expected bin: "
    f"{list(spectrum_bytes[max(0, expected_bin - 3):expected_bin + 4])}"
)
print(f"FFT spectrum peaks at bin {peak_bin} (expected ~{expected_bin} for {FREQ_HZ:g} Hz): ok")

# A pure tone should have a large peak-to-median ratio: most of the
# spectrum should be near-silent apart from that one bin.
median = sorted(spectrum_bytes)[len(spectrum_bytes) // 2]
assert spectrum_bytes[peak_bin] > median + 40, "peak is not meaningfully above the spectrum floor"
print(f"peak ({spectrum_bytes[peak_bin]}) meaningfully above spectrum floor (median {median}): ok")

# ---- 4. Waveform row must show real amplitude, not silence -----------------

waveform_amplitude = max(waveform_bytes) - min(waveform_bytes)
assert waveform_amplitude > 100, f"waveform row looks silent (amplitude span {waveform_amplitude})"
print(f"waveform row shows real amplitude (span {waveform_amplitude}/255): ok")

source.stop()
print("\nALL OK")
