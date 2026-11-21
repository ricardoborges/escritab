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
from app.pipeline import dettato_transcriber, harmony
from app.pipeline.audio_cleaner import apply_bandpass_filter, apply_noise_gate, mix_stem_files
from app.pipeline.exporters import export_ascii_tab, export_gp5, export_musicxml
from app.pipeline.fretboard import optimize_fretboard
from app.pipeline.midi_transcriber import _run_basic_pitch_or_fallback, finalize_notes_to_midi

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
    return re.sub(r"[_]+", " ", stem).strip() or "Sem nome"


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
            y = s if y is None else y[: len(s)] + s[: len(y)]
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


def clear_outputs(job_dir: Path, part: Part) -> None:
    shutil.rmtree(part_dir(job_dir, part.part_id), ignore_errors=True)
    part.artifacts = {}
    part.stats = {}


ONSET_TOLERANCE_S = 0.15


def slice_notes(notes: list[NoteEvent], start: float, end: float) -> list[NoteEvent]:
    """Notes whose onset falls inside [start, end), clipped at the end and shifted to start at 0.

    A note attacked just before the start (a hand-placed edge is never exact) is pulled in at 0."""
    out = [
        NoteEvent(
            pitch=n.pitch,
            start=max(0.0, n.start - start),
            end=min(n.end, end) - start,
            velocity=n.velocity,
        )
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


def _basic_pitch_excerpt(
    job: JobMetadata, job_dir: Path, part: Part, params: TranscriptionParams
) -> list[NoteEvent]:
    """Basic Pitch on the excerpt (plus a margin) of the selected stem(s); absolute times."""
    paths = _stem_paths(job, job_dir, params.stem)
    t0 = max(0.0, part.start - BASIC_PITCH_MARGIN_S)
    dur = part.end + BASIC_PITCH_MARGIN_S - t0
    mixed, sr = None, 22050
    for p in paths:
        y, _ = librosa.load(str(p), sr=sr, mono=True, offset=t0, duration=dur)
        mixed = y if mixed is None else mixed[: len(y)] + y[: len(mixed)]
    if mixed is None or mixed.size == 0:
        return []
    if params.bandpass_enabled:
        mixed = apply_bandpass_filter(mixed, sr, lowcut_hz=params.lowcut_hz, highcut_hz=params.highcut_hz)
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
    return [NoteEvent(pitch=n.pitch, start=n.start + t0, end=n.end + t0, velocity=n.velocity) for n in notes]


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

    if params.engine == "dettato" and dettato_transcriber.is_available():
        try:
            return _cached_stem_notes(job, job_dir, params.stem), f"dettato (stem {params.stem})"
        except PartError:
            raise
        except Exception as exc:  # weights download, OOM, unreadable audio...
            reason = f"dettato falhou: {exc}"[:160]
            return _basic_pitch_excerpt(job, job_dir, part, params), f"basic_pitch (fallback — {reason})"
    return _basic_pitch_excerpt(job, job_dir, part, params), "basic_pitch"


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
