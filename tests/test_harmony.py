from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline import harmony

SR = harmony.SR
NOTE = {"C": 0, "C#": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "Bb": 10, "B": 11}
CHORDS = {
    "C": ("C", [0, 4, 7]), "Am": ("A", [0, 3, 7]), "F": ("F", [0, 4, 7]), "G": ("G", [0, 4, 7]),
    "Dm": ("D", [0, 3, 7]), "E": ("E", [0, 4, 7]),
}


def _tone(midi: int, t: np.ndarray) -> np.ndarray:
    f = 440.0 * 2 ** ((midi - 69) / 12)
    return sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 5))


def _song(progression: list[str], seconds_per_chord: float = 2.0, repeats: int = 3) -> np.ndarray:
    """Block chords (bass note + close voicing, a few harmonics each), re-attacked every beat."""
    t = np.arange(int(SR * seconds_per_chord)) / SR
    env = np.exp(-3.0 * (t % 0.5))
    out = []
    for _ in range(repeats):
        for name in progression:
            root, ivs = CHORDS[name]
            r = NOTE[root]
            y = _tone(36 + r, t) + sum(_tone(60 + r + iv, t) for iv in ivs)
            out.append(0.1 * y * env)
    return np.concatenate(out).astype(np.float32)


def _beats(duration: float) -> list[float]:
    return list(np.arange(0.5, duration, 0.5))


def test_major_progression_key_chords_and_loop():
    y = _song(["C", "Am", "F", "G"])
    duration = len(y) / SR
    r = harmony.analyze(y, _beats(duration), duration)
    assert r["key"]["name"] == "Dó maior"
    assert r["key"]["scale"] == ["C", "D", "E", "F", "G", "A", "B"]
    labels = [c["label"] for c in r["chords"]]
    for name in ("C", "Am", "F", "G"):
        assert name in labels, labels
    assert r["stats"]["diatonic_share"] > 0.9
    numerals = [f["numeral"] for f in r["field"]]
    assert numerals == ["I", "ii", "iii", "IV", "V", "vi", "vii°"]
    loop = r["loops"][0]
    assert sorted(loop["chords"]) == ["Am", "C", "F", "G"] and loop["count"] >= 2
    used = {f["triad"] for f in r["field"] if f["share"] > 0.1}
    assert used == {"C", "Am", "F", "G"}


def test_minor_key_flags_harmonic_minor_dominant():
    y = _song(["Am", "Dm", "E", "Am"])
    duration = len(y) / SR
    r = harmony.analyze(y, _beats(duration), duration)
    assert r["key"]["tonic_name"] == "A" and r["key"]["family"] == "minor"
    e = [c for c in r["chords"] if c["label"] == "E"]
    assert e and not e[0]["diatonic"] and e[0]["numeral"] == "V"
    assert "menor harmônica" in e[0]["explain"]


def test_roman_numerals_are_chromatic_and_case_sensitive():
    a = 9
    assert harmony.roman(5, a, "maj") == "bVI"
    assert harmony.roman(7, a, "maj") == "bVII"
    assert harmony.roman(11, a, "dim") == "ii°"
    assert harmony.roman(4, a, "7") == "V7"
    assert harmony.roman(4, a, "5") == "V5"
    assert harmony.roman(4, a, "5", implied="min") == "v"


def test_find_loops_prefers_coverage_and_skips_repeats_of_shorter_loops():
    seq = ["A", "F", "G"] * 4 + ["E", "A"] * 3
    loops = harmony.find_loops(seq)
    assert loops[0][0] == ("A", "F", "G") and len(loops[0][1]) == 4
    assert all(p != ("A", "F", "G", "A", "F", "G") for p, _ in loops)


def test_harmony_endpoint_computes_and_caches(tmp_path: Path):
    import io
    import soundfile as sf

    client = TestClient(create_app(workspace_root=tmp_path))
    buf = io.BytesIO()
    sf.write(buf, _song(["C", "G"], repeats=2), SR, format="WAV")
    res = client.post(
        "/api/projects",
        files={"file": ("song.wav", buf.getvalue(), "audio/wav")},
        data={"is_isolated_stem": "true", "name": "Teste", "mode": "classic"},
    )
    pid = res.json()["job_id"]
    assert client.get(f"/api/projects/{pid}/harmony").json() == {"harmony": None}
    res = client.post(f"/api/projects/{pid}/harmony")
    assert res.status_code == 200, res.text
    h = res.json()["harmony"]
    assert h["chords"] and h["key"]["name"]
    assert client.get(f"/api/projects/{pid}/harmony").json()["harmony"]["key"] == h["key"]
