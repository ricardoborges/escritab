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


def test_monophonic_reduction_keeps_one_voice():
    from app.pipeline.midi_transcriber import reduce_to_monophonic

    notes = [
        NoteEvent(pitch=64, start=0.00, end=0.40, velocity=100),
        NoteEvent(pitch=76, start=0.01, end=0.30, velocity=60),   # octave ghost at the same onset
        NoteEvent(pitch=67, start=0.20, end=0.50, velocity=30),   # quiet bleed under the first note
        NoteEvent(pitch=69, start=0.50, end=0.90, velocity=95, bend=2.0),
    ]
    out = reduce_to_monophonic(notes)
    assert [n.pitch for n in out] == [64, 69]
    assert out[0].end == 0.40
    assert out[1].bend == 2.0


def test_postprocess_suppresses_power_chord_overtones_and_syncs_chord_onsets():
    # F#5 power chord (42, 49, 54) with a distorted 3rd-harmonic overtone at C#4 (61 = 42+19 = 49+12)
    # whose velocity (77) is slightly above 0.75 * 96 (72), plus a 35ms onset spread across strings.
    raw = [
        NoteEvent(pitch=42, start=0.100, end=0.26, velocity=96),
        NoteEvent(pitch=49, start=0.118, end=0.26, velocity=88),
        NoteEvent(pitch=54, start=0.132, end=0.26, velocity=91),
        NoteEvent(pitch=61, start=0.135, end=0.25, velocity=77),  # overtone of power chord (42+19 and 49+12)
    ]
    params = TranscriptionParams(
        minimum_note_length_ms=60.0,
        suppress_harmonics=True,
        harmonic_velocity_ratio=0.75,
        tuning="standard_e",
    )
    cleaned, stats = postprocess_notes(raw, params)
    assert [n.pitch for n in cleaned] == [42, 49, 54]
    assert stats["harmonics_removed"] == 1
    # Onsets of a simultaneous chord are aligned so exporters don't split them across grid points
    assert len({round(n.start, 4) for n in cleaned}) == 1


def test_postprocess_removes_high_chord_overtone_even_at_high_velocity():
    # In a 3-note power chord (42, 49, 54), a 4th note at 61 (+19 above 42 AND +12 above 49)
    # with velocity 92 (95.8% of 96) is still a chord overtone and must be removed.
    raw = [
        NoteEvent(pitch=42, start=1.279, end=1.592, velocity=96),
        NoteEvent(pitch=49, start=1.279, end=1.595, velocity=88),
        NoteEvent(pitch=54, start=1.279, end=1.598, velocity=95),
        NoteEvent(pitch=61, start=1.279, end=1.592, velocity=92),
    ]
    params = TranscriptionParams(suppress_harmonics=True, harmonic_velocity_ratio=0.75)
    cleaned, stats = postprocess_notes(raw, params)
    assert [n.pitch for n in cleaned] == [42, 49, 54]
    assert stats["harmonics_removed"] == 1


def test_monophonic_reduction_keeps_octave_consistency_in_octave_doubled_lines():
    from app.pipeline.midi_transcriber import reduce_to_monophonic

    # Dettato track with simultaneous octave doublings (62/74, 61/73, 59/71) where the lower
    # octave happens to have slightly higher velocity on some attacks (61 vel 106 vs 73 vel 95)
    notes = [
        NoteEvent(pitch=62, start=0.08, end=0.23, velocity=100),
        NoteEvent(pitch=74, start=0.08, end=0.23, velocity=108),
        NoteEvent(pitch=61, start=0.24, end=0.39, velocity=106),
        NoteEvent(pitch=73, start=0.25, end=0.40, velocity=95),
        NoteEvent(pitch=62, start=0.41, end=0.57, velocity=98),
        NoteEvent(pitch=74, start=0.42, end=0.58, velocity=108),
        NoteEvent(pitch=59, start=0.59, end=0.90, velocity=90),
        NoteEvent(pitch=71, start=0.59, end=0.90, velocity=98),
    ]
    out = reduce_to_monophonic(notes)
    # Must stay in the same octave (74, 73, 74, 71), not jump down an octave to 61 in the middle!
    assert [n.pitch for n in out] == [74, 73, 74, 71]


def test_postprocess_quantizes_on_warped_beat_grid_and_writes_tempo_map(tmp_path: Path):
    import pretty_midi
    from app.pipeline.midi_transcriber import finalize_notes_to_midi

    # Beat 0 at 0.20s, Beat 1 at 0.60s (0.40s beat = 150 BPM -> 1/16 = 0.10s)
    beats = [0.20, 0.60, 1.00, 1.40]
    raw = [
        NoteEvent(pitch=52, start=0.212, end=0.405, velocity=100),  # snaps to 0.20..0.40 (beat 0.0..0.5)
        NoteEvent(pitch=55, start=0.491, end=0.610, velocity=95),   # snaps to 0.50..0.60 (beat 0.75..1.0)
    ]
    params = TranscriptionParams(quantize_grid="1/16", bpm=150.0, suppress_harmonics=False)
    mid_path = tmp_path / "warped.mid"
    cleaned, _ = finalize_notes_to_midi(raw, mid_path, params, beats=beats)

    assert [(round(n.start, 3), round(n.end, 3)) for n in cleaned] == [(0.20, 0.40), (0.50, 0.60)]
    pm = pretty_midi.PrettyMIDI(str(mid_path))
    _, tempos = pm.get_tempo_changes()
    assert any(abs(t - 150.0) < 1.0 for t in tempos)



