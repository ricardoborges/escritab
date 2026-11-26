"""Multi-instrument audio -> MIDI with Dettato (https://github.com/kobimusic/dettato).

Dettato chains four models (transcriber -> evidence repairer -> instrument grouper -> drums) and
returns one MIDI track per detected instrument (General MIDI program + name) plus a drum track.
The drum model only runs for sources recorded at >= 40 kHz, so the full-song transcription is fed
the original upload, never a resampled/filtered copy.

The models (~28M params) are loaded once per process and reused; inference is serialized with a
lock because the whole chain is GPU/CPU bound and not documented as thread-safe.
"""
from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path
from typing import Any, Sequence

import pretty_midi

from app.models import MidiTrackInfo, NoteEvent
from app.pipeline import rhythm

GUITAR_PROGRAMS = frozenset(range(24, 32))  # GM 25-32: acoustic / electric / overdriven / distortion guitars

_model: Any = None
_model_lock = threading.Lock()


def is_available() -> bool:
    return importlib.util.find_spec("dettato") is not None


def _get_model() -> Any:
    """Load the four Dettato models once (weights come from the Hugging Face Hub on first use,
    or from the folder in the DETTATO_WEIGHTS env var)."""
    global _model
    if _model is None:
        from dettato import Dettato

        from app.pipeline import model_downloads

        model_downloads.ensure_dettato()  # first run: download with progress for the UI
        _model = Dettato.from_pretrained()  # cuda when available, otherwise cpu
    return _model


def model_device() -> str:
    return str(getattr(_model, "device", "unloaded"))


def _note_range(notes: Any) -> tuple[str | None, str | None]:
    if len(notes) == 0:
        return None, None
    pitches = [int(p) for p in notes[:, 2]]
    return (
        pretty_midi.note_number_to_name(min(pitches)),
        pretty_midi.note_number_to_name(max(pitches)),
    )


def _run(audio_path: Path) -> Any:
    with _model_lock:
        return _get_model().transcribe(str(audio_path))


MIN_PART_NOTES = 20      # a guitar role part smaller than this stays merged
MIN_PART_SHARE = 0.03    # ... or smaller than this share of the guitar track


def _role_parts(t: Any, split_roles: bool) -> list[tuple[str, int, bool, Any, str | None]]:
    """One Dettato track -> [(name, program, is_drum, notes, role)]. Guitar tracks are split into
    rhythm / lead parts when both parts are substantial; lead-like non-guitar tracks are flagged."""
    from app.pipeline import guitar_roles

    program, notes = int(t.program), t.notes
    if t.is_drum:
        return [(t.name, program, True, notes, None)]
    if program in GUITAR_PROGRAMS:
        if split_roles and len(notes):
            lead = guitar_roles.split_roles(notes)
            n_lead = int(lead.sum())
            n_rhythm = len(notes) - n_lead
            min_part = max(MIN_PART_NOTES, MIN_PART_SHARE * len(notes))
            if n_lead >= min_part and n_rhythm >= min_part:
                return [
                    (f"{t.name} - Base", program, False, notes[~lead], "rhythm"),
                    (f"{t.name} - Solo/Riff", program, False, notes[lead], "lead"),
                ]
        return [(t.name, program, False, notes, None)]
    role = "lead_candidate" if guitar_roles.is_lead_candidate(program, False, notes) else None
    return [(t.name, program, False, notes, role)]


def _write_midi(
    parts: list[tuple[str, int, bool, Any, str | None]],
    path: Path,
    bpm: float = 120.0,
    beats: Sequence[float] | None = None,
) -> None:
    max_t = max((float(notes[:, 1].max()) for _, _, _, notes, _ in parts if len(notes)), default=0.0)
    pm = rhythm.build_pretty_midi(beats=beats, bpm=bpm, max_time=max_t)
    for name, program, is_drum, notes, _ in parts:
        ins = pretty_midi.Instrument(program=0 if is_drum else program, is_drum=is_drum, name=name)
        for on, off, pitch, vel in notes:
            ins.notes.append(
                pretty_midi.Note(velocity=int(vel), pitch=int(pitch), start=float(on), end=float(off))
            )
        pm.instruments.append(ins)
    path.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(path))


def transcribe_multitrack(
    audio_path: Path,
    output_midi_path: Path,
    split_guitar_roles: bool = True,
    bpm: float = 120.0,
    beats: Sequence[float] | None = None,
) -> tuple[list[MidiTrackInfo], dict[str, Any]]:
    """Transcribe a whole song. Writes a multi-track MIDI (and a JSON summary next to it).

    With ``split_guitar_roles`` every guitar track is split into "Base" (rhythm) and "Solo/Riff"
    (lead) tracks. Track ``index`` matches the instrument order inside the written MIDI file."""
    result = _run(audio_path)
    parts = [part for t in result.tracks for part in _role_parts(t, split_guitar_roles)]
    _write_midi(parts, output_midi_path, bpm=bpm, beats=beats)

    tracks: list[MidiTrackInfo] = []
    for idx, (name, program, is_drum, notes, role) in enumerate(parts):
        lowest, highest = (None, None) if is_drum else _note_range(notes)
        tracks.append(
            MidiTrackInfo(
                index=idx,
                name=name,
                program=None if is_drum else program,
                is_drum=is_drum,
                note_count=int(len(notes)),
                is_guitar=(not is_drum) and program in GUITAR_PROGRAMS,
                role=role,
                lowest_note=lowest,
                highest_note=highest,
            )
        )

    info = result.summary()
    info["device"] = model_device()
    info["split_guitar_roles"] = split_guitar_roles
    output_midi_path.with_suffix(".json").write_text(
        json.dumps(info, indent=2, default=str), encoding="utf-8"
    )
    return tracks, info


def default_tab_tracks(tracks: list[MidiTrackInfo]) -> list[int]:
    """Tracks to use for the guitar tab when the user hasn't picked any: every guitar track."""
    return [t.index for t in tracks if t.is_guitar and t.note_count > 0]


def notes_from_midi_tracks(midi_path: Path, track_indices: list[int]) -> list[NoteEvent]:
    """Merge the notes of the selected (non-drum) tracks of a multi-track MIDI."""
    pm = pretty_midi.PrettyMIDI(str(midi_path))
    wanted = set(track_indices)
    notes: list[NoteEvent] = []
    for idx, inst in enumerate(pm.instruments):
        if idx not in wanted or inst.is_drum:
            continue
        for n in inst.notes:
            notes.append(
                NoteEvent(
                    pitch=int(n.pitch),
                    start=float(n.start),
                    end=float(n.end),
                    velocity=int(n.velocity),
                )
            )
    notes.sort(key=lambda x: (x.start, x.pitch))
    return notes


def transcribe_stem_notes(audio_path: Path) -> list[NoteEvent]:
    """Transcribe an (already isolated) stem and return its pitched notes.

    The stem is supposed to hold one instrument, so every pitched track is kept: the grouper's
    instrument naming is its weakest stage and may split one guitar into e.g. guitar + piano."""
    result = _run(audio_path)
    notes: list[NoteEvent] = []
    for t in result.tracks:
        if t.is_drum:
            continue
        for on, off, pitch, vel in t.notes:
            notes.append(
                NoteEvent(
                    pitch=int(pitch),
                    start=float(on),
                    end=float(off),
                    velocity=int(max(1, min(127, round(float(vel))))),
                )
            )
    notes.sort(key=lambda x: (x.start, x.pitch))
    return notes
