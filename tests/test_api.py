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
