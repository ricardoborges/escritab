from pathlib import Path
import numpy as np
import pretty_midi
from app.pipeline import rhythm


def test_beat_map_absorbs_initial_offset_and_tempo_drift():
    # First beat at 0.28s, then slight tempo acceleration (0.50s -> 0.48s -> 0.46s)
    beats = [0.28, 0.78, 1.26, 1.72, 2.18]
    bm = rhythm.BeatMap.from_beats(beats, bpm=120.0)

    assert abs(bm.time_to_beat(0.28) - 0.0) < 1e-6
    assert abs(bm.time_to_beat(0.78) - 1.0) < 1e-6
    assert abs(bm.time_to_beat(1.02) - 1.5) < 1e-6  # midpoint between 0.78 and 1.26
    assert bm.time_to_beat(0.04) < -0.4             # pickup before beat 0

    # Inverse round-trip
    sample_t = np.array([0.05, 0.28, 1.02, 1.72, 2.64])
    reconstructed = bm.beat_to_time(bm.time_to_beat(sample_t))
    assert np.allclose(reconstructed, sample_t, atol=1e-6)


def test_beat_map_for_excerpt_handles_edge_tolerance_and_fallback():
    beats = [0.50, 1.00, 1.50, 2.00, 2.50, 3.00]
    # Cut 30ms AFTER the beat at 1.00s -> 1.00s (t_rel = -0.03s) still anchors beat 0.0
    bm = rhythm.BeatMap.for_excerpt(beats, bpm=120.0, start=1.03, end=2.60)
    assert abs(bm.time_to_beat(0.0)) < 0.1  # note at t_rel=0.0 snaps to beat 0, not pickup

    # Cut at 1.20s (well after 1.00s) -> 1.50s (t_rel = 0.30s) becomes beat 0.0; t_rel=0.05 is pickup
    bm_pick = rhythm.BeatMap.for_excerpt(beats, bpm=120.0, start=1.20, end=2.60)
    assert abs(bm_pick.time_to_beat(0.30) - 0.0) < 1e-6
    assert abs(bm_pick.time_to_beat(0.05) - (-0.5)) < 1e-6

    # Fallback without beats -> uniform 120 BPM starting at 0.0
    bm_fb = rhythm.BeatMap.from_beats([], bpm=120.0)
    assert abs(bm_fb.time_to_beat(0.5) - 1.0) < 1e-6


def test_build_pretty_midi_aligns_beats_to_ticks_and_preserves_seconds(tmp_path: Path):
    beats = [0.279, 0.673, 1.045, 1.416, 1.811, 2.206]
    pm = rhythm.build_pretty_midi(beats, bpm=152.0, max_time=2.8, resolution=480)
    ins = pretty_midi.Instrument(program=30, name="Guitar")
    for b in beats:
        ins.notes.append(pretty_midi.Note(velocity=100, pitch=52, start=b, end=b + 0.2))
    pm.instruments.append(ins)

    out = tmp_path / "tempo_mapped.mid"
    pm.write(str(out))

    reloaded = pretty_midi.PrettyMIDI(str(out))
    tempos_times, tempos = reloaded.get_tempo_changes()
    assert len(tempos) >= 4
    # Every beat note must sit on an exact multiple of 480 ticks AND within 2ms of original audio time
    for orig_b, note in zip(beats, reloaded.instruments[0].notes):
        tick = reloaded.time_to_tick(note.start)
        assert tick % 480 == 0
        assert abs(note.start - orig_b) < 0.002


def test_is_triplet_beat_distinguishes_binary_from_triplets():
    assert rhythm.is_triplet_beat([]) is False
    assert rhythm.is_triplet_beat([0.0, 0.5]) is False
    assert rhythm.is_triplet_beat([0.0, 0.25, 0.5, 0.75]) is False
    assert rhythm.is_triplet_beat([0.0, 0.75]) is False  # dotted eighth + 16th
    assert rhythm.is_triplet_beat([0.0, 0.333, 0.667]) is True
    assert rhythm.is_triplet_beat([0.0, 0.666]) is True  # quarter+eighth shuffle triplet
    assert rhythm.is_triplet_beat([0.0, 0.167, 0.333, 0.5, 0.667, 0.833]) is True
