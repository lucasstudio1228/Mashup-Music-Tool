"""Shared helpers: sinh WAV tổng hợp để test toàn bộ pipeline."""
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


def generate_test_wav(
    path: str,
    duration_sec: float = 30.0,
    sample_rate: int = 48000,
    channels: int = 2,
    frequency: float = 432.0,   # Hz - tần số thiền định
    fade_in_sec: float = 3.0,
    fade_out_sec: float = 3.0,
    amplitude: float = 0.3,
) -> str:
    """Tạo WAV test tone với fade in/out để simulate nhạc thiền."""
    n_samples = int(duration_sec * sample_rate)
    t = np.linspace(0, duration_sec, n_samples)

    # Sine wave với harmonic nhẹ
    signal = (amplitude * np.sin(2 * np.pi * frequency * t) +
              0.1 * amplitude * np.sin(2 * np.pi * frequency * 2 * t))

    # Stereo
    if channels == 2:
        signal = np.column_stack([signal, signal])

    # Fade in/out (clip nếu track quá ngắn)
    fade_in_samples = min(int(fade_in_sec * sample_rate), n_samples)
    fade_out_samples = min(int(fade_out_sec * sample_rate), n_samples)
    fade_in = np.linspace(0, 1, fade_in_samples)
    fade_out = np.linspace(1, 0, fade_out_samples)
    if signal.ndim == 2:
        fade_in = fade_in[:, None]
        fade_out = fade_out[:, None]
    signal[:fade_in_samples] *= fade_in
    signal[-fade_out_samples:] *= fade_out

    sf.write(path, signal, sample_rate, subtype='PCM_24')
    return path


def create_test_library(n_tracks: int = 16, tmpdir: str = None) -> list[str]:
    """Tạo library n test WAV files với duration khác nhau."""
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp()
    paths = []
    durations = [25, 32, 28, 41, 35, 29, 38, 33, 27, 44, 31, 36, 30, 42, 26, 39]
    freqs = [432, 528, 396, 417, 440, 528, 432, 396, 417, 440, 432, 528, 396, 417, 440, 432]
    for i in range(n_tracks):
        p = Path(tmpdir) / f"test_track_{i + 1:02d}.wav"
        generate_test_wav(str(p), duration_sec=durations[i % len(durations)],
                          frequency=freqs[i % len(freqs)])
        paths.append(str(p))
    return paths
