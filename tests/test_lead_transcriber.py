import numpy as np

from app.pipeline import lead_transcriber as lt

SR = 44100
DETUNE = 0.28  # the "band" is 28 cents sharp

# (start, dur, pitch, kind, bend)
SOLO = [
    (0.00, 0.25, 64, "pick", 0), (0.25, 0.25, 67, "pick", 0), (0.50, 0.12, 69, "pick", 0),
    (0.62, 0.13, 71, "legato", 0),                                   # hammer-on
    (0.75, 0.25, 69, "pick", 0), (1.00, 0.20, 69, "pick", 0), (1.20, 0.20, 69, "pick", 0),
    (1.40, 0.60, 72, "bend", 2),                                     # full bend
    (2.10, 0.50, 74, "vib", 0),                                      # wide vibrato
    (2.70, 0.15, 76, "pick", 0), (2.85, 0.15, 74, "legato", 0),      # pull-off
    (3.05, 0.10, 57, "pick", 0), (3.15, 0.10, 59, "pick", 0), (3.25, 0.10, 60, "pick", 0),
    (3.35, 0.40, 62, "pick", 0),
]


def _synth(score, gain=8.0, seed=0):
    """Phase-continuous additive tone, picked attacks, hard tanh distortion (a high-gain lead)."""
    rng = np.random.default_rng(seed)
    total = int((max(s + d for s, d, *_ in score) + 0.3) * SR)
    f0, amp, attack = np.zeros(total), np.zeros(total), np.zeros(total)
    for s, d, p, kind, bend in score:
        a, b = int(s * SR), int((s + d) * SR)
        t = np.arange(b - a) / SR
        midi = np.full(b - a, p + DETUNE)
        if kind == "bend":
            midi += bend * np.clip((t - 0.08) / 0.12, 0, 1)
        if kind == "vib":
            midi += 0.45 * np.sin(2 * np.pi * 5.5 * t) * np.clip(t / 0.1, 0, 1)
        f0[a:b] = 440 * 2 ** ((midi - 69) / 12)
        env = np.exp(-t * 2.5) * 0.9 + 0.1
        if kind == "legato":
            env *= 0.7
        else:
            n = int(0.004 * SR)
            attack[a:a + n] += rng.normal(0, 0.6, n)
        rel = int(0.01 * SR)
        env[-rel:] *= np.linspace(1, 0.2, rel)
        amp[a:b] = env
    phase = 2 * np.pi * np.cumsum(f0) / SR
    x = sum((0.6 ** k) * np.sin((k + 1) * phase) for k in range(8)) * amp + attack
    return (np.tanh(gain * x) * 0.5 + rng.normal(0, 0.003, total)).astype(np.float32)


def test_lead_transcription_of_distorted_detuned_solo():
    notes, info = lt.transcribe_lead(_synth(SOLO), SR, fmin=70, fmax=1400, min_note_s=0.05)

    assert abs(info["tuning_offset_cents"] - DETUNE * 100) < 8
    got = [(round(n.start, 2), n.pitch) for n in notes]
    hits = sum(
        any(abs(n.start - s) <= 0.04 and n.pitch == p for n in notes) for s, _, p, _, _ in SOLO
    )
    assert hits == len(SOLO), got
    assert len(notes) == len(SOLO), got  # no ghost notes: vibrato and bend stay single notes

    bent = [n for n in notes if abs(n.start - 1.40) < 0.04]
    assert bent and bent[0].pitch == 72 and bent[0].bend == 2.0
    assert all(n.bend == 0.0 for n in notes if n is not bent[0])


def test_tuning_offset_is_circular():
    # +48 and -48 cents are both ~a quarter-tone-and-a-bit off: the same offset (±0.5) seen from
    # either side of the semitone boundary, not "no offset" as a linear mean would say
    midi = np.concatenate([np.full(50, 60.48), np.full(50, 64.52)])
    assert abs(abs(lt.estimate_tuning_offset(midi, np.ones(100))) - 0.5) < 0.03
    midi = np.concatenate([np.full(50, 60.20), np.full(50, 64.30)])
    assert abs(lt.estimate_tuning_offset(midi, np.ones(100)) - 0.25) < 0.03


def _track(segments):
    """A PitchTrack from (fractional midi, seconds) pieces, a picked onset at each piece."""
    midi, onsets = [], []
    for m, dur in segments:
        onsets.append(len(midi))
        midi.extend([m] * int(round(dur / lt.HOP_S)))
    n = len(midi)
    return lt.PitchTrack(
        midi=np.array(midi, dtype=float), conf=np.ones(n), rms=np.ones(n),
        onsets=np.array(onsets, dtype=np.int64), tuning_offset=0.0, tracker="test",
    )


def test_quantize_prefers_scale_tones_only_when_ambiguous():
    pen = lt.scale_penalty([9, 0, 2, 4, 7])  # A minor pentatonic
    assert lt.quantize(71.45, None) == 71
    assert lt.quantize(71.45, pen) == 72     # 55 cents flat C, not B
    assert lt.quantize(71.2, pen) == 71      # clearly a B: a chromatic note stays
    assert list(lt.quantize(np.array([68.9, 70.6]), pen)) == [69, 71]  # B is out of the scale too: plain rounding
    assert lt.scale_penalty(None) is None


def test_segment_notes_uses_the_scale_for_an_out_of_tune_note():
    track = _track([(69.0, 0.2), (71.45, 0.2), (76.0, 0.2)])
    plain = [n.pitch for n in lt.segment_notes(track, 0.05)]
    stats = {}
    biased = [n.pitch for n in lt.segment_notes(track, 0.05, [9, 0, 2, 4, 7], stats)]
    assert plain == [69, 71, 76]
    assert biased == [69, 72, 76]
    assert stats["scale_corrected"] == 1
