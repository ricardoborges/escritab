"""Projects and parts: one song analysed once, transcribed excerpt by excerpt.

The heavy analysis (full-song Dettato MIDI, or the Dettato transcription of a whole Demucs stem)
runs once per project and is cached; a part only slices those notes by time, so creating or moving
a part never re-runs the network and the model always keeps the context around the excerpt.
Basic Pitch (frame-level, cheap) runs on the excerpt itself, with a small margin.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from app.models import JobMetadata, NoteEvent, Part, TabParams, TranscriptionParams
from app.pipeline import dettato_transcriber, harmony, instrumental, lead_transcriber, lyrics, scales
from app.pipeline.audio_cleaner import apply_bandpass_filter, apply_noise_gate, mix_stem_files
from app.pipeline.exporters import export_ascii_tab, export_gp5, export_musicxml
from app.pipeline.fretboard import optimize_fretboard
from app.pipeline.midi_transcriber import (
    _run_basic_pitch_or_fallback,
    _tuning_pitch_bounds,
    finalize_notes_to_midi,
)

PEAKS_FILE = "peaks.json"
PEAKS_PER_SECOND = 50
BASIC_PITCH_MARGIN_S = 1.0


class PartError(Exception):
    """A request that cannot be fulfilled with the project's current analysis (-> HTTP 400/422)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_name(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"^input_", "", stem)
    name = re.sub(r"[_]+", " ", stem).strip()
    # "01 - Song", "1. Song", "03) Song": drop the track number ("7 Rings" / "1984" stay)
    name = re.sub(r"^\d{1,3}\s*[-–—.)]+\s*", "", name).strip() or name
    return name or "Sem nome"


# ---------- audio analysis (duration, beats, waveform peaks) ----------

def analyze_audio(job: JobMetadata, job_dir: Path) -> None:
    """Fill duration, BPM and beat times, and write the waveform peaks the UI draws."""
    y, sr = librosa.load(str(job_dir / job.input_path), sr=22050, mono=True)
    job.duration = round(float(len(y) / sr), 3)
    if y.size >= sr:
        try:
            tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
            bpm = float(np.asarray(tempo).flat[0])
            if np.isfinite(bpm) and 30.0 < bpm < 300.0:
                job.detected_bpm = round(bpm, 1)
            job.beats = [round(float(t), 3) for t in librosa.frames_to_time(frames, sr=sr)]
        except Exception:
            job.beats = []
    bucket = max(1, sr // PEAKS_PER_SECOND)
    n = int(np.ceil(len(y) / bucket)) if y.size else 0
    padded = np.zeros(n * bucket, dtype=np.float32)
    padded[: len(y)] = np.abs(y)
    peaks = padded.reshape(n, bucket).max(axis=1) if n else np.zeros(0)
    top = float(peaks.max()) if peaks.size else 0.0
    if top > 0:
        peaks = peaks / top
    (job_dir / PEAKS_FILE).write_text(
        json.dumps({"duration": job.duration, "peaks": [round(float(v), 3) for v in peaks]}),
        encoding="utf-8",
    )


HARMONY_FILE = "harmony.json"
NON_HARMONIC_STEMS = {"drums", "vocals"}
VOICE_PROGRAMS = set(range(52, 56)) | {85}  # choir / voice / synth voice: melody, not harmony


def _add_signals(a: np.ndarray | None, b: np.ndarray) -> np.ndarray:
    """Sum two 1D audio arrays, zero-padding the shorter one instead of truncating."""
    if a is None:
        return b.astype(np.float32, copy=True)
    if len(a) < len(b):
        out = b.astype(np.float32, copy=True)
        out[: len(a)] += a
        return out
    out = a.astype(np.float32, copy=True)
    out[: len(b)] += b
    return out


def compute_harmony(job: JobMetadata, job_dir: Path) -> dict:
    """Key, chords and harmonic field of the whole song (cached in harmony.json).

    Uses the stems without drums and vocals when they exist (a much cleaner chroma), otherwise the
    harmonic part of the mix; blends in the full-song Dettato MIDI when it has been generated."""
    stems = [s for s in job.stems if s not in NON_HARMONIC_STEMS]
    bass_y = None
    if stems:
        y = None
        for name in stems:
            s, _ = librosa.load(str(job_dir / job.stems[name]), sr=harmony.SR, mono=True)
            if name == "bass":
                bass_y = s
            y = _add_signals(y, s)
        source = "stems: " + " + ".join(stems)
    else:
        y, _ = librosa.load(str(job_dir / job.input_path), sr=harmony.SR, mono=True)
        y = librosa.effects.harmonic(y, margin=2.0)
        source = "mix (sem bateria via HPSS)"

    midi_notes = None
    full_midi = job_dir / job.artifacts.get("multitrack_midi", "full_song.mid")
    if job.multitrack and full_midi.exists():
        indices = [t.index for t in job.multitrack if not t.is_drum and t.program not in VOICE_PROGRAMS]
        midi_notes = dettato_transcriber.notes_from_midi_tracks(full_midi, indices) or None

    duration = job.duration or len(y) / harmony.SR
    result = harmony.analyze(y, job.beats, duration, bass_y=bass_y, midi_notes=midi_notes, source=source)
    result["created_at"] = now_iso()
    result["stems"] = sorted(stems)
    (job_dir / HARMONY_FILE).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def load_harmony(job_dir: Path) -> dict | None:
    path = job_dir / HARMONY_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ---------- lyrics ----------

LYRICS_FILE = "lyrics.json"
LYRICS_DRAFT_FILE = "lyrics_draft.json"   # text chosen / typed but not aligned yet
PROMPT_CHARS = 600  # Whisper reads at most ~224 tokens of prompt


def _vocals_audio(job: JobMetadata, job_dir: Path) -> tuple[np.ndarray, str]:
    """The vocals stem (much better for Whisper) or, without stems, the whole mix."""
    if "vocals" in job.stems:
        path, source = job_dir / job.stems["vocals"], "stem de vocais"
    elif job.is_isolated_stem and job.stems:
        path, source = job_dir / next(iter(job.stems.values())), "pista enviada"
    else:
        path, source = job_dir / job.input_path, "mix completo (sem stem de vocais)"
    y, _ = librosa.load(str(path), sr=lyrics.WHISPER_SR, mono=True)
    return y, source


def align_project_lyrics(
    job: JobMetadata, job_dir: Path, text: str, language: str | None, model: str
) -> dict:
    """Whisper on the vocals, then align the pasted lyrics to what it heard (or keep Whisper's
    own text as a draft when nothing was pasted). Saved in lyrics.json."""
    audio, source = _vocals_audio(job, job_dir)
    prompt = " ".join(text.split())[:PROMPT_CHARS] if text.strip() else None
    heard, info = lyrics.transcribe_words(audio, model_name=model, language=language, prompt=prompt)
    if not heard:
        raise PartError("O Whisper não ouviu nenhuma palavra cantada nesta música.", status=422)
    try:
        if text.strip():
            lines, mode = lyrics.align_lyrics(text, heard), "aligned"
        else:
            lines, mode = lyrics.lines_from_whisper(info, heard), "whisper"
    except lyrics.LyricsError as exc:
        raise PartError(str(exc), status=422) from exc
    if not lines:
        raise PartError("Nenhuma linha cantada encontrada.", status=422)
    lyrics.refine_line_starts(lines, lyrics.voice_activity(audio, lyrics.WHISPER_SR))
    n_words = sum(len(l["words"]) for l in lines)
    data = {
        "version": 1,
        "mode": mode,
        "text": text if text.strip() else lyrics.lines_to_text(lines),
        "language": info["language"],
        "model": info["model"],
        "device": info["device"],
        "source": source,
        "created_at": now_iso(),
        "lines": lines,
        "onsets_refined": True,
        "stats": {
            "lines": len(lines),
            "words": n_words,
            "heard_words": len(heard),
            "matched_share": round(sum(w["matched"] for l in lines for w in l["words"]) / max(1, n_words), 3),
        },
    }
    (job_dir / LYRICS_FILE).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (job_dir / LYRICS_DRAFT_FILE).unlink(missing_ok=True)  # the aligned text supersedes the draft
    return data


def retag_project_lyrics(job_dir: Path, text: str) -> dict:
    """New section tags for the aligned lyrics, without running Whisper again."""
    path = job_dir / LYRICS_FILE
    if not path.exists():
        raise PartError("Alinhe a letra primeiro.", status=422)
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        data["lines"] = lyrics.retag_lines(text, data["lines"])
    except lyrics.LyricsError as exc:
        raise PartError(str(exc), status=422) from exc
    data["text"] = text
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (job_dir / LYRICS_DRAFT_FILE).unlink(missing_ok=True)
    return data


def save_lyrics_draft(job_dir: Path, text: str, language: str | None, source: str | None) -> dict | None:
    path = job_dir / LYRICS_DRAFT_FILE
    if not text.strip():
        path.unlink(missing_ok=True)
        return None
    draft = {"text": text, "language": language, "source": source, "updated_at": now_iso()}
    path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return draft


def load_lyrics_draft(job_dir: Path) -> dict | None:
    path = job_dir / LYRICS_DRAFT_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


_ACTIVITY_CACHE: dict[str, tuple[float, instrumental.SongActivity]] = {}


def song_activity(job: JobMetadata, job_dir: Path) -> instrumental.SongActivity | None:
    """Note activity of the full-song MIDI (lead / pitched / drums per bin), cached by file mtime."""
    full_midi = job_dir / job.artifacts.get("multitrack_midi", "full_song.mid")
    if not job.multitrack or not full_midi.exists():
        return None
    key, mtime = str(full_midi), full_midi.stat().st_mtime
    hit = _ACTIVITY_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    import pretty_midi

    roles = {t.index: t for t in job.multitrack}
    pm = pretty_midi.PrettyMIDI(str(full_midi))
    tracks = []
    for idx, inst in enumerate(pm.instruments):
        info = roles.get(idx)
        if info is not None and not info.is_drum and info.program in VOICE_PROGRAMS:
            continue  # the sung melody is not instrumental activity
        tracks.append({
            "role": info.role if info else None,
            "is_drum": inst.is_drum,
            "onsets": [n.start for n in inst.notes],
            "spans": [(n.start, n.end) for n in inst.notes],
        })
    act = instrumental.build_activity(tracks, job.duration or pm.get_end_time())
    _ACTIVITY_CACHE[key] = (mtime, act)
    return act


def stems_mix(job: JobMetadata, job_dir: Path, names: list[str]) -> Path:
    """Stereo sum of the chosen stems (for the player), cached as mix_<a>+<b>.wav."""
    names = sorted(set(names))
    unknown = [n for n in names if n not in job.stems]
    if not names or unknown:
        raise PartError(f"Stem inexistente: {', '.join(unknown) or '(nenhum)'}.", status=422)
    if len(names) == 1:
        return job_dir / job.stems[names[0]]
    out = job_dir / f"mix_{'+'.join(names)}.wav"
    sources = [job_dir / job.stems[n] for n in names]
    if out.exists() and out.stat().st_mtime >= max(p.stat().st_mtime for p in sources):
        return out
    mixed, sr = None, None
    for path in sources:
        data, rate = sf.read(str(path), dtype="float32", always_2d=True)
        if sr is None:
            sr = rate
        elif rate != sr:  # Demucs writes every stem at the same rate; resample just in case
            data = librosa.resample(data.T, orig_sr=rate, target_sr=sr).T
        if mixed is None:
            mixed = data.copy()
            continue
        if data.shape[1] != mixed.shape[1]:  # mono + stereo: widen the mono one
            data = np.repeat(data[:, :1], mixed.shape[1], axis=1) if data.shape[1] == 1 else data
            if mixed.shape[1] == 1:
                mixed = np.repeat(mixed, data.shape[1], axis=1)
        n = max(len(mixed), len(data))
        if len(mixed) < n:
            mixed = np.pad(mixed, ((0, n - len(mixed)), (0, 0)))
        mixed[: len(data)] += data
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.98:
        mixed *= 0.95 / peak
    tmp = out.with_suffix(".tmp.wav")
    sf.write(str(tmp), mixed, sr, subtype="PCM_16")
    tmp.replace(out)
    return out


def lyrics_payload(job: JobMetadata, job_dir: Path) -> dict:
    """Saved lyrics plus the chord sheet built from the current harmony analysis (when there is one)."""
    path = job_dir / LYRICS_FILE
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if data and data.get("mode") == "aligned" and lyrics.repair_lines(data["lines"]):
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")  # aligned before the outlier fix
    if data and not data.get("onsets_refined"):
        try:
            audio, _ = _vocals_audio(job, job_dir)
            lyrics.refine_line_starts(data["lines"], lyrics.voice_activity(audio, lyrics.WHISPER_SR))
        except Exception:  # no audio to read: keep Whisper's times
            pass
        data["onsets_refined"] = True
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    harm = load_harmony(job_dir)
    sheet = None
    sections: list[dict] = []
    if data:
        chords = harm["chords"] if harm else []
        field = harm["field"] if harm else []
        duration = job.duration or data["lines"][-1]["end"]
        sheet = lyrics.chord_sheet(data["lines"], chords, field, duration)
        act = song_activity(job, job_dir)
        classify = (lambda start, end: instrumental.classify(start, end, act, chords)) if act else None
        sections = lyrics.infer_sections(data.get("text", ""), data["lines"], chords, duration, classify=classify)
    return {"lyrics": data, "sheet": sheet, "sections": sections, "has_harmony": harm is not None,
            "draft": load_lyrics_draft(job_dir)}


SAME_PART_S = 1.0


def parts_from_lyrics(
    job: JobMetadata, job_dir: Path, only_intro: bool = False, updated: list[Part] | None = None
) -> list[Part]:
    """One part per section inferred from the lyrics. Returns the parts created.

    A part with the section's name but other bounds (the lyrics were re-aligned or repaired) gets
    the new bounds, unless its tab was already generated (then it is left alone); those parts are
    appended to ``updated``. Sections already saved as parts are skipped.
    only_intro: just the instrumental intro (from the start to the first sung stanza), if any."""
    sections = lyrics_payload(job, job_dir)["sections"]
    if not sections:
        raise PartError("Alinhe a letra primeiro: as partes saem das seções da letra.", status=422)
    if only_intro:
        sections = [s for s in sections[:1] if s["kind"] == "instrumental" and s["start"] == 0]
    created = []
    for s in sections:
        try:
            start, end = check_range(job, s["start"], s["end"])
        except PartError:
            continue
        name = s["name"][:60]
        same_name = [p for p in job.parts if p.name == name]
        if any(abs(p.start - start) < SAME_PART_S and abs(p.end - end) < SAME_PART_S for p in same_name):
            continue
        stale = next((p for p in same_name if not p.artifacts), None)
        if stale is not None:
            stale.start, stale.end = start, end
            stale.stats = {}
            if updated is not None:
                updated.append(stale)
        elif not same_name:
            created.append(new_part(job, name, start, end))
    job.parts.sort(key=lambda p: (p.start, p.created_at))
    return created


def ensure_project(job: JobMetadata, job_dir: Path) -> bool:
    """Bring a project (or a pre-parts session) up to date. Returns True when it changed."""
    changed = False
    if not job.name:
        job.name = default_name(job.original_filename)
        changed = True
    if job.duration <= 0 or not (job_dir / PEAKS_FILE).exists():
        analyze_audio(job, job_dir)
        changed = True
    if not job.parts and _migrate_legacy_tab(job, job_dir):
        changed = True
    return changed


def _migrate_legacy_tab(job: JobMetadata, job_dir: Path) -> bool:
    """A session from before parts had one tab of the whole song: keep it as a part."""
    names = {k: job.artifacts.get(k) for k in ("midi", "musicxml", "gp5", "txt")}
    if not names["musicxml"] or not (job_dir / names["musicxml"]).exists():
        return False
    part = Part(
        part_id=uuid.uuid4().hex[:8], name="Música inteira", start=0.0, end=job.duration,
        created_at=job.created_at, transcription_params=job.transcription_params,
        tab_params=job.tab_params, stats=dict(job.stats),
    )
    pdir = part_dir(job_dir, part.part_id)
    pdir.mkdir(parents=True, exist_ok=True)
    targets = {"midi": "guitar.mid", "musicxml": "tab.musicxml", "gp5": "tab.gp5", "txt": "tab.txt"}
    for key, src in names.items():
        if src and (job_dir / src).exists():
            shutil.copy2(job_dir / src, pdir / targets[key])
            part.artifacts[key] = targets[key]
    for key in names:
        job.artifacts.pop(key, None)
    job.artifacts.pop("preprocessed_wav", None)
    job.parts.append(part)
    return True


# ---------- parts ----------

def part_dir(job_dir: Path, part_id: str) -> Path:
    return job_dir / "parts" / re.sub(r"[^A-Za-z0-9_-]", "", part_id)


def find_part(job: JobMetadata, part_id: str) -> Part:
    for p in job.parts:
        if p.part_id == part_id:
            return p
    raise PartError("Parte não encontrada.", status=404)


def check_range(job: JobMetadata, start: float, end: float) -> tuple[float, float]:
    limit = job.duration or end
    start, end = max(0.0, start), min(end, limit)
    if end - start < 0.5:
        raise PartError("O trecho precisa ter pelo menos meio segundo.")
    return round(start, 3), round(end, 3)


def new_part(job: JobMetadata, name: str, start: float, end: float) -> Part:
    start, end = check_range(job, start, end)
    part = Part(part_id=uuid.uuid4().hex[:8], name=name.strip(), start=start, end=end, created_at=now_iso())
    job.parts.append(part)
    job.parts.sort(key=lambda p: (p.start, p.created_at))
    return part


FIT_MIN_S = 0.3  # a neighbour trimmed shorter than this is removed


def insert_part_fitted(job: JobMetadata, job_dir: Path, name: str, start: float, end: float) -> tuple[Part, list[str]]:
    """A new part that takes its place in the timeline: the parts it overlaps are trimmed (the one
    before ends where it starts, the one after starts where it ends), a part containing it is split
    in two, and parts it covers completely are removed. Changed parts lose their generated tab.
    Returns the new part and a description of every change."""
    start, end = check_range(job, start, end)
    changes: list[str] = []
    for p in list(job.parts):
        if p.end <= start + 1e-3 or p.start >= end - 1e-3:
            continue  # no overlap
        old = (p.start, p.end)
        if p.start < start and p.end > end:  # contains the new part: split
            tail_start, tail_end = end, p.end
            p.end = start
            clear_outputs(job_dir, p)
            twin = new_part(job, p.name, tail_start, tail_end)
            changes.append(f"{p.name} dividida: {fmt_clock(old[0])}–{fmt_clock(start)} e {fmt_clock(twin.start)}–{fmt_clock(twin.end)}")
            continue
        if p.start < start:
            p.end = start
        elif p.end > end:
            p.start = end
        else:
            p.start = p.end  # covered completely
        if p.end - p.start < FIT_MIN_S:
            clear_outputs(job_dir, p)
            job.parts.remove(p)
            changes.append(f"{p.name} removida (ficou dentro da parte nova)")
        elif (p.start, p.end) != old:
            clear_outputs(job_dir, p)
            changes.append(f"{p.name}: {fmt_clock(old[0])}–{fmt_clock(old[1])} → {fmt_clock(p.start)}–{fmt_clock(p.end)}")
    part = new_part(job, name, start, end)
    return part, changes


def fit_lyrics_to_parts(job: JobMetadata, job_dir: Path) -> list[str]:
    """After the parts changed: each stanza of the synced lyrics whose part (same name, e.g.
    "Verso 2") no longer holds all of it is pulled inside the part's bounds, so its lines show in
    the part and not in the new one next to it. Returns a description of every change."""
    if not (job_dir / LYRICS_FILE).exists() or not job.parts:
        return []
    payload = lyrics_payload(job, job_dir)
    data, sections = payload["lyrics"], payload["sections"]
    if not data:
        return []
    lines = data["lines"]
    changes: list[str] = []
    for part in job.parts:
        best, best_overlap = None, 0.0
        for s in sections:
            if s["kind"] != "sung" or s["name"] != part.name or "lines" not in s:
                continue
            first, last = s["lines"]
            overlap = min(part.end, lines[last - 1]["end"]) - max(part.start, lines[first]["start"])
            if overlap > best_overlap:
                best, best_overlap = s, overlap
        if best is None:
            continue
        moved = lyrics.fit_lines_to_range(lines, *best["lines"], part.start, part.end)
        if moved:
            changes.append(f"letra de {part.name}: {moved} linha(s) movida(s) para dentro da parte")
    if changes:
        (job_dir / LYRICS_FILE).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return changes


def fmt_clock(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"


def clear_outputs(job_dir: Path, part: Part) -> None:
    shutil.rmtree(part_dir(job_dir, part.part_id), ignore_errors=True)
    part.artifacts = {}
    part.stats = {}


ONSET_TOLERANCE_S = 0.15


def slice_notes(notes: list[NoteEvent], start: float, end: float) -> list[NoteEvent]:
    """Notes whose onset falls inside [start, end), clipped at the end and shifted to start at 0.

    A note attacked just before the start (a hand-placed edge is never exact) is pulled in at 0."""
    out = [
        n.model_copy(update={"start": max(0.0, n.start - start), "end": min(n.end, end) - start})
        for n in notes
        if start - ONSET_TOLERANCE_S <= n.start < end and min(n.end, end) - max(n.start, start) > 0.01
    ]
    out.sort(key=lambda n: (n.start, n.pitch))
    return out


def _stem_paths(job: JobMetadata, job_dir: Path, spec: str) -> list[Path]:
    names = [s.strip() for s in spec.split("+") if s.strip()]
    if not names:
        raise PartError("Nenhuma stem selecionada.")
    missing = [n for n in names if n not in job.stems]
    if missing:
        raise PartError(f"Stem '{missing[0]}' não encontrada neste projeto. Separe as stems primeiro.")
    return [job_dir / job.stems[n] for n in names]


def _slug(spec: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", spec).strip("_") or "stem"


def _cached_stem_notes(job: JobMetadata, job_dir: Path, spec: str) -> list[NoteEvent]:
    """Dettato over the whole stem (mix), once per stem combination."""
    cache = job_dir / f"stem_notes_{_slug(spec)}.json"
    if cache.exists():
        return [NoteEvent(**n) for n in json.loads(cache.read_text(encoding="utf-8"))]
    paths = _stem_paths(job, job_dir, spec)
    audio = paths[0]
    if len(paths) > 1:
        audio = mix_stem_files(paths, job_dir / f"stem_mix_{_slug(spec)}.wav")
    notes = dettato_transcriber.transcribe_stem_notes(audio)
    cache.write_text(json.dumps([n.model_dump() for n in notes]), encoding="utf-8")
    return notes


def _excerpt_audio(
    job: JobMetadata, job_dir: Path, part: Part, params: TranscriptionParams, sr: int
) -> tuple[np.ndarray | None, float]:
    """The excerpt (plus a margin) of the selected stem(s), mono at `sr`, band-passed when asked.
    Returns (audio, t0) where t0 is the absolute time of the first sample."""
    paths = _stem_paths(job, job_dir, params.stem)
    t0 = max(0.0, part.start - BASIC_PITCH_MARGIN_S)
    dur = part.end + BASIC_PITCH_MARGIN_S - t0
    mixed = None
    for p in paths:
        y, _ = librosa.load(str(p), sr=sr, mono=True, offset=t0, duration=dur)
        mixed = _add_signals(mixed, y)
    if mixed is None or mixed.size == 0:
        return None, t0
    peak = float(np.max(np.abs(mixed)))
    if peak > 0.98:
        mixed = mixed * (0.95 / peak)
    if params.bandpass_enabled:
        mixed = apply_bandpass_filter(mixed, sr, lowcut_hz=params.lowcut_hz, highcut_hz=params.highcut_hz)
        peak = float(np.max(np.abs(mixed)))
        if peak > 0.98:
            mixed = mixed * (0.95 / peak)
    return mixed, t0


def _lead_excerpt(
    job: JobMetadata, job_dir: Path, part: Part, params: TranscriptionParams
) -> tuple[list[NoteEvent], str]:
    """Monophonic pitch tracking (CREPE) on the excerpt: solos and single-note riffs."""
    audio, t0 = _excerpt_audio(job, job_dir, part, params, lead_transcriber.SR)
    if audio is None:
        return [], "lead"
    lo, hi = _tuning_pitch_bounds(params.tuning, params.max_frets)
    scale = scales.parse_scale_id(params.scale)
    if params.scale and scale is None:
        raise PartError(f"Escala inválida: {params.scale}")
    notes, info = lead_transcriber.transcribe_lead(
        audio,
        lead_transcriber.SR,
        fmin=float(librosa.midi_to_hz(lo - 1)),
        fmax=float(librosa.midi_to_hz(hi + 1)),
        min_note_s=params.minimum_note_length_ms / 1000.0,
        voicing_threshold=params.voicing_threshold,
        onset_threshold=params.onset_threshold,
        scale_pcs=scales.scale_pcs(*scale) if scale else None,
    )
    shifted = [n.model_copy(update={"start": n.start + t0, "end": n.end + t0}) for n in notes]
    cents = info["tuning_offset_cents"]
    desc = f"solo/riff monofônico ({info['tracker']}, afinação {cents:+.0f} cents"
    if scale:
        desc += f", escala {scales.scale_label(*scale)}: {info['scale_corrected']} notas ajustadas"
    return shifted, desc + ")"


def _basic_pitch_excerpt(
    job: JobMetadata, job_dir: Path, part: Part, params: TranscriptionParams
) -> list[NoteEvent]:
    """Basic Pitch on the excerpt (plus a margin) of the selected stem(s); absolute times."""
    sr = 22050
    mixed, t0 = _excerpt_audio(job, job_dir, part, params, sr)
    if mixed is None:
        return []
    mixed = apply_noise_gate(mixed, sr, threshold_db=params.noise_gate_db)
    peak = float(np.max(np.abs(mixed)))
    if peak > 0.98:
        mixed = mixed * (0.95 / peak)
    pdir = part_dir(job_dir, part.part_id)
    pdir.mkdir(parents=True, exist_ok=True)
    clip = pdir / "_excerpt.wav"
    sf.write(str(clip), mixed.astype(np.float32), sr)
    try:
        notes = _run_basic_pitch_or_fallback(clip, params)
    finally:
        clip.unlink(missing_ok=True)
    return [n.model_copy(update={"start": n.start + t0, "end": n.end + t0}) for n in notes]


def part_raw_notes(
    job: JobMetadata, job_dir: Path, part: Part, params: TranscriptionParams
) -> tuple[list[NoteEvent], str]:
    """The excerpt's notes (absolute times) from the analysis the parameters ask for."""
    if params.source == "multitrack":
        full_midi = job_dir / job.artifacts.get("multitrack_midi", "full_song.mid")
        if not job.multitrack or not full_midi.exists():
            raise PartError("Gere o MIDI completo da música (Dettato) antes de usar suas faixas.")
        indices = params.track_indices or dettato_transcriber.default_tab_tracks(job.multitrack)
        if not indices:
            raise PartError("Nenhuma faixa de guitarra detectada no MIDI completo. Marque as faixas a usar.")
        params.track_indices = indices
        return dettato_transcriber.notes_from_midi_tracks(full_midi, indices), "dettato (MIDI completo)"

    if params.engine == "lead":
        return _lead_excerpt(job, job_dir, part, params)
    if params.engine == "dettato" and dettato_transcriber.is_available():
        try:
            return _cached_stem_notes(job, job_dir, params.stem), f"dettato (stem {params.stem})"
        except PartError:
            raise
        except Exception as exc:  # weights download, OOM, unreadable audio...
            reason = f"dettato falhou: {exc}"[:160]
            return _basic_pitch_excerpt(job, job_dir, part, params), f"basic_pitch (fallback — {reason})"
    return _basic_pitch_excerpt(job, job_dir, part, params), "basic_pitch"


# ---------- scales of a part (solo ear-training aid) ----------

SCALE_CHROMA_SR = 22050


def _excerpt_chroma_profile(job: JobMetadata, job_dir: Path, part: Part, stem: str | None) -> tuple[np.ndarray, str]:
    """Energy-weighted pitch-class profile of the excerpt's audio (a stem when there is one)."""
    names = [s for s in (stem or "").split("+") if s in job.stems]
    if not names:
        names = ["guitar"] if "guitar" in job.stems else []
    paths = [job_dir / job.stems[n] for n in names] or [job_dir / job.input_path]
    source = " + ".join(names) if names else "mix"
    y = None
    for p in paths:
        s, _ = librosa.load(str(p), sr=SCALE_CHROMA_SR, mono=True, offset=part.start, duration=part.end - part.start)
        y = _add_signals(y, s)
    if y is None or y.size < SCALE_CHROMA_SR // 4:
        return np.zeros(12), source
    if not names:
        y = librosa.effects.harmonic(y, margin=2.0)
    tuning = float(librosa.estimate_tuning(y=y, sr=SCALE_CHROMA_SR))
    # from E3: the register of solos; the chugging of the rhythm guitar stays mostly below
    chroma = librosa.feature.chroma_cqt(
        y=y, sr=SCALE_CHROMA_SR, hop_length=512, tuning=tuning, bins_per_octave=36,
        fmin=librosa.note_to_hz("E3"), n_octaves=4,
    )
    rms = librosa.feature.rms(y=y, hop_length=512)[0][: chroma.shape[1]]
    prof = (chroma[:, : len(rms)] * rms).sum(axis=1)
    prof = np.clip(prof - np.percentile(prof, 25), 0.0, None)  # chroma never drops to 0: remove its floor
    return prof, source


def part_scales(job: JobMetadata, job_dir: Path, part: Part, stem: str | None = None) -> dict:
    """Scales the excerpt is probably built on, best first, with the evidence used."""
    audio_prof, audio_source = _excerpt_chroma_profile(job, job_dir, part, stem)
    sources = [f"áudio do trecho ({audio_source})"]
    profile = audio_prof / (audio_prof.sum() or 1.0)
    midi_path = part_dir(job_dir, part.part_id) / part.artifacts.get("midi", "")
    if part.artifacts.get("midi") and midi_path.exists():
        notes = dettato_transcriber.notes_from_midi_tracks(midi_path, [0])
        note_prof = scales.note_profile(notes)
        if note_prof.sum() > 0:
            # the transcribed line is the solo itself; the audio still holds the backing guitars
            profile = 0.65 * note_prof / note_prof.sum() + 0.35 * profile
            sources.insert(0, f"notas transcritas da parte ({len(notes)})")
    if profile.sum() <= 0:
        raise PartError("Não há notas suficientes neste trecho para sugerir escalas.", status=422)

    harm = load_harmony(job_dir)
    key_tonic = key_family = None
    roots = spans = None
    if harm:
        key_tonic, key_family = harm["key"]["tonic"], harm["key"]["family"]
        roots, spans = scales.chord_root_weights(harm["chords"], part.start, part.end)
        sources.append(f"tom ({harm['key']['name']}) e acordes do trecho")
    return {
        "scales": scales.suggest_scales(profile, key_tonic, key_family, roots, spans),
        "profile": [round(float(v), 4) for v in profile / (profile.sum() or 1.0)],
        "sources": sources,
        "has_harmony": harm is not None,
    }


def generate_part(
    job: JobMetadata, job_dir: Path, part: Part, tparams: TranscriptionParams, tab: TabParams
) -> str:
    """Excerpt notes -> guitar clean-up -> MIDI -> fretboard -> MusicXML / GP5 / TXT. Returns the ASCII tab."""
    raw, engine = part_raw_notes(job, job_dir, part, tparams)
    excerpt = slice_notes(raw, part.start, part.end)
    clear_outputs(job_dir, part)
    pdir = part_dir(job_dir, part.part_id)
    pdir.mkdir(parents=True, exist_ok=True)
    notes, stats = finalize_notes_to_midi(excerpt, pdir / "guitar.mid", tparams)
    if not notes:
        raise PartError(
            "Nenhuma nota de guitarra neste trecho. Confira as faixas/stem escolhidas ou ajuste os limites.",
            status=422,
        )
    stats["engine"] = engine
    slices, tab_stats = optimize_fretboard(notes, tab)
    title = f"{job.name} — {part.name}"
    export_musicxml(slices, tab, pdir / "tab.musicxml", title=title)
    export_gp5(slices, tab, pdir / "tab.gp5", title=title)
    txt = export_ascii_tab(slices, tab, pdir / "tab.txt", title=title)
    part.transcription_params = tparams
    part.tab_params = tab
    part.artifacts = {"midi": "guitar.mid", "musicxml": "tab.musicxml", "gp5": "tab.gp5", "txt": "tab.txt"}
    part.stats = {**stats, **tab_stats}
    return txt.read_text(encoding="utf-8")
