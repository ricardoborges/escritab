import io
from pathlib import Path
import numpy as np
import pretty_midi
import soundfile as sf
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import MidiTrackInfo
from app.pipeline import dettato_transcriber


def _wav_bytes(sr: int = 22050, seconds: float = 1.5) -> bytes:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    buf = io.BytesIO()
    sf.write(buf, (0.4 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32), sr, format="WAV")
    return buf.getvalue()


def test_multitrack_notes_payload_serializes_and_invalidates_cache_on_change(tmp_path: Path):
    midi_path = tmp_path / "full_song.mid"
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    gtr = pretty_midi.Instrument(program=29, is_drum=False, name="Electric Guitar - Solo/Riff")
    gtr.notes.append(pretty_midi.Note(velocity=105, pitch=64, start=0.5, end=1.25))
    gtr.notes.append(pretty_midi.Note(velocity=90, pitch=59, start=0.1, end=0.45))
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    drums.notes.append(pretty_midi.Note(velocity=110, pitch=36, start=0.0, end=0.1))
    pm.instruments.extend([gtr, drums])
    pm.write(str(midi_path))

    tracks = [
        MidiTrackInfo(index=0, name="Electric Guitar - Solo/Riff", program=29, is_drum=False, is_guitar=True, role="lead", note_count=2),
        MidiTrackInfo(index=1, name="Drums", program=None, is_drum=True, is_guitar=False, role=None, note_count=1),
    ]
    payload = dettato_transcriber.multitrack_notes_payload(midi_path, tracks, duration=2.0)
    assert payload["duration"] == 2.0
    assert len(payload["tracks"]) == 2
    # Sorted by (start, pitch): [0.1, 0.45, 59, 90] comes before [0.5, 1.25, 64, 105]
    assert payload["tracks"][0]["notes"] == [[0.1, 0.45, 59, 90], [0.5, 1.25, 64, 105]]
    assert payload["tracks"][0]["role"] == "lead"
    assert payload["tracks"][1]["is_drum"] is True
    assert payload["tracks"][1]["notes"] == [[0.0, 0.1, 36, 110]]

    # Overwriting the MIDI file with a new note must invalidate the cache
    pm2 = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    gtr2 = pretty_midi.Instrument(program=29, is_drum=False, name="Electric Guitar - Solo/Riff")
    gtr2.notes.append(pretty_midi.Note(velocity=100, pitch=67, start=0.2, end=0.8))
    pm2.instruments.append(gtr2)
    pm2.write(str(midi_path))
    tracks2 = [MidiTrackInfo(index=0, name="Electric Guitar - Solo/Riff", program=29, is_drum=False, is_guitar=True, role="lead", note_count=1)]
    payload2 = dettato_transcriber.multitrack_notes_payload(midi_path, tracks2, duration=1.5)
    assert payload2["tracks"][0]["notes"] == [[0.2, 0.8, 67, 100]]


def test_api_multitrack_notes_404_before_and_200_after_transcription(tmp_path: Path, monkeypatch):
    client = TestClient(create_app(workspace_root=tmp_path))
    res = client.post(
        "/api/projects",
        files={"file": ("song.wav", _wav_bytes(), "audio/wav")},
        data={"is_isolated_stem": "false", "mode": "dettato"},
    )
    pid = res.json()["job_id"]

    # Before multitrack transcription -> 404
    assert client.get(f"/api/projects/{pid}/multitrack/notes").status_code == 404

    class _FakeTrack:
        def __init__(self, name, program, notes, is_drum=False):
            self.name, self.program, self.notes, self.is_drum = name, program, notes, is_drum

    class _FakeRes:
        tracks = [
            _FakeTrack("Electric Guitar", 27, np.array([[0.25, 0.75, 52, 96]], dtype=float)),
            _FakeTrack("Drums", 0, np.array([[0.0, 0.1, 38, 100]], dtype=float), is_drum=True),
        ]
        def summary(self):
            return {"tracks": 2}

    monkeypatch.setattr(dettato_transcriber, "is_available", lambda: True)
    monkeypatch.setattr(dettato_transcriber, "_run", lambda path: _FakeRes())

    assert client.post(f"/api/projects/{pid}/multitrack").status_code == 200
    notes_res = client.get(f"/api/projects/{pid}/multitrack/notes")
    assert notes_res.status_code == 200, notes_res.text
    body = notes_res.json()
    assert body["duration"] >= 1.4
    assert [t["name"] for t in body["tracks"]] == ["Electric Guitar", "Drums"]
    assert body["tracks"][0]["notes"][0][2:] == [52, 96]
    assert body["tracks"][1]["is_drum"] is True
