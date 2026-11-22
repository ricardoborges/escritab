import hashlib

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline import model_downloads as md


@pytest.fixture(autouse=True)
def clean_registry():
    md._downloads.clear()
    yield
    md._downloads.clear()


def test_download_file_records_progress(tmp_path):
    src = tmp_path / "weights.bin"
    data = b"x" * (3 * md.CHUNK + 123)
    src.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    dest = tmp_path / "cache" / "model.pt"

    md.download_file(src.as_uri(), dest, "Teste", check=lambda h: h == sha)

    assert dest.read_bytes() == data
    assert not (tmp_path / "cache" / "model.pt.part").exists()
    [d] = md.active_downloads()
    assert d["label"] == "Teste" and d["done"] and d["error"] is None
    assert d["downloaded"] == d["total"] == len(data)


def test_download_file_bad_checksum_leaves_nothing(tmp_path):
    src = tmp_path / "weights.bin"
    src.write_bytes(b"abc")
    dest = tmp_path / "model.pt"

    with pytest.raises(RuntimeError):
        md.download_file(src.as_uri(), dest, "Teste", check=lambda h: False)

    assert not dest.exists() and not (tmp_path / "model.pt.part").exists()
    [d] = md.active_downloads()
    assert d["done"] and "checksum" in d["error"]


def test_finished_downloads_expire(monkeypatch):
    did = md._start("Velho", "a.bin", 10)
    md._finish(did)
    md._downloads[did]["finished"] -= md.KEEP_FINISHED_S + 1
    assert md.active_downloads() == []


def test_tracked_tqdm_reports_to_registry():
    bar = md._tracked_tqdm("Dettato (grouper)", "model.safetensors")(total=1000)
    bar.update(400)
    [d] = md.active_downloads()
    assert d["downloaded"] == 400 and d["total"] == 1000 and not d["done"]
    bar.close()
    [d] = md.active_downloads()
    assert d["done"] and d["downloaded"] == 1000


def test_ensure_helpers_never_raise(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(md, "download_file", boom)
    monkeypatch.setattr(md, "whisper_root", lambda: md.Path("/nonexistent-escritab"))
    md.ensure_whisper("tiny")  # logs and lets whisper download it itself


def test_downloads_endpoint(tmp_path):
    md._progress(md._start("Whisper turbo", "large-v3-turbo.pt", 2000), 500)
    client = TestClient(create_app(tmp_path))
    body = client.get("/api/downloads").json()
    [d] = body["downloads"]
    assert d["label"] == "Whisper turbo" and d["downloaded"] == 500 and d["total"] == 2000
