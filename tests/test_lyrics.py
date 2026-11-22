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


def _timed(text: str, t0: float = 10.0, line_s: float = 3.0, gaps: dict[int, float] | None = None) -> list[dict]:
    """Align `text` to a fake Whisper that heard every word, one line every `line_s` seconds
    (plus extra pauses before the lines in `gaps`)."""
    heard, t = [], t0
    for i, line in enumerate(lyrics.parse_lyrics(text)):
        t += (gaps or {}).get(i, 0.0)
        for k, w in enumerate(line["words"]):
            heard.append({"text": w, "start": t + k * 0.4, "end": t + k * 0.4 + 0.35, "prob": 0.9})
        t += line_s
    return lyrics.align_lyrics(text, heard)


SONG = """[Verse]
Sangue nel cielo
Fiamme nell'oscurità
Spada affilata

[Verse 2]
Lame scintillano
Cuore di guerriero
Draghi nel mirino

[Chorus]
Ali spezzate
Furore
Vendetta
Draghi crollano

[Guitar Solo]

[Verse 3]
Forza sovrumana
Armatura splendente
Fuoco contro fuoco

[Bridge]
Urla nella notte
Cenere sotto i piedi
Lacrime e potere

[Chorus]
Ali spezzate
Furore
Vendetta
Draghi crollano
"""


def test_infer_sections_from_tags_with_intro_solo_and_outro():
    lines = _timed(SONG, gaps={10: 12.0})  # a 12 s pause before Verse 3: the solo
    secs = lyrics.infer_sections(SONG, lines, chords=[], duration=lines[-1]["end"] + 20)
    assert [s["name"] for s in secs] == [
        "Intro", "Verso", "Verso 2", "Refrão (1)", "Solo", "Verso 3", "Ponte", "Refrão (2)", "Outro",
    ]
    assert secs[0]["start"] == 0.0 and secs[-1]["end"] == round(lines[-1]["end"] + 20, 2)
    assert all(a["end"] == b["start"] for a, b in zip(secs, secs[1:]))  # contiguous
    assert secs[4]["kind"] == "instrumental" and secs[4]["source"] == "tag"
    assert secs[1]["start"] == round(lines[0]["start"] - lyrics.LEAD_IN_S, 2)


def test_infer_sections_snaps_to_chord_changes():
    text = "[Verse]\nlinha um\nlinha dois\n[Chorus]\nrefrão um\nrefrão dois"
    lines = _timed(text, t0=1.0)
    chorus_t = lines[2]["start"]
    chords = [{"start": 0.0}, {"start": chorus_t - 0.8}, {"start": chorus_t + 2.0}]
    secs = lyrics.infer_sections(text, lines, chords, duration=lines[-1]["end"] + 1)
    assert [s["name"] for s in secs] == ["Verso", "Refrão"]
    assert secs[1]["start"] == round(chorus_t - 0.8, 2)


def test_infer_sections_without_tags_finds_the_repeated_chorus():
    text = ("primeira estrofe aqui\nsegue a história\n\n"
            "o refrão que volta\ncantado de novo\n\n"
            "outra estrofe nova\ncom outra história\n\n"
            "o refrão que volta\ncantado outra vez")
    lines = _timed(text, t0=1.0)
    secs = lyrics.infer_sections(text, lines, chords=[], duration=lines[-1]["end"] + 1)
    assert [s["name"] for s in secs] == ["Verso 1", "Refrão (1)", "Verso 2", "Refrão (2)"]
    assert {s["source"] for s in secs} == {"guess", "repeat"}


def test_parts_from_lyrics_endpoint(tmp_path: Path, monkeypatch):
    text = "[Verse]\nlinha um\nlinha dois\n[Chorus]\nrefrão um\nrefrão dois"
    lines = _timed(text, t0=5.0)
    heard = [{**w, "prob": 0.9} for l in lines for w in l["words"]]
    monkeypatch.setattr(lyrics, "transcribe_words", lambda audio, **kw: (
        heard, {"model": "small", "device": "cpu", "language": "pt", "segments": []}))
    client = TestClient(create_app(workspace_root=tmp_path))
    buf = io.BytesIO()
    sf.write(buf, np.zeros(22050 * 60, dtype=np.float32), 22050, format="WAV")
    pid = client.post(
        "/api/projects",
        files={"file": ("song.wav", buf.getvalue(), "audio/wav")},
        data={"is_isolated_stem": "true", "name": "Teste", "mode": "classic"},
    ).json()["job_id"]
    # without lyrics there is nothing to infer from
    assert client.post(f"/api/projects/{pid}/parts/from-lyrics").status_code == 422
    res = client.post(f"/api/projects/{pid}/lyrics", json={"text": text, "model": "small"})
    assert res.status_code == 200, res.text
    # the voice starts at 5 s: what comes before it is the intro
    assert [s["name"] for s in res.json()["sections"]] == ["Intro", "Verso", "Refrão", "Outro"]
    res = client.post(f"/api/projects/{pid}/parts/from-lyrics", params={"only_intro": "true"})
    assert res.status_code == 200 and res.json()["created"] == 1
    assert [p["name"] for p in res.json()["job"]["parts"]] == ["Intro"]
    res = client.post(f"/api/projects/{pid}/parts/from-lyrics")
    assert res.status_code == 200 and res.json()["created"] == 3
    assert [p["name"] for p in res.json()["job"]["parts"]] == ["Intro", "Verso", "Refrão", "Outro"]
    # running it again does not duplicate the parts
    assert client.post(f"/api/projects/{pid}/parts/from-lyrics").json()["created"] == 0


def test_fit_lines_to_range_squeezes_the_stanza_inside_the_part():
    text = "[Verse]\nlinha um dois\nlinha tres quatro\nlinha cinco seis"
    lines = _timed(text, t0=10.0)  # lines at 10, 13, 16 (each ~1.15 s)
    assert lyrics.fit_lines_to_range(lines, 0, 3, 10.5, 16.7) == 2
    first, mid, last = lines
    assert first["start"] == 10.5 and first["end"] == 11.15  # squeezed, still ends where it did
    assert [w["start"] for w in first["words"]] == sorted(w["start"] for w in first["words"])
    assert mid["start"] == 13.0  # already inside: untouched
    assert last["start"] == 16.0 and last["end"] == 16.7  # its tail squeezed into the part's end
    assert all(w["end"] <= 16.7 for w in last["words"])
    # inside already: nothing to do
    assert lyrics.fit_lines_to_range(lines, 0, 3, 10.0, 20.0) == 0


def test_inserted_part_pulls_the_next_stanza_out_of_it(tmp_path: Path, monkeypatch):
    """A Ponte fitted over the start of the Refrão: the Refrão's first line, which fell inside the
    new part, moves to where the Refrão part starts now."""
    text = "[Verse]\nlinha um\nlinha dois\n[Chorus]\nrefrão um\nrefrão dois"
    lines = _timed(text, t0=5.0)  # Verso at 5 and 8 s, Refrão at 11 and 14 s
    heard = [{**w, "prob": 0.9} for l in lines for w in l["words"]]
    monkeypatch.setattr(lyrics, "transcribe_words", lambda audio, **kw: (
        heard, {"model": "small", "device": "cpu", "language": "pt", "segments": []}))
    client = TestClient(create_app(workspace_root=tmp_path))
    buf = io.BytesIO()
    sf.write(buf, np.zeros(22050 * 30, dtype=np.float32), 22050, format="WAV")
    pid = client.post(
        "/api/projects",
        files={"file": ("song.wav", buf.getvalue(), "audio/wav")},
        data={"is_isolated_stem": "true", "name": "Teste", "mode": "classic"},
    ).json()["job_id"]
    assert client.post(f"/api/projects/{pid}/lyrics", json={"text": text, "model": "small"}).status_code == 200
    assert client.post(f"/api/projects/{pid}/parts/from-lyrics").status_code == 200

    res = client.post(f"/api/projects/{pid}/parts", json={"name": "Ponte", "start": 9.5, "end": 11.5, "fit": True})
    assert res.status_code == 200, res.text
    assert res.json()["lyrics_changed"] is True
    assert any("Refrão" in c and "letra" in c for c in res.json()["changes"])
    ly = client.get(f"/api/projects/{pid}/lyrics").json()["lyrics"]
    assert ly["lines"][2]["start"] == 11.5  # was 11.0, inside the Ponte
    assert ly["lines"][1]["start"] == 8.0   # the Verso was not touched

    # moving a part's bounds does the same
    refrao = next(p for p in res.json()["job"]["parts"] if p["name"] == "Refrão")
    res = client.patch(f"/api/projects/{pid}/parts/{refrao['part_id']}", json={"start": 12.0})
    assert res.json()["lyrics_changed"] is True
    assert client.get(f"/api/projects/{pid}/lyrics").json()["lyrics"]["lines"][2]["start"] == 12.0


def test_parts_from_lyrics_skips_trailing_section_clamped_below_half_second(tmp_path: Path):
    import json
    from app.models import JobMetadata
    from app.projects import LYRICS_FILE, parts_from_lyrics

    job = JobMetadata(
        job_id="j1", created_at="2026-01-01T00:00:00Z", original_filename="a.wav",
        input_path="a.wav", duration=3.45,
    )
    lines = [
        {"section": "Verso", "new_section": True, "para": False, "start": 0.5, "end": 2.5,
         "words": [{"text": "oi", "start": 0.5, "end": 2.5, "matched": True}], "matched_share": 1.0},
        {"section": "Outro", "new_section": True, "para": False, "start": 3.35, "end": 3.60,
         "words": [{"text": "fim", "start": 3.35, "end": 3.60, "matched": True}], "matched_share": 1.0},
    ]
    (tmp_path / LYRICS_FILE).write_text(
        json.dumps({"text": "[Verse]\noi\n[Outro]\nfim", "lines": lines}), encoding="utf-8"
    )
    created = parts_from_lyrics(job, tmp_path)
    assert [p.name for p in created] == ["Verso"]






def test_untagged_instrumental_stretches_are_named_from_the_music():
    from app.pipeline import instrumental

    # 60 s song: verse 0-10, gap 10-22 (lead guitar over it), verse 22-30, gap 30-42 (drums + one chord), verse 42-50
    text = "linha um\nlinha dois\n\nlinha tres\nlinha quatro\n\nlinha cinco\nlinha seis"
    lines = []
    for start, words in [(0.5, ["linha", "um"]), (5.0, ["linha", "dois"]), (22.0, ["linha", "tres"]),
                         (26.0, ["linha", "quatro"]), (42.0, ["linha", "cinco"]), (46.0, ["linha", "seis"])]:
        ws = [{"text": w, "start": start + i, "end": start + i + 0.8, "matched": True} for i, w in enumerate(words)]
        lines.append({"section": None, "new_section": False, "para": start in (22.0, 42.0),
                      "start": ws[0]["start"], "end": ws[-1]["end"], "words": ws, "matched_share": 1.0})
    beats = np.arange(0, 60, 0.25)
    tracks = [
        {"role": None, "is_drum": True, "onsets": list(beats), "spans": []},
        {"role": "rhythm", "is_drum": False, "onsets": list(np.arange(0, 60, 0.5)), "spans": []},
        {"role": "lead", "is_drum": False, "onsets": list(np.arange(11, 21.5, 0.25)),
         "spans": [(t, t + 0.25) for t in np.arange(11, 21.5, 0.25)]},
    ]
    act = instrumental.build_activity(tracks, 60.0)
    chords = [{"label": "Am", "start": 0, "end": 30}, {"label": "E5", "start": 30, "end": 42},
              {"label": "C", "start": 42, "end": 60}]
    secs = lyrics.infer_sections(text, lines, chords, 60.0,
                                 classify=lambda s, e: instrumental.classify(s, e, act, chords))
    names = [s["name"] for s in secs]
    assert "Solo" in names and "Breakdown" in names, names
    solo = next(s for s in secs if s["name"] == "Solo")
    assert solo["source"] == "inferred" and "guitarra solo" in solo["why"]


def test_retag_keeps_the_timing_and_rejects_changed_words():
    import pytest

    text = "linha um\nlinha dois\n\nrefrão um\nrefrão dois"
    lines = _timed(text, t0=5.0)
    new = lyrics.retag_lines("[Verso 1]\nlinha um\nlinha dois\n\n[Refrão]\nrefrão um\nrefrão dois", lines)
    assert [l["start"] for l in new] == [l["start"] for l in lines]
    assert new[0]["section"] == "Verso 1" and new[0]["new_section"]
    assert new[2]["section"] == "Refrão" and new[2]["new_section"]
    with pytest.raises(lyrics.LyricsError):
        lyrics.retag_lines("[Verso]\nlinha um\nlinha TRES\n\nrefrão um\nrefrão dois", lines)


def test_words_heard_far_from_their_line_are_not_stretched_over_a_solo():
    text = "[Verso]\nlacrime e potere\n\n[Refrão]\nali spezzate"
    heard = [
        {"text": "lacrime", "start": 61.7, "end": 62.5, "prob": 0.9},
        {"text": "e", "start": 70.6, "end": 72.0, "prob": 0.3},        # "heard" during the solo
        {"text": "potere", "start": 120.3, "end": 121.7, "prob": 0.3},
        {"text": "ali", "start": 122.3, "end": 123.9, "prob": 0.9},
        {"text": "spezzate", "start": 123.9, "end": 125.2, "prob": 0.9},
    ]
    lines = lyrics.align_lyrics(text, heard)
    assert lines[0]["end"] < 66.0, lines[0]
    assert lines[1]["start"] == 122.3
    # lyrics saved before the fix are repaired the same way
    broken = [dict(l, words=[dict(w) for w in l["words"]]) for l in lines]
    broken[0]["words"][2].update(start=120.3, end=121.7, matched=True)
    broken[0]["end"] = 121.7
    assert lyrics.repair_lines(broken) and broken[0]["end"] < 66.0
    assert not lyrics.repair_lines(broken)  # idempotent


def test_line_start_moves_to_the_voice_onset_only_across_silence():
    sr = 16000
    t = np.arange(int(sr * 6)) / sr
    voice = np.zeros_like(t)
    voice[(t >= 1.0) & (t < 1.6)] = 0.5   # an earlier sung word
    voice[(t >= 3.0) & (t < 5.0)] = 0.5   # the line really starts at 3.0 s
    audio = (voice * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    act = lyrics.voice_activity(audio, sr)

    def line(start, words):
        ws = [{"text": w, "start": start + i * 0.4, "end": start + i * 0.4 + 0.35, "matched": True} for i, w in enumerate(words)]
        return {"section": None, "new_section": False, "para": False, "start": ws[0]["start"], "end": ws[-1]["end"],
                "words": ws, "matched_share": 1.0}

    early = [line(2.2, ["sangue", "nel", "cielo"])]          # Whisper put it in the silence before 3.0
    assert lyrics.refine_line_starts(early, act) == 1
    assert abs(early[0]["start"] - 3.0) < 0.05
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(early[0]["words"], early[0]["words"][1:]))
    sung = [line(1.2, ["cuore", "di", "guerriero"])]         # already inside the voice: nothing to fix
    assert lyrics.refine_line_starts(sung, act) == 0
