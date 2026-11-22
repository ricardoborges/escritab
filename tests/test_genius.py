from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline import genius

PAGE = """
<html><body>
<div data-lyrics-container="true" class="Lyrics__Container">
  <div data-exclude-from-selection="true"><span>12 Contributors</span><h2>Song Lyrics</h2></div>
  [Verse 1]<br/>First <i>line</i>  here<br><a href="#"><span>Second &amp; line</span></a><br/>
  <br/>[Chorus]<br/>Chorus line
</div>
<div class="Ad">ignore me</div>
<div data-lyrics-container="true">Last line&#x27;s end<br/><img src="x.png"/>Final</div>
</body></html>
"""


def test_extract_lyrics_keeps_headers_and_breaks_and_skips_excluded_blocks():
    text = genius.extract_lyrics(PAGE)
    assert text == "[Verse 1]\nFirst line here\nSecond & line\n\n[Chorus]\nChorus line\nLast line's end\nFinal"


def test_extract_lyrics_drops_translated_title_header():
    page = '<div data-lyrics-container="true">[Letra de "Garota"]<br/><br/>[Verso 1]<br/>Olha</div>'
    assert genius.extract_lyrics(page) == "[Verso 1]\nOlha"


def test_extract_lyrics_empty_page():
    assert genius.extract_lyrics("<html><body><p>nada</p></body></html>") == ""


def test_genius_endpoints_with_network_stubbed(tmp_path: Path, monkeypatch):
    def fake_api_get(path, params=None):
        if path == "/search":
            assert params["q"] == "garota"
            return {"hits": [
                {"type": "song", "result": {"id": 7, "title": "Garota", "artist_names": "Tom",
                                            "url": "https://genius.com/x", "lyrics_state": "complete",
                                            "release_date_components": {"year": 1962}}},
                {"type": "album", "result": {"id": 9}},
            ]}
        assert path == "/songs/7"
        return {"song": {"id": 7, "title": "Garota", "artist_names": "Tom", "url": "https://genius.com/x", "language": "pt"}}

    class FakeRes:
        status_code = 200
        text = PAGE

    monkeypatch.setattr(genius, "_api_get", fake_api_get)
    monkeypatch.setattr(genius.httpx, "get", lambda *a, **k: FakeRes())
    client = TestClient(create_app(workspace_root=tmp_path))

    res = client.get("/api/lyrics/search", params={"q": "garota"})
    assert res.status_code == 200
    results = res.json()["results"]
    assert [r["id"] for r in results] == [7]
    assert results[0]["year"] == 1962

    res = client.get("/api/lyrics/genius/7")
    assert res.status_code == 200
    body = res.json()
    assert body["language"] == "pt"
    assert body["text"].startswith("[Verse 1]\nFirst line here")


def test_genius_without_token_is_a_readable_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(genius, "get_token", lambda: None)
    client = TestClient(create_app(workspace_root=tmp_path))
    res = client.get("/api/lyrics/search", params={"q": "x"})
    assert res.status_code == 502
    assert "GENIUS_TOKEN_ACCESS" in res.json()["detail"]


def test_lyrics_draft_is_saved_loaded_and_cleared(tmp_path: Path):
    import io
    import numpy as np
    import soundfile as sf

    client = TestClient(create_app(workspace_root=tmp_path))
    buf = io.BytesIO()
    sf.write(buf, np.zeros(22050, dtype=np.float32), 22050, format="WAV")
    pid = client.post("/api/projects", files={"file": ("a.wav", buf.getvalue(), "audio/wav")},
                      data={"name": "X"}).json()["job_id"]

    assert client.get(f"/api/projects/{pid}/lyrics").json()["draft"] is None
    res = client.put(f"/api/projects/{pid}/lyrics/draft",
                     json={"text": "[Verso 1]\nOla", "language": "pt", "source": "Genius: X · Y"})
    assert res.status_code == 200
    draft = client.get(f"/api/projects/{pid}/lyrics").json()["draft"]
    assert draft["text"] == "[Verso 1]\nOla" and draft["language"] == "pt" and draft["source"] == "Genius: X · Y"

    client.put(f"/api/projects/{pid}/lyrics/draft", json={"text": "  "})
    assert client.get(f"/api/projects/{pid}/lyrics").json()["draft"] is None
