# Multi-Instrument MIDI Piano Roll Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an interactive, 60-FPS full-song multi-instrument MIDI Piano Roll in the project's **MIDI** tab so users can select one or more instruments (each rendered in a distinct color), read note names directly inside bars and in a live "Notas agora" readout (with guitar fret/string hints and key-scale row highlighting), click notes/keys to hear them isolated, and follow playback synced with either the slowed audio player (`WaveSurfer`) or the virtual-instrument `MidiPlayer`.

**Architecture:** Expose `GET /api/projects/{project_id}/multitrack/notes` backed by an mtime-cached `pretty_midi` serializer in `dettato_transcriber.py` returning compact `[start, end, pitch, velocity]` tuples per track. Extend `MidiPlayer` (`app/static/midi_player.js`) with `ticksAtSeconds`, 60-FPS `currentSeconds`, and SoundFont `auditionNote`. Build a self-contained Canvas 2D `PianoRoll` (`app/static/piano_roll.js`) using prefix-max binary search (`O(log N)`) for viewport culling, and wire instrument filter chips, dual-clock playhead sync, and WebAudio fallback audition in `app/static/index.html`, `app/static/styles.css`, and `app/static/app.js`.

**Tech Stack:** Python 3.11, FastAPI, PrettyMIDI, HTML5 `<canvas>` 2D, Web Audio API / FluidSynth (`js-synthesizer`), Vanilla ES2022 JS, Pytest + Node.js (`v22`).

**Spec:** `docs/superpowers/specs/2026-11-27-midi-piano-roll-design.md`

## Global Constraints

- **OS & Shell:** Windows 11 with PowerShell terminal. Always use PowerShell syntax and `uv run pytest`.
- **No New Dependencies:** Use only packages already in the environment (`pretty_midi`, `fastapi`, Vanilla JS, Node.js for JS unit tests).
- **Performance:** Viewport rendering must use binary search (`visibleNoteRange` with `maxEndPrefix`) so sustained notes starting before `tStart` remain visible and 10,000-note songs render smoothly at 60 FPS.
- **HTML Hygiene:** Every `id="..."` in `app/static/index.html` must remain globally unique (`tests/test_api.py::test_html_has_no_duplicate_ids_and_js_assigns_sequential_melodic_channels` enforces this).

---

### Task 1: Backend Multi-Track Notes Endpoint & Mtime Cache (`dettato_transcriber.py`, `main.py`)

**Files:**
- Modify: `app/pipeline/dettato_transcriber.py:146-192`
- Modify: `app/main.py:240-252`
- Create: `tests/test_piano_roll.py`

**Interfaces:**
- Consumes: `app.models.MidiTrackInfo`, `pretty_midi.PrettyMIDI`
- Produces:
  - `dettato_transcriber.multitrack_notes_payload(midi_path: Path, tracks: list[MidiTrackInfo], duration: float = 0.0) -> dict[str, Any]`
  - `GET /api/projects/{project_id}/multitrack/notes` -> `{"duration": float, "tracks": [{"index": int, "name": str, "program": int | None, "is_drum": bool, "is_guitar": bool, "role": str | None, "notes": list[list[float | int]]}]}` (or `404` when `full_song.mid` / `job.multitrack` is missing)

- [ ] **Step 1: Write the failing backend tests in `tests/test_piano_roll.py`**

```python
import io
from pathlib import Path
import numpy as np
import pretty_midi
import soundfile as sf
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import MidiTrackInfo
from app.pipeline import dettato_transcriber


def _wav_bytes(sr: int = 22050, seconds: float = 1.5) -> bytes:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    buf = io.BytesIO()
    sf.write(buf, (0.4 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32), sr, format="WAV")
    return buf.getvalue()


def test_multitrack_notes_payload_serializes_and_invalidates_cache_on_change(tmp_path: Path):
    midi_path = tmp_path / "full_song.mid"
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    gtr = pretty_midi.Instrument(program=29, is_drum=False, name="Electric Guitar - Solo/Riff")
    gtr.notes.append(pretty_midi.Note(velocity=105, pitch=64, start=0.5, end=1.25))
    gtr.notes.append(pretty_midi.Note(velocity=90, pitch=59, start=0.1, end=0.45))
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    drums.notes.append(pretty_midi.Note(velocity=110, pitch=36, start=0.0, end=0.1))
    pm.instruments.extend([gtr, drums])
    pm.write(str(midi_path))

    tracks = [
        MidiTrackInfo(index=0, name="Electric Guitar - Solo/Riff", program=29, is_drum=False, is_guitar=True, role="lead", note_count=2),
        MidiTrackInfo(index=1, name="Drums", program=None, is_drum=True, is_guitar=False, role=None, note_count=1),
    ]
    payload = dettato_transcriber.multitrack_notes_payload(midi_path, tracks, duration=2.0)
    assert payload["duration"] == 2.0
    assert len(payload["tracks"]) == 2
    # Sorted by (start, pitch): [0.1, 0.45, 59, 90] comes before [0.5, 1.25, 64, 105]
    assert payload["tracks"][0]["notes"] == [[0.1, 0.45, 59, 90], [0.5, 1.25, 64, 105]]
    assert payload["tracks"][0]["role"] == "lead"
    assert payload["tracks"][1]["is_drum"] is True
    assert payload["tracks"][1]["notes"] == [[0.0, 0.1, 36, 110]]

    # Overwriting the MIDI file with a new note must invalidate the cache
    pm2 = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    gtr2 = pretty_midi.Instrument(program=29, is_drum=False, name="Electric Guitar - Solo/Riff")
    gtr2.notes.append(pretty_midi.Note(velocity=100, pitch=67, start=0.2, end=0.8))
    pm2.instruments.append(gtr2)
    pm2.write(str(midi_path))
    tracks2 = [MidiTrackInfo(index=0, name="Electric Guitar - Solo/Riff", program=29, is_drum=False, is_guitar=True, role="lead", note_count=1)]
    payload2 = dettato_transcriber.multitrack_notes_payload(midi_path, tracks2, duration=1.5)
    assert payload2["tracks"][0]["notes"] == [[0.2, 0.8, 67, 100]]


def test_api_multitrack_notes_404_before_and_200_after_transcription(tmp_path: Path, monkeypatch):
    client = TestClient(create_app(workspace_root=tmp_path))
    res = client.post(
        "/api/projects",
        files={"file": ("song.wav", _wav_bytes(), "audio/wav")},
        data={"is_isolated_stem": "false", "mode": "dettato"},
    )
    pid = res.json()["job_id"]

    # Before multitrack transcription -> 404
    assert client.get(f"/api/projects/{pid}/multitrack/notes").status_code == 404

    class _FakeTrack:
        def __init__(self, name, program, notes, is_drum=False):
            self.name, self.program, self.notes, self.is_drum = name, program, notes, is_drum

    class _FakeRes:
        tracks = [
            _FakeTrack("Electric Guitar", 27, np.array([[0.25, 0.75, 52, 96]], dtype=float)),
            _FakeTrack("Drums", 0, np.array([[0.0, 0.1, 38, 100]], dtype=float), is_drum=True),
        ]
        def summary(self):
            return {"tracks": 2}

    monkeypatch.setattr(dettato_transcriber, "is_available", lambda: True)
    monkeypatch.setattr(dettato_transcriber, "_run", lambda path: _FakeRes())

    assert client.post(f"/api/projects/{pid}/multitrack").status_code == 200
    notes_res = client.get(f"/api/projects/{pid}/multitrack/notes")
    assert notes_res.status_code == 200, notes_res.text
    body = notes_res.json()
    assert body["duration"] >= 1.4
    assert [t["name"] for t in body["tracks"]] == ["Electric Guitar", "Drums"]
    assert body["tracks"][0]["notes"][0][2:] == [52, 96]
    assert body["tracks"][1]["is_drum"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_piano_roll.py -v`
Expected: FAIL with `AttributeError: module 'app.pipeline.dettato_transcriber' has no attribute 'multitrack_notes_payload'`

- [ ] **Step 3: Implement `multitrack_notes_payload` in `app/pipeline/dettato_transcriber.py` and `GET /api/projects/{project_id}/multitrack/notes` in `app/main.py`**

Append to `app/pipeline/dettato_transcriber.py`:

```python
_NOTES_CACHE: dict[str, tuple[tuple[int, int, int], dict[str, Any]]] = {}


def multitrack_notes_payload(
    midi_path: Path,
    tracks: list[MidiTrackInfo],
    duration: float = 0.0,
) -> dict[str, Any]:
    """Compact per-track note arrays [[start, end, pitch, velocity], ...] for the browser Piano Roll."""
    st = midi_path.stat()
    sig = (st.st_mtime_ns, st.st_size, len(tracks))
    key = str(midi_path)
    cached = _NOTES_CACHE.get(key)
    if cached and cached[0] == sig:
        data = cached[1]
        if duration > data["duration"]:
            return {**data, "duration": round(float(duration), 4)}
        return data

    pm = pretty_midi.PrettyMIDI(str(midi_path))
    out_tracks: list[dict[str, Any]] = []
    for info in tracks:
        inst = pm.instruments[info.index] if 0 <= info.index < len(pm.instruments) else None
        raw_notes = inst.notes if inst is not None else []
        ordered = sorted(raw_notes, key=lambda n: (n.start, n.pitch))
        serialized = [
            [round(float(n.start), 4), round(float(n.end), 4), int(n.pitch), int(n.velocity)]
            for n in ordered
        ]
        out_tracks.append({
            "index": info.index,
            "name": info.name,
            "program": info.program,
            "is_drum": info.is_drum,
            "is_guitar": info.is_guitar,
            "role": info.role,
            "notes": serialized,
        })

    total_dur = round(max(float(duration or 0.0), float(pm.get_end_time())), 4)
    payload = {"duration": total_dur, "tracks": out_tracks}
    _NOTES_CACHE[key] = (sig, payload)
    return payload
```

In `app/main.py` right after `@app.post("/api/projects/{project_id}/multitrack")` (`line ~248`), add:

```python
    @app.get("/api/projects/{project_id}/multitrack/notes")
    def get_multitrack_notes(project_id: str):
        """Per-track note list of the full-song MIDI for the browser Piano Roll."""
        job, job_dir = load(project_id)
        midi_name = job.artifacts.get("multitrack_midi", "full_song.mid")
        full_midi = job_dir / midi_name
        if not job.multitrack or not full_midi.is_file():
            raise HTTPException(status_code=404, detail="MIDI completo ainda não foi gerado.")
        return dettato_transcriber.multitrack_notes_payload(full_midi, job.multitrack, duration=job.duration)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_piano_roll.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```powershell
git add app/pipeline/dettato_transcriber.py app/main.py tests/test_piano_roll.py; git commit -m "feat(api): add cached multitrack notes endpoint for Piano Roll"
```

---

### Task 2: `MidiPlayer` Inverse Seek (`ticksAtSeconds`), Smooth 60-FPS Clock & Note Audition (`app/static/midi_player.js`)

**Files:**
- Modify: `app/static/midi_player.js`
- Modify: `tests/test_midi_player_tempo.py`

**Interfaces:**
- Consumes: `this.tempoMap`, `this.synth`, `this.routes`, `this.fonts` in `MidiPlayer`
- Produces:
  - `MidiPlayer.prototype.ticksAtSeconds(sec: number) -> number`
  - `MidiPlayer.prototype.currentSeconds() -> number`
  - `MidiPlayer.prototype.auditionNote(channel: number, pitch: number, velocity?: number, durationMs?: number) -> boolean`

- [ ] **Step 1: Write the failing tests in `tests/test_midi_player_tempo.py`**

Append to `tests/test_midi_player_tempo.py`:

```python
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_midi_player_ticks_at_seconds_current_seconds_and_audition(tmp_path: Path):
    beats = [0.25, 0.70, 1.10, 1.50, 2.10, 2.70]
    pm = rhythm.build_pretty_midi(beats, bpm=135.0, max_time=3.0, resolution=480)
    ins = pretty_midi.Instrument(program=30)
    ins.notes.append(pretty_midi.Note(velocity=100, pitch=64, start=0.25, end=2.6))
    pm.instruments.append(ins)
    mid_path = tmp_path / "inv.mid"
    pm.write(str(mid_path))

    js_path = (Path(__file__).resolve().parent.parent / "app" / "static" / "midi_player.js").as_posix()
    mid_posix = mid_path.as_posix()
    script = f"""
      const fs = require('fs');
      global.window = {{}};
      let nowMs = 1000;
      global.performance = {{ now: () => nowMs }};
      require({json.dumps(js_path)});
      const MP = global.window.MidiPlayer;
      const bytes = fs.readFileSync({json.dumps(mid_posix)});
      const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);

      const mp = new MP();
      mp.division = 480;
      mp.total = 2880;
      mp.tempoMap = MP.parseTempoMap(ab, 480);

      // 1. Inverse roundtrip: ticksAtSeconds(seconds(t)) === t
      const checkTicks = [0, 240, 480, 720, 1200, 1920, 2400];
      const roundtripDiffs = checkTicks.map(t => Math.abs(mp.ticksAtSeconds(mp.seconds(t)) - t));

      // 2. Smooth 60-FPS currentSeconds interpolation while playing
      mp.position = 480;
      mp._anchorTick = 480;
      mp._anchorPerf = 1000;
      mp.playing = false;
      const stoppedSec = mp.currentSeconds();
      mp.playing = true;
      nowMs = 1120; // +120ms since last tick poll
      const playingSec = mp.currentSeconds();

      // 3. auditionNote returns false without synth, true when synth + font route exist
      const noSynthOk = mp.auditionNote(0, 64, 100, 50) === false;
      const events = [];
      mp.synth = {{
        midiProgramSelect: (ch, fid, bank, prog) => events.push(['prog', ch, fid, bank, prog]),
        midiNoteOn: (ch, p, v) => events.push(['on', ch, p, v]),
        midiNoteOff: (ch, p) => events.push(['off', ch, p]),
      }};
      mp.fonts.set('sf1', 7);
      mp.routes.set(2, {{ sf: 'sf1', bank: 0, program: 29 }});
      const withSynthOk = mp.auditionNote(2, 64, 100, 20) === true;
      setTimeout(() => {{
        console.log(JSON.stringify({{
          maxDiff: Math.max(...roundtripDiffs),
          deltaPlaying: playingSec - stoppedSec,
          noSynthOk,
          withSynthOk,
          events,
        }}));
      }}, 35);
    """
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    res = json.loads(proc.stdout.strip())
    assert res["maxDiff"] <= 1
    assert abs(res["deltaPlaying"] - 0.12) < 0.01
    assert res["noSynthOk"] is True
    assert res["withSynthOk"] is True
    assert ["on", 2, 64, 100] in res["events"]
    assert ["off", 2, 64] in res["events"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_midi_player_tempo.py -v`
Expected: FAIL with `TypeError: mp.ticksAtSeconds is not a function`

- [ ] **Step 3: Implement `ticksAtSeconds`, `currentSeconds`, and `auditionNote` in `app/static/midi_player.js`**

Update `app/static/midi_player.js`:
1. In `constructor()`, initialize:
   ```javascript
   this._anchorTick = 0;
   this._anchorPerf = 0;
   this._auditionTimers = new Map();
   ```
2. Add helper `_setTick(tick)`:
   ```javascript
   _setTick(tick) {
     this.position = tick;
     this._anchorTick = tick;
     this._anchorPerf = typeof performance !== "undefined" ? performance.now() : 0;
   }
   ```
   Use `this._setTick(0)` in `setSong`, `stop`, and clock end-of-song reset; `this._setTick(await this.synth.retrievePlayerCurrentTick())` in `pause`; `this._setTick(Math.max(0, Math.min(ticks, this.total || ticks)))` in `seek`; `this._setTick(resumeAt)` when starting `play()`; and `this._setTick(tick)` inside `_startClock()`.
3. Add `ticksAtSeconds(sec)`, `currentSeconds()`, and `auditionNote(channel, pitch, velocity = 96, durationMs = 420)`:
   ```javascript
   ticksAtSeconds(sec) {
     const t = Math.max(0, Number(sec) || 0);
     if (this.tempoMap && this.tempoMap.length) {
       let lo = 0, hi = this.tempoMap.length - 1;
       while (lo < hi) {
         const mid = (lo + hi + 1) >> 1;
         if (this.tempoMap[mid].sec <= t) lo = mid;
         else hi = mid - 1;
       }
       const s = this.tempoMap[lo];
       return Math.round(s.tick + ((t - s.sec) * 1e6 * this.division) / s.usPerBeat);
     }
     return Math.round((t * this.bpm * this.division) / 60);
   }

   currentSeconds() {
     const base = this.seconds(this.playing ? this._anchorTick : this.position);
     if (!this.playing || typeof performance === "undefined") return base;
     const dt = Math.max(0, (performance.now() - this._anchorPerf) / 1000);
     const cap = this.total ? this.seconds(this.total) : Infinity;
     return Math.min(cap, base + dt);
   }

   auditionNote(channel, pitch, velocity = 96, durationMs = 420) {
     if (!this.synth) return false;
     const ch = Math.max(0, Math.min(15, Number(channel) || 0));
     if (!this._applyRoute(ch, 0) && ch !== 9) return false;
     if (this.ctx && this.ctx.state === "suspended") this.ctx.resume();
     const key = `${ch}:${pitch}`;
     const prev = this._auditionTimers.get(key);
     if (prev) {
       clearTimeout(prev);
       this.synth.midiNoteOff(ch, pitch);
     }
     this.synth.midiNoteOn(ch, pitch, Math.max(1, Math.min(127, Math.round(velocity))));
     const timer = setTimeout(() => {
       this._auditionTimers.delete(key);
       if (this.synth) this.synth.midiNoteOff(ch, pitch);
     }, Math.max(60, durationMs));
     this._auditionTimers.set(key, timer);
     return true;
   }
   ```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_midi_player_tempo.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```powershell
git add app/static/midi_player.js tests/test_midi_player_tempo.py; git commit -m "feat(player): add ticksAtSeconds, 60fps currentSeconds, and note audition to MidiPlayer"
```

---

### Task 3: Standalone `PianoRoll` Engine (`app/static/piano_roll.js`)

**Files:**
- Create: `app/static/piano_roll.js`
- Modify: `tests/test_piano_roll.py`

**Interfaces:**
- Consumes: Canvas 2D context (in browser), `{ duration, tracks }` payload, `beats`, `parts`, `harmony`, `tuningPitches`
- Produces: `window.PianoRoll` with:
  - Static helpers:
    - `PianoRoll.TRACK_COLORS`
    - `PianoRoll.trackColor(index)`
    - `PianoRoll.prepareTracks(rawTracks)`
    - `PianoRoll.visibleNoteRange(notes, maxEndPrefix, tStart, tEnd)` -> `[startIdx, endIdx]`
    - `PianoRoll.pitchRange(tracks, selectedSet)` -> `{ minPitch, maxPitch }`
    - `PianoRoll.activeNotesAt(tracks, selectedSet, t)` -> `Array<ActiveNote>`
    - `PianoRoll.noteLabel(pitch, useFlats = false, isDrum = false)` -> `{ name, pt, short }`
    - `PianoRoll.guitarPositions(pitch, tuningPitches = [40, 45, 50, 55, 59, 64], maxFrets = 24)` -> `Array<{ string, fret }>`
  - Instance methods:
    - `new PianoRoll(canvas, callbacks)` (`callbacks`: `{ onSeek, onSelectPart, onAuditionNote, onHoverNote, onActiveChange }`)
    - `setData({ duration, tracks, beats, parts, harmony, tuningPitches })`
    - `setSelectedTracks(indicesIterable)`
    - `setHarmony(harmony)` / `setParts(parts)` / `setSelectedPart(partId)` / `setTuning(pitches, maxFrets)`
    - `setFollow(enabled)` / `setHighlightScale(enabled)`
    - `setTime(sec, isPlaying)`
    - `zoomBy(factor, anchorSec)` / `fitRange(t0, t1)` / `fitAll()`
    - `resize()` / `destroy()`

- [ ] **Step 1: Write the failing Node.js unit tests in `tests/test_piano_roll.py`**

Append to `tests/test_piano_roll.py`:

```python
import json
import shutil
import subprocess
import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_piano_roll_static_algorithms_in_node():
    js_path = (Path(__file__).resolve().parent.parent / "app" / "static" / "piano_roll.js").as_posix()
    script = f"""
      global.window = {{}};
      require({json.dumps(js_path)});
      const PR = global.window.PianoRoll;

      const tracks = PR.prepareTracks([
        {{
          index: 0, name: 'Guitar Base', is_drum: false, is_guitar: true, role: 'rhythm',
          notes: [
            [0.5, 6.0, 40, 100],   // long E2 drone across several seconds!
            [1.0, 1.5, 47, 90],
            [2.0, 2.5, 52, 95],
            [7.0, 7.5, 55, 85],
          ],
        }},
        {{
          index: 1, name: 'Lead Solo', is_drum: false, is_guitar: true, role: 'lead',
          notes: [
            [3.2, 3.8, 76, 110],
          ],
        }},
        {{
          index: 2, name: 'Drums', is_drum: true, is_guitar: false, role: null,
          notes: [
            [3.2, 3.3, 38, 100],
          ],
        }},
      ]);

      // 1. Long sustained note [0.5, 6.0] MUST still be included when viewport is [3.0, 4.0]!
      const range = PR.visibleNoteRange(tracks[0].notes, tracks[0].maxEndPrefix, 3.0, 4.0);

      // 2. Pitch range adapts to selected tracks (excluding unselected drums/lead)
      const pRangeBase = PR.pitchRange(tracks, new Set([0]));
      const pRangeBoth = PR.pitchRange(tracks, new Set([0, 1]));

      // 3. Active notes at t=3.3s with tracks 0 & 1 selected: sorted high pitch (76) -> low pitch (40)
      const active = PR.activeNotesAt(tracks, new Set([0, 1]), 3.3);

      // 4. Note labels (sharps, flats, and GM drums) + guitar fretboard positions
      const lblSharp = PR.noteLabel(66, false, false);
      const lblFlat = PR.noteLabel(70, true, false);
      const lblDrum = PR.noteLabel(38, false, true);
      const posE4 = PR.guitarPositions(64, [40, 45, 50, 55, 59, 64], 24);

      console.log(JSON.stringify({{
        range,
        pRangeBase,
        pRangeBoth,
        activePitches: active.map(a => a.pitch),
        lblSharp,
        lblFlat,
        lblDrum,
        posE4,
        distinctColors: new Set([0,1,2,3,4,5,6,7].map(i => PR.trackColor(i))).size,
      }}));
    """
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    out = json.loads(proc.stdout.strip())
    assert out["range"][0] == 0 and out["range"][1] == 3  # index 0 (0.5..6.0) included, index 3 (7.0..7.5) excluded
    assert out["pRangeBase"]["minPitch"] <= 40 and out["pRangeBase"]["maxPitch"] < 70
    assert out["pRangeBoth"]["maxPitch"] >= 76
    assert out["activePitches"] == [76, 40]
    assert out["lblSharp"]["name"] == "F#4" and "Fá#4" in out["lblSharp"]["pt"]
    assert out["lblFlat"]["name"] == "Bb4" and "Sib4" in out["lblFlat"]["pt"]
    assert "Snare" in out["lblDrum"]["short"] or "Caixa" in out["lblDrum"]["pt"]
    assert {"string": 1, "fret": 0} in out["posE4"]
    assert {"string": 2, "fret": 5} in out["posE4"]
    assert out["distinctColors"] == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_piano_roll.py -k "static_algorithms" -v`
Expected: FAIL with `Error: Cannot find module '.../app/static/piano_roll.js'`

- [ ] **Step 3: Implement `app/static/piano_roll.js`**

Create `app/static/piano_roll.js` implementing `window.PianoRoll`:
- `TRACK_COLORS`: `["#3b82f6", "#f59e0b", "#10b981", "#a855f7", "#f43f5e", "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#eab308"]`
- `SHARP_NAMES` / `FLAT_NAMES` / `PT_MAP` + `GM_DRUMS` (`35: ["Kick", "Bumbo"]`, `36: ["Kick", "Bumbo"]`, `38: ["Snare", "Caixa"]`, `40: ["Snare", "Caixa"]`, `42: ["HH", "Chimbal fechado"]`, `46: ["OHH", "Chimbal aberto"]`, `49: ["Crash", "Prato de ataque"]`, `51: ["Ride", "Condução"]`, etc.)
- Pure static methods (`trackColor`, `prepareTracks`, `visibleNoteRange`, `pitchRange`, `activeNotesAt`, `noteLabel`, `guitarPositions`) matching all contracts above.
- Full Canvas 2D interactive renderer (`KEY_W = 56`, `RULER_H = 28`, black/white key stripes, `C` octave lines, scale-tone row highlight when `highlightScale` is true and harmony has `key`, `job.beats` beat/4-beat bar lines, song `parts` header lane, rounded colored note bars with inner note text when `w >= 24 && rowH >= 11`, overlap vertical sub-offset when multiple tracks share a pitch, golden-white playhead highlight, hover hit-testing, click note/key to audition, click ruler/empty space to seek, drag-to-pan, `Ctrl+Wheel` zoom around cursor, `fitRange`, `fitAll`, `setFollow`, `ResizeObserver`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_piano_roll.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```powershell
git add app/static/piano_roll.js tests/test_piano_roll.py; git commit -m "feat(ui): add virtualized Canvas 2D PianoRoll engine and note-reading helpers"
```

---

### Task 4: Studio UI Integration — HTML, CSS, Filter Chips, Live Readout & Dual Sync (`index.html`, `styles.css`, `app.js`)

**Files:**
- Modify: `app/static/index.html:480-500,914-916`
- Modify: `app/static/styles.css`
- Modify: `app/static/app.js`
- Modify: `tests/test_piano_roll.py`

**Interfaces:**
- Consumes: `GET /api/projects/{id}/multitrack/notes`, `window.PianoRoll`, `window.MidiPlayer` (`currentSeconds`, `ticksAtSeconds`, `auditionNote`), `projWave`, `H.data`, `playPitches`
- Produces:
  - Complete interactive Piano Roll card (`#pianoRollBox`) inside `#mixerBox` on the **MIDI** tab, color-matched with `#multitrackTracks`, synced at 60 FPS with both `projWave` and `sound.player`.

- [ ] **Step 1: Write the failing HTML/asset integration test in `tests/test_piano_roll.py`**

Append to `tests/test_piano_roll.py`:

```python
def test_piano_roll_html_elements_and_static_script_served(tmp_path: Path):
    import re

    client = TestClient(create_app(workspace_root=tmp_path))
    html = client.get("/").text
    assert "/static/piano_roll.js" in html
    for elem_id in ("pianoRollBox", "prTrackChips", "prNowBar", "pianoRollCanvas", "prTooltip"):
        assert f'id="{elem_id}"' in html, f"missing #{elem_id} in index.html"

    ids = re.findall(r'\bid="([^"]+)"', html)
    duplicates = {x for x in ids if ids.count(x) > 1}
    assert not duplicates, f"Duplicate HTML IDs: {duplicates}"

    js_res = client.get("/static/piano_roll.js")
    assert js_res.status_code == 200
    assert "class PianoRoll" in js_res.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_piano_roll.py -k "html_elements" -v`
Expected: FAIL with `AssertionError: assert '/static/piano_roll.js' in html`

- [ ] **Step 3: Wire HTML, CSS, and `app.js` integration**

1. **`app/static/index.html`:**
   - Inside `#mixerBox` (right after `#midiPlayer`, before closing `#mixerBox`), add `#pianoRollBox`:
     ```html
          <div class="piano-roll-box hidden" id="pianoRollBox">
            <div class="pr-head">
              <div>
                <strong>Piano Roll — aprenda lendo as notas</strong>
                <span class="muted small">Clique nos instrumentos para combinar cores, ou clique numa nota/tecla para ouvi-la isolada.</span>
              </div>
              <div class="pr-quick-picks" role="group" aria-label="Seleção rápida de instrumentos">
                <button type="button" class="btn-outline btn-small" data-pr-pick="melodic">Melódicas</button>
                <button type="button" class="btn-outline btn-small" data-pr-pick="guitar">Guitarras / Solos</button>
                <button type="button" class="btn-outline btn-small" data-pr-pick="none">Limpar</button>
              </div>
            </div>
            <div class="pr-chips" id="prTrackChips" role="group" aria-label="Instrumentos visíveis no Piano Roll"></div>
            <div class="pr-toolbar">
              <div class="pr-zoom-btns">
                <button type="button" class="btn-outline btn-small" id="prZoomOutBtn" title="Diminuir zoom horizontal">&minus;</button>
                <button type="button" class="btn-outline btn-small" id="prZoomInBtn" title="Aumentar zoom horizontal">+</button>
                <button type="button" class="btn-outline btn-small" id="prFitPartBtn" title="Enquadrar a parte selecionada no player">Trecho</button>
                <button type="button" class="btn-outline btn-small" id="prFitAllBtn" title="Ver a música inteira">Tudo</button>
              </div>
              <div class="pr-toggles">
                <label class="checkbox-row small"><input type="checkbox" id="prFollowCheck" checked /><span>Seguir agulha</span></label>
                <label class="checkbox-row small"><input type="checkbox" id="prScaleCheck" checked /><span>Destacar notas do tom</span></label>
              </div>
            </div>
            <div class="pr-now-bar" id="prNowBar" aria-live="polite">
              <span class="muted small">Dê play (no áudio acima ou nos instrumentos virtuais) ou clique em qualquer nota para ler os detalhes.</span>
            </div>
            <div class="pr-canvas-wrap" id="prCanvasWrap">
              <canvas id="pianoRollCanvas" aria-label="Piano Roll multi-instrumento da música"></canvas>
              <div class="pr-tooltip hidden" id="prTooltip" role="tooltip"></div>
            </div>
            <div id="prStatus" class="status-banner hidden"></div>
          </div>
     ```
   - Before `<script src="/static/app.js"></script>`, add `<script src="/static/piano_roll.js"></script>`.

2. **`app/static/styles.css`:**
   - Add styles for `.track-color-dot` (10px circle inside `.mixer-row .track-name`), `.piano-roll-box`, `.pr-head`, `.pr-quick-picks`, `.pr-chips`, `.pr-chip` (pill button with `--tc` color swatch, active border/glow, dimmed state when unchecked), `.pr-toolbar`, `.pr-now-bar`, `.pr-now-pill`, `.pr-canvas-wrap` (`position: relative; height: 390px; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); background: #0b0e14;`), `#pianoRollCanvas` (`width: 100%; height: 100%; display: block;`), and `.pr-tooltip`.

3. **`app/static/app.js`:**
   - Add `<span class="track-color-dot">` in `renderMixer()` using `PianoRoll.trackColor(t.index)` so mixer rows and Piano Roll chips share identical instrument colors.
   - Initialize and manage `PR` controller state (`instance`, `projectId`, `sig`, `rafId`, `selected`, `pinnedNote`):
     - `prLoadNotes()` called from `renderMixer()` (invalidated on `runMultitrack()` and `stopAllAudio()`), fetching `/api/projects/${projectId}/multitrack/notes`, initializing `PianoRoll` on `#pianoRollCanvas`, defaulting `PR.selected` to all melodic tracks with notes (`!t.is_drum && t.note_count > 0`, or all non-empty tracks if only drums exist), rendering `#prTrackChips` (`Click` toggles track, `Alt+Click` or `dblclick` solos just that track), and wiring `[data-pr-pick]`, zoom/fit buttons (`#prFitPartBtn` fits `sel.cur` when a part/draft is selected), `#prFollowCheck`, and `#prScaleCheck`.
     - Sync harmony & parts whenever `renderHarmony()`, `drawPartRegions()`, or `setSelection()` runs, and call `PR.instance.resize()` when `openTab("analysis")` opens the `MIDI` tab.
     - Dual playhead sync:
       - Hook `projWave.onTime` and `projWave.ws.on("seeking")` -> when `!(sound.player && sound.player.playing)`, call `prUpdateTime(t, projWave.ws.isPlaying())`.
       - Hook `sound.player.onState` -> start/stop a `requestAnimationFrame` loop while `sound.player.playing` is true that calls `prUpdateTime(sound.player.currentSeconds(), true)`.
       - `onSeek(t)`: if `sound.player && sound.player.playing`, call `sound.player.seek(sound.player.ticksAtSeconds(t))`; otherwise call `projWave && projWave.ws.setTime(t)` (and if `sound.player && sound.player.total`, update `sound.player.seek(sound.player.ticksAtSeconds(t))` while paused too).
     - Note & keyboard audition (`prAudition({ trackIndex, isDrum, pitch, velocity, duration })`):
       - Try `sound.player` channel route (`sound.player.auditionNote(ch, pitch, velocity, durMs)`).
       - Fallback: for drums, synthesize a crisp WebAudio percussive burst on `scaleAudio`; for melodic notes, play a velocity/duration-aware WebAudio note on `scaleAudio` (`playSinglePitch(pitch, velocity, durS)`).
     - Live `#prNowBar` + hover `#prTooltip`: display colored pills with `E4 (Mi4 · 2ª c.5)` for active notes under the playhead (or the clicked/pinned note when paused), using the project's active guitar tuning (`TUNINGS` loaded from `/api/tunings`).

- [ ] **Step 4: Run full test suite to verify everything passes**

Run: `uv run pytest -q`
Expected: ALL tests PASS with zero regressions (134+ passed).

- [ ] **Step 5: Commit**

```powershell
git add app/static/index.html app/static/styles.css app/static/app.js tests/test_piano_roll.py; git commit -m "feat(piano-roll): integrate multi-instrument MIDI Piano Roll into web studio"
```
