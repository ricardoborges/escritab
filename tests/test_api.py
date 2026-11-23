import io
import json
from pathlib import Path
from fastapi.testclient import TestClient
import numpy as np
import soundfile as sf
from app.main import create_app
from app.models import NoteEvent
from app.projects import slice_notes


def _make_wav_bytes(freq_hz: float = 110.0, duration_s: float = 1.0, sr: int = 22050) -> bytes:
    """Plucked notes every 0.5 s (a decaying tone), so any excerpt holds note onsets."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    since_pluck = t % 0.5
    audio = (0.8 * np.sin(2 * np.pi * freq_hz * t) * np.exp(-6.0 * since_pluck)).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV")
    return buf.getvalue()


def _create_project(client: TestClient, wav: bytes, name: str = "Minha música") -> dict:
    res = client.post(
        "/api/projects",
        files={"file": ("suno_guitar.wav", wav, "audio/wav")},
        data={"is_isolated_stem": "true", "name": name, "mode": "classic"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_project_part_pipeline_isolated_stem(tmp_path: Path):
    client = TestClient(create_app(workspace_root=tmp_path))
    assert len(client.get("/api/tunings").json()) == 8

    project = _create_project(client, _make_wav_bytes(duration_s=3.0))
    pid = project["job_id"]
    assert project["name"] == "Minha música"
    assert project["is_isolated_stem"] is True
    assert "guitar" in project["stems"]
    assert abs(project["duration"] - 3.0) < 0.05

    peaks = client.get(f"/api/projects/{pid}/peaks").json()
    assert peaks["peaks"] and max(peaks["peaks"]) == 1.0

    listed = client.get("/api/projects").json()
    assert listed[0]["job_id"] == pid and listed[0]["name"] == "Minha música"

    res_part = client.post(f"/api/projects/{pid}/parts", json={"name": "Riff", "start": 0.5, "end": 2.5})
    assert res_part.status_code == 200
    part_id = res_part.json()["part"]["part_id"]

    res_gen = client.post(
        f"/api/projects/{pid}/parts/{part_id}/generate",
        json={
            "transcription": {
                "engine": "basic_pitch", "source": "stem", "stem": "guitar",
                "onset_threshold": 0.3, "frame_threshold": 0.2, "minimum_note_length_ms": 40.0,
                "tuning": "drop_d", "bpm": 120.0,
            },
            "tab": {"tuning": "drop_d", "max_frets": 24, "fret_preference": "low", "bpm": 120.0},
        },
    )
    assert res_gen.status_code == 200, res_gen.text
    gen = res_gen.json()
    assert gen["part"]["stats"]["total_notes"] >= 1
    assert set(gen["part"]["artifacts"]) == {"midi", "musicxml", "gp5", "txt"}
    assert gen["ascii_preview"]
    assert "Minha música — Riff" in gen["ascii_preview"]

    res_file = client.get(f"/api/projects/{pid}/parts/{part_id}/files/tab.gp5")
    assert res_file.status_code == 200 and len(res_file.content) > 50

    # renaming keeps the tab; moving the excerpt drops it (it no longer matches)
    renamed = client.patch(f"/api/projects/{pid}/parts/{part_id}", json={"name": "Riff principal"}).json()
    assert renamed["part"]["name"] == "Riff principal" and renamed["part"]["artifacts"]
    moved = client.patch(f"/api/projects/{pid}/parts/{part_id}", json={"start": 1.0}).json()
    assert moved["part"]["start"] == 1.0 and moved["part"]["artifacts"] == {}
    assert client.get(f"/api/projects/{pid}/parts/{part_id}/files/tab.gp5").status_code == 404

    assert client.delete(f"/api/projects/{pid}/parts/{part_id}").json()["job"]["parts"] == []
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_part_rejects_tiny_range_and_multitrack_without_analysis(tmp_path: Path):
    client = TestClient(create_app(workspace_root=tmp_path))
    pid = _create_project(client, _make_wav_bytes(duration_s=2.0))["job_id"]
    res = client.post(f"/api/projects/{pid}/parts", json={"name": "x", "start": 1.0, "end": 1.2})
    assert res.status_code == 400

    part_id = client.post(f"/api/projects/{pid}/parts", json={"name": "Intro", "start": 0, "end": 1.5}).json()["part"]["part_id"]
    res = client.post(
        f"/api/projects/{pid}/parts/{part_id}/generate",
        json={"transcription": {"source": "multitrack"}, "tab": {}},
    )
    assert res.status_code == 400
    assert "MIDI completo" in res.json()["detail"]


def test_generate_returns_422_on_silent_excerpt(tmp_path: Path):
    client = TestClient(create_app(workspace_root=tmp_path))
    buf = io.BytesIO()
    sf.write(buf, np.zeros(22050 * 2, dtype=np.float32), 22050, format="WAV")
    pid = _create_project(client, buf.getvalue())["job_id"]
    part_id = client.post(f"/api/projects/{pid}/parts", json={"name": "Solo", "start": 0, "end": 2}).json()["part"]["part_id"]
    res = client.post(
        f"/api/projects/{pid}/parts/{part_id}/generate",
        json={
            "transcription": {"engine": "basic_pitch", "source": "stem", "stem": "guitar",
                              "onset_threshold": 0.7, "frame_threshold": 0.5},
            "tab": {},
        },
    )
    assert res.status_code == 422
    assert "Nenhuma nota" in res.json()["detail"]


def test_legacy_session_becomes_project_with_whole_song_part(tmp_path: Path):
    job_dir = tmp_path / "abc123"
    job_dir.mkdir()
    (job_dir / "input_old_song.wav").write_bytes(_make_wav_bytes(duration_s=1.5))
    (job_dir / "tab.musicxml").write_text("<score-partwise/>", encoding="utf-8")
    (job_dir / "tab.gp5").write_bytes(b"gp5")
    (job_dir / "job.json").write_text(json.dumps({
        "job_id": "abc123", "created_at": "2026-01-01T00:00:00+00:00",
        "original_filename": "old_song.wav", "input_path": "input_old_song.wav",
        "artifacts": {"musicxml": "tab.musicxml", "gp5": "tab.gp5"},
    }), encoding="utf-8")

    client = TestClient(create_app(workspace_root=tmp_path))
    project = client.get("/api/projects/abc123").json()
    assert project["name"] == "old song"
    assert project["duration"] > 1.4
    [part] = project["parts"]
    assert part["name"] == "Música inteira" and part["start"] == 0.0
    assert part["artifacts"] == {"musicxml": "tab.musicxml", "gp5": "tab.gp5"}
    res = client.get(f"/api/projects/abc123/parts/{part['part_id']}/files/tab.musicxml")
    assert res.status_code == 200
    # migrating twice must not duplicate the part
    assert len(client.get("/api/projects/abc123").json()["parts"]) == 1


def test_slice_notes_keeps_onsets_inside_and_shifts_to_zero():
    notes = [
        NoteEvent(pitch=40, start=0.5, end=1.5),   # starts well before: dropped
        NoteEvent(pitch=41, start=0.9, end=1.2),   # just before the edge: pulled in at 0
        NoteEvent(pitch=42, start=1.0, end=1.4),
        NoteEvent(pitch=44, start=2.8, end=4.0),   # rings past the end: clipped
        NoteEvent(pitch=45, start=3.0, end=3.5),   # starts at the end: dropped
    ]
    out = slice_notes(notes, 1.0, 3.0)
    assert [(n.pitch, round(n.start, 3), round(n.end, 3)) for n in out] == [(41, 0.0, 0.2), (42, 0.0, 0.4), (44, 1.8, 2.0)]


def test_root_serves_web_ui_and_assets(tmp_path: Path):
    client = TestClient(create_app(workspace_root=tmp_path))
    res_html = client.get("/")
    assert res_html.status_code == 200
    assert "alphaTab" in res_html.text
    assert "EscriTab" in res_html.text
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_system_reports_compute_device(tmp_path: Path):
    client = TestClient(create_app(workspace_root=tmp_path))
    info = client.get("/api/system").json()
    assert info["device"] in ("cuda", "cpu")
    assert "torch_version" in info
    if info["device"] == "cpu" and info["gpu_name"]:
        assert info["hint"]  # explains why the detected GPU isn't used


def test_part_lead_engine_writes_monophonic_tab(tmp_path: Path):
    client = TestClient(create_app(workspace_root=tmp_path))
    pid = _create_project(client, _make_wav_bytes(duration_s=3.0))["job_id"]
    part_id = client.post(f"/api/projects/{pid}/parts", json={"name": "Solo", "start": 0.0, "end": 3.0}).json()["part"]["part_id"]
    res = client.post(
        f"/api/projects/{pid}/parts/{part_id}/generate",
        json={
            "transcription": {
                "engine": "lead", "source": "stem", "stem": "guitar", "monophonic": True,
                "voicing_threshold": 0.4, "tuning": "standard_e", "bpm": 120.0,
            },
            "tab": {"tuning": "standard_e", "max_frets": 24, "fret_preference": "high", "bpm": 120.0},
        },
    )
    assert res.status_code == 200, res.text
    stats = res.json()["part"]["stats"]
    assert stats["total_notes"] >= 1
    assert stats["engine"].startswith("solo/riff monofônico")


def test_part_scales_endpoint_and_lead_with_scale(tmp_path: Path):
    client = TestClient(create_app(workspace_root=tmp_path))
    pid = _create_project(client, _make_wav_bytes(freq_hz=440.0, duration_s=3.0))["job_id"]
    part_id = client.post(f"/api/projects/{pid}/parts", json={"name": "Solo", "start": 0.0, "end": 3.0}).json()["part"]["part_id"]

    res = client.get(f"/api/projects/{pid}/parts/{part_id}/scales")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["scales"] and len(data["profile"]) == 12 and data["has_harmony"] is False
    assert all(9 in {n["pc"] for n in s["notes"]} for s in data["scales"][:3])  # an A (440 Hz) is in them
    assert client.get(f"/api/projects/{pid}/parts/nope/scales").status_code == 404

    body = {
        "transcription": {"engine": "lead", "source": "stem", "stem": "guitar", "scale": "9:minor_pentatonic",
                          "tuning": "standard_e", "bpm": 120.0},
        "tab": {"tuning": "standard_e", "max_frets": 24, "fret_preference": "high", "bpm": 120.0},
    }
    res = client.post(f"/api/projects/{pid}/parts/{part_id}/generate", json=body)
    assert res.status_code == 200, res.text
    part = res.json()["part"]
    assert "escala A pentatônica menor" in part["stats"]["engine"]
    assert part["transcription_params"]["scale"] == "9:minor_pentatonic"

    body["transcription"]["scale"] = "9:bogus"
    assert client.post(f"/api/projects/{pid}/parts/{part_id}/generate", json=body).status_code == 400


def test_html_has_no_duplicate_ids_and_js_assigns_sequential_melodic_channels(tmp_path: Path):
    import re
    client = TestClient(create_app(workspace_root=tmp_path))
    html = client.get("/").text
    ids = re.findall(r'\bid="([^"]+)"', html)
    duplicates = {x for x in ids if ids.count(x) > 1}
    assert not duplicates, f"Duplicate HTML IDs found: {duplicates}"

    js = client.get("/static/app.js").text
    assert "tabLoopCheck" in js
    # melodic channel assignment must skip drums without advancing the melodic index by raw track.index
    assert "t.index % MELODIC_CHANNELS.length" not in js


def test_multitrack_and_separate_preserve_parts_created_while_running(tmp_path: Path, monkeypatch):
    from app.pipeline import dettato_transcriber

    client = TestClient(create_app(workspace_root=tmp_path))
    pid = _create_project(client, _make_wav_bytes(duration_s=3.0))["job_id"]

    def fake_transcribe_multitrack(audio_path, full_midi, split_guitar_roles=True):
        # Simulate the user creating a part while Dettato is running
        res_part = client.post(f"/api/projects/{pid}/parts", json={"name": "Durante Dettato", "start": 0.0, "end": 1.5})
        assert res_part.status_code == 200
        full_midi.write_bytes(b"MThd")
        return [], {"device": "cpu"}

    monkeypatch.setattr(dettato_transcriber, "is_available", lambda: True)
    monkeypatch.setattr(dettato_transcriber, "transcribe_multitrack", fake_transcribe_multitrack)

    res = client.post(f"/api/projects/{pid}/multitrack")
    assert res.status_code == 200
    assert [p["name"] for p in res.json()["job"]["parts"]] == ["Durante Dettato"]
    assert [p["name"] for p in client.get(f"/api/projects/{pid}").json()["parts"]] == ["Durante Dettato"]

    def fake_separate_stems(input_audio_path, job_dir, is_isolated_stem=False, model_name="htdemucs_6s"):
        res_part = client.post(f"/api/projects/{pid}/parts", json={"name": "Durante Demucs", "start": 1.0, "end": 2.5})
        assert res_part.status_code == 200
        return {"guitar": "stem_guitar.wav"}, "cpu"

    monkeypatch.setattr("app.main.separate_stems", fake_separate_stems)
    res_sep = client.post(f"/api/projects/{pid}/separate")
    assert res_sep.status_code == 200
    assert [p["name"] for p in client.get(f"/api/projects/{pid}").json()["parts"]] == ["Durante Dettato", "Durante Demucs"]


def test_excerpt_audio_pads_mismatched_stem_lengths_and_normalizes_before_gate(tmp_path: Path):
    from app.models import JobMetadata, Part, TranscriptionParams
    from app.projects import _basic_pitch_excerpt, _excerpt_audio

    sr = 22050
    s1 = np.full(sr * 1, 0.6, dtype=np.float32)      # 1.0 s
    s2 = np.full(sr * 2, 0.6, dtype=np.float32)      # 2.0 s
    sf.write(tmp_path / "s1.wav", s1, sr)
    sf.write(tmp_path / "s2.wav", s2, sr)

    job = JobMetadata(
        job_id="j1", created_at="2026-01-01T00:00:00Z", original_filename="a.wav",
        input_path="s2.wav", duration=2.0, stems={"guitar": "s1.wav", "other": "s2.wav"},
    )
    part = Part(part_id="p1", name="P", start=0.0, end=2.0, created_at="2026-01-01T00:00:00Z")
    params = TranscriptionParams(stem="guitar+other", bandpass_enabled=False, noise_gate_db=-20.0)

    mixed, _ = _excerpt_audio(job, tmp_path, part, params, sr)
    assert mixed is not None
    # Must preserve the full 2.0 s length of the longest stem, not truncate to 1.0 s
    assert len(mixed) == sr * 2
    # And peak must be normalized so summing two 0.6 stems doesn't clip > 1.0
    assert float(np.max(np.abs(mixed))) <= 1.0



def test_default_name_drops_the_track_number():
    from app.projects import default_name

    assert default_name("01 - Il Sussurro dei Draghi v4.wav") == "Il Sussurro dei Draghi v4"
    assert default_name("1. Song.mp3") == "Song"
    assert default_name("7 Rings.mp3") == "7 Rings"
    assert default_name("1984.wav") == "1984"


def test_stems_mix_sums_the_chosen_stems(tmp_path: Path):
    from app.models import JobMetadata
    from app.projects import PartError, stems_mix
    import pytest

    sr = 22050
    for name, val in [("bass", 0.2), ("guitar", 0.3)]:
        sf.write(str(tmp_path / f"stem_{name}.wav"), np.full((sr, 2), val, dtype=np.float32), sr)
    job = JobMetadata(job_id="j", created_at="2026-01-01T00:00:00Z", original_filename="a.wav", input_path="a.wav",
                      stems={"bass": "stem_bass.wav", "guitar": "stem_guitar.wav"})
    assert stems_mix(job, tmp_path, ["guitar"]).name == "stem_guitar.wav"
    out = stems_mix(job, tmp_path, ["guitar", "bass"])
    assert out.name == "mix_bass+guitar.wav"
    data, rate = sf.read(str(out))
    assert rate == sr and data.shape == (sr, 2) and abs(data[100, 0] - 0.5) < 0.01
    with pytest.raises(PartError):
        stems_mix(job, tmp_path, ["vocals"])


def test_new_part_fits_into_the_timeline(tmp_path: Path):
    from app.models import JobMetadata
    from app.projects import insert_part_fitted, new_part

    job = JobMetadata(job_id="j", created_at="2026-01-01T00:00:00Z", original_filename="a.wav", input_path="a.wav",
                      duration=100.0)
    new_part(job, "Verso 3", 40.0, 56.0)
    new_part(job, "Verso 4", 56.0, 64.0)
    new_part(job, "Curta", 52.0, 52.6)
    # a breakdown at the end of verse 3, running into verse 4
    part, changes = insert_part_fitted(job, tmp_path, "Breakdown", 50.0, 57.0)
    bounds = {p.name: (p.start, p.end) for p in job.parts}
    assert bounds["Verso 3"] == (40.0, 50.0)
    assert bounds["Breakdown"] == (50.0, 57.0)
    assert bounds["Verso 4"] == (57.0, 64.0)
    assert "Curta" not in bounds and len(changes) == 3
    # a part in the middle of another splits it
    insert_part_fitted(job, tmp_path, "Solo", 42.0, 45.0)
    verses = sorted((p.start, p.end) for p in job.parts if p.name == "Verso 3")
    assert verses == [(40.0, 42.0), (45.0, 50.0)]



def test_export_qtab(tmp_path: Path):
    import zipfile

    client = TestClient(create_app(workspace_root=tmp_path))
    pid = _create_project(client, _make_wav_bytes(duration_s=3.0))["job_id"]
    part_id = client.post(f"/api/projects/{pid}/parts", json={"name": "Riff", "start": 0.5, "end": 2.5}).json()["part"]["part_id"]

    # no harmony analysis yet: nothing to show
    assert client.get(f"/api/projects/{pid}/export.qtab").status_code == 422

    field = [{"degree": d, "function": f} for d, f in enumerate("TSTSDTD", start=1)]
    chords = [
        {"start": 0.0, "end": 1.2, "label": "Em", "root": 4, "quality": "min", "bass": None,
         "numeral": "i", "diatonic": True, "degree": 1},
        {"start": 1.2, "end": 2.0, "label": "B", "root": 11, "quality": "maj", "bass": None,
         "numeral": "V", "diatonic": False, "degree": 5},
        {"start": 2.0, "end": 3.0, "label": "C", "root": 0, "quality": "maj", "bass": None,
         "numeral": "bVI", "diatonic": True, "degree": 6},
    ]
    (tmp_path / pid / "harmony.json").write_text(json.dumps(
        {"key": {"tonic": 4, "tonic_name": "E", "name": "Mi menor", "mode": "aeolian"},
         "field": field, "chords": chords}), encoding="utf-8")

    # whole song at full speed
    res = client.get(f"/api/projects/{pid}/export.qtab")
    assert res.status_code == 200, res.text
    assert ".qtab" in res.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(res.content))
    assert set(z.namelist()) == {"manifest.json", "backing.flac", "chords.json", "sections.json", "lyrics.json"}
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["format"] == "qtab" and manifest["version"] == 1 and manifest["rate"] == 1.0
    assert manifest["key"]["name"] == "Mi menor" and manifest["functions"]["T"]["color"] == "#10b981"
    assert abs(manifest["audio"]["duration"] - 3.0) < 0.05
    got = json.loads(z.read("chords.json"))
    assert [(c["label"], c["function"], c["start"], c["end"]) for c in got] == [
        ("Em", "T", 0.0, 1.2), ("B", "X", 1.2, 2.0), ("C", "T", 2.0, 3.0)]
    assert json.loads(z.read("sections.json")) == [{"id": part_id, "name": "Riff", "start": 0.5, "end": 2.5}]
    assert json.loads(z.read("lyrics.json")) == []
    audio, sr = sf.read(io.BytesIO(z.read("backing.flac")))
    assert abs(len(audio) / sr - 3.0) < 0.05

    # an excerpt at half speed: times start at 0, are clipped to it and stretched
    res = client.get(f"/api/projects/{pid}/export.qtab", params={"rate": 0.5, "start": 1.0, "end": 2.0, "label": "Riff"})
    assert res.status_code == 200, res.text
    assert "50%25" in res.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(res.content))
    manifest = json.loads(z.read("manifest.json"))
    assert abs(manifest["audio"]["duration"] - 2.0) < 0.05
    assert manifest["range"] == {"start": 1.0, "end": 2.0}
    got = json.loads(z.read("chords.json"))
    assert [(c["label"], c["start"], c["end"]) for c in got] == [("Em", 0.0, 0.4), ("B", 0.4, 2.0)]
    assert json.loads(z.read("sections.json"))[0] == {"id": part_id, "name": "Riff", "start": 0.0, "end": 2.0}

    assert client.get(f"/api/projects/{pid}/export.qtab", params={"rate": 3}).status_code == 422
