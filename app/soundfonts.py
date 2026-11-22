"""Local SoundFont (.sf2 / .sf3) library for the browser players.

Folders come from ESCRITAB_SOUNDFONTS (several separated by os.pathsep), default ./soundfonts
(a folder or a junction/symlink to wherever the SoundFonts are installed). Each file gets an
opaque id, so the download endpoint never takes a path from the client.
"""
from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path
from typing import Any

EXTS = {".sf2", ".sf3"}
_cache: dict[tuple[str, float, int], dict[str, Any]] = {}


def soundfont_dirs() -> list[Path]:
    raw = os.environ.get("ESCRITAB_SOUNDFONTS", "soundfonts")
    return [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]


def read_presets(path: Path) -> list[dict[str, Any]]:
    """Preset headers (bank, program, name) from the pdta/phdr chunk of a SoundFont."""
    with open(path, "rb") as f:
        header = f.read(12)
        if len(header) < 12:
            raise ValueError("not a SoundFont")
        riff, size, form = struct.unpack("<4sI4s", header)
        if riff != b"RIFF" or form not in (b"sfbk", b"sfpk"):
            raise ValueError("not a SoundFont")
        end = 8 + size
        while f.tell() < end:
            head = f.read(8)
            if len(head) < 8:
                break
            cid, csz = struct.unpack("<4sI", head)
            if cid == b"LIST" and f.read(4) == b"pdta":
                stop = f.tell() + csz - 4
                while f.tell() < stop:
                    subhead = f.read(8)
                    if len(subhead) < 8:
                        break
                    sid, ssz = struct.unpack("<4sI", subhead)
                    if sid == b"phdr":
                        data = f.read(ssz)
                        out = []
                        for i in range(0, len(data) - 38, 38):  # the last record is the EOP terminator
                            name, prog, bank = struct.unpack("<20sHH", data[i:i + 24])
                            out.append({"bank": bank, "program": prog,
                                        "name": name.split(b"\0")[0].decode("latin-1").strip()})
                        return sorted(out, key=lambda p: (p["bank"], p["program"]))
                    f.seek(ssz + (ssz & 1), 1)
                break
            elif cid == b"LIST":
                f.seek(csz - 4 + (csz & 1), 1)
            else:
                f.seek(csz + (csz & 1), 1)
    return []


def _describe(path: Path) -> dict[str, Any]:
    st = path.stat()
    key = (str(path), st.st_mtime, st.st_size)
    if key not in _cache:
        try:
            presets = read_presets(path)
        except (OSError, ValueError, struct.error):
            presets = []
        melodic = {p["program"] for p in presets if p["bank"] == 0}
        _cache[key] = {
            "id": hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12],
            "name": path.stem,
            "file": path.name,
            "size_mb": round(st.st_size / 2**20, 1),
            # a General MIDI bank covers (almost) every program: usable as the base for a whole song
            "general_midi": len(melodic) >= 100,
            "presets": presets,
        }
    return _cache[key]


def list_soundfonts() -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for d in soundfont_dirs():
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() in EXTS and p.is_file():
                info = _describe(p)
                seen.setdefault(info["id"], {**info, "_path": p})
    # GM banks first (the natural base), then by size so the light ones lead
    items = sorted(seen.values(), key=lambda s: (not s["general_midi"], s["size_mb"]))
    return items


def public(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in s.items() if not k.startswith("_")} for s in items]


INFO_IDS = {b"ifil", b"isng", b"INAM", b"irom", b"iver", b"ICRD", b"IENG", b"IPRD", b"ICOP", b"ICMT", b"ISFT"}


def _clean_info(path: Path) -> tuple[bytes, int, int] | None:
    """If the INFO list has sub-chunks outside the SF2 spec (some editors nest a LIST in it, which
    FluidSynth rejects), return (fixed INFO chunk, its start, its end) in the file; else None."""
    try:
        file_size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(12)
            head = f.read(8)
            if len(head) < 8:
                return None
            cid, csz = struct.unpack("<4sI", head)
            if cid != b"LIST" or f.read(4) != b"INFO":
                return None
            start, stop = 12, 20 + csz
            if stop > file_size:
                return None
            kept, dirty = [], False
            while f.tell() < stop:
                subhead = f.read(8)
                if len(subhead) < 8:
                    return None
                sid, ssz = struct.unpack("<4sI", subhead)
                need = ssz + (ssz & 1)
                data = f.read(need)
                if len(data) < need:
                    return None
                if sid in INFO_IDS:
                    kept.append(sid + struct.pack("<I", ssz) + data)
                else:
                    dirty = True
            if not dirty or f.tell() != stop:
                return None
            body = b"INFO" + b"".join(kept)
            return b"LIST" + struct.pack("<I", len(body)) + body, start, stop
    except (OSError, struct.error):
        return None


def servable_path(path: Path, cache_dir: Path) -> Path:
    """The file itself, or a cached copy with a spec-compliant INFO header (samples untouched)."""
    fix = _clean_info(path)
    if fix is None:
        return path
    st = path.stat()
    out = cache_dir / f"{path.stem}-{int(st.st_mtime)}-{st.st_size}.sf2"
    if not out.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        info, start, stop = fix
        tmp = out.with_suffix(".tmp")
        with open(path, "rb") as src, open(tmp, "wb") as dst:
            src.seek(8)
            form = src.read(4)
            src.seek(stop)
            rest_len = st.st_size - stop
            dst.write(b"RIFF" + struct.pack("<I", 4 + len(info) + rest_len) + form + info)
            while chunk := src.read(1 << 20):
                dst.write(chunk)
        tmp.replace(out)
    return out


def path_for(sf_id: str) -> Path | None:
    for s in list_soundfonts():
        if s["id"] == sf_id:
            return s["_path"]
    return None
