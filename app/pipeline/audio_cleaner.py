from __future__ import annotations
from pathlib import Path
import librosa
import numpy as np
from scipy.signal import butter, sosfiltfilt
import soundfile as sf


def load_mono_audio(path: Path, target_sr: int | None = None) -> tuple[np.ndarray, int]:
    audio, sr = librosa.load(str(path), sr=target_sr, mono=True)
    return audio.astype(np.float32), int(sr)


def apply_bandpass_filter(
    audio: np.ndarray,
    sr: int,
    lowcut_hz: float = 75.0,
    highcut_hz: float = 5500.0,
    order: int = 4,
) -> np.ndarray:
    nyquist = 0.5 * sr
    low = max(10.0, lowcut_hz) / nyquist
    high = min(nyquist - 50.0, highcut_hz) / nyquist
    if low >= high or low <= 0.0 or high >= 1.0:
        return audio
    sos = butter(order, [low, high], btype="bandpass", output="sos")
    filtered = sosfiltfilt(sos, audio)
    return filtered.astype(np.float32)


def apply_noise_gate(
    audio: np.ndarray,
    sr: int,
    threshold_db: float = -45.0,
    frame_ms: float = 20.0,
) -> np.ndarray:
    if threshold_db <= -100.0 or audio.size == 0:
        return audio
    threshold_linear = 10.0 ** (threshold_db / 20.0)
    frame_len = max(64, int(sr * (frame_ms / 1000.0)))
    output = audio.copy()
    for start in range(0, len(output), frame_len):
        end = min(len(output), start + frame_len)
        frame = output[start:end]
        rms = float(np.sqrt(np.mean(frame**2) + 1e-12))
        if rms < threshold_linear:
            output[start:end] = 0.0
    return output


def mix_stem_files(stem_paths: list[Path], output_path: Path) -> Path:
    if not stem_paths:
        raise ValueError("At least one stem path is required.")
    signals: list[np.ndarray] = []
    base_sr: int | None = None
    for p in stem_paths:
        sig, sr = load_mono_audio(p, target_sr=base_sr)
        if base_sr is None:
            base_sr = sr
        signals.append(sig)

    max_len = max(len(s) for s in signals)
    mixed = np.zeros(max_len, dtype=np.float32)
    for s in signals:
        mixed[: len(s)] += s

    peak = float(np.max(np.abs(mixed))) if mixed.size > 0 else 0.0
    if peak > 0.98:
        mixed = mixed * (0.95 / peak)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), mixed, base_sr or 44100)
    return output_path


def prepare_clean_audio(
    stem_paths: list[Path],
    output_path: Path,
    bandpass_enabled: bool = True,
    lowcut_hz: float = 75.0,
    highcut_hz: float = 5500.0,
    noise_gate_db: float = -45.0,
) -> Path:
    mixed_path = output_path.parent / "_temp_mixed.wav"
    mix_stem_files(stem_paths, mixed_path)
    audio, sr = load_mono_audio(mixed_path)
    if mixed_path.exists():
        mixed_path.unlink()

    if bandpass_enabled:
        audio = apply_bandpass_filter(audio, sr, lowcut_hz=lowcut_hz, highcut_hz=highcut_hz)

    audio = apply_noise_gate(audio, sr, threshold_db=noise_gate_db)

    peak = float(np.max(np.abs(audio))) if audio.size > 0 else 0.0
    if peak > 0.98:
        audio = audio * (0.95 / peak)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), audio, sr)
    return output_path


def detect_bpm(audio_path: Path, default_bpm: float = 120.0) -> float:
    try:
        audio, sr = librosa.load(str(audio_path), sr=22050, mono=True, duration=60.0)
        if audio.size < sr:
            return default_bpm
        tempo, _ = librosa.beat.beat_track(y=audio, sr=sr)
        bpm_val = float(np.asarray(tempo).flat[0])
        if not np.isfinite(bpm_val) or bpm_val <= 30.0 or bpm_val >= 300.0:
            return default_bpm
        return round(bpm_val, 1)
    except Exception:
        return default_bpm
