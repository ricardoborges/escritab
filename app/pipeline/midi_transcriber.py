from __future__ import annotations
from pathlib import Path
import librosa
import numpy as np
import pretty_midi
from app.models import NoteEvent, TUNINGS, TranscriptionParams


def _tuning_pitch_bounds(tuning_id: str, max_frets: int = 24) -> tuple[int, int]:
    preset = TUNINGS.get(tuning_id, TUNINGS["standard_e"])
    min_pitch = min(preset.pitches)
    max_pitch = max(preset.pitches) + max_frets
    return min_pitch, max_pitch


def _run_basic_pitch_or_fallback(
    audio_path: Path,
    params: TranscriptionParams,
) -> list[NoteEvent]:
    min_pitch, max_pitch = _tuning_pitch_bounds(params.tuning, params.max_frets)
    min_freq = float(librosa.midi_to_hz(min_pitch))
    max_freq = float(librosa.midi_to_hz(max_pitch))

    try:
        from basic_pitch.inference import predict
        from basic_pitch import ICASSP_2022_MODEL_PATH

        _, midi_data, note_events = predict(
            str(audio_path),
            model_or_model_path=ICASSP_2022_MODEL_PATH,
            onset_threshold=params.onset_threshold,
            frame_threshold=params.frame_threshold,
            minimum_note_length=params.minimum_note_length_ms,
            minimum_frequency=min_freq,
            maximum_frequency=max_freq,
        )
        extracted: list[NoteEvent] = []
        for start_s, end_s, pitch, amplitude, _ in note_events:
            vel = int(np.clip(round(float(amplitude) * 127.0), 1, 127))
            extracted.append(
                NoteEvent(
                    pitch=int(pitch),
                    start=float(start_s),
                    end=float(end_s),
                    velocity=vel,
                )
            )
        if extracted:
            return extracted
    except Exception:
        pass

    # Fallback deterministic Librosa pYIN + onset note extractor
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    if y.size == 0:
        return []
    hop_length = 256
    f0, voiced_flag, voiced_probs = librosa.pyin(
        y,
        fmin=min_freq,
        fmax=max_freq,
        sr=sr,
        hop_length=hop_length,
    )
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    max_rms = float(np.max(rms)) if rms.size > 0 else 1.0
    times = librosa.times_like(f0, sr=sr, hop_length=hop_length)
    frame_dt = float(hop_length / sr)

    notes: list[NoteEvent] = []
    cur_pitch: int | None = None
    cur_start: float = 0.0
    cur_vels: list[int] = []

    for idx, (freq, is_voiced, prob) in enumerate(zip(f0, voiced_flag, voiced_probs)):
        amp_norm = float(rms[idx] / (max_rms + 1e-9)) if idx < len(rms) else 0.0
        active = (
            bool(is_voiced)
            and np.isfinite(freq)
            and float(prob) >= params.frame_threshold * 0.5
            and amp_norm >= params.onset_threshold * 0.25
        )
        if active:
            midi_p = int(round(float(librosa.hz_to_midi(freq))))
            vel = int(np.clip(round(amp_norm * 120.0), 20, 127))
            if cur_pitch is None:
                cur_pitch = midi_p
                cur_start = float(times[idx])
                cur_vels = [vel]
            elif midi_p == cur_pitch:
                cur_vels.append(vel)
            else:
                notes.append(
                    NoteEvent(
                        pitch=cur_pitch,
                        start=cur_start,
                        end=float(times[idx]),
                        velocity=int(np.mean(cur_vels)),
                    )
                )
                cur_pitch = midi_p
                cur_start = float(times[idx])
                cur_vels = [vel]
        else:
            if cur_pitch is not None:
                notes.append(
                    NoteEvent(
                        pitch=cur_pitch,
                        start=cur_start,
                        end=float(times[idx]) + frame_dt,
                        velocity=int(np.mean(cur_vels)),
                    )
                )
                cur_pitch = None
                cur_vels = []

    if cur_pitch is not None and len(times) > 0:
        notes.append(
            NoteEvent(
                pitch=cur_pitch,
                start=cur_start,
                end=float(times[-1]) + frame_dt,
                velocity=int(np.mean(cur_vels)),
            )
        )
    return notes


def reduce_to_monophonic(notes: list[NoteEvent], onset_window_s: float = 0.030) -> list[NoteEvent]:
    """One note at a time: per onset group keep the loudest note (ties -> the lower one, since a
    polyphonic model's extra notes are mostly harmonics above the played note), drop notes that
    start while a much louder note is still ringing, then cut each note at the next onset."""
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))
    picked: list[NoteEvent] = []
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j].start - ordered[i].start <= onset_window_s:
            j += 1
        best = max(ordered[i:j], key=lambda n: (n.velocity, -n.pitch))
        # a quiet onset under a sustaining louder note is bleed / a ghost, not a new note
        if picked and best.start < picked[-1].end and best.velocity < 0.5 * picked[-1].velocity:
            i = j
            continue
        picked.append(best)
        i = j
    out: list[NoteEvent] = []
    for k, n in enumerate(picked):
        end = n.end
        if k + 1 < len(picked):
            end = min(end, picked[k + 1].start)
        if end - n.start > 0.005:
            out.append(n.model_copy(update={"end": end}))
    return out


def postprocess_notes(
    notes: list[NoteEvent],
    params: TranscriptionParams,
) -> tuple[list[NoteEvent], dict[str, int]]:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    num_strings = len(preset.pitches)
    min_pitch, max_pitch = _tuning_pitch_bounds(params.tuning, params.max_frets)
    min_len_s = params.minimum_note_length_ms / 1000.0

    short_removed = 0
    out_of_range_adjusted = 0
    duration_filtered: list[NoteEvent] = []

    for n in sorted(notes, key=lambda x: (x.start, x.pitch)):
        if (n.end - n.start) < min_len_s:
            short_removed += 1
            continue
        pitch = n.pitch
        if pitch < min_pitch:
            while pitch < min_pitch:
                pitch += 12
            out_of_range_adjusted += 1
        elif pitch > max_pitch:
            while pitch > max_pitch:
                pitch -= 12
            out_of_range_adjusted += 1
        duration_filtered.append(n.model_copy(update={"pitch": pitch}))

    # Harmonic suppression (+12 octave and +19 octave+fifth within 35ms onset window)
    harmonics_removed = 0
    if params.suppress_harmonics and duration_filtered:
        kept: list[NoteEvent] = []
        for idx, candidate in enumerate(duration_filtered):
            is_spurious = False
            for other in duration_filtered:
                if other is candidate:
                    continue
                if abs(candidate.start - other.start) <= 0.035:
                    interval = candidate.pitch - other.pitch
                    if interval in (12, 19):
                        if candidate.velocity < (
                            other.velocity * params.harmonic_velocity_ratio
                        ):
                            is_spurious = True
                            break
            if is_spurious:
                harmonics_removed += 1
            else:
                kept.append(candidate)
    else:
        kept = duration_filtered

    # Group simultaneous onsets (within 30ms) to enforce max polyphony = num_strings
    polyphony_capped: list[NoteEvent] = []
    i = 0
    while i < len(kept):
        group = [kept[i]]
        j = i + 1
        while j < len(kept) and (kept[j].start - group[0].start) <= 0.030:
            group.append(kept[j])
            j += 1
        # Deduplicate identical pitches in the same onset slice (keep loudest)
        by_pitch: dict[int, NoteEvent] = {}
        for g in group:
            if g.pitch not in by_pitch or g.velocity > by_pitch[g.pitch].velocity:
                by_pitch[g.pitch] = g
        unique_group = list(by_pitch.values())
        if len(unique_group) > num_strings:
            unique_group.sort(key=lambda x: x.velocity, reverse=True)
            unique_group = unique_group[:num_strings]
        unique_group.sort(key=lambda x: x.pitch)
        polyphony_capped.extend(unique_group)
        i = j

    monophonic_removed = 0
    if params.monophonic:
        before = len(polyphony_capped)
        polyphony_capped = reduce_to_monophonic(polyphony_capped)
        monophonic_removed = before - len(polyphony_capped)

    # Optional rhythmic quantization
    final_notes: list[NoteEvent] = []
    if params.quantize_grid in ("1/16", "1/32") and params.bpm > 0:
        subdiv = 4.0 if params.quantize_grid == "1/16" else 8.0
        step = (60.0 / params.bpm) / subdiv
        for n in polyphony_capped:
            q_start = round(n.start / step) * step
            q_end = max(q_start + step, round(n.end / step) * step)
            final_notes.append(
                n.model_copy(update={"start": round(q_start, 6), "end": round(q_end, 6)})
            )
    else:
        final_notes = polyphony_capped

    stats = {
        "short_notes_removed": short_removed,
        "harmonics_removed": harmonics_removed,
        "out_of_range_adjusted": out_of_range_adjusted,
        "monophonic_removed": monophonic_removed,
        "total_notes": len(final_notes),
    }
    return final_notes, stats


def notes_to_midi_file(
    notes: list[NoteEvent],
    output_midi_path: Path,
    bpm: float = 120.0,
) -> Path:
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(bpm))
    guitar_program = pretty_midi.instrument_name_to_program("Distortion Guitar")
    instrument = pretty_midi.Instrument(program=guitar_program, name="Guitar")
    for n in notes:
        instrument.notes.append(
            pretty_midi.Note(
                velocity=int(np.clip(n.velocity, 1, 127)),
                pitch=int(n.pitch),
                start=float(n.start),
                end=float(max(n.start + 0.02, n.end)),
            )
        )
    pm.instruments.append(instrument)
    output_midi_path.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(output_midi_path))
    return output_midi_path


def midi_file_to_notes(midi_path: Path) -> list[NoteEvent]:
    pm = pretty_midi.PrettyMIDI(str(midi_path))
    notes: list[NoteEvent] = []
    for inst in pm.instruments:
        if inst.is_drum:
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


def _extract_raw_notes(
    audio_path: Path,
    params: TranscriptionParams,
) -> tuple[list[NoteEvent], str]:
    """Run the requested engine; fall back to Basic Pitch if Dettato is missing or fails."""
    if params.engine == "lead":
        from app.pipeline import lead_transcriber

        lo, hi = _tuning_pitch_bounds(params.tuning, params.max_frets)
        y, _ = librosa.load(str(audio_path), sr=lead_transcriber.SR, mono=True)
        notes, info = lead_transcriber.transcribe_lead(
            y,
            lead_transcriber.SR,
            fmin=float(librosa.midi_to_hz(lo - 1)),
            fmax=float(librosa.midi_to_hz(hi + 1)),
            min_note_s=params.minimum_note_length_ms / 1000.0,
            voicing_threshold=params.voicing_threshold,
            onset_threshold=params.onset_threshold,
        )
        return notes, f"lead ({info['tracker']}, afinação {info['tuning_offset_cents']:+.0f} cents)"
    if params.engine == "dettato":
        from app.pipeline import dettato_transcriber

        if not dettato_transcriber.is_available():
            reason = "dettato não instalado"
        else:
            try:
                return dettato_transcriber.transcribe_stem_notes(audio_path), "dettato"
            except Exception as exc:  # weights download, OOM, unreadable audio...
                reason = f"dettato falhou: {exc}"[:200]
        return _run_basic_pitch_or_fallback(audio_path, params), f"basic_pitch (fallback — {reason})"
    return _run_basic_pitch_or_fallback(audio_path, params), "basic_pitch"


def finalize_notes_to_midi(
    raw_notes: list[NoteEvent],
    output_midi_path: Path,
    params: TranscriptionParams,
) -> tuple[list[NoteEvent], dict[str, int | float | str]]:
    """Guitar post-processing (range folding, harmonics, polyphony cap, quantization) + MIDI write."""
    cleaned_notes, stats = postprocess_notes(raw_notes, params)
    if cleaned_notes:
        notes_to_midi_file(cleaned_notes, output_midi_path, bpm=params.bpm)
        min_p = min(n.pitch for n in cleaned_notes)
        max_p = max(n.pitch for n in cleaned_notes)
        stats["lowest_note"] = str(librosa.midi_to_note(min_p))
        stats["highest_note"] = str(librosa.midi_to_note(max_p))
    return cleaned_notes, stats


def transcribe_audio_to_midi(
    audio_path: Path,
    output_midi_path: Path,
    params: TranscriptionParams,
) -> tuple[list[NoteEvent], dict[str, int | float | str]]:
    raw_notes, engine_used = _extract_raw_notes(audio_path, params)
    cleaned_notes, stats = finalize_notes_to_midi(raw_notes, output_midi_path, params)
    stats["engine"] = engine_used
    return cleaned_notes, stats
