import io
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline import lyrics


def _heard(spec: list[tuple[str, float]], dur: float = 0.4) -> list[dict]:
    return [{"text": w, "start": t, "end": t + dur, "prob": 0.9} for w, t in spec]


def test_parse_lyrics_sections_and_paragraphs():
    text = "[Verse 1]\nOlá, mundo\nsegunda linha\n\nterceira\n[Chorus]\nRefrão aqui\n"
    lines = lyrics.parse_lyrics(text)
    assert [l["words"] for l in lines] == [["Olá,", "mundo"], ["segunda", "linha"], ["terceira"], ["Refrão", "aqui"]]
    assert lines[0]["section"] == "Verso 1" and lines[0]["new_section"]
    assert lines[2]["para"] and not lines[2]["new_section"]
    assert lines[3]["section"] == "Refrão" and lines[3]["new_section"]
    assert lyrics.norm_word("Coração,") == "coracao"


def test_align_matches_misheard_words_and_interpolates_missing_ones():
    text = "She breathes the thunder\nShe stirs the tide"
    heard = _heard([("They", 10.0), ("breeze", 10.5), ("the", 11.0), ("thunder", 11.5),
                    ("Distirce", 13.0), ("tide", 14.0)])
    lines = lyrics.align_lyrics(text, heard)
    w1 = lines[0]["words"]
    assert all(w["matched"] for w in w1[1:])  # "breathes" ~ "breeze"
    assert w1[1]["start"] == 10.5 and w1[0]["start"] < 10.5
    w2 = lines[1]["words"]
    assert w2[-1]["start"] == 14.0 and w2[-1]["matched"]
    starts = [w["start"] for l in lines for w in l["words"]]
    assert starts == sorted(starts)
    assert 11.9 <= w2[0]["start"] <= 14.0  # placed between "thunder" and "tide"


def test_unmatched_words_hug_their_anchor_across_a_long_gap():
    text = "first line here\nsecond line now"
    heard = _heard([("first", 1.0), ("line", 1.4), ("now", 60.0)])
    lines = lyrics.align_lyrics(text, heard)
    assert lines[0]["words"][2]["start"] < 3.0           # "here" follows "line"
    assert lines[1]["words"][0]["start"] > 55.0          # "second" sung just before "now"


def test_chord_sheet_places_chords_over_the_sung_word_and_adds_instrumentals():
    lines = lyrics.align_lyrics("one two three four", _heard([("one", 10.0), ("two", 11.0), ("three", 12.0), ("four", 13.0)]))
    field = [{"function": f} for f in "TSTSDSD"]
    chords = [
        {"start": 0.0, "end": 11.05, "label": "Am", "numeral": "i", "diatonic": True, "degree": 1},
        {"start": 11.05, "end": 12.1, "label": "F", "numeral": "bVI", "diatonic": True, "degree": 6},
        {"start": 12.1, "end": 20.0, "label": "E", "numeral": "V", "diatonic": False, "degree": None},
    ]
    sheet = lyrics.chord_sheet(lines, chords, field, duration=20.0)
    assert sheet[0]["kind"] == "chords" and [c["label"] for c in sheet[0]["chords"]] == ["Am"]  # intro
    lyric = next(i for i in sheet if i["kind"] == "lyric")
    assert lyric["text"] == "one two three four"
    placed = {c["label"]: c for c in lyric["chords"]}
    assert placed["Am"]["pos"] == 0
    assert placed["F"]["pos"] == lyric["text"].index("two")  # 11.05 s: "two" is being sung
    assert placed["E"]["pos"] == lyric["text"].index("three")
    assert placed["E"]["fn"] == "X" and placed["F"]["fn"] == "S"
    assert sheet[-1]["kind"] == "chords"  # outro after the last line


def test_lyrics_endpoint_aligns_with_whisper_stubbed(tmp_path: Path, monkeypatch):
    calls = {}

    def fake_transcribe(audio, model_name="turbo", language=None, prompt=None):
        calls["prompt"] = prompt
        heard = _heard([("hello", 0.5), ("world", 1.0), ("again", 2.0)])
        return heard, {"model": model_name, "device": "cpu", "language": "en", "segments": []}

    monkeypatch.setattr(lyrics, "transcribe_words", fake_transcribe)
    client = TestClient(create_app(workspace_root=tmp_path))
    buf = io.BytesIO()
    sf.write(buf, np.zeros(22050 * 3, dtype=np.float32), 22050, format="WAV")
    pid = client.post(
        "/api/projects",
        files={"file": ("song.wav", buf.getvalue(), "audio/wav")},
        data={"is_isolated_stem": "true", "name": "Teste", "mode": "classic"},
    ).json()["job_id"]
    assert client.get(f"/api/projects/{pid}/lyrics").json()["lyrics"] is None

    res = client.post(f"/api/projects/{pid}/lyrics", json={"text": "[Verse]\nHello world\nagain", "model": "small"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert calls["prompt"].startswith("[Verse] Hello world")
    assert data["lyrics"]["stats"]["matched_share"] == 1.0
    assert [i["kind"] for i in data["sheet"]][:2] == ["section", "lyric"]
    assert data["has_harmony"] is False
    assert client.get(f"/api/projects/{pid}/lyrics").json()["lyrics"]["lines"][1]["words"][0]["text"] == "again"
