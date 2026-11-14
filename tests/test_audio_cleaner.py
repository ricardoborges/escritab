from pathlib import Path
import numpy as np
import soundfile as sf
from app.pipeline.audio_cleaner import (
    apply_bandpass_filter,
    apply_noise_gate,
    detect_bpm,
    mix_stem_files,
    prepare_clean_audio,
)


def test_bandpass_filter_attenuates_sub_bass_and_ultra_highs():
    sr = 44100
    duration = 1.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    sub_bass = np.sin(2 * np.pi * 30.0 * t)       # 30 Hz (below 75 Hz)
    guitar_tone = np.sin(2 * np.pi * 440.0 * t)   # 440 Hz (in passband)
    high_hiss = np.sin(2 * np.pi * 12000.0 * t)   # 12 kHz (above 5500 Hz)

    filtered_sub = apply_bandpass_filter(sub_bass, sr, lowcut_hz=75.0, highcut_hz=5500.0)
    filtered_guitar = apply_bandpass_filter(guitar_tone, sr, lowcut_hz=75.0, highcut_hz=5500.0)
    filtered_high = apply_bandpass_filter(high_hiss, sr, lowcut_hz=75.0, highcut_hz=5500.0)

    rms_sub = np.sqrt(np.mean(filtered_sub**2))
    rms_guitar = np.sqrt(np.mean(filtered_guitar**2))
    rms_high = np.sqrt(np.mean(filtered_high**2))

    assert rms_guitar > 0.65
    assert rms_sub < 0.25
    assert rms_high < 0.15


def test_noise_gate_silences_quiet_tail():
    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    signal = np.concatenate([
        0.5 * np.sin(2 * np.pi * 220.0 * t[: sr // 2]),   # loud first half (-6 dB)
        0.001 * np.sin(2 * np.pi * 220.0 * t[sr // 2 :]), # quiet tail (-60 dB)
    ])
    gated = apply_noise_gate(signal, sr, threshold_db=-40.0)
    assert np.max(np.abs(gated[: sr // 2])) > 0.4
    assert np.max(np.abs(gated[int(sr * 0.6) :])) == 0.0


def test_mix_stems_and_prepare_clean_audio(tmp_path: Path):
    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    stem1 = 0.6 * np.sin(2 * np.pi * 330.0 * t)
    stem2 = 0.6 * np.sin(2 * np.pi * 440.0 * t)

    p1 = tmp_path / "guitar.wav"
    p2 = tmp_path / "other.wav"
    sf.write(p1, stem1, sr)
    sf.write(p2, stem2, sr)

    mixed_path = tmp_path / "mixed.wav"
    mix_stem_files([p1, p2], mixed_path)
    assert mixed_path.exists()
    mixed_audio, _ = sf.read(mixed_path)
    assert np.max(np.abs(mixed_audio)) <= 1.0

    clean_path = tmp_path / "clean.wav"
    prepare_clean_audio([p1, p2], clean_path, bandpass_enabled=True, noise_gate_db=-45.0)
    assert clean_path.exists()


def test_detect_bpm_returns_valid_tempo(tmp_path: Path):
    sr = 22050
    duration = 4.0
    audio = np.zeros(int(sr * duration), dtype=np.float32)
    # Place impulses every 0.5s -> 120 BPM
    for sec in np.arange(0.0, duration, 0.5):
        idx = int(sec * sr)
        audio[idx : min(idx + 400, len(audio))] = 0.9
    wav_path = tmp_path / "click.wav"
    sf.write(wav_path, audio, sr)

    bpm = detect_bpm(wav_path, default_bpm=120.0)
    assert 60.0 <= bpm <= 240.0
