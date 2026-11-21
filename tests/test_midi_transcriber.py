from pathlib import Path
import numpy as np
import soundfile as sf
from app.models import NoteEvent, TranscriptionParams
from app.pipeline.midi_transcriber import (
    midi_file_to_notes,
    notes_to_midi_file,
    postprocess_notes,
    transcribe_audio_to_midi,
)


def test_postprocess_removes_short_notes_and_spurious_harmonics():
    raw_notes = [
        # Fundamental E2 (pitch 40) with velocity 100
        NoteEvent(pitch=40, start=0.00, end=0.50, velocity=100),
        # Spurious 2nd harmonic E3 (pitch 52 = 40+12) within 20ms, velocity 60 (< 75% of 100)
        NoteEvent(pitch=52, start=0.015, end=0.45, velocity=60),
        # Spurious 3rd harmonic B3 (pitch 59 = 40+19) within 10ms, velocity 55 (< 75% of 100)
        NoteEvent(pitch=59, start=0.010, end=0.40, velocity=55),
        # Genuine high melody note with high velocity (90 >= 75% of 100)
        NoteEvent(pitch=64, start=0.00, end=0.50, velocity=90),
        # Ghost note shorter than 60ms (duration 30ms)
        NoteEvent(pitch=45, start=0.60, end=0.63, velocity=80),
    ]
    params = TranscriptionParams(
        minimum_note_length_ms=60.0,
        suppress_harmonics=True,
        harmonic_velocity_ratio=0.75,
        quantize_grid="off",
        tuning="standard_e",
    )
    cleaned, stats = postprocess_notes(raw_notes, params)
    pitches = [n.pitch for n in cleaned]
    assert pitches == [40, 64]
    assert stats["harmonics_removed"] == 2
    assert stats["short_notes_removed"] == 1


def test_postprocess_polyphony_cap_and_quantization():
    # 8 simultaneous notes on a 6-string guitar -> only top 6 by velocity survive
    raw_notes = [
        NoteEvent(pitch=40 + i, start=0.02, end=0.26, velocity=50 + i * 5)
        for i in range(8)
    ]
    params = TranscriptionParams(
        minimum_note_length_ms=50.0,
        suppress_harmonics=False,
        quantize_grid="1/16",
        bpm=120.0,  # 1/16 note = 0.125s
        tuning="standard_e",
    )
    cleaned, _ = postprocess_notes(raw_notes, params)
    assert len(cleaned) == 6
    # Highest velocities should be kept (i=2..7 -> pitches 42..47)
    assert sorted(n.pitch for n in cleaned) == [42, 43, 44, 45, 46, 47]
    # Quantized start should snap 0.02 -> 0.0 and end 0.26 -> 0.25
    for n in cleaned:
        assert abs(n.start - 0.0) < 1e-6
        assert abs(n.end - 0.25) < 1e-6


def test_midi_roundtrip_and_audio_transcription(tmp_path: Path):
    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    # Synthesize a clear A2 (110 Hz) followed by E3 (164.81 Hz)
    audio = np.concatenate([
        0.8 * np.sin(2 * np.pi * 110.0 * t[: sr // 2]),
        0.8 * np.sin(2 * np.pi * 164.81 * t[sr // 2 :]),
    ]).astype(np.float32)
    wav_path = tmp_path / "test_guitar.wav"
    sf.write(wav_path, audio, sr)

    midi_path = tmp_path / "guitar_clean.mid"
    params = TranscriptionParams(
        engine="basic_pitch",
        onset_threshold=0.3,
        frame_threshold=0.2,
        minimum_note_length_ms=50.0,
    )
    notes, stats = transcribe_audio_to_midi(wav_path, midi_path, params)
    assert len(notes) >= 1
    assert midi_path.exists()
    assert stats["total_notes"] == len(notes)

    reloaded = midi_file_to_notes(midi_path)
    assert len(reloaded) == len(notes)
