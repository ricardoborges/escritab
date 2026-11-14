import io
from pathlib import Path
from fastapi.testclient import TestClient
import numpy as np
import soundfile as sf
from app.main import create_app


def _make_wav_bytes(freq_hz: float = 110.0, duration_s: float = 1.0, sr: int = 22050) -> bytes:
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    audio = (0.8 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV")
    return buf.getvalue()


def test_full_api_pipeline_isolated_stem(tmp_path: Path):
    app = create_app(workspace_root=tmp_path)
    client = TestClient(app)

    # 1. GET /api/tunings
    res_tunings = client.get("/api/tunings")
    assert res_tunings.status_code == 200
    assert len(res_tunings.json()) == 8

    # 2. POST /api/jobs/upload
    wav_bytes = _make_wav_bytes(freq_hz=110.0, duration_s=1.2)
    res_upload = client.post(
        "/api/jobs/upload",
        files={"file": ("suno_guitar.wav", wav_bytes, "audio/wav")},
        data={"is_isolated_stem": "true"},
    )
    assert res_upload.status_code == 200
    job = res_upload.json()
    job_id = job["job_id"]
    assert job["is_isolated_stem"] is True

    # 3. POST /api/jobs/{job_id}/separate
    res_sep = client.post(f"/api/jobs/{job_id}/separate", json={"model": "htdemucs_6s"})
    assert res_sep.status_code == 200
    sep_data = res_sep.json()
    assert "guitar" in sep_data["stems"]

    # 4. POST /api/jobs/{job_id}/transcribe
    res_trans = client.post(
        f"/api/jobs/{job_id}/transcribe",
        json={
            "stem": "guitar",
            "onset_threshold": 0.3,
            "frame_threshold": 0.2,
            "minimum_note_length_ms": 40.0,
            "suppress_harmonics": True,
            "tuning": "drop_d",
            "bpm": 120.0,
        },
    )
    assert res_trans.status_code == 200
    trans_data = res_trans.json()
    assert trans_data["stats"]["total_notes"] >= 1

    # 5. POST /api/jobs/{job_id}/tab
    res_tab = client.post(
        f"/api/jobs/{job_id}/tab",
        json={
            "tuning": "drop_d",
            "max_frets": 24,
            "fret_preference": "low",
            "bpm": 120.0,
        },
    )
    assert res_tab.status_code == 200
    tab_data = res_tab.json()
    assert "musicxml" in tab_data["artifacts"]
    assert "gp5" in tab_data["artifacts"]
    assert "txt" in tab_data["artifacts"]
    assert "ascii_preview" in tab_data

    # 6. GET /api/jobs/{job_id}/files/tab.gp5
    res_file = client.get(f"/api/jobs/{job_id}/files/tab.gp5")
    assert res_file.status_code == 200
    assert len(res_file.content) > 50


def test_transcribe_returns_422_on_silent_audio(tmp_path: Path):
    app = create_app(workspace_root=tmp_path)
    client = TestClient(app)

    buf = io.BytesIO()
    sf.write(buf, np.zeros(22050, dtype=np.float32), 22050, format="WAV")

    res_upload = client.post(
        "/api/jobs/upload",
        files={"file": ("silent.wav", buf.getvalue(), "audio/wav")},
        data={"is_isolated_stem": "true"},
    )
    job_id = res_upload.json()["job_id"]
    client.post(f"/api/jobs/{job_id}/separate", json={"model": "htdemucs_6s"})

    res_trans = client.post(
        f"/api/jobs/{job_id}/transcribe",
        json={"stem": "guitar", "onset_threshold": 0.7, "frame_threshold": 0.5},
    )
    assert res_trans.status_code == 422
    assert "Nenhuma nota" in res_trans.json()["detail"]


def test_root_serves_web_ui_and_assets(tmp_path: Path):
    app = create_app(workspace_root=tmp_path)
    client = TestClient(app)

    res_html = client.get("/")
    assert res_html.status_code == 200
    assert "AlphaTab" in res_html.text or "alphaTab" in res_html.text
    assert "EscriTab" in res_html.text

    res_css = client.get("/static/styles.css")
    assert res_css.status_code == 200

    res_js = client.get("/static/app.js")
    assert res_js.status_code == 200

