from app.models import NoteEvent, TabParams
from app.pipeline.fretboard import optimize_fretboard


def test_viterbi_assigns_distinct_strings_for_chords():
    # E5 power chord in Standard E: E2 (40), B2 (47), E3 (52)
    notes = [
        NoteEvent(pitch=40, start=0.0, end=0.5, velocity=100),
        NoteEvent(pitch=47, start=0.0, end=0.5, velocity=95),
        NoteEvent(pitch=52, start=0.0, end=0.5, velocity=90),
    ]
    params = TabParams(tuning="standard_e", max_frets=24, fret_preference="low")
    slices, stats = optimize_fretboard(notes, params)
    assert len(slices) == 1
    strings_used = [n.string_idx for n in slices[0].notes]
    assert len(strings_used) == len(set(strings_used)) == 3
    # In low position: E2=(str 0, fret 0), B2=(str 1, fret 2), E3=(str 2, fret 2)
    mapping = {n.string_idx: n.fret for n in slices[0].notes}
    assert mapping == {0: 0, 1: 2, 2: 2}
    assert stats["transposed_notes"] == 0


def test_viterbi_drop_d_power_chord_barre_bonus():
    # E5 power chord in Drop D: E2 (40), B2 (47), E3 (52) -> barre on fret 2 across strings 0, 1, 2
    notes = [
        NoteEvent(pitch=40, start=0.0, end=0.5, velocity=100),
        NoteEvent(pitch=47, start=0.0, end=0.5, velocity=95),
        NoteEvent(pitch=52, start=0.0, end=0.5, velocity=90),
    ]
    params = TabParams(tuning="drop_d", max_frets=24, fret_preference="low")
    slices, _ = optimize_fretboard(notes, params)
    mapping = {n.string_idx: n.fret for n in slices[0].notes}
    assert mapping == {0: 2, 1: 2, 2: 2}


def test_viterbi_maintains_ergonomic_position_in_high_preference():
    # Melody: C4 (60), D4 (62), E4 (64) played fast with "high" fret preference
    notes = [
        NoteEvent(pitch=60, start=0.0, end=0.2, velocity=90),
        NoteEvent(pitch=62, start=0.2, end=0.4, velocity=90),
        NoteEvent(pitch=64, start=0.4, end=0.6, velocity=90),
    ]
    params = TabParams(tuning="standard_e", max_frets=24, fret_preference="high")
    slices, _ = optimize_fretboard(notes, params)
    assert len(slices) == 3
    frets = [s.notes[0].fret for s in slices]
    # Should stay in the high region (frets >= 5) rather than open string E4 (fret 0)
    assert all(f >= 5 for f in frets)
    assert max(frets) - min(frets) <= 5


def test_viterbi_seven_string_drop_a_and_out_of_range_transposition():
    # Note below A1 (pitch 33): F1 (pitch 29) -> transposed +12 to F2 (41)
    notes = [
        NoteEvent(pitch=29, start=0.0, end=0.5, velocity=100),
        NoteEvent(pitch=33, start=0.5, end=1.0, velocity=100),  # Open A1 on 7-string Drop A
    ]
    params = TabParams(tuning="seven_drop_a", max_frets=24, fret_preference="low")
    slices, stats = optimize_fretboard(notes, params)
    assert len(slices) == 2
    assert stats["transposed_notes"] == 1
    assert slices[0].notes[0].pitch == 41
    assert slices[1].notes[0].string_idx == 0
    assert slices[1].notes[0].fret == 0
