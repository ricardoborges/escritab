import numpy as np

from app.pipeline.guitar_roles import chord_ratio, is_lead_candidate, split_roles


def _n(on, pitch, dur=0.15, vel=100):
    return [on, on + dur, pitch, vel]


def _power_chords(t0, t1, root=50, step=0.19):
    """Rhythm: power chords (root, 5th, octave) in eighth notes, like a verse riff."""
    out = []
    t = t0
    while t < t1:
        out += [_n(t, root), _n(t, root + 7), _n(t, root + 12)]
        t += step
    return out


def test_verse_power_chords_are_rhythm():
    notes = np.array(_power_chords(0, 8), float)
    assert not split_roles(notes).any()


def test_solo_over_pedal_rhythm_is_peeled_off():
    # rhythm keeps chugging a pedal note; the solo notes start at the same instants, an octave+ above
    rows, solo = [], []
    t, k = 0.0, 0
    melody = [69, 71, 72, 74, 76, 74, 72, 71]
    while t < 6:
        rows.append(_n(t, 50))
        rows.append(_n(t + 0.005, melody[k % len(melody)]))
        solo.append(len(rows) - 1)
        t += 0.19
        k += 1
    notes = np.array(rows, float)
    lead = split_roles(notes)
    assert lead[solo].all()
    assert not lead[[i for i in range(len(rows)) if i not in solo]].any()


def test_single_note_intro_melody_without_rhythm_is_lead():
    melody = [64, 66, 67, 69, 71, 69, 67, 66] * 3
    notes = np.array([_n(i * 0.25, p) for i, p in enumerate(melody)], float)
    assert split_roles(notes).all()


def test_low_single_note_riff_stays_rhythm():
    riff = [40, 40, 43, 40, 45, 40, 47, 45] * 4  # palm-muted low-string riff
    notes = np.array([_n(i * 0.2, p) for i, p in enumerate(riff)], float)
    assert not split_roles(notes).any()


def test_lead_line_above_rhythm_chords_is_lead():
    rows = _power_chords(0, 6, root=45)            # rhythm roof ~57
    lead_idx = []
    for i, p in enumerate([67, 69, 71, 72, 74, 72, 71, 69] * 2):
        rows.append(_n(0.09 + i * 0.37, p))         # off the rhythm's onsets
        lead_idx.append(len(rows) - 1)
    notes = np.array(rows, float)
    lead = split_roles(notes)
    assert lead[lead_idx].mean() >= 0.9
    assert lead[: len(rows) - len(lead_idx)].mean() <= 0.05


def test_input_order_does_not_matter():
    rows = _power_chords(0, 3) + [_n(0.005 + 0.19 * i, 72) for i in range(10)]
    notes = np.array(rows, float)
    perm = np.random.default_rng(0).permutation(len(notes))
    assert (split_roles(notes)[perm] == split_roles(notes[perm])).all()


def test_lead_candidate_detection():
    mono_high = np.array([_n(i * 0.25, 64 + (i % 5)) for i in range(40)], float)
    chords = np.array(_power_chords(0, 4), float)
    assert chord_ratio(mono_high) == 0.0
    assert is_lead_candidate(40, False, mono_high)        # "violin" playing a guitar melody
    assert is_lead_candidate(80, False, mono_high)        # "synth lead"
    assert not is_lead_candidate(52, False, mono_high)    # voice
    assert not is_lead_candidate(33, False, mono_high)    # bass
    assert not is_lead_candidate(26, False, mono_high)    # already a guitar
    assert not is_lead_candidate(40, False, chords)       # polyphonic
    assert not is_lead_candidate(40, True, mono_high)     # drums


def test_lead_candidate_allows_octave_doubled_synth_lead():
    # Dettato often transcribes a guitar solo as "synth lead" (program 80) with simultaneous octave doublings
    rows = []
    for i in range(40):
        t = i * 0.22
        p = 62 + (i % 7)
        rows.append(_n(t, p, vel=100))
        rows.append(_n(t + 0.005, p + 12, vel=85))  # octave doubling at the same onset
    octave_doubled = np.array(rows, float)
    assert is_lead_candidate(80, False, octave_doubled)

