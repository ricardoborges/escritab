import numpy as np

from app.models import NoteEvent
from app.pipeline import scales


def _profile(weights):
    p = np.zeros(12)
    for pc, w in weights.items():
        p[pc] = w
    return p


B_MINOR_PENT = {11: 3.0, 2: 2.0, 4: 2.0, 6: 2.5, 9: 1.5}


def test_pentatonic_wins_when_it_covers_every_note():
    top = scales.suggest_scales(_profile(B_MINOR_PENT), key_tonic=11, key_family="minor")
    assert top[0]["id"] == "11:minor_pentatonic"
    assert top[0]["name"] == "Si pentatônica menor"
    assert [n["name"] for n in top[0]["notes"]] == ["B", "D", "E", "F#", "A"]
    assert [n["degree"] for n in top[0]["notes"]] == ["1", "b3", "4", "5", "b7"]
    assert top[0]["coverage"] == 1.0 and not top[0]["outside"]
    assert "Ré pentatônica maior" in top[0]["same_notes_as"]
    # one entry per set of notes
    ids = [frozenset(n["pc"] for n in s["notes"]) for s in top]
    assert len(ids) == len(set(ids))
    assert abs(sum(s["probability"] for s in top) - 1.0) < 0.05


def test_extra_notes_select_the_seven_note_scale():
    prof = _profile({**B_MINOR_PENT, 1: 1.0, 7: 1.0})  # + C# and G: B natural minor
    top = scales.suggest_scales(prof, key_tonic=11, key_family="minor")
    assert top[0]["id"] == "11:aeolian"
    prof = _profile({**B_MINOR_PENT, 5: 0.8})          # + F (b5): blues
    assert scales.suggest_scales(prof, key_tonic=11, key_family="minor")[0]["id"] == "11:blues"


def test_key_and_chords_decide_between_relative_scales():
    prof = _profile(B_MINOR_PENT)
    assert scales.suggest_scales(prof, key_tonic=2, key_family="major")[0]["id"] == "2:major_pentatonic"
    chords = [{"start": 0, "end": 4, "root": 11, "quality": "min"}, {"start": 4, "end": 6, "root": 7, "quality": "maj"}]
    roots, spans = scales.chord_root_weights(chords, 0.0, 6.0)
    top = scales.suggest_scales(prof, chord_roots=roots, chord_spans=spans)
    assert top[0]["id"] == "11:minor_pentatonic"
    assert top[0]["chord_fit"] is not None


def test_note_profile_and_scale_ids():
    notes = [NoteEvent(pitch=71, start=0, end=0.5, velocity=100), NoteEvent(pitch=62, start=0.5, end=0.6)]
    p = scales.note_profile(notes)
    assert p[11] > p[2] > 0 and p.sum() == p[11] + p[2]
    assert scales.parse_scale_id("11:minor_pentatonic") == (11, "minor_pentatonic")
    assert scales.parse_scale_id("x:minor_pentatonic") is None
    assert scales.parse_scale_id("3:nope") is None
    assert scales.scale_pcs(9, "minor_pentatonic") == [9, 0, 2, 4, 7]
    assert scales.scale_label(10, "ionian") == "Bb maior (jônio)"
