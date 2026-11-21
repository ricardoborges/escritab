import struct
from pathlib import Path

from fastapi.testclient import TestClient

from app import soundfonts
from app.main import create_app


def _chunk(cid: bytes, data: bytes) -> bytes:
    return cid + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")


def _write_sf2(path: Path, presets: list[tuple[int, int, str]]) -> None:
    """Minimal RIFF/sfbk with only what the preset reader looks at (INFO list + pdta/phdr)."""
    recs = b"".join(
        struct.pack("<20sHHHIII", name.encode("latin-1"), prog, bank, 0, 0, 0, 0)
        for bank, prog, name in presets + [(0, 0, "EOP")]
    )
    info = b"LIST" + struct.pack("<I", 4 + 12) + b"INFO" + _chunk(b"ifil", struct.pack("<HH", 2, 1))
    pdta_body = b"pdta" + _chunk(b"phdr", recs)
    pdta = b"LIST" + struct.pack("<I", len(pdta_body)) + pdta_body
    body = b"sfbk" + info + pdta
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def test_lists_soundfonts_with_presets_and_serves_them(tmp_path: Path, monkeypatch):
    lib = tmp_path / "sf"
    (lib / "sub").mkdir(parents=True)
    _write_sf2(lib / "GM.sf2", [(0, p, f"P{p}") for p in range(128)] + [(128, 0, "Standard")])
    _write_sf2(lib / "sub" / "Guitars.sf2", [(0, 0, "Dist Guitar"), (0, 29, "Heavy")])
    (lib / "readme.txt").write_text("not a soundfont")
    monkeypatch.setenv("ESCRITAB_SOUNDFONTS", str(lib))

    client = TestClient(create_app(workspace_root=tmp_path / "jobs"))
    data = client.get("/api/soundfonts").json()
    fonts = data["soundfonts"]
    assert [f["name"] for f in fonts] == ["GM", "Guitars"]           # GM bank first
    assert fonts[0]["general_midi"] and not fonts[1]["general_midi"]
    assert fonts[1]["presets"] == [
        {"bank": 0, "program": 0, "name": "Dist Guitar"},
        {"bank": 0, "program": 29, "name": "Heavy"},
    ]
    assert all("_path" not in f for f in fonts)

    res = client.get(f"/api/soundfonts/{fonts[1]['id']}")
    assert res.status_code == 200
    assert res.content == (lib / "sub" / "Guitars.sf2").read_bytes()
    assert client.get("/api/soundfonts/..%2F..%2Fetc").status_code == 404


def test_missing_folder_lists_nothing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ESCRITAB_SOUNDFONTS", str(tmp_path / "nope"))
    assert soundfonts.list_soundfonts() == []


def test_nested_list_in_info_is_served_repaired(tmp_path: Path, monkeypatch):
    lib = tmp_path / "sf"
    lib.mkdir()
    _write_sf2(lib / "Drums.sf2", [(128, 0, "Drum")])
    raw = (lib / "Drums.sf2").read_bytes()
    # splice a non-standard LIST sub-chunk into INFO (what FluidSynth rejects)
    info_size = struct.unpack("<I", raw[16:20])[0]
    junk = b"LIST" + struct.pack("<I", 8) + b"JUNKdata"
    end = 20 + info_size
    bad = raw[:16] + struct.pack("<I", info_size + len(junk)) + raw[20:end] + junk + raw[end:]
    bad = b"RIFF" + struct.pack("<I", len(bad) - 8) + bad[8:]
    (lib / "Drums.sf2").write_bytes(bad)
    monkeypatch.setenv("ESCRITAB_SOUNDFONTS", str(lib))

    client = TestClient(create_app(workspace_root=tmp_path / "jobs"))
    font = client.get("/api/soundfonts").json()["soundfonts"][0]
    assert font["presets"] == [{"bank": 128, "program": 0, "name": "Drum"}]
    served = client.get(f"/api/soundfonts/{font['id']}").content
    assert served == raw                       # junk gone, everything else byte-identical
