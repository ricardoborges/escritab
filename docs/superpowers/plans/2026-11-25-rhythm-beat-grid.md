# Rhythm, Beat-Grid & Auto-Triplets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align MIDI tempo maps and tablature measures to the song's real `job.beats` timeline, supporting pickup (anacrusis) measures, variable-tempo MIDI exports (`full_song.mid` and `guitar.mid`), multi-tempo frontend playback, and automatic per-beat triplet (`3:2`) quantization on a unified `GRID = 24` engine.

**Architecture:** Introduce `app/pipeline/rhythm.py` with an immutable `BeatMap` (`seconds ↔ beats` piecewise-linear bijection with edge sanitization and fallback to uniform `bpm`) and `build_pretty_midi` (encoding real `set_tempo` events at `480` ticks/beat so wall-clock seconds and DAW barlines both match). Upgrade `app/pipeline/exporters.py` to `GRID = 24` units/quarter note with automatic per-beat binary-vs-triplet classification, pickup measure layout, and off-beat half-bar splitting, then wire `job.beats` through `dettato_transcriber.py`, `midi_transcriber.py`, `projects.py`, `main.py`, `batch.py`, and `app/static/midi_player.js`.

**Tech Stack:** Python 3.11, NumPy, PrettyMIDI + Mido, PyGuitarPro (`guitarpro`), MusicXML 3.1 (`xml.etree.ElementTree`), Vanilla JS (`DataView` SMF parser, Node.js + Pytest).

**Spec:** `docs/superpowers/specs/2026-11-25-rhythm-beat-grid-design.md`

## Global Constraints

- **OS & Shell:** Windows 11 with PowerShell terminal. Always run commands with PowerShell syntax and `uv run pytest`.
- **Backward Compatibility:** `beats: Sequence[float] | None = None` must be optional across all functions so callers and existing tests passing only `bpm` continue working unchanged.
- **No New External Dependencies:** Use only packages already installed (`numpy`, `pretty_midi`, `mido`, `guitarpro`, standard library).
- **Exact Measure Closure:** Every generated measure in MusicXML and GP5 must sum strictly to its declared beat capacity (`pickup_beats * 24` for Measure 0 when `is_pickup=True`, and `96` for regular `4/4` measures).

---

### Task 1: Core `BeatMap`, Triplet Classifier & Dynamic-Tempo `PrettyMIDI` (`app/pipeline/rhythm.py`)

**Files:**
- Create: `app/pipeline/rhythm.py`
- Create: `tests/test_rhythm.py`

**Interfaces:**
- Consumes: `numpy`, `mido`, `pretty_midi`
- Produces:
  - `BEAT_EDGE_TOL_S: float = 0.08`
  - `sanitize_beats(beats: Sequence[float] | None, min_time: float = -0.08) -> np.ndarray`
  - `BeatMap`:
    - `BeatMap.from_beats(beats: Sequence[float] | None, bpm: float = 120.0) -> BeatMap`
    - `BeatMap.for_excerpt(beats: Sequence[float] | None, bpm: float, start: float, end: float) -> BeatMap`
    - `time_to_beat(t: float | np.ndarray) -> float | np.ndarray`
    - `beat_to_time(b: float | np.ndarray) -> float | np.ndarray`
    - `bpm: float`, `step_s: float`, `beats: np.ndarray`
  - `build_pretty_midi(beats: Sequence[float] | None, bpm: float = 120.0, max_time: float = 0.0, resolution: int = 480) -> pretty_midi.PrettyMIDI`
  - `is_triplet_beat(phases: Sequence[float]) -> bool`

- [ ] **Step 1: Write the failing tests in `tests/test_rhythm.py`**

```python
from pathlib import Path
import numpy as np
import pretty_midi
from app.pipeline import rhythm


def test_beat_map_absorbs_initial_offset_and_tempo_drift():
    # First beat at 0.28s, then slight tempo acceleration (0.50s -> 0.48s -> 0.46s)
    beats = [0.28, 0.78, 1.26, 1.72, 2.18]
    bm = rhythm.BeatMap.from_beats(beats, bpm=120.0)

    assert abs(bm.time_to_beat(0.28) - 0.0) < 1e-6
    assert abs(bm.time_to_beat(0.78) - 1.0) < 1e-6
    assert abs(bm.time_to_beat(1.02) - 1.5) < 1e-6  # midpoint between 0.78 and 1.26
    assert bm.time_to_beat(0.04) < -0.4             # pickup before beat 0

    # Inverse round-trip
    sample_t = np.array([0.05, 0.28, 1.02, 1.72, 2.64])
    reconstructed = bm.beat_to_time(bm.time_to_beat(sample_t))
    assert np.allclose(reconstructed, sample_t, atol=1e-6)


def test_beat_map_for_excerpt_handles_edge_tolerance_and_fallback():
    beats = [0.50, 1.00, 1.50, 2.00, 2.50, 3.00]
    # Cut 30ms AFTER the beat at 1.00s -> 1.00s (t_rel = -0.03s) still anchors beat 0.0
    bm = rhythm.BeatMap.for_excerpt(beats, bpm=120.0, start=1.03, end=2.60)
    assert abs(bm.time_to_beat(0.0)) < 0.1  # note at t_rel=0.0 snaps to beat 0, not pickup

    # Cut at 1.20s (well after 1.00s) -> 1.50s (t_rel = 0.30s) becomes beat 0.0; t_rel=0.05 is pickup
    bm_pick = rhythm.BeatMap.for_excerpt(beats, bpm=120.0, start=1.20, end=2.60)
    assert abs(bm_pick.time_to_beat(0.30) - 0.0) < 1e-6
    assert abs(bm_pick.time_to_beat(0.05) - (-0.5)) < 1e-6

    # Fallback without beats -> uniform 120 BPM starting at 0.0
    bm_fb = rhythm.BeatMap.from_beats([], bpm=120.0)
    assert abs(bm_fb.time_to_beat(0.5) - 1.0) < 1e-6


def test_build_pretty_midi_aligns_beats_to_ticks_and_preserves_seconds(tmp_path: Path):
    beats = [0.279, 0.673, 1.045, 1.416, 1.811, 2.206]
    pm = rhythm.build_pretty_midi(beats, bpm=152.0, max_time=2.8, resolution=480)
    ins = pretty_midi.Instrument(program=30, name="Guitar")
    for b in beats:
        ins.notes.append(pretty_midi.Note(velocity=100, pitch=52, start=b, end=b + 0.2))
    pm.instruments.append(ins)

    out = tmp_path / "tempo_mapped.mid"
    pm.write(str(out))

    reloaded = pretty_midi.PrettyMIDI(str(out))
    tempos_times, tempos = reloaded.get_tempo_changes()
    assert len(tempos) >= 4
    # Every beat note must sit on an exact multiple of 480 ticks AND within 2ms of original audio time
    for orig_b, note in zip(beats, reloaded.instruments[0].notes):
        tick = reloaded.time_to_tick(note.start)
        assert tick % 480 == 0
        assert abs(note.start - orig_b) < 0.002


def test_is_triplet_beat_distinguishes_binary_from_triplets():
    assert rhythm.is_triplet_beat([]) is False
    assert rhythm.is_triplet_beat([0.0, 0.5]) is False
    assert rhythm.is_triplet_beat([0.0, 0.25, 0.5, 0.75]) is False
    assert rhythm.is_triplet_beat([0.0, 0.75]) is False  # dotted eighth + 16th
    assert rhythm.is_triplet_beat([0.0, 0.333, 0.667]) is True
    assert rhythm.is_triplet_beat([0.0, 0.666]) is True  # quarter+eighth shuffle triplet
    assert rhythm.is_triplet_beat([0.0, 0.167, 0.333, 0.5, 0.667, 0.833]) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rhythm.py -v`
Expected: FAIL with `ImportError: cannot import name 'rhythm' from 'app.pipeline'`

- [ ] **Step 3: Implement `app/pipeline/rhythm.py`**

```python
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
    if not beats:
        return np.zeros(0, dtype=np.float64)
    arr = np.asarray(beats, dtype=np.float64)
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
        if beats:
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
    points: list[tuple[int, float]] = [(0, 0.0)]
    if clean[0] >= 0.25 * med_dt:
        lead_beats = max(1, int(round(clean[0] / med_dt)))
        cur_tick = lead_beats * resolution
        points.append((cur_tick, float(clean[0])))
        rest = clean[1:]
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
        us_per_beat = _clamp_tempo_us((n_sec - t_sec) * 1_000_000.0 / span_beats)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_rhythm.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```powershell
git add app/pipeline/rhythm.py tests/test_rhythm.py; git commit -m "feat(rhythm): add BeatMap, triplet detector, and dynamic-tempo PrettyMIDI builder"
```

---

### Task 2: `GRID = 24`, Pickup Measures, Auto-Triplets & Half-Bar Splitting in Exporters (`app/pipeline/exporters.py`)

**Files:**
- Modify: `app/pipeline/exporters.py`
- Modify: `tests/test_exporters.py`

**Interfaces:**
- Consumes: `BeatMap`, `is_triplet_beat` from `app.pipeline.rhythm`
- Produces:
  - `GRID: int = 24`, `BEATS_PER_BAR: int = 4`, `MEASURE_UNITS: int = 96`, `FILL_GAP_UNITS: int = 6`
  - `DurSpec(NamedTuple)`: `(units: int, gp_value: int, xml_type: str, dotted: bool = False, tuplet: tuple[int, int] | None = None)`
  - `_Measure`: `events: list[_Event]`, `beats: int = 4`, `is_pickup: bool = False` (`capacity -> int`)
  - `_layout_measures(slices: list[TabSlice], bpm: float, beats: Sequence[float] | None = None) -> list[_Measure]`
  - `_layout(slices: list[TabSlice], bpm: float, beats: Sequence[float] | None = None) -> list[list[_Event]]`
  - `export_musicxml(slices, params, output_path, title="...", beats=None) -> Path`
  - `export_gp5(slices, params, output_path, title="...", beats=None) -> Path`
  - `export_ascii_tab(slices, params, output_path, title="...", beats=None) -> Path`

- [ ] **Step 1: Write the failing tests in `tests/test_exporters.py`**

Append to `tests/test_exporters.py`:

```python
def _sl(start: float, end: float, fret: int = 5, pitch: int = 50, string_idx: int = 2) -> TabSlice:
    return TabSlice(
        start=start,
        end=end,
        notes=[FrettedNote(string_idx=string_idx, fret=fret, pitch=pitch, start=start, end=end)],
    )


def test_layout_aligns_to_warped_beats_without_spurious_ties():
    from app.pipeline.exporters import MEASURE_UNITS, _layout_measures

    # Song starts after 0.28s silence and accelerates slightly; four quarter-note attacks on beats
    beats = [0.28, 0.78, 1.26, 1.72, 2.18]
    slices = [_sl(0.282, 0.76), _sl(0.778, 1.24), _sl(1.262, 1.70), _sl(1.721, 2.16)]
    measures = _layout_measures(slices, bpm=125.0, beats=beats)

    assert len(measures) == 1
    assert not measures[0].is_pickup
    assert sum(ev.dur.units for ev in measures[0].events) == MEASURE_UNITS
    assert [ev.dur.xml_type for ev in measures[0].events] == ["quarter", "quarter", "quarter", "quarter"]
    assert not any(ev.tie_from_prev or ev.tie_to_next for ev in measures[0].events)


def test_pickup_measure_exported_in_musicxml_and_gp5(tmp_path: Path):
    # Beat 1 of Measure 1 is at 0.50s (120 BPM); an eighth-note pickup plays at 0.25s (-0.5 beat)
    beats = [0.50, 1.00, 1.50, 2.00, 2.50]
    slices = [_sl(0.25, 0.49, fret=5, pitch=50), _sl(0.50, 1.00, fret=7, pitch=52)]
    params = TabParams(tuning="standard_e", bpm=120.0)

    xml_root = ET.parse(export_musicxml(slices, params, tmp_path / "p.musicxml", beats=beats)).getroot()
    xml_measures = xml_root.findall(".//part/measure")
    assert xml_measures[0].attrib.get("implicit") == "yes"
    assert xml_measures[0].attrib.get("number") == "0"
    assert xml_measures[0].findtext("attributes/time/beats") == "1"
    assert xml_measures[1].attrib.get("number") == "1"
    assert xml_measures[1].findtext("attributes/time/beats") == "4"

    song = guitarpro.parse(str(export_gp5(slices, params, tmp_path / "p.gp5", beats=beats)))
    assert song.measureHeaders[0].timeSignature.numerator == 1
    assert song.measureHeaders[1].timeSignature.numerator == 4


def test_auto_triplets_in_musicxml_and_gp5(tmp_path: Path):
    from app.pipeline.exporters import MEASURE_UNITS, _layout_measures

    # 120 BPM (1 beat = 0.5s): Beat 1 has 8th-note triplets (0, 1/3, 2/3 of 0.5s), Beat 2 has straight 8ths
    slices = [
        _sl(0.000, 0.160, fret=5, pitch=50),
        _sl(0.167, 0.325, fret=7, pitch=52),
        _sl(0.333, 0.490, fret=8, pitch=53),
        _sl(0.500, 0.740, fret=5, pitch=50),
        _sl(0.750, 0.990, fret=7, pitch=52),
    ]
    params = TabParams(tuning="standard_e", bpm=120.0)
    measures = _layout_measures(slices, bpm=120.0)
    assert sum(ev.dur.units for ev in measures[0].events) == MEASURE_UNITS
    assert [ev.dur.tuplet for ev in measures[0].events[:3]] == [(3, 2), (3, 2), (3, 2)]
    assert measures[0].events[3].dur.tuplet is None

    xml_root = ET.parse(export_musicxml(slices, params, tmp_path / "trip.musicxml")).getroot()
    actuals = [el.text for el in xml_root.findall(".//time-modification/actual-notes")]
    assert actuals == ["3", "3", "3"]
    tuplet_types = [el.attrib["type"] for el in xml_root.findall(".//notations/tuplet")]
    assert tuplet_types == ["start", "stop"]

    song = guitarpro.parse(str(export_gp5(slices, params, tmp_path / "trip.gp5")))
    beats_m0 = song.tracks[0].measures[0].voices[0].beats
    assert [(b.duration.value, b.duration.tuplet.enters, b.duration.tuplet.times) for b in beats_m0[:3]] == [
        (8, 3, 2),
        (8, 3, 2),
        (8, 3, 2),
    ]
    assert (beats_m0[3].duration.value, beats_m0[3].duration.tuplet.enters) == (8, 1)


def test_offbeat_syncopation_splits_across_middle_of_bar():
    from app.pipeline.exporters import _layout

    # 120 BPM: quarter note starting on the "&" of beat 2 (t = 0.75s -> pos 36) lasting to beat 3.5 (t = 1.25s -> pos 60)
    measures = _layout([_sl(0.75, 1.25)], bpm=120.0)
    attack_pieces = [ev for ev in measures[0] if ev.slice is not None]
    assert [(ev.start, ev.dur.units, ev.tie_from_prev, ev.tie_to_next) for ev in attack_pieces] == [
        (36, 12, False, True),
        (48, 12, True, False),
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_exporters.py -v`
Expected: FAIL with `ImportError: cannot import name '_layout_measures'`

- [ ] **Step 3: Implement `GRID = 24`, pickup measures, auto-triplets, and half-bar splitting in `app/pipeline/exporters.py`**

Replace `app/pipeline/exporters.py` with:

```python
from __future__ import annotations
from dataclasses import dataclass
import math
from pathlib import Path
from typing import NamedTuple, Sequence
import xml.etree.ElementTree as ET
import guitarpro
from app.models import TUNINGS, TabParams, TabSlice
from app.pipeline.rhythm import BeatMap, is_triplet_beat

GUITAR_MIDI_PROGRAM = 30  # General MIDI "Distortion Guitar" (0-based)

PITCH_CLASS_NAMES = [
    ("C", 0),
    ("C", 1),
    ("D", 0),
    ("D", 1),
    ("E", 0),
    ("F", 0),
    ("F", 1),
    ("G", 0),
    ("G", 1),
    ("A", 0),
    ("A", 1),
    ("B", 0),
]


def _midi_to_step_alter_octave(midi_pitch: int) -> tuple[str, int, int]:
    pc = midi_pitch % 12
    octave = (midi_pitch // 12) - 1
    step, alter = PITCH_CLASS_NAMES[pc]
    return step, alter, octave


GRID = 24                           # divisions per quarter note: LCM(8 binary 32nds, 6 triplet 16ths)
BEATS_PER_BAR = 4
MEASURE_UNITS = BEATS_PER_BAR * GRID  # 96 units in 4/4
HALF_BAR_UNITS = 2 * GRID             # 48 units (beat 3 boundary in 4/4)
FILL_GAP_UNITS = 6                    # a gap up to a 16th before the next note is read as legato


class DurSpec(NamedTuple):
    units: int
    gp_value: int
    xml_type: str
    dotted: bool = False
    tuplet: tuple[int, int] | None = None


# Binary durations (multiples of 3), longest first
_BINARY_DURATIONS: list[DurSpec] = [
    DurSpec(96, 1, "whole", False, None),
    DurSpec(72, 2, "half", True, None),
    DurSpec(48, 2, "half", False, None),
    DurSpec(36, 4, "quarter", True, None),
    DurSpec(24, 4, "quarter", False, None),
    DurSpec(18, 8, "eighth", True, None),
    DurSpec(12, 8, "eighth", False, None),
    DurSpec(9, 16, "16th", True, None),
    DurSpec(6, 16, "16th", False, None),
    DurSpec(3, 32, "32nd", False, None),
]

# Triplet (3:2) durations inside a single 24-unit beat (multiples of 4), longest first
_TRIPLET_DURATIONS: list[DurSpec] = [
    DurSpec(24, 4, "quarter", False, None),       # full beat rest or tied sustain needs no tuplet mark
    DurSpec(16, 4, "quarter", False, (3, 2)),     # quarter-note triplet (2/3 of a beat)
    DurSpec(12, 8, "eighth", True, (3, 2)),       # dotted-eighth triplet (3/6 of a beat)
    DurSpec(8, 8, "eighth", False, (3, 2)),       # eighth-note triplet (1/3 of a beat)
    DurSpec(4, 16, "16th", False, (3, 2)),        # 16th-note sextuplet (1/6 of a beat)
]


@dataclass
class _Event:
    """One written note/chord or rest inside a measure."""
    start: int                      # units from the start of the measure
    dur: DurSpec
    slice: TabSlice | None          # None = rest
    tie_from_prev: bool = False
    tie_to_next: bool = False
    tuplet_start: bool = False
    tuplet_stop: bool = False


@dataclass
class _Measure:
    events: list[_Event]
    beats: int = BEATS_PER_BAR
    is_pickup: bool = False

    @property
    def capacity(self) -> int:
        return self.beats * GRID


def _split_binary_units(n: int) -> list[DurSpec]:
    out: list[DurSpec] = []
    while n > 0:
        d = next(d for d in _BINARY_DURATIONS if d.units <= n)
        out.append(d)
        n -= d.units
    return out


def _split_triplet_units(n: int) -> list[DurSpec]:
    out: list[DurSpec] = []
    while n > 0:
        d = next(d for d in _TRIPLET_DURATIONS if d.units <= n)
        out.append(d)
        n -= d.units
    return out


def _decompose_in_measure(
    pos0: int,
    pos1: int,
    m_beats: int,
    m_start_beat: int,
    triplet_beats: set[int],
) -> list[tuple[int, DurSpec]]:
    """Split [pos0, pos1) inside one measure respecting binary/triplet beat boundaries and 4/4 half-bar."""
    out: list[tuple[int, DurSpec]] = []
    cur = pos0
    while cur < pos1:
        beat_in_m = cur // GRID
        abs_beat = m_start_beat + beat_in_m
        if abs_beat in triplet_beats:
            b_end = min(pos1, (beat_in_m + 1) * GRID)
            for d in _split_triplet_units(b_end - cur):
                out.append((cur, d))
                cur += d.units
            continue

        # Binary region: advance up to the next triplet beat (or end of span)
        b_end = pos1
        for k in range(beat_in_m + 1, m_beats):
            if (m_start_beat + k) in triplet_beats:
                b_end = min(pos1, k * GRID)
                break

        # Off-beat syncopations in 4/4 must not straddle the middle of the bar (pos = 48) as one glyph
        if m_beats == 4 and (cur % GRID != 0) and cur < HALF_BAR_UNITS < b_end:
            b_end = HALF_BAR_UNITS

        for d in _split_binary_units(b_end - cur):
            out.append((cur, d))
            cur += d.units
    return out


def _mark_tuplet_brackets(events: list[_Event]) -> None:
    """Pair tuplet_start / tuplet_stop per contiguous group of (3, 2) events inside each beat."""
    i = 0
    while i < len(events):
        if events[i].dur.tuplet is None:
            i += 1
            continue
        beat_idx = events[i].start // GRID
        j = i
        while j < len(events) and events[j].dur.tuplet is not None and (events[j].start // GRID) == beat_idx:
            j += 1
        events[i].tuplet_start = True
        events[j - 1].tuplet_stop = True
        i = j


def _merge_slices(a: TabSlice, b: TabSlice) -> TabSlice:
    """Two slices quantized onto the same grid point -> one chord (one note per string)."""
    used = {n.string_idx for n in a.notes}
    extra = [n for n in b.notes if n.string_idx not in used]
    notes = sorted(a.notes + extra, key=lambda n: n.string_idx)
    return TabSlice(start=a.start, end=max(a.end, b.end), notes=notes)


def _quantize_onset(b_on: float, triplet_beats: set[int]) -> int:
    b_idx = math.floor(b_on + 1e-9)
    step = 4 if b_idx in triplet_beats else 3
    q = int(round((b_on * GRID) / step)) * step
    # If rounding pushed across a beat boundary into a beat of the opposite grid, re-snap on that beat
    q_beat = q // GRID
    if q_beat != b_idx and (q % GRID != 0):
        step2 = 4 if q_beat in triplet_beats else 3
        q = int(round((b_on * GRID) / step2)) * step2
    return q


def _quantize_end(on_u: int, b_end: float, triplet_beats: set[int]) -> int:
    raw = b_end * GRID - on_u
    end_beat = math.floor(b_end + 1e-9)
    on_beat = on_u // GRID
    if on_beat in triplet_beats or end_beat in triplet_beats:
        cand = int(round((b_end * GRID) / 4)) * 4
        return max(on_u + 4, cand)
    # Binary: lengths on the 16th grid (6 units), or 32nd (3 units) when genuinely short (< 4.5 units)
    length = 3 if raw < 4.5 else 6 * max(1, int(round(raw / 6.0)))
    cand = on_u + length
    # Never leave a non-multiple-of-3 tail inside a binary beat if `on_u` came from a triplet beat
    if cand % 3 != 0:
        cand = int(round(cand / 3.0)) * 3
    return max(on_u + 3, cand)


def _layout_measures(
    slices: list[TabSlice],
    bpm: float,
    beats: Sequence[float] | None = None,
) -> list[_Measure]:
    """Slices (seconds) -> measures on a 24-unit/beat grid with beat-warping, pickup bar, and auto-triplets."""
    bmap = BeatMap.from_beats(beats, bpm=bpm)
    ordered = sorted(slices, key=lambda x: x.start)
    if not ordered:
        empty = [_Event(start=0, dur=_BINARY_DURATIONS[0], slice=None)]
        return [_Measure(events=empty, beats=BEATS_PER_BAR, is_pickup=False)]

    beat_onsets = [float(bmap.time_to_beat(sl.start)) for sl in ordered]
    beat_ends = [float(bmap.time_to_beat(sl.end)) for sl in ordered]

    by_beat: dict[int, list[float]] = {}
    for b_on in beat_onsets:
        # An attack within 8% of the next beat belongs to that beat's downbeat, not the previous beat's tail
        snapped_near = round(b_on)
        if abs(b_on - snapped_near) < 0.08:
            continue
        b_idx = math.floor(b_on)
        by_beat.setdefault(b_idx, []).append(b_on - b_idx)

    triplet_beats = {b_idx for b_idx, phases in by_beat.items() if is_triplet_beat(phases)}

    items: list[list] = []  # [on_u, end_u, slice]
    for sl, b_on, b_end in zip(ordered, beat_onsets, beat_ends):
        on = _quantize_onset(b_on, triplet_beats)
        end = _quantize_end(on, b_end, triplet_beats)
        if items and on <= items[-1][0]:
            items[-1][2] = _merge_slices(items[-1][2], sl)
            items[-1][1] = max(items[-1][1], end)
            continue
        items.append([on, end, sl])

    for i in range(len(items) - 1):
        nxt = items[i + 1][0]
        items[i][1] = min(items[i][1], nxt)
        gap = nxt - items[i][1]
        if 0 < gap <= FILL_GAP_UNITS:
            items[i][1] = nxt

    min_on = items[0][0]
    has_pickup = min_on < 0
    pickup_beats = 0
    shift_beats = 0
    if has_pickup:
        total_pre_beats = math.ceil(-min_on / GRID)
        pickup_beats = ((total_pre_beats - 1) % BEATS_PER_BAR) + 1
        shift_beats = total_pre_beats
        shift_u = shift_beats * GRID
        for it in items:
            it[0] += shift_u
            it[1] += shift_u
        triplet_beats = {b + shift_beats for b in triplet_beats}

    # Build measure boundaries [m_u0, m_u1, beats, is_pickup, start_beat_idx]
    last_u = max((it[1] for it in items), default=0)
    m_bounds: list[tuple[int, int, int, bool, int]] = []
    cur_u = 0
    cur_b = 0
    if has_pickup:
        cap = pickup_beats * GRID
        m_bounds.append((0, cap, pickup_beats, True, 0))
        cur_u = cap
        cur_b = pickup_beats
    rem_u = max(0, last_u - cur_u)
    n_regular = max(1 if not m_bounds else 0, -(-rem_u // MEASURE_UNITS))
    for _ in range(n_regular):
        m_bounds.append((cur_u, cur_u + MEASURE_UNITS, BEATS_PER_BAR, False, cur_b))
        cur_u += MEASURE_UNITS
        cur_b += BEATS_PER_BAR
    total_u = m_bounds[-1][1]

    segments: list[tuple[int, int, TabSlice | None]] = []
    pos = 0
    for on, end, sl in items:
        if on > pos:
            segments.append((pos, on, None))
        segments.append((on, end, sl))
        pos = end
    if pos < total_u:
        segments.append((pos, total_u, None))

    measures = [_Measure(events=[], beats=b, is_pickup=pk) for _, _, b, pk, _ in m_bounds]
    for a, b, sl in segments:
        pieces: list[tuple[int, int, DurSpec]] = []
        t = a
        for m_idx, (mu0, mu1, m_beats, _, m_start_beat) in enumerate(m_bounds):
            if t >= b:
                break
            if mu1 <= t:
                continue
            seg_end = min(b, mu1)
            for rel_pos, d in _decompose_in_measure(t - mu0, seg_end - mu0, m_beats, m_start_beat, triplet_beats):
                pieces.append((m_idx, rel_pos, d))
            t = seg_end
        for k, (m_idx, rel_pos, d) in enumerate(pieces):
            measures[m_idx].events.append(
                _Event(
                    start=rel_pos,
                    dur=d,
                    slice=sl,
                    tie_from_prev=sl is not None and k > 0,
                    tie_to_next=sl is not None and k < len(pieces) - 1,
                )
            )

    for m in measures:
        _mark_tuplet_brackets(m.events)
    return measures


def _layout(
    slices: list[TabSlice],
    bpm: float,
    beats: Sequence[float] | None = None,
) -> list[list[_Event]]:
    return [m.events for m in _layout_measures(slices, bpm, beats=beats)]


def export_musicxml(
    slices: list[TabSlice],
    params: TabParams,
    output_path: Path,
    title: str = "EscriTab Transcription",
    beats: Sequence[float] | None = None,
) -> Path:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    num_strings = len(preset.pitches)

    score = ET.Element("score-partwise", version="3.1")
    work = ET.SubElement(score, "work")
    ET.SubElement(work, "work-title").text = title

    part_list = ET.SubElement(score, "part-list")
    score_part = ET.SubElement(part_list, "score-part", id="P1")
    ET.SubElement(score_part, "part-name").text = f"Guitar ({preset.name})"
    score_inst = ET.SubElement(score_part, "score-instrument", id="P1-I1")
    ET.SubElement(score_inst, "instrument-name").text = "Distortion Guitar"
    midi_inst = ET.SubElement(score_part, "midi-instrument", id="P1-I1")
    ET.SubElement(midi_inst, "midi-channel").text = "1"
    ET.SubElement(midi_inst, "midi-program").text = str(GUITAR_MIDI_PROGRAM + 1)

    part = ET.SubElement(score, "part", id="P1")
    measures = _layout_measures(slices, params.bpm, beats=beats)
    has_pickup = bool(measures and measures[0].is_pickup)

    for m_idx, m in enumerate(measures):
        m_num = m_idx if has_pickup else m_idx + 1
        measure = ET.SubElement(part, "measure", number=str(m_num))
        if m.is_pickup:
            measure.set("implicit", "yes")

        if m_idx == 0:
            attrs = ET.SubElement(measure, "attributes")
            ET.SubElement(attrs, "divisions").text = str(GRID)
            key = ET.SubElement(attrs, "key")
            ET.SubElement(key, "fifths").text = "0"
            time_el = ET.SubElement(attrs, "time")
            ET.SubElement(time_el, "beats").text = str(m.beats)
            ET.SubElement(time_el, "beat-type").text = "4"
            clef = ET.SubElement(attrs, "clef")
            ET.SubElement(clef, "sign").text = "TAB"
            ET.SubElement(clef, "line").text = "5"

            staff_details = ET.SubElement(attrs, "staff-details")
            ET.SubElement(staff_details, "staff-lines").text = str(num_strings)
            for s_idx, pitch in enumerate(preset.pitches):
                step, alter, octave = _midi_to_step_alter_octave(pitch)
                st = ET.SubElement(staff_details, "staff-tuning", line=str(s_idx + 1))
                ET.SubElement(st, "tuning-step").text = step
                if alter != 0:
                    ET.SubElement(st, "tuning-alter").text = str(alter)
                ET.SubElement(st, "tuning-octave").text = str(octave)

            direction = ET.SubElement(measure, "direction", placement="above")
            dir_type = ET.SubElement(direction, "direction-type")
            metronome = ET.SubElement(dir_type, "metronome")
            ET.SubElement(metronome, "beat-unit").text = "quarter"
            ET.SubElement(metronome, "per-minute").text = str(int(round(params.bpm)))
            ET.SubElement(direction, "sound", tempo=str(int(round(params.bpm))))
        elif m_idx == 1 and has_pickup and m.beats != measures[0].beats:
            attrs = ET.SubElement(measure, "attributes")
            time_el = ET.SubElement(attrs, "time")
            ET.SubElement(time_el, "beats").text = str(m.beats)
            ET.SubElement(time_el, "beat-type").text = "4"

        for ev in m.events:
            units, _, xml_type, dotted, tuplet = ev.dur
            if ev.slice is None:
                note_el = ET.SubElement(measure, "note")
                rest = ET.SubElement(note_el, "rest")
                if units == m.capacity and not m.is_pickup:
                    rest.set("measure", "yes")
                ET.SubElement(note_el, "duration").text = str(units)
                ET.SubElement(note_el, "voice").text = "1"
                ET.SubElement(note_el, "type").text = xml_type
                if dotted:
                    ET.SubElement(note_el, "dot")
                if tuplet is not None:
                    tm = ET.SubElement(note_el, "time-modification")
                    ET.SubElement(tm, "actual-notes").text = str(tuplet[0])
                    ET.SubElement(tm, "normal-notes").text = str(tuplet[1])
                if ev.tuplet_start or ev.tuplet_stop:
                    notations = ET.SubElement(note_el, "notations")
                    if ev.tuplet_start:
                        ET.SubElement(notations, "tuplet", type="start")
                    if ev.tuplet_stop:
                        ET.SubElement(notations, "tuplet", type="stop")
                continue

            for n_idx, fn in enumerate(ev.slice.notes):
                note_el = ET.SubElement(measure, "note")
                if n_idx > 0:
                    ET.SubElement(note_el, "chord")
                step, alter, octave = _midi_to_step_alter_octave(fn.pitch)
                pitch_el = ET.SubElement(note_el, "pitch")
                ET.SubElement(pitch_el, "step").text = step
                if alter != 0:
                    ET.SubElement(pitch_el, "alter").text = str(alter)
                ET.SubElement(pitch_el, "octave").text = str(octave)

                ET.SubElement(note_el, "duration").text = str(units)
                if ev.tie_from_prev:
                    ET.SubElement(note_el, "tie", type="stop")
                if ev.tie_to_next:
                    ET.SubElement(note_el, "tie", type="start")
                ET.SubElement(note_el, "voice").text = "1"
                ET.SubElement(note_el, "type").text = xml_type
                if dotted:
                    ET.SubElement(note_el, "dot")
                if tuplet is not None:
                    tm = ET.SubElement(note_el, "time-modification")
                    ET.SubElement(tm, "actual-notes").text = str(tuplet[0])
                    ET.SubElement(tm, "normal-notes").text = str(tuplet[1])

                notations = ET.SubElement(note_el, "notations")
                if ev.tie_from_prev:
                    ET.SubElement(notations, "tied", type="stop")
                if ev.tie_to_next:
                    ET.SubElement(notations, "tied", type="start")
                if n_idx == 0:
                    if ev.tuplet_start:
                        ET.SubElement(notations, "tuplet", type="start")
                    if ev.tuplet_stop:
                        ET.SubElement(notations, "tuplet", type="stop")
                technical = ET.SubElement(notations, "technical")
                xml_string_num = num_strings - fn.string_idx
                ET.SubElement(technical, "string").text = str(xml_string_num)
                ET.SubElement(technical, "fret").text = str(fn.fret)
                if fn.bend >= 1.0 and not ev.tie_from_prev:
                    bend_el = ET.SubElement(technical, "bend")
                    ET.SubElement(bend_el, "bend-alter").text = f"{fn.bend:g}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(score, space="  ")
    xml_bytes = ET.tostring(score, encoding="utf-8", xml_declaration=True)
    output_path.write_bytes(xml_bytes)
    return output_path


def _gp_bend(semitones: float) -> guitarpro.models.BendEffect:
    """Bend up and hold. pyguitarpro bend point values are quarter tones (full bend = 4)."""
    q = int(round(semitones * 2))
    return guitarpro.models.BendEffect(
        type=guitarpro.models.BendType.bend,
        value=int(round(semitones * 50)),
        points=[
            guitarpro.models.BendPoint(position=0, value=0),
            guitarpro.models.BendPoint(position=6, value=q),
            guitarpro.models.BendPoint(position=12, value=q),
        ],
    )


def export_gp5(
    slices: list[TabSlice],
    params: TabParams,
    output_path: Path,
    title: str = "EscriTab Transcription",
    beats: Sequence[float] | None = None,
) -> Path:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    num_strings = len(preset.pitches)
    song = guitarpro.models.Song()
    song.title = title
    song.tempo = int(round(params.bpm))

    track = song.tracks[0]
    track.name = f"Guitar ({preset.id})"
    track.fretCount = params.max_frets
    track.strings = [
        guitarpro.models.GuitarString(number=i + 1, value=pitch)
        for i, pitch in enumerate(reversed(preset.pitches))
    ]

    measures = _layout_measures(slices, params.bpm, beats=beats)
    while len(song.measureHeaders) < len(measures):
        header = guitarpro.models.MeasureHeader(number=len(song.measureHeaders) + 1)
        song.measureHeaders.append(header)
        track.measures.append(guitarpro.models.Measure(track=track, header=header))

    for m_idx, m in enumerate(measures):
        header = song.measureHeaders[m_idx]
        header.timeSignature.numerator = m.beats
        header.timeSignature.denominator.value = 4

        voice = track.measures[m_idx].voices[0]
        voice.beats.clear()
        for ev in m.events:
            _, gp_value, _, dotted, tuplet = ev.dur
            gp_tuplet = (
                guitarpro.models.Tuplet(enters=tuplet[0], times=tuplet[1])
                if tuplet is not None
                else guitarpro.models.Tuplet()
            )
            duration = guitarpro.models.Duration(value=gp_value, isDotted=dotted, tuplet=gp_tuplet)
            if ev.slice is None:
                voice.beats.append(
                    guitarpro.models.Beat(
                        voice=voice, status=guitarpro.models.BeatStatus.rest, duration=duration
                    )
                )
                continue
            beat = guitarpro.models.Beat(
                voice=voice, status=guitarpro.models.BeatStatus.normal, duration=duration
            )
            for fn in ev.slice.notes:
                note = guitarpro.models.Note(
                    beat=beat,
                    value=fn.fret,
                    velocity=fn.velocity,
                    string=num_strings - fn.string_idx,
                    type=(
                        guitarpro.models.NoteType.tie
                        if ev.tie_from_prev
                        else guitarpro.models.NoteType.normal
                    ),
                )
                if fn.bend >= 1.0 and not ev.tie_from_prev:
                    note.effect.bend = _gp_bend(fn.bend)
                beat.notes.append(note)
            voice.beats.append(beat)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    guitarpro.write(song, str(output_path), version=(5, 1, 0))
    return output_path


def _ascii_cell(fret: int, bend: float) -> str:
    """'7', or '7b9' for a note bent up to the pitch of fret 9."""
    return f"{fret}b{fret + int(round(bend))}" if bend >= 1.0 else str(fret)


def export_ascii_tab(
    slices: list[TabSlice],
    params: TabParams,
    output_path: Path,
    title: str = "EscriTab Transcription",
    beats: Sequence[float] | None = None,
) -> Path:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    num_strings = len(preset.pitches)
    max_label_len = max((len(name) for name in preset.string_names), default=2)
    labels = [name.ljust(max_label_len) for name in reversed(preset.string_names)]

    header_lines = [
        f"# {title}",
        f"# Afinação: {preset.name} | BPM: {params.bpm:.1f}",
        "",
    ]

    columns_per_system = 16

    cols: list[tuple[int, dict[int, str]]] = []
    for m_idx, m in enumerate(_layout_measures(slices, params.bpm, beats=beats)):
        for ev in m.events:
            if ev.slice is not None and not ev.tie_from_prev:
                cells = {fn.string_idx: _ascii_cell(fn.fret, fn.bend) for fn in ev.slice.notes}
                cols.append((m_idx + 1, cells))

    if not cols:
        cols.append((1, {}))

    systems: list[str] = []
    for start_idx in range(0, len(cols), columns_per_system):
        chunk = cols[start_idx : start_idx + columns_per_system]
        rows = [f"{lbl}|" for lbl in labels]
        cur_measure = chunk[0][0]
        for m_num, col_map in chunk:
            if m_num != cur_measure:
                for r in range(num_strings):
                    rows[r] += "|"
                cur_measure = m_num
            width = max([1] + [len(c) for c in col_map.values()])
            for r in range(num_strings):
                s_idx = (num_strings - 1) - r
                cell = col_map.get(s_idx, "")
                rows[r] += "-" + cell.ljust(width, "-") + "--"
        for r in range(num_strings):
            rows[r] += "|"
        systems.append("\n".join(rows))

    full_text = "\n".join(header_lines) + "\n\n".join(systems) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(full_text, encoding="utf-8")
    return output_path
```

- [ ] **Step 4: Run test to verify all exporter tests pass**

Run: `uv run pytest tests/test_exporters.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```powershell
git add app/pipeline/exporters.py tests/test_exporters.py; git commit -m "feat(exporters): upgrade layout to GRID=24 with beat-warping, pickup bars, and auto-triplets"
```

---

### Task 3: Wire Beat-Grid & Dynamic Tempo Maps into Transcribers, Projects, API & Batch CLI

**Files:**
- Modify: `app/pipeline/dettato_transcriber.py:94-120`
- Modify: `app/pipeline/midi_transcriber.py:172-180,280-325,380-404`
- Modify: `app/projects.py:527-541,738-763`
- Modify: `app/main.py:231-238`
- Modify: `app/batch.py:284-288`
- Modify: `tests/test_dettato_transcriber.py`
- Modify: `tests/test_midi_transcriber.py`
- Modify: `tests/test_api.py:242-247`

**Interfaces:**
- Consumes: `BeatMap`, `build_pretty_midi`, `BEAT_EDGE_TOL_S` from `app.pipeline.rhythm`, and `beats` arg in `app.pipeline.exporters`
- Produces:
  - `dettato_transcriber.transcribe_multitrack(audio_path, output_midi_path, split_guitar_roles=True, bpm=120.0, beats=None)`
  - `midi_transcriber.postprocess_notes(notes, params, beats=None)`
  - `midi_transcriber.notes_to_midi_file(notes, output_midi_path, bpm=120.0, beats=None)`
  - `midi_transcriber.finalize_notes_to_midi(raw_notes, output_midi_path, params, beats=None)`
  - `projects.slice_notes(notes, start, end, beats=None)`

- [ ] **Step 1: Write the failing tests in `tests/test_dettato_transcriber.py`, `tests/test_midi_transcriber.py`, and `tests/test_api.py`**

Append to `tests/test_dettato_transcriber.py`:

```python
def test_multitrack_encodes_project_bpm_and_beat_tempo_map(tmp_path: Path, fake_dettato):
    beats = [0.25, 0.65, 1.05, 1.45, 1.85]  # 150 BPM (0.40s per beat) after 0.25s lead-in
    out_mid = tmp_path / "full_150.mid"
    dettato_transcriber.transcribe_multitrack(
        tmp_path / "in.wav", out_mid, bpm=150.0, beats=beats
    )
    pm = pretty_midi.PrettyMIDI(str(out_mid))
    _, tempos = pm.get_tempo_changes()
    assert any(abs(t - 150.0) < 1.0 for t in tempos)
    assert not np.allclose(tempos, 120.0)
```

Append to `tests/test_midi_transcriber.py`:

```python
def test_postprocess_quantizes_on_warped_beat_grid_and_writes_tempo_map(tmp_path: Path):
    import pretty_midi
    from app.pipeline.midi_transcriber import finalize_notes_to_midi

    # Beat 0 at 0.20s, Beat 1 at 0.60s (0.40s beat = 150 BPM -> 1/16 = 0.10s)
    beats = [0.20, 0.60, 1.00, 1.40]
    raw = [
        NoteEvent(pitch=52, start=0.212, end=0.405, velocity=100),  # snaps to 0.20..0.40 (beat 0.0..0.5)
        NoteEvent(pitch=55, start=0.491, end=0.610, velocity=95),   # snaps to 0.50..0.60 (beat 0.75..1.0)
    ]
    params = TranscriptionParams(quantize_grid="1/16", bpm=150.0, suppress_harmonics=False)
    mid_path = tmp_path / "warped.mid"
    cleaned, _ = finalize_notes_to_midi(raw, mid_path, params, beats=beats)

    assert [(round(n.start, 3), round(n.end, 3)) for n in cleaned] == [(0.20, 0.40), (0.50, 0.60)]
    pm = pretty_midi.PrettyMIDI(str(mid_path))
    _, tempos = pm.get_tempo_changes()
    assert any(abs(t - 150.0) < 1.0 for t in tempos)
```

Update `fake_transcribe_multitrack` in `tests/test_api.py:242` to accept `**kwargs` and add an API beat-alignment test at the bottom of `tests/test_api.py`:

```python
def test_generate_part_passes_job_beats_into_tab_and_midi(tmp_path: Path, fake_dettato):
    import xml.etree.ElementTree as ET

    client = TestClient(create_app(workspace_root=tmp_path))
    res = client.post(
        "/api/projects",
        files={"file": ("song.wav", _make_wav_bytes(duration_s=2.5), "audio/wav")},
        data={"is_isolated_stem": "false", "mode": "dettato"},
    )
    pid = res.json()["job_id"]

    # Inject known beats starting at 0.25s with 150 BPM spacing (0.40s)
    job_path = tmp_path / pid / "job.json"
    meta = json.loads(job_path.read_text(encoding="utf-8"))
    meta["detected_bpm"] = 150.0
    meta["beats"] = [0.25, 0.65, 1.05, 1.45, 1.85, 2.25]
    job_path.write_text(json.dumps(meta), encoding="utf-8")

    assert client.post(f"/api/projects/{pid}/multitrack").status_code == 200
    part = client.post(f"/api/projects/{pid}/parts", json={"name": "Intro", "start": 0.0, "end": 2.0}).json()["part"]
    gen = client.post(
        f"/api/projects/{pid}/parts/{part['part_id']}/generate",
        json={"transcription": {"source": "multitrack", "bpm": 150.0}, "tab": {"bpm": 150.0}},
    )
    assert gen.status_code == 200, gen.text
    xml_bytes = client.get(f"/api/projects/{pid}/parts/{part['part_id']}/files/tab.musicxml").content
    root = ET.fromstring(xml_bytes)
    assert root.findtext(".//attributes/divisions") == "24"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dettato_transcriber.py tests/test_midi_transcriber.py tests/test_api.py -k "tempo_map or warped_beat_grid or passes_job_beats" -v`
Expected: FAIL with `TypeError: transcribe_multitrack() got an unexpected keyword argument 'bpm'`

- [ ] **Step 3: Update `dettato_transcriber.py`, `midi_transcriber.py`, `projects.py`, `main.py`, `batch.py`, and `tests/test_api.py`**

In `app/pipeline/dettato_transcriber.py`:
- Import `Sequence` from `typing` and `rhythm` from `app.pipeline`.
- Update `_write_midi` and `transcribe_multitrack`:

```python
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
    result = _run(audio_path)
    parts = [part for t in result.tracks for part in _role_parts(t, split_guitar_roles)]
    _write_midi(parts, output_midi_path, bpm=bpm, beats=beats)
```

In `app/pipeline/midi_transcriber.py`:
- Import `Sequence` from `typing` and `from app.pipeline import rhythm`.
- Add `beats: Sequence[float] | None = None` parameter to `postprocess_notes(notes: list[NoteEvent], params: TranscriptionParams, beats: Sequence[float] | None = None)` and replace the quantization block (`lines 280-293`) with:

```python
    # Optional rhythmic quantization (warped onto BeatMap when beats are provided)
    final_notes: list[NoteEvent] = []
    if params.quantize_grid in ("1/16", "1/32") and params.bpm > 0:
        subdiv = 4.0 if params.quantize_grid == "1/16" else 8.0
        bmap = rhythm.BeatMap.from_beats(beats, bpm=params.bpm)
        by_slot: dict[tuple[int, int], NoteEvent] = {}
        for n in polyphony_capped:
            b_start = float(bmap.time_to_beat(n.start))
            b_end = float(bmap.time_to_beat(n.end))
            slot_on = int(round(b_start * subdiv))
            slot_off = max(slot_on + 1, int(round(b_end * subdiv)))
            q_start = max(0.0, float(bmap.beat_to_time(slot_on / subdiv)))
            q_end = max(q_start + 0.02, float(bmap.beat_to_time(slot_off / subdiv)))
            cand = n.model_copy(update={"start": round(q_start, 6), "end": round(q_end, 6)})
            key = (slot_on, cand.pitch)
            if key not in by_slot or cand.velocity > by_slot[key].velocity:
                by_slot[key] = cand
        final_notes = sorted(by_slot.values(), key=lambda x: (x.start, x.pitch))
    else:
        final_notes = polyphony_capped
```

```python
def notes_to_midi_file(
    notes: list[NoteEvent],
    output_midi_path: Path,
    bpm: float = 120.0,
    beats: Sequence[float] | None = None,
) -> Path:
    max_t = max((float(n.end) for n in notes), default=0.0)
    pm = rhythm.build_pretty_midi(beats=beats, bpm=float(bpm), max_time=max_t)
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
```

```python
def finalize_notes_to_midi(
    raw_notes: list[NoteEvent],
    output_midi_path: Path,
    params: TranscriptionParams,
    beats: Sequence[float] | None = None,
) -> tuple[list[NoteEvent], dict[str, int | float | str]]:
    """Guitar post-processing (range folding, harmonics, polyphony cap, quantization) + MIDI write."""
    cleaned_notes, stats = postprocess_notes(raw_notes, params, beats=beats)
    if cleaned_notes:
        notes_to_midi_file(cleaned_notes, output_midi_path, bpm=params.bpm, beats=beats)
        min_p = min(n.pitch for n in cleaned_notes)
        max_p = max(n.pitch for n in cleaned_notes)
        stats["lowest_note"] = str(librosa.midi_to_note(min_p))
        stats["highest_note"] = str(librosa.midi_to_note(max_p))
    return cleaned_notes, stats
```

In `app/projects.py`:
- Import `Sequence` from `typing` and `rhythm` from `app.pipeline`.
- Update `slice_notes` and `generate_part`:

```python
def slice_notes(
    notes: list[NoteEvent],
    start: float,
    end: float,
    beats: Sequence[float] | None = None,
) -> list[NoteEvent]:
    """Notes whose onset falls inside [start, end), clipped at the end and shifted to start at 0."""
    tol = ONSET_TOLERANCE_S
    if beats:
        bmap = rhythm.BeatMap.for_excerpt(beats, bpm=120.0, start=start, end=end)
        tol = min(ONSET_TOLERANCE_S, 0.35 * bmap.step_s)
    out = [
        n.model_copy(update={"start": max(0.0, n.start - start), "end": min(n.end, end) - start})
        for n in notes
        if start - tol <= n.start < end and min(n.end, end) - max(n.start, start) > 0.01
    ]
    out.sort(key=lambda n: (n.start, n.pitch))
    return out
```

```python
def generate_part(
    job: JobMetadata, job_dir: Path, part: Part, tparams: TranscriptionParams, tab: TabParams
) -> str:
    """Excerpt notes -> guitar clean-up -> MIDI -> fretboard -> MusicXML / GP5 / TXT. Returns the ASCII tab."""
    raw, engine = part_raw_notes(job, job_dir, part, tparams)
    excerpt = slice_notes(raw, part.start, part.end, beats=job.beats or None)
    bmap = rhythm.BeatMap.for_excerpt(job.beats, bpm=tab.bpm, start=part.start, end=part.end)
    local_beats = list(bmap.beats) if job.beats else None
    clear_outputs(job_dir, part)
    pdir = part_dir(job_dir, part.part_id)
    pdir.mkdir(parents=True, exist_ok=True)
    notes, stats = finalize_notes_to_midi(excerpt, pdir / "guitar.mid", tparams, beats=local_beats)
    if not notes:
        raise PartError(
            "Nenhuma nota de guitarra neste trecho. Confira as faixas/stem escolhidas ou ajuste os limites.",
            status=422,
        )
    stats["engine"] = engine
    slices, tab_stats = optimize_fretboard(notes, tab)
    title = f"{job.name} — {part.name}"
    export_musicxml(slices, tab, pdir / "tab.musicxml", title=title, beats=local_beats)
    export_gp5(slices, tab, pdir / "tab.gp5", title=title, beats=local_beats)
    txt = export_ascii_tab(slices, tab, pdir / "tab.txt", title=title, beats=local_beats)
    part.transcription_params = tparams
    part.tab_params = tab
    part.artifacts = {"midi": "guitar.mid", "musicxml": "tab.musicxml", "gp5": "tab.gp5", "txt": "tab.txt"}
    part.stats = {**stats, **tab_stats}
    return txt.read_text(encoding="utf-8")
```

In `app/main.py:233` and `app/batch.py:285`:
- Pass `bpm=job.detected_bpm, beats=job.beats or None` to `dettato_transcriber.transcribe_multitrack(...)`.
- In `tests/test_api.py:242`, change `def fake_transcribe_multitrack(audio_path, full_midi, split_guitar_roles=True):` to `def fake_transcribe_multitrack(audio_path, full_midi, split_guitar_roles=True, **kwargs):`.

- [ ] **Step 4: Run tests to verify all pass**

Run: `uv run pytest tests/test_dettato_transcriber.py tests/test_midi_transcriber.py tests/test_api.py tests/test_batch.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add app/pipeline/dettato_transcriber.py app/pipeline/midi_transcriber.py app/projects.py app/main.py app/batch.py tests/test_dettato_transcriber.py tests/test_midi_transcriber.py tests/test_api.py; git commit -m "feat(pipeline): wire BeatMap tempo maps into MIDI exports, slicing, API, and CLI"
```

---

### Task 4: Multi-Tempo SMF Track-0 Parser in Frontend `MidiPlayer` (`app/static/midi_player.js`)

**Files:**
- Modify: `app/static/midi_player.js:25-30,64-82,191-194`
- Create: `tests/test_midi_player_tempo.py`

**Interfaces:**
- Consumes: Standard MIDI File (`ArrayBuffer`) produced by `rhythm.build_pretty_midi`
- Produces:
  - `MidiPlayer.parseTempoMap(buf)` -> `Array<{tick: number, usPerBeat: number, sec: number}>`
  - Accurate `MidiPlayer.prototype.seconds(ticks)` across variable `set_tempo` changes

- [ ] **Step 1: Write the failing test in `tests/test_midi_player_tempo.py`**

```python
import json
from pathlib import Path
import subprocess
import shutil
import pytest
import pretty_midi
from app.pipeline import rhythm


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_midi_player_js_seconds_matches_pretty_midi_across_tempo_changes(tmp_path: Path):
    beats = [0.279, 0.673, 1.045, 1.416, 1.950, 2.550, 3.050]  # accelerating & decelerating
    pm = rhythm.build_pretty_midi(beats, bpm=140.0, max_time=3.5, resolution=480)
    ins = pretty_midi.Instrument(program=30)
    ins.notes.append(pretty_midi.Note(velocity=100, pitch=60, start=0.279, end=3.1))
    pm.instruments.append(ins)

    mid_path = tmp_path / "var_tempo.mid"
    pm.write(str(mid_path))
    reloaded = pretty_midi.PrettyMIDI(str(mid_path))

    sample_ticks = [0, 240, 480, 960, 1440, 2160, 2880, 3360]
    expected_secs = [float(reloaded.tick_to_time(t)) for t in sample_ticks]

    js_path = (Path(__file__).resolve().parent.parent / "app" / "static" / "midi_player.js").as_posix()
    mid_posix = mid_path.as_posix()
    script = f"""
      const fs = require('fs');
      global.window = {{}};
      require('{js_path}');
      const MP = global.window.MidiPlayer;
      const bytes = fs.readFileSync('{mid_posix}');
      const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
      const mp = new MP();
      mp.division = 480;
      mp.tempoMap = MP.parseTempoMap(ab, 480);
      const ticks = {json.dumps(sample_ticks)};
      console.log(JSON.stringify(ticks.map(t => mp.seconds(t))));
    """
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    actual_secs = json.loads(proc.stdout.strip())
    for tick, got, exp in zip(sample_ticks, actual_secs, expected_secs):
        assert abs(got - exp) < 0.002, f"tick={tick}: got {got}s != expected {exp}s"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_midi_player_tempo.py -v`
Expected: FAIL with `TypeError: MP.parseTempoMap is not a function`

- [ ] **Step 3: Implement `MidiPlayer.parseTempoMap` and update `seconds(ticks)` in `app/static/midi_player.js`**

In `app/static/midi_player.js`:
1. Initialize `this.tempoMap = null;` in `constructor()`.
2. Add `static parseTempoMap(buf, division)` to `MidiPlayer` that scans Track 0 (`MTrk`) variable-length delta-times and `0xFF 0x51 0x03` (`set_tempo`) meta-events, building `[{ tick, usPerBeat, sec }, ...]`.
3. In `setSong`, after computing `this.division`, set `this.tempoMap = MidiPlayer.parseTempoMap(this.midi, this.division);`.
4. Update `seconds(ticks)`:

```javascript
  static parseTempoMap(buf, division) {
    const u8 = new Uint8Array(buf);
    const dv = new DataView(buf);
    if (u8.length < 22 || dv.getUint32(0) !== 0x4d546864) return null; // "MThd"
    let pos = 8 + dv.getUint32(4);
    if (pos + 8 > u8.length || dv.getUint32(pos) !== 0x4d54726b) return null; // "MTrk"
    const end = Math.min(u8.length, pos + 8 + dv.getUint32(pos + 4));
    pos += 8;

    const readVlq = () => {
      let v = 0;
      while (pos < end) {
        const b = u8[pos++];
        v = (v << 7) | (b & 0x7f);
        if (!(b & 0x80)) break;
      }
      return v;
    };

    const segs = [{ tick: 0, usPerBeat: 500000, sec: 0 }];
    let tick = 0;
    let running = 0;
    while (pos < end) {
      tick += readVlq();
      if (pos >= end) break;
      let status = u8[pos];
      if (status < 0x80) {
        status = running;
      } else {
        pos++;
        if (status < 0xf0) running = status;
      }
      if (status === 0xff) {
        const metaType = u8[pos++];
        const len = readVlq();
        if (metaType === 0x51 && len === 3 && pos + 3 <= end) {
          const us = (u8[pos] << 16) | (u8[pos + 1] << 8) | u8[pos + 2];
          const prev = segs[segs.length - 1];
          const sec = prev.sec + ((tick - prev.tick) * prev.usPerBeat) / (1e6 * division);
          if (tick === prev.tick) prev.usPerBeat = us;
          else segs.push({ tick, usPerBeat: us, sec });
        }
        pos += len;
      } else if (status === 0xf0 || status === 0xf7) {
        pos += readVlq();
      } else {
        const hi = status & 0xf0;
        pos += hi === 0xc0 || hi === 0xd0 ? 1 : 2;
      }
    }
    return segs;
  }

  seconds(ticks) {
    if (this.tempoMap && this.tempoMap.length) {
      let lo = 0, hi = this.tempoMap.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (this.tempoMap[mid].tick <= ticks) lo = mid;
        else hi = mid - 1;
      }
      const s = this.tempoMap[lo];
      return s.sec + ((ticks - s.tick) * s.usPerBeat) / (1e6 * this.division);
    }
    return (ticks * 60) / (this.bpm * this.division);
  }
```

- [ ] **Step 4: Run full test suite to verify everything passes**

Run: `uv run pytest -v`
Expected: ALL tests PASS with zero regressions.

- [ ] **Step 5: Commit**

```powershell
git add app/static/midi_player.js tests/test_midi_player_tempo.py; git commit -m "feat(player): support variable-tempo SMF maps in MidiPlayer clock"
```
