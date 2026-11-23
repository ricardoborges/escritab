"""Export to .qtab: a backing track plus the timed chords, for VstCam / Quantifier Studio.

A .qtab is a zip that another app (VstCam) loads as its backing track and draws the chord lane over
the camera, in sync with the backing's transport. Every time inside it is already on the exported
timeline: 0 = first sample of the backing audio, after the excerpt was cut and slowed down. The
reader never has to know where the excerpt was in the song or how much it was slowed.

Layout (format described in docs/qtab-format.md):
    manifest.json   format, version, title, audio info, rate, bpm, beats, key, function colors
    backing.flac    the chosen audio (original mix or stems), cut and time-stretched
    chords.json     detected chords with their harmonic function (T / S / D / X)
    sections.json   the song's parts
    lyrics.json     aligned lyric lines (when there are lyrics)
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from app import projects
from app.models import JobMetadata
from app.projects import PartError

QTAB_FORMAT = "qtab"
QTAB_VERSION = 1
AUDIO_NAME = "backing.flac"
MIN_RATE, MAX_RATE = 0.25, 1.0

# the colors of the chord lane in the EscriTab UI (styles.css --fn-*)
FUNCTIONS = {
    "T": {"name": "tônica", "color": "#10b981"},
    "S": {"name": "subdominante", "color": "#3b82f6"},
    "D": {"name": "dominante", "color": "#f59e0b"},
    "X": {"name": "fora do campo", "color": "#ec4899"},
}


def chord_function(chord: dict, field: list[dict]) -> str:
    """T / S / D from the degree's function in the key's harmonic field; X when outside it."""
    degree = chord.get("degree")
    if not chord.get("diatonic") or not degree or degree > len(field):
        return "X"
    fn = field[degree - 1].get("function")
    return fn if fn in FUNCTIONS else "X"


def _load_audio(job: JobMetadata, job_dir: Path, stems: list[str]) -> tuple[np.ndarray, int]:
    """(channels, samples) float32 at the file's own rate: the original upload or the stems mix."""
    path = projects.stems_mix(job, job_dir, stems) if stems else job_dir / job.input_path
    y, sr = librosa.load(str(path), sr=None, mono=False)
    return np.atleast_2d(y).astype(np.float32), int(sr)


def _t(t: float, start: float, rate: float) -> float:
    return round((t - start) / rate, 4)


def _overlaps(item: dict, start: float, end: float) -> bool:
    return item["end"] > start and item["start"] < end


def _span(item: dict, start: float, end: float, rate: float) -> dict:
    """start/end clipped to [start, end) and moved to the exported timeline."""
    return {"start": _t(max(item["start"], start), start, rate), "end": _t(min(item["end"], end), start, rate)}


def build_qtab(
    job: JobMetadata,
    job_dir: Path,
    stems: list[str] | None = None,
    rate: float = 1.0,
    start: float | None = None,
    end: float | None = None,
) -> bytes:
    """The .qtab zip for [start, end) of the song (default: all of it)."""
    stems = sorted(set(stems or []))
    if not MIN_RATE <= rate <= MAX_RATE:
        raise PartError(f"Velocidade fora do intervalo ({MIN_RATE:.0%} a {MAX_RATE:.0%}).", status=422)
    harm = projects.load_harmony(job_dir)
    if not harm or not harm.get("chords"):
        raise PartError("Esta música ainda não tem acordes detectados (análise harmônica).", status=422)
    duration = job.duration or 0.0
    start = max(0.0, start or 0.0)
    end = min(duration, end) if end is not None and duration else (end or duration)
    if end - start < 0.5:
        raise PartError("Trecho curto demais para exportar.", status=422)

    # ---- audio: cut, then slow down keeping the pitch (phase vocoder) ----
    y, sr = _load_audio(job, job_dir, stems)
    y = y[:, int(round(start * sr)): int(round(end * sr))]
    if rate < 1.0:
        y = librosa.effects.time_stretch(y, rate=rate)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0.99:
        y *= 0.97 / peak
    audio = io.BytesIO()
    sf.write(audio, y.T, sr, format="FLAC", subtype="PCM_16")

    # ---- chords, sections, lyrics ----
    field = harm.get("field") or []
    chords = [
        {**_span(c, start, end, rate), "label": c["label"], "function": chord_function(c, field),
         "numeral": c.get("numeral"), "root": c.get("root"), "quality": c.get("quality"), "bass": c.get("bass")}
        for c in harm["chords"] if _overlaps(c, start, end)
    ]
    sections = [
        {"id": p.part_id, "name": p.name, **_span({"start": p.start, "end": p.end}, start, end, rate)}
        for p in sorted(job.parts, key=lambda p: p.start) if _overlaps({"start": p.start, "end": p.end}, start, end)
    ]
    lines: list[dict] = []
    lyrics_path = job_dir / projects.LYRICS_FILE
    if lyrics_path.exists():
        data = json.loads(lyrics_path.read_text(encoding="utf-8"))
        for ln in data.get("lines", []):
            if not _overlaps(ln, start, end):
                continue
            words = ln.get("words", [])
            lines.append({
                **_span(ln, start, end, rate),
                "section": ln.get("section"),
                "text": " ".join(w["text"] for w in words),
                "words": [{"text": w["text"], "start": _t(w["start"], start, rate), "end": _t(w["end"], start, rate)}
                          for w in words if start <= w["start"] < end],
            })

    key = harm.get("key") or {}
    manifest = {
        "format": QTAB_FORMAT,
        "version": QTAB_VERSION,
        "generator": "EscriTab",
        "created_at": projects.now_iso(),
        "title": job.name or projects.default_name(job.original_filename),
        "audio": {
            "file": AUDIO_NAME,
            "source": "+".join(stems) if stems else "mix",
            "sample_rate": sr,
            "channels": int(y.shape[0]),
            "duration": round(y.shape[1] / sr, 4),
        },
        "rate": rate,
        "range": {"start": round(start, 4), "end": round(end, 4)},
        "bpm": round((job.detected_bpm or 120.0) * rate, 2),
        "beats": [_t(b, start, rate) for b in job.beats if start <= b < end],
        "key": {"tonic": key.get("tonic"), "tonic_name": key.get("tonic_name"), "name": key.get("name"),
                "mode": key.get("mode")} if key else None,
        "functions": FUNCTIONS,
        "files": {"chords": "chords.json", "sections": "sections.json", "lyrics": "lyrics.json"},
        "counts": {"chords": len(chords), "sections": len(sections), "lyrics": len(lines)},
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2), zipfile.ZIP_DEFLATED)
        z.writestr(AUDIO_NAME, audio.getvalue(), zipfile.ZIP_STORED)  # FLAC is already compressed
        for name, payload in (("chords.json", chords), ("sections.json", sections), ("lyrics.json", lines)):
            z.writestr(name, json.dumps(payload, ensure_ascii=False), zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def qtab_filename(job: JobMetadata, rate: float, label: str = "") -> str:
    base = job.name or projects.default_name(job.original_filename)
    if label:
        base += f" - {label}"
    if rate < 1.0:
        base += f" ({round(rate * 100)}%)"
    return re.sub(r'[\\/:*?"<>|]+', "_", base).strip() + ".qtab"
