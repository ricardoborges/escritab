import argparse
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app import batch


def _write_wav(path: Path, freq_hz: float = 110.0, duration_s: float = 3.0, sr: int = 22050) -> Path:
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    audio = (0.8 * np.sin(2 * np.pi * freq_hz * t) * np.exp(-6.0 * (t % 0.5))).astype(np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio, sr)
    return path


def test_find_audio_files_filters_and_recurses(tmp_path: Path):
    _write_wav(tmp_path / "b.wav")
    _write_wav(tmp_path / "A.WAV")
    (tmp_path / "notes.txt").write_text("x")
    _write_wav(tmp_path / "sub" / "c.wav")

    top = batch.find_audio_files(tmp_path, recursive=False)
    assert [p.name for p in top] == ["A.WAV", "b.wav"]
    deep = batch.find_audio_files(tmp_path, recursive=True)
    assert [p.name for p in deep] == ["A.WAV", "b.wav", "c.wav"]
    assert batch.find_audio_files(tmp_path, recursive=False, extensions=("flac",)) == []


def test_parse_steps_orders_and_validates():
    assert batch.parse_steps("harmony, stems") == ["stems", "harmony"]
    assert batch.parse_steps("all") == list(batch.STEPS)
    with pytest.raises(argparse.ArgumentTypeError):
        batch.parse_steps("stems,bogus")


def test_main_imports_then_reuses_projects(tmp_path: Path, capsys):
    songs = tmp_path / "songs"
    _write_wav(songs / "01 - Primeira.wav", 110.0)
    _write_wav(songs / "Segunda.wav", 220.0)
    ws = tmp_path / "ws"
    argv = [str(songs), "--workspace", str(ws), "--isolated-stem", "--steps", "stems,harmony"]

    assert batch.main(argv) == 0
    index = json.loads((ws / batch.INDEX_FILE).read_text(encoding="utf-8"))
    assert len(index) == 2
    jobs = {json.loads((ws / jid / "job.json").read_text(encoding="utf-8"))["name"]: jid for jid in index.values()}
    assert set(jobs) == {"Primeira", "Segunda"}
    for jid in jobs.values():
        meta = json.loads((ws / jid / "job.json").read_text(encoding="utf-8"))
        assert meta["stems"] == {"guitar": "stem_guitar.wav"}
        assert (ws / jid / "harmony.json").exists()

    # second run: same projects, every step already done
    capsys.readouterr()
    assert batch.main(argv) == 0
    out = capsys.readouterr().out
    assert "existente" in out and "novo" not in out
    assert "pulado: stems,harmony" in out
    assert sorted(p.name for p in ws.iterdir() if p.is_dir()) == sorted(jobs.values())


def test_failed_step_does_not_stop_the_batch(tmp_path: Path, monkeypatch):
    songs = tmp_path / "songs"
    _write_wav(songs / "a.wav", 110.0)
    _write_wav(songs / "b.wav", 220.0)
    (songs / "broken.mp3").write_bytes(b"not audio")
    calls = []

    def boom(job, job_dir, opts):
        calls.append(job.name)
        raise RuntimeError("GPU sem memória")

    monkeypatch.setitem(batch.STEP_FUNCS, "harmony", boom)
    code = batch.main([str(songs), "--workspace", str(tmp_path / "ws"), "--isolated-stem",
                       "--steps", "stems,harmony"])
    assert code == 1
    assert calls == ["a", "b"]  # the unreadable file failed at import, the others still ran


def test_dry_run_creates_nothing(tmp_path: Path, capsys):
    songs = tmp_path / "songs"
    _write_wav(songs / "a.wav")
    ws = tmp_path / "ws"
    assert batch.main([str(songs), "--workspace", str(ws), "--dry-run"]) == 0
    assert "a.wav" in capsys.readouterr().out
    assert not ws.exists()


def test_qtab_step_requires_output_dir(tmp_path: Path):
    _write_wav(tmp_path / "a.wav")
    assert batch.main([str(tmp_path), "--steps", "qtab", "--workspace", str(tmp_path / "ws")]) == 2


def test_duplicate_file_in_the_same_run_is_skipped(tmp_path: Path, capsys):
    songs = tmp_path / "songs"
    _write_wav(songs / "a.wav")
    _write_wav(songs / "copia de a.wav")
    ws = tmp_path / "ws"
    assert batch.main([str(songs), "--workspace", str(ws), "--isolated-stem", "--steps", "stems"]) == 0
    assert "mesmo conteúdo de a.wav" in capsys.readouterr().out
    assert len([p for p in ws.iterdir() if p.is_dir()]) == 1


def test_reuses_project_uploaded_in_the_studio_and_fixes_its_name(tmp_path: Path, capsys):
    from app.job_manager import JobManager

    songs = tmp_path / "songs"
    wav = _write_wav(songs / "05 - Unkillable Dragon v4.wav")
    ws = tmp_path / "ws"
    manager = JobManager(workspace_root=ws)
    uploaded = manager.create_job(wav.name, is_isolated_stem=True, audio_bytes=wav.read_bytes())
    uploaded.name = "Unkillable Dragon v4"  # named before the version tag was dropped
    manager.save_job(uploaded)

    assert batch.main([str(songs), "--workspace", str(ws), "--isolated-stem", "--steps", "stems"]) == 0
    assert "existente" in capsys.readouterr().out
    assert [p.name for p in ws.iterdir() if p.is_dir()] == [uploaded.job_id]
    assert manager.get_job(uploaded.job_id).name == "Unkillable Dragon"


def test_new_project_name_drops_number_and_version(tmp_path: Path):
    from app.job_manager import JobManager

    _write_wav(tmp_path / "songs" / "09 - Dragões no Abismo v4.wav")
    ws = tmp_path / "ws"
    assert batch.main([str(tmp_path / "songs"), "--workspace", str(ws), "--isolated-stem", "--steps", "stems"]) == 0
    assert [j.name for j in JobManager(workspace_root=ws).list_jobs()] == ["Dragões no Abismo"]
