import io
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pretty_midi
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import TranscriptionParams
from app.pipeline import dettato_transcriber, midi_transcriber


# ---- a stand-in for dettato.Dettato (same Track / Transcription shape, no weights) ----

@dataclass
class FakeTrack:
    name: str
    program: int
    notes: np.ndarray
    is_drum: bool = False


@dataclass
class FakeTranscription:
    tracks: list
    info: dict = field(default_factory=dict)

    def save_midi(self, path: str) -> None:
        pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
        for t in self.tracks:
            ins = pretty_midi.Instrument(program=0 if t.is_drum else t.program, is_drum=t.is_drum, name=t.name)
            for on, off, pitch, vel in t.notes:
                ins.notes.append(pretty_midi.Note(velocity=int(vel), pitch=int(pitch), start=float(on), end=float(off)))
            pm.instruments.append(ins)
        pm.write(path)

    def summary(self) -> dict:
        return {"tracks": [{"name": t.name, "notes": len(t.notes)} for t in self.tracks]}


def _song() -> FakeTranscription:
    return FakeTranscription(tracks=[
        FakeTrack("Piano", 0, np.array([[0.0, 0.5, 60, 90], [0.5, 1.0, 64, 90]], float)),
        FakeTrack("Electric Guitar", 26, np.array([[0.0, 0.4, 40, 100], [0.5, 0.9, 47, 100], [1.0, 1.4, 52, 100]], float)),
        FakeTrack("Electric Bass", 33, np.array([[0.0, 1.0, 28, 100]], float)),
        FakeTrack("Drums", 0, np.array([[0.0, 0.1, 36, 110], [0.5, 0.6, 38, 100]], float), is_drum=True),
    ])


class FakeModel:
    device = "cpu"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result or _song(), error, []

    def transcribe(self, path):
        self.calls.append(path)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def fake_dettato(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(dettato_transcriber, "is_available", lambda: True)
    monkeypatch.setattr(dettato_transcriber, "_get_model", lambda: model)
    monkeypatch.setattr(dettato_transcriber, "_model", model)
    return model


def _wav_bytes(sr: int = 44100, seconds: float = 1.5) -> bytes:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    buf = io.BytesIO()
    sf.write(buf, (0.5 * np.sin(2 * np.pi * 110 * t)).astype(np.float32), sr, format="WAV")
    return buf.getvalue()


# ---- unit ----

def test_multitrack_writes_midi_and_flags_guitar_tracks(tmp_path: Path, fake_dettato):
    tracks, info = dettato_transcriber.transcribe_multitrack(tmp_path / "in.wav", tmp_path / "full.mid")

    assert [t.name for t in tracks] == ["Piano", "Electric Guitar", "Electric Bass", "Drums"]
    assert [t.is_guitar for t in tracks] == [False, True, False, False]
    assert tracks[3].is_drum and tracks[3].program is None
    assert tracks[1].lowest_note == "E2" and tracks[1].highest_note == "E3"
    assert dettato_transcriber.default_tab_tracks(tracks) == [1]
    assert (tmp_path / "full.mid").exists() and (tmp_path / "full.json").exists()
    assert info["device"] == "cpu"

    # MIDI instrument order == track index, and drums are never merged
    assert [n.pitch for n in dettato_transcriber.notes_from_midi_tracks(tmp_path / "full.mid", [1])] == [40, 47, 52]
    assert dettato_transcriber.notes_from_midi_tracks(tmp_path / "full.mid", [3]) == []


def test_stem_engine_keeps_all_pitched_tracks(tmp_path: Path, fake_dettato):
    notes, stats = midi_transcriber.transcribe_audio_to_midi(
        tmp_path / "stem.wav", tmp_path / "g.mid", TranscriptionParams(engine="dettato", suppress_harmonics=False)
    )
    assert stats["engine"] == "dettato"
    # piano (2) + guitar (3) + bass (1), never drum hits; the bass E1 folds up to E2 and merges
    # with the guitar's simultaneous E2, so 5 notes remain
    assert stats["total_notes"] == len(notes) == 5
    assert all(40 <= n.pitch <= 88 for n in notes)


def test_falls_back_to_basic_pitch_when_dettato_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(dettato_transcriber, "is_available", lambda: True)
    monkeypatch.setattr(dettato_transcriber, "_get_model", lambda: FakeModel(error=RuntimeError("CUDA OOM")))
    monkeypatch.setattr(midi_transcriber, "_run_basic_pitch_or_fallback", lambda path, params: [])
    _, stats = midi_transcriber.transcribe_audio_to_midi(tmp_path / "x.wav", tmp_path / "x.mid", TranscriptionParams())
    assert stats["engine"].startswith("basic_pitch (fallback")
    assert "CUDA OOM" in stats["engine"]


# ---- API ----

def _new_project(client: TestClient, mode: str = "dettato", isolated: bool = False) -> dict:
    res = client.post(
        "/api/projects",
        files={"file": ("song.wav", _wav_bytes(), "audio/wav")},
        data={"is_isolated_stem": str(isolated).lower(), "mode": mode},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _generate(client: TestClient, pid: str, part_id: str, transcription: dict):
    return client.post(
        f"/api/projects/{pid}/parts/{part_id}/generate",
        json={"transcription": transcription, "tab": {"tuning": "standard_e", "bpm": 120.0}},
    )


def test_api_full_song_multitrack_parts_to_tab(tmp_path: Path, fake_dettato):
    client = TestClient(create_app(workspace_root=tmp_path))
    project = _new_project(client)
    pid = project["job_id"]
    assert project["mode"] == "dettato"
    assert project["stems"] == {}  # Dettato mode never runs Demucs

    whole = client.post(f"/api/projects/{pid}/parts", json={"name": "Tudo", "start": 0, "end": 1.5}).json()["part"]
    # tab from multitrack before the multitrack exists -> clear 400
    assert _generate(client, pid, whole["part_id"], {"source": "multitrack"}).status_code == 400

    res = client.post(f"/api/projects/{pid}/multitrack")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["default_tab_tracks"] == [1]
    assert fake_dettato.calls[-1].endswith("input_song.wav")  # original upload, full sample rate
    assert client.get(f"/api/projects/{pid}/files/full_song.mid").status_code == 200

    # no Demucs needed: the guitar track of the full-song MIDI becomes the tab
    res = _generate(client, pid, whole["part_id"], {"source": "multitrack", "suppress_harmonics": False})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["part"]["stats"]["total_notes"] == 3
    assert body["part"]["transcription_params"]["track_indices"] == [1]
    assert "gp5" in body["part"]["artifacts"]

    # a part only slices the analysis: guitar notes at 0.5 and 1.0 s, the model is not run again
    calls = len(fake_dettato.calls)
    riff = client.post(f"/api/projects/{pid}/parts", json={"name": "Riff", "start": 0.5, "end": 1.5}).json()["part"]
    res = _generate(client, pid, riff["part_id"], {"source": "multitrack", "suppress_harmonics": False})
    assert res.json()["part"]["stats"]["total_notes"] == 2
    assert len(fake_dettato.calls) == calls

    # explicit selection: piano track instead of guitar
    res = _generate(client, pid, whole["part_id"], {"source": "multitrack", "track_indices": [0], "suppress_harmonics": False})
    assert res.json()["part"]["stats"]["total_notes"] == 2


def test_api_stem_parts_reuse_one_dettato_run(tmp_path: Path, fake_dettato):
    client = TestClient(create_app(workspace_root=tmp_path))
    pid = _new_project(client, mode="classic", isolated=True)["job_id"]
    totals = []
    for name, start, end in (("A", 0.0, 0.8), ("B", 0.5, 1.5)):
        part = client.post(f"/api/projects/{pid}/parts", json={"name": name, "start": start, "end": end}).json()["part"]
        res = _generate(client, pid, part["part_id"],
                        {"engine": "dettato", "source": "stem", "stem": "guitar", "suppress_harmonics": False})
        assert res.status_code == 200, res.text
        assert res.json()["part"]["stats"]["engine"] == "dettato (stem guitar)"
        totals.append(res.json()["part"]["stats"]["total_notes"])
    assert len(fake_dettato.calls) == 1  # the whole stem was transcribed once and cached
    assert all(t > 0 for t in totals)


def test_api_multitrack_503_when_dettato_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(dettato_transcriber, "is_available", lambda: False)
    client = TestClient(create_app(workspace_root=tmp_path))
    pid = _new_project(client)["job_id"]
    res = client.post(f"/api/projects/{pid}/multitrack")
    assert res.status_code == 503
    assert "pip install" in res.json()["detail"]


def test_multitrack_splits_guitar_into_base_and_solo(tmp_path: Path, monkeypatch):
    rows = []
    for k in range(40):                      # rhythm power chords + a solo note on top of each
        t = k * 0.19
        rows += [[t, t + 0.15, 50, 100], [t, t + 0.15, 57, 100], [t, t + 0.15, 62, 100]] if k < 20 else [[t, t + 0.15, 50, 100]]
        if k >= 20:
            rows.append([t + 0.005, t + 0.15, 70 + (k % 5), 100])
    song = FakeTranscription(tracks=[
        FakeTrack("electric guitar", 26, np.array(rows, float)),
        FakeTrack("violin", 40, np.array([[i * 0.25, i * 0.25 + 0.2, 64 + i % 4, 90] for i in range(30)], float)),
    ])
    model = FakeModel(result=song)
    monkeypatch.setattr(dettato_transcriber, "_get_model", lambda: model)
    monkeypatch.setattr(dettato_transcriber, "_model", model)

    tracks, info = dettato_transcriber.transcribe_multitrack(tmp_path / "in.wav", tmp_path / "full.mid")
    assert [(t.name, t.role) for t in tracks] == [
        ("electric guitar - Base", "rhythm"),
        ("electric guitar - Solo/Riff", "lead"),
        ("violin", "lead_candidate"),
    ]
    assert tracks[1].note_count == 20 and tracks[0].note_count == 80
    assert dettato_transcriber.default_tab_tracks(tracks) == [0, 1]
    assert info["split_guitar_roles"] is True
    # MIDI order matches the indices
    assert [i.name for i in pretty_midi.PrettyMIDI(str(tmp_path / "full.mid")).instruments][:2] == [
        "electric guitar - Base", "electric guitar - Solo/Riff"]

    tracks, _ = dettato_transcriber.transcribe_multitrack(
        tmp_path / "in.wav", tmp_path / "full2.mid", split_guitar_roles=False)
    assert [t.name for t in tracks] == ["electric guitar", "violin"]


def test_multitrack_encodes_project_bpm_and_beat_tempo_map(tmp_path: Path, fake_dettato):
    beats = [0.25, 0.65, 1.05, 1.45, 1.85]  # 150 BPM (0.40s per beat) after 0.25s lead-in
    out_mid = tmp_path / "full_150.mid"
    dettato_transcriber.transcribe_multitrack(
        tmp_path / "in.wav", out_mid, bpm=150.0, beats=beats
    )
    pm = pretty_midi.PrettyMIDI(str(out_mid))
    _, tempos = pm.get_tempo_changes()
    assert any(abs(t - 150.0) < 1.0 for t in tempos)
    assert not np.allclose(tempos, 120.0)
