"""Beat-grid timeline, dynamic MIDI tempo map, and binary-vs-triplet beat classification."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import mido
import numpy as np
import pretty_midi

BEAT_EDGE_TOL_S = 0.08
MIN_BEAT_DT_S = 0.15   # 400 BPM ceiling
MAX_BEAT_DT_S = 2.50   # 24 BPM floor


def _safe_bpm(bpm: float) -> float:
    return float(bpm) if np.isfinite(bpm) and 20.0 < bpm < 360.0 else 120.0


def sanitize_beats(beats: Sequence[float] | None, min_time: float = -BEAT_EDGE_TOL_S) -> np.ndarray:
    """Strictly increasing beat times with plausible musical spacing."""
    if beats is None:
        return np.zeros(0, dtype=np.float64)
    arr = np.asarray(beats, dtype=np.float64)
    if arr.size == 0:
        return np.zeros(0, dtype=np.float64)
    arr = np.sort(arr[np.isfinite(arr) & (arr >= min_time)])
    if arr.size == 0:
        return np.zeros(0, dtype=np.float64)
    kept = [float(arr[0])]
    for t in arr[1:]:
        dt = float(t) - kept[-1]
        if dt < MIN_BEAT_DT_S:
            continue
        kept.append(float(t))
    if len(kept) < 2:
        return np.asarray(kept, dtype=np.float64)
    # Split into contiguous runs without multi-measure dropouts (> MAX_BEAT_DT_S); keep longest run
    runs: list[list[float]] = [[kept[0]]]
    for t in kept[1:]:
        if t - runs[-1][-1] <= MAX_BEAT_DT_S:
            runs[-1].append(t)
        else:
            runs.append([t])
    best = max(runs, key=len)
    return np.asarray(best, dtype=np.float64)


@dataclass(frozen=True)
class BeatMap:
    """Continuous piecewise-linear mapping between seconds and beat coordinates (beat 0 = first beat)."""
    beats: np.ndarray      # >= 2 monotonic beat times in seconds
    step_s: float          # median seconds per beat (used to extrapolate outside [beats[0], beats[-1]])
    bpm: float

    @classmethod
    def from_beats(cls, beats: Sequence[float] | None, bpm: float = 120.0) -> "BeatMap":
        safe = _safe_bpm(bpm)
        clean = sanitize_beats(beats, min_time=-BEAT_EDGE_TOL_S)
        if clean.size >= 2:
            step_s = float(np.median(np.diff(clean)))
            return cls(beats=clean, step_s=step_s, bpm=round(60.0 / step_s, 1))
        step_s = 60.0 / safe
        b0 = float(clean[0]) if clean.size == 1 else 0.0
        return cls(beats=np.array([b0, b0 + step_s], dtype=np.float64), step_s=step_s, bpm=round(safe, 1))

    @classmethod
    def for_excerpt(
        cls,
        beats: Sequence[float] | None,
        bpm: float,
        start: float,
        end: float,
    ) -> "BeatMap":
        """BeatMap on the excerpt's shifted [0, end - start] timeline."""
        safe = _safe_bpm(bpm)
        dur = max(0.0, float(end - start))
        step_hint = 60.0 / safe
        if beats is not None:
            shifted = [float(b - start) for b in beats if (start - BEAT_EDGE_TOL_S) <= b <= (end + step_hint * 2)]
        else:
            shifted = []
        clean = sanitize_beats(shifted, min_time=-BEAT_EDGE_TOL_S)
        if clean.size >= 2 and clean[0] <= dur:
            step_s = float(np.median(np.diff(clean)))
            return cls(beats=clean, step_s=step_s, bpm=round(60.0 / step_s, 1))
        if clean.size == 1 and clean[0] <= dur:
            b0 = float(clean[0])
            return cls(beats=np.array([b0, b0 + step_hint], dtype=np.float64), step_s=step_hint, bpm=round(safe, 1))
        return cls(beats=np.array([0.0, step_hint], dtype=np.float64), step_s=step_hint, bpm=round(safe, 1))

    def time_to_beat(self, t: float | np.ndarray) -> float | np.ndarray:
        arr = np.asarray(t, dtype=np.float64)
        b_idx = np.arange(len(self.beats), dtype=np.float64)
        out = np.interp(arr, self.beats, b_idx)
        lo = arr < self.beats[0]
        hi = arr > self.beats[-1]
        if np.any(lo):
            out = np.where(lo, (arr - self.beats[0]) / self.step_s, out)
        if np.any(hi):
            out = np.where(hi, (len(self.beats) - 1) + (arr - self.beats[-1]) / self.step_s, out)
        return float(out) if np.ndim(t) == 0 else out

    def beat_to_time(self, b: float | np.ndarray) -> float | np.ndarray:
        arr = np.asarray(b, dtype=np.float64)
        b_idx = np.arange(len(self.beats), dtype=np.float64)
        out = np.interp(arr, b_idx, self.beats)
        lo = arr < 0.0
        hi = arr > (len(self.beats) - 1)
        if np.any(lo):
            out = np.where(lo, self.beats[0] + arr * self.step_s, out)
        if np.any(hi):
            out = np.where(hi, self.beats[-1] + (arr - (len(self.beats) - 1)) * self.step_s, out)
        return float(out) if np.ndim(b) == 0 else out


def _clamp_tempo_us(us: float) -> int:
    return int(np.clip(round(us), 150_000, 3_000_000))  # 20..400 BPM


def build_pretty_midi(
    beats: Sequence[float] | None,
    bpm: float = 120.0,
    max_time: float = 0.0,
    resolution: int = 480,
) -> pretty_midi.PrettyMIDI:
    """Create a PrettyMIDI whose Track 0 encodes `beats` as exact multiples of `resolution` ticks."""
    safe = _safe_bpm(bpm)
    clean = sanitize_beats(beats, min_time=-BEAT_EDGE_TOL_S)
    if clean.size < 2:
        return pretty_midi.PrettyMIDI(resolution=resolution, initial_tempo=safe)

    med_dt = float(np.median(np.diff(clean)))
    # Anchor t=0.0 at tick=0 while keeping every subsequent beat on an exact multiple of `resolution`
    has_preroll = float(clean[0]) >= 0.002
    points: list[tuple[int, float]] = [(0, 0.0)]
    if has_preroll:
        lead_beats = max(1, int(round(clean[0] / med_dt)))
        cur_tick = lead_beats * resolution
        points.append((cur_tick, float(clean[0])))
    else:
        cur_tick = 0
    rest = clean[1:]

    for bt in rest:
        if bt <= points[-1][1] + 0.05:
            continue
        cur_tick += resolution
        points.append((cur_tick, float(bt)))

    if len(points) < 2:
        return pretty_midi.PrettyMIDI(resolution=resolution, initial_tempo=safe)

    target_end = max(float(max_time), points[-1][1]) + med_dt * 2.0
    extra_beats = max(1, int(np.ceil((target_end - points[-1][1]) / med_dt)))
    end_tick = points[-1][0] + extra_beats * resolution

    mid = mido.MidiFile(ticks_per_beat=resolution)
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("time_signature", time=0, numerator=4, denominator=4))

    prev_event_tick = 0
    for i in range(len(points) - 1):
        t_tick, t_sec = points[i]
        n_tick, n_sec = points[i + 1]
        span_beats = max(1, (n_tick - t_tick) // resolution)
        raw_us = (n_sec - t_sec) * 1_000_000.0 / span_beats
        if i == 0 and has_preroll:
            us_per_beat = int(np.clip(round(raw_us), 1_000, 16_777_215))
        else:
            us_per_beat = _clamp_tempo_us(raw_us)
        tr.append(mido.MetaMessage("set_tempo", time=t_tick - prev_event_tick, tempo=us_per_beat))
        prev_event_tick = t_tick

    tail_us = _clamp_tempo_us(med_dt * 1_000_000.0)
    tr.append(mido.MetaMessage("set_tempo", time=points[-1][0] - prev_event_tick, tempo=tail_us))
    tr.append(mido.MetaMessage("end_of_track", time=max(1, end_tick - points[-1][0])))
    mid.tracks.append(tr)
    return pretty_midi.PrettyMIDI(mido_object=mid)


_BIN16_GRID = np.array([0.0, 0.25, 0.5, 0.75, 1.0], dtype=np.float64)
_TRIP_GRID = np.array([0.0, 1.0 / 6.0, 1.0 / 3.0, 0.5, 2.0 / 3.0, 5.0 / 6.0, 1.0], dtype=np.float64)
_TRIP_ONLY = np.array([1.0 / 6.0, 1.0 / 3.0, 2.0 / 3.0, 5.0 / 6.0], dtype=np.float64)


def is_triplet_beat(phases: Sequence[float]) -> bool:
    """True when fractional beat onsets in [0, 1) clearly subdivide into triplets/sextuplets (3:2)."""
    inner = [float(p) for p in phases if 0.08 <= p <= 0.92]
    if not inner:
        return False
    if all(abs(p - 0.5) <= 0.09 for p in inner):
        return False
    arr = np.asarray(inner, dtype=np.float64)[:, None]
    d_bin = np.min(np.abs(arr - _BIN16_GRID[None, :]), axis=1)
    d_trip = np.min(np.abs(arr - _TRIP_GRID[None, :]), axis=1)
    d_trip_only = np.min(np.abs(arr - _TRIP_ONLY[None, :]), axis=1)
    if not np.any(d_trip_only <= 0.085):
        return False
    rms_bin = float(np.sqrt(np.mean(d_bin ** 2)))
    rms_trip = float(np.sqrt(np.mean(d_trip ** 2)))
    return (rms_trip + 0.035) < rms_bin
