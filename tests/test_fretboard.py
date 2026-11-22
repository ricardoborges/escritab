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


def test_viterbi_high_preference_avoids_wound_low_strings_at_high_frets():
    # In a solo around the 12th fret (B4=71, C#5=73, D5=74) with an occasional lower note (E4=64 or F#4=66),
    # the optimizer must NEVER place notes on strings 0 or 1 (E2, A2) at frets >= 12.
    notes = [
        NoteEvent(pitch=71, start=0.0, end=0.2, velocity=100),   # B4
        NoteEvent(pitch=73, start=0.2, end=0.4, velocity=100),   # C#5
        NoteEvent(pitch=66, start=0.4, end=0.6, velocity=100),   # F#4
        NoteEvent(pitch=64, start=0.6, end=0.8, velocity=100),   # E4
        NoteEvent(pitch=59, start=0.8, end=1.0, velocity=100),   # B3
        NoteEvent(pitch=74, start=1.0, end=1.2, velocity=100),   # D5
    ]
    params = TabParams(tuning="standard_e", max_frets=24, fret_preference="high")
    slices, _ = optimize_fretboard(notes, params)
    for sl in slices:
        fn = sl.notes[0]
        # Never put solo notes on the 6th or 5th string (E2, A2) above fret 9
        assert not (fn.string_idx <= 1 and fn.fret >= 10), f"Bad placement: string={fn.string_idx}, fret={fn.fret}, pitch={fn.pitch}"
        # Even B3 (59) near fret 12 should be on string 2 (D3, fret 9) or string 3 (G3, fret 4), not string 0 (E2, fret 19) or string 1 (A2, fret 14)
        assert fn.string_idx >= 2


def test_viterbi_standard_power_chord_shape_bonus_and_45ms_grouping():
    # F#5 power chord (42, 49, 54) where the 3rd string onset lags by 38ms (typical Dettato spread)
    notes = [
        NoteEvent(pitch=42, start=0.100, end=0.30, velocity=100),
        NoteEvent(pitch=49, start=0.115, end=0.30, velocity=95),
        NoteEvent(pitch=54, start=0.138, end=0.30, velocity=90),
    ]
    params = TabParams(tuning="standard_e", max_frets=24, fret_preference="auto")
    slices, _ = optimize_fretboard(notes, params)
    # Must group all 3 notes into a single chord slice (not split the 0.138s note into a separate slice)
    assert len(slices) == 1
    mapping = {n.string_idx: n.fret for n in slices[0].notes}
    assert mapping == {0: 2, 1: 4, 2: 4}

