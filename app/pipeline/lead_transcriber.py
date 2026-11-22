"""Monophonic guitar line (solo / single-note riff) -> notes, with a pitch tracker instead of a
polyphonic transcriber.

Polyphonic models (Dettato, Basic Pitch) hedge on distorted leads: the odd harmonics and the
compression of a high-gain tone come out as octave/fifth ghost notes and overlapping duplicates.
A solo is one note at a time, so tracking a single f0 is both simpler and much more precise:

  1. f0 + periodicity every 5 ms with CREPE (torchcrepe, Viterbi decoding -> few octave jumps);
     librosa pYIN when torchcrepe is not installed;
  2. the recording's global tuning offset (a band a few cents off A440) is measured on the steady
     frames and removed, so rounding to semitones does not flip notes;
  3. voiced regions are cut into notes at picking onsets (spectral flux) and at *step* pitch
     changes (hammer-ons, pull-offs, slides); a *gradual* pitch change with no new attack is a
     bend or vibrato and stays one note, whose bend amount is recorded;
  4. optionally, the scale the solo is built on biases the rounding to semitones: a pitch sitting
     between an in-scale and an out-of-scale note (a sharp/flat attack, a bend not quite reaching
     its target) goes to the scale tone. Clearly out-of-scale notes (chromatic passing notes) stay.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging

import librosa
import numpy as np
from scipy.ndimage import median_filter

from app.models import NoteEvent

log = logging.getLogger(__name__)

SR = 16000            # CREPE's native rate
HOP = 80              # 5 ms frames
HOP_S = HOP / SR
GAP_FILL_S = 0.015    # unvoiced holes this short inside a note (pick noise, dropouts) are bridged
STEADY_TOL = 0.30     # semitones: "on" a pitch
GLIDE_RATE = 60.0     # semitones/s: a pitch change slower than this, with no new attack, is a
                      # bend/vibrato (fast bend ~40 st/s); hammer-ons/pull-offs jump in a frame or two
GLIDE_SLOPE = 6.0     # semitones/s: a run whose pitch keeps drifting this fast is a bend passage
MAX_BEND = 3          # semitones: wider gradual changes are slides (two notes), not bends
ONSET_GUARD_S = 0.020  # an onset this close to a pitch change means the new note was picked
ONSET_SNAP_S = 0.035   # a note start this close to an onset is moved onto it
SCALE_BIAS = 0.3       # semitones of extra "distance" for an out-of-scale note: moves the rounding
                       # boundary 15 cents towards it, so only genuinely ambiguous pitches flip


@dataclass
class PitchTrack:
    midi: np.ndarray        # fractional MIDI pitch per frame (nan = unvoiced)
    conf: np.ndarray        # voicing confidence 0..1
    rms: np.ndarray         # frame energy
    onsets: np.ndarray      # onset frame indices
    tuning_offset: float    # semitones removed from `midi` (positive = recording was sharp)
    tracker: str


# ---------------------------------------------------------------- pitch tracking

def _crepe(y: np.ndarray, fmin: float, fmax: float) -> tuple[np.ndarray, np.ndarray]:
    import torch
    import torchcrepe

    device = "cuda" if torch.cuda.is_available() else "cpu"
    prev_bench = torch.backends.cudnn.benchmark
    prev_det = torch.backends.cudnn.deterministic
    # same audio -> same notes: cuDNN autotuning picks kernels per run and their sums differ slightly
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    try:
        audio = torch.from_numpy(y.astype(np.float32))[None]
        f0, per = torchcrepe.predict(
            audio, SR, HOP, fmin, fmax, model="full",
            decoder=torchcrepe.decode.viterbi, return_periodicity=True,
            batch_size=512, device=device,
        )
        per = torchcrepe.filter.median(per, 3)
        per = torchcrepe.threshold.Silence(-60.0)(per, audio, SR, HOP)
        f0 = f0[0].cpu().numpy().astype(np.float64)
        per = per[0].cpu().numpy().astype(np.float64)
        if device == "cuda":
            torch.cuda.empty_cache()
        return f0, per
    finally:
        torch.backends.cudnn.benchmark = prev_bench
        torch.backends.cudnn.deterministic = prev_det


def _pyin(y: np.ndarray, fmin: float, fmax: float) -> tuple[np.ndarray, np.ndarray]:
    f0, _, prob = librosa.pyin(y, fmin=fmin, fmax=fmax, sr=SR, hop_length=HOP, frame_length=1024)
    return np.nan_to_num(f0, nan=0.0), np.nan_to_num(prob, nan=0.0)


def estimate_tuning_offset(midi: np.ndarray, weight: np.ndarray) -> float:
    """Circular weighted mean of the deviation from the nearest semitone, over steady frames
    (bends and slides would bias it). Returns semitones in [-0.5, 0.5)."""
    ok = np.isfinite(midi)
    if ok.sum() < 20:
        return 0.0
    d = np.abs(np.diff(midi, prepend=np.nan))
    steady = ok & (np.nan_to_num(d, nan=1.0) < 0.05)
    if steady.sum() < 20:
        steady = ok
    dev = midi[steady] - np.round(midi[steady])
    w = weight[steady]
    ang = np.angle(np.sum(w * np.exp(2j * np.pi * dev)))
    return float(ang / (2 * np.pi))


def track_pitch(
    y: np.ndarray, fmin: float, fmax: float, voicing_threshold: float, onset_threshold: float
) -> PitchTrack:
    """y: mono audio at SR."""
    try:
        f0, conf = _crepe(y, fmin, fmax)
        tracker = "crepe"
    except ImportError:
        f0, conf = _pyin(y, fmin, fmax)
        tracker = "pyin"

    n = len(f0)
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=HOP, center=True)[0]
    rms = np.pad(rms, (0, max(0, n - len(rms))))[:n]

    voiced = (conf >= voicing_threshold) & (f0 > 0)
    midi = np.full(n, np.nan)
    midi[voiced] = librosa.hz_to_midi(f0[voiced])

    offset = estimate_tuning_offset(midi, conf)
    midi = midi - offset

    # superflux-style onset envelope: the frequency max-filter keeps vibrato from firing onsets
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP, lag=2, max_size=3)
    env = np.pad(env, (0, max(0, n - len(env))))[:n]
    onsets = librosa.onset.onset_detect(
        onset_envelope=env, sr=SR, hop_length=HOP, units="frames", backtrack=True,
        delta=0.03 + 0.15 * onset_threshold, wait=int(0.05 / HOP_S),
    )
    return PitchTrack(midi, conf, rms, np.asarray(onsets, np.int64), offset, tracker)


# ---------------------------------------------------------------- segmentation

def _voiced_regions(midi: np.ndarray) -> list[tuple[int, int]]:
    voiced = np.isfinite(midi)
    gap = max(1, int(round(GAP_FILL_S / HOP_S)))
    regions: list[tuple[int, int]] = []
    i, n = 0, len(midi)
    while i < n:
        if not voiced[i]:
            i += 1
            continue
        a = i
        while i < n:
            if voiced[i]:
                i += 1
                continue
            j = i
            while j < n and not voiced[j]:
                j += 1
            if j < n and j - i <= gap:
                i = j
            else:
                break
        regions.append((a, i))
    return regions


def _runs(q: np.ndarray) -> list[list[int]]:
    """Run-length encode integer pitches -> [[start, end, pitch], ...] (end exclusive)."""
    runs: list[list[int]] = []
    a = 0
    for i in range(1, len(q) + 1):
        if i == len(q) or q[i] != q[a]:
            runs.append([a, i, int(q[a])])
            a = i
    return runs


def _absorb_short(runs: list[list[int]], min_frames: int, m: np.ndarray) -> list[list[int]]:
    """Fold runs shorter than a note (transients, glide passages, vibrato flicker) into the
    neighbour closest in pitch, shortest first."""
    runs = [r[:] for r in runs]
    while len(runs) > 1:
        lens = [r[1] - r[0] for r in runs]
        k = int(np.argmin(lens))
        if lens[k] >= min_frames:
            break
        cand = [j for j in (k - 1, k + 1) if 0 <= j < len(runs)]
        med = float(np.nanmedian(m[runs[k][0]:runs[k][1]]))
        j = min(cand, key=lambda c: (abs(runs[c][2] - med), -(runs[c][1] - runs[c][0])))
        if j < k:
            runs[j][1] = runs[k][1]
        else:
            runs[j][0] = runs[k][0]
        del runs[k]
        # neighbours that now share a pitch become one run
        merged = [runs[0]]
        for r in runs[1:]:
            if r[2] == merged[-1][2]:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        runs = merged
    return runs


def _nearest_onset(onsets: np.ndarray, frame: int, tol: int) -> int | None:
    k = int(np.searchsorted(onsets, frame))
    best = None
    for j in (k - 1, k):
        if 0 <= j < len(onsets) and abs(int(onsets[j]) - frame) <= tol:
            if best is None or abs(int(onsets[j]) - frame) < abs(best - frame):
                best = int(onsets[j])
    return best


def _is_gliding(m: np.ndarray, r0: int, r1: int) -> bool:
    """A run whose pitch drifts steadily (the middle of a bend) rather than sitting on a note.
    Uses a line fit, so frame jitter and vibrato (which averages out) do not count."""
    if r1 - r0 < 4:
        return False
    t = np.arange(r1 - r0) * HOP_S
    slope = np.polyfit(t, m[r0:r1], 1)[0]
    return abs(slope) >= GLIDE_SLOPE and abs(slope) * t[-1] >= 0.4


def _max_rate(m: np.ndarray, boundary: int, half: int = 4) -> float:
    """Fastest pitch change (semitones/s) in a short window around a run boundary."""
    lo, hi = max(0, boundary - half), min(len(m), boundary + half + 1)
    if hi - lo < 2:
        return float("inf")
    return float(np.max(np.abs(np.diff(m[lo:hi])))) / HOP_S


def scale_penalty(scale_pcs, bias: float = SCALE_BIAS) -> np.ndarray | None:
    """Per pitch class: 0 inside the scale, `bias` outside (None = no scale given)."""
    if not scale_pcs:
        return None
    pen = np.full(12, bias)
    pen[[int(pc) % 12 for pc in scale_pcs]] = 0.0
    return pen


def quantize(x, pen: np.ndarray | None):
    """Round fractional MIDI pitch(es) to semitones, preferring in-scale notes when ambiguous."""
    arr = np.asarray(x, dtype=float)
    if pen is None:
        q = np.round(arr)
    else:
        lo = np.floor(arr)
        hi = lo + 1
        cost_lo = (arr - lo) + pen[lo.astype(int) % 12]
        cost_hi = (hi - arr) + pen[hi.astype(int) % 12]
        q = np.where(cost_hi < cost_lo, hi, lo)
    q = q.astype(int)
    return int(q) if q.ndim == 0 else q


def _clean_lead_notes(notes: list[NoteEvent]) -> list[NoteEvent]:
    """Repair subharmonic octave-drop glitches and drop quiet low rhythm-guitar bleed in upper-register solos."""
    if len(notes) < 3:
        return notes
    out = [n.model_copy() for n in notes]
    # 1. Subharmonic octave drops: a short note ~12 semitones below both its immediate neighbours
    for i in range(1, len(out) - 1):
        prev_n, cur_n, next_n = out[i - 1], out[i], out[i + 1]
        if cur_n.end - cur_n.start > 0.35:
            continue
        if cur_n.start - prev_n.end > 0.25 or next_n.start - cur_n.end > 0.25:
            continue
        up = cur_n.pitch + 12
        if abs(up - prev_n.pitch) <= 3 and abs(up - next_n.pitch) <= 3:
            out[i] = cur_n.model_copy(update={"pitch": up})

    # 2. When the phrase is clearly an upper-register solo (median >= D4=62), drop low rhythm-guitar
    # chug bleed (more than 10 semitones below median and quieter than the solo's median velocity)
    pitches = np.array([n.pitch for n in out])
    med_pitch = float(np.median(pitches))
    if med_pitch >= 62.0:
        med_vel = float(np.median([n.velocity for n in out]))
        out = [
            n for n in out
            if not (n.pitch <= med_pitch - 12 and (n.velocity < 0.85 * med_vel or n.pitch < 55))
        ]
    return out


def segment_notes(
    track: PitchTrack, min_note_s: float, scale_pcs=None, stats: dict | None = None
) -> list[NoteEvent]:
    pen = scale_penalty(scale_pcs)
    corrected = 0
    min_frames = max(2, int(round(min_note_s / HOP_S)))
    guard = int(round(ONSET_GUARD_S / HOP_S))
    snap = int(round(ONSET_SNAP_S / HOP_S))
    onsets = track.onsets
    peak_rms = float(np.max(track.rms)) if track.rms.size else 1.0
    notes: list[NoteEvent] = []

    def picked_near(f: int) -> bool:
        k = np.searchsorted(onsets, f - guard)
        return k < len(onsets) and onsets[k] <= f + guard

    for a, b in _voiced_regions(track.midi):
        if b - a < min_frames:
            continue
        seg = track.midi[a:b].copy()
        # bridge the filled gaps, then smooth pitch jitter (25 ms) without blurring note steps
        idx = np.arange(len(seg))
        ok = np.isfinite(seg)
        seg[~ok] = np.interp(idx[~ok], idx[ok], seg[ok])
        seg = median_filter(seg, size=5, mode="nearest")
        runs = _absorb_short(_runs(quantize(seg, pen)), min_frames, seg)

        # join runs into notes: a slow (bend / vibrato) change with no new attack stays one note
        groups: list[dict] = []
        prev_gliding = False
        for r0, r1, p in runs:
            gliding = _is_gliding(seg, r0, r1)
            if groups:
                g = groups[-1]
                step = p - g["pitch"]
                # bend: within reach, no new attack, and the pitch got there gradually: a slow
                # change at the boundary, or a drifting run on either side of it
                is_bend = (
                    0 < abs(step) <= MAX_BEND
                    and not picked_near(a + r0)
                    and (gliding or prev_gliding or _max_rate(seg, r0) < GLIDE_RATE)
                )
                prev_gliding = gliding
                # a return to the note's own pitch (bend release, vibrato) never starts a note
                if step == 0 or is_bend:
                    g["end"] = r1
                    continue
            prev_gliding = gliding
            groups.append({"start": r0, "end": r1, "pitch": p})

        # anchor picked attacks on the onset: the pitch-run boundary (where f0 crosses the half
        # semitone) wobbles by tens of ms around a pick's noise burst; the onset does not
        n_seg = b - a
        for k, g in enumerate(groups):
            o_abs = _nearest_onset(onsets, a + g["start"], snap)
            if o_abs is None or not 0 <= o_abs - a < n_seg:
                continue
            o = o_abs - a
            lo = groups[k - 1]["start"] + 1 if k > 0 else 0
            if not lo <= o < g["end"]:
                continue
            if k > 0 and groups[k - 1]["end"] == g["start"]:
                groups[k - 1]["end"] = o
            g["start"] = o

        for g in groups:
            # repeated picking on one pitch: split at every onset well inside the note
            cuts = [g["start"]]
            for o in onsets[(onsets > a + g["start"]) & (onsets < a + g["end"])]:
                o_rel = int(o) - a
                if o_rel - cuts[-1] >= min_frames and g["end"] - o_rel >= min_frames:
                    cuts.append(o_rel)
            cuts.append(g["end"])
            for s, e in zip(cuts[:-1], cuts[1:]):
                body = seg[s:e]
                attack = body[: max(min_frames, 1)]
                med = float(np.median(attack))
                pitch = quantize(med, pen)
                if pen is not None and pitch != int(np.round(med)):
                    corrected += 1
                # highest pitch held for >= 40 ms above the attack, in whole semitones (½ / full
                # bends); needs +0.7 st so a wide vibrato (±0.5 st) is not read as a bend
                held = np.sort(body)[-min(len(body), int(0.04 / HOP_S))]
                # (a bend usually lands on a scale tone: with a scale, an ambiguous target goes there)
                bend = float(quantize(held - 0.2, pen) - pitch)
                if bend < 1.0:
                    bend = 0.0
                energy = float(np.max(track.rms[a + s:a + e])) / (peak_rms + 1e-9)
                notes.append(
                    NoteEvent(
                        pitch=pitch,
                        start=round((a + s) * HOP_S, 4),
                        end=round((a + e) * HOP_S, 4),
                        velocity=int(np.clip(round(35 + 90 * np.sqrt(energy)), 1, 127)),
                        bend=min(bend, float(MAX_BEND)),
                    )
                )
    if stats is not None:
        stats["scale_corrected"] = corrected
    return _clean_lead_notes(notes)


def transcribe_lead(
    y: np.ndarray,
    sr: int,
    fmin: float,
    fmax: float,
    min_note_s: float = 0.06,
    voicing_threshold: float = 0.45,
    onset_threshold: float = 0.5,
    scale_pcs=None,
) -> tuple[list[NoteEvent], dict[str, float | str]]:
    """Mono audio of a single guitar line -> notes (seconds from the start of `y`).

    scale_pcs: pitch classes of the solo's scale, to settle ambiguous pitches (None = off)."""
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    if y.size < SR // 10:
        return [], {"tracker": "none", "tuning_offset_cents": 0.0}
    peak = float(np.max(np.abs(y)))
    if peak > 0:
        y = y / peak * 0.9
    track = track_pitch(y, fmin, fmax, voicing_threshold, onset_threshold)
    seg_stats: dict = {}
    notes = segment_notes(track, min_note_s, scale_pcs, seg_stats)
    info = {
        "tracker": track.tracker,
        "tuning_offset_cents": round(track.tuning_offset * 100, 1),
        "scale_corrected": seg_stats.get("scale_corrected", 0),
    }
    log.info("lead transcription: %d notes, %s", len(notes), info)
    return notes, info
