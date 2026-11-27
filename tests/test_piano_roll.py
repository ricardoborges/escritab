import io
import json
from pathlib import Path
import shutil
import subprocess
import numpy as np
import pretty_midi
import pytest
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


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_piano_roll_instance_and_interactions_in_node():
    js_path = (Path(__file__).resolve().parent.parent / "app" / "static" / "piano_roll.js").as_posix()
    script = f"""
      global.window = {{ devicePixelRatio: 2 }};
      require({json.dumps(js_path)});
      const PR = global.window.PianoRoll;

      // 7-string tuning + edge cases on static helpers
      const pos7 = PR.guitarPositions(35, [35, 40, 45, 50, 55, 59, 64], 24);
      const emptyRange = PR.pitchRange([], new Set());
      const narrowRange = PR.pitchRange(PR.prepareTracks([{{ index: 0, name: 'Single', notes: [[0, 1, 60, 80]] }}]), new Set([0]));

      // Mock Canvas 2D + event listener registry
      const listeners = {{}};
      const ctxCalls = [];
      const fakeCtx = new Proxy({{
        save() {{}}, restore() {{}}, scale() {{}}, translate() {{}},
        beginPath() {{}}, closePath() {{}}, moveTo() {{}}, lineTo() {{}},
        rect() {{}}, roundRect() {{}}, clip() {{}}, fill() {{}}, stroke() {{}},
        fillRect(...args) {{ ctxCalls.push(['fillRect', args]); }},
        strokeRect() {{}},
        fillText(txt) {{ ctxCalls.push(['fillText', txt]); }},
        measureText(txt) {{ return {{ width: String(txt).length * 6 }}; }},
      }}, {{
        set(target, prop, value) {{ target[prop] = value; return true; }},
        get(target, prop) {{ return target[prop] ?? (() => {{}}); }},
      }});
      const fakeCanvas = {{
        width: 800, height: 400, clientWidth: 800, clientHeight: 400,
        style: {{}},
        getBoundingClientRect() {{ return {{ left: 0, top: 0, width: 800, height: 400 }}; }},
        getContext() {{ return fakeCtx; }},
        addEventListener(type, fn) {{ (listeners[type] ||= []).push(fn); }},
        removeEventListener(type, fn) {{
          listeners[type] = (listeners[type] || []).filter(f => f !== fn);
        }},
      }};

      const events = {{ seeks: [], parts: [], auditions: [], hovers: [], actives: [] }};
      const pr = new PR(fakeCanvas, {{
        onSeek: (sec) => events.seeks.push(sec),
        onSelectPart: (part) => events.parts.push(part.part_id),
        onAuditionNote: (n) => events.auditions.push(n),
        onHoverNote: (info) => events.hovers.push(info ? info.pitch : null),
        onActiveChange: (active, pinned) => events.actives.push({{ count: active.length, pinned: pinned ? pinned.pitch : null }}),
      }});

      pr.setData({{
        duration: 10.0,
        tracks: [
          {{ index: 0, name: 'Base', is_drum: false, is_guitar: true, role: 'rhythm', notes: [[1.0, 2.0, 57, 90]] }},
          {{ index: 1, name: 'Lead', is_drum: false, is_guitar: true, role: 'lead', notes: [[1.0, 2.0, 64, 110]] }},
        ],
        beats: [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
        parts: [{{ part_id: 'p1', name: 'Intro', start: 0.0, end: 4.0 }}],
        harmony: {{ key: {{ tonic: 9, tonic_name: 'A', scale: ['A', 'B', 'C', 'D', 'E', 'F', 'G'] }} }},
        tuningPitches: [40, 45, 50, 55, 59, 64],
      }});
      pr.setSelectedTracks([0, 1]);
      pr.setSelectedPart('p1');
      pr.setHighlightScale(true);
      const useFlatsA = pr.useFlats;
      const scaleHasA = pr.scalePcs.has(9);
      pr.setFollow(true);
      pr.setTime(1.2, true);
      pr.fitRange(0.0, 5.0);
      pr.zoomBy(1.5, 1.2);
      pr.fitAll();
      pr.resize();

      // Simulate clicking on a left piano key (x=20 inside KEY_W=56, y=200 inside grid)
      const fire = (type, ev) => (listeners[type] || []).forEach(fn => fn({{ button: 0, preventDefault() {{}}, ...ev }}));
      fire('mousedown', {{ clientX: 20, clientY: 200 }});
      fire('mouseup', {{ clientX: 20, clientY: 200 }});

      // Click directly on Lead note at [1.0, 2.0, 64] (x ~ 160, y ~ 140)
      fire('mousemove', {{ clientX: 160, clientY: 140 }});
      fire('mousedown', {{ clientX: 160, clientY: 140 }});
      fire('mouseup', {{ clientX: 160, clientY: 140 }});
      const pinnedAfterNoteClick = pr.pinnedNote ? pr.pinnedNote.pitch : null;

      // Simulate clicking on the Intro part in the ruler (y=8 < RULER_H=28, x=120 > KEY_W)
      fire('mousedown', {{ clientX: 120, clientY: 8 }});
      fire('mouseup', {{ clientX: 120, clientY: 8 }});

      // Simulate clicking empty grid space to seek (x=400, y=350)
      fire('mousedown', {{ clientX: 400, clientY: 350 }});
      fire('mouseup', {{ clientX: 400, clientY: 350 }});

      // Simulate Ctrl+Wheel zoom in & horizontal drag pan (no seek)
      const seeksBeforeDrag = events.seeks.length;
      fire('wheel', {{ clientX: 250, clientY: 200, ctrlKey: true, deltaY: -120 }});
      const pxAfterZoom = pr.pxPerSec;
      fire('mousedown', {{ clientX: 300, clientY: 300 }});
      fire('mousemove', {{ clientX: 200, clientY: 300 }});
      fire('mouseup', {{ clientX: 200, clientY: 300 }});
      const seeksAfterDrag = events.seeks.length;
      const tStartAfterDrag = pr.tStart;

      // Test flat-key harmony parsing (F major -> Bb -> useFlats = true)
      pr.setHarmony({{ key: {{ tonic: 5, tonic_name: 'F', scale: ['F', 'G', 'A', 'Bb', 'C', 'D', 'E'] }} }});
      const useFlatsF = pr.useFlats;

      // Simulate hover leave
      fire('mouseleave', {{}});

      pr.destroy();

      console.log(JSON.stringify({{
        pos7,
        emptyRange,
        narrowSpan: narrowRange.maxPitch - narrowRange.minPitch,
        useFlatsDefault: useFlatsA,
        useFlatsF,
        scaleHasA,
        drewTextCount: ctxCalls.filter(c => c[0] === 'fillText').length,
        keyAuditions: events.auditions.length,
        pinnedAfterNoteClick,
        hoveredNote: events.hovers.includes(64),
        partSelects: events.parts,
        seeks: events.seeks.length,
        dragDidNotSeek: seeksBeforeDrag === seeksAfterDrag,
        pxZoomedIn: pxAfterZoom > 75,
        tStartAfterDrag,
        activesCount: events.actives.length,
        listenersAfterDestroy: Object.values(listeners).reduce((s, a) => s + a.length, 0),
      }}));
    """
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    out = json.loads(proc.stdout.strip())
    assert out["pos7"] == [{"string": 7, "fret": 0}]
    assert out["emptyRange"] == {"minPitch": 40, "maxPitch": 76}
    assert out["narrowSpan"] >= 14
    assert out["useFlatsDefault"] is False
    assert out["useFlatsF"] is True
    assert out["scaleHasA"] is True
    assert out["drewTextCount"] > 0
    assert out["keyAuditions"] >= 2
    assert out["pinnedAfterNoteClick"] == 64
    assert out["hoveredNote"] is True
    assert out["partSelects"] == ["p1"]
    assert out["seeks"] >= 2
    assert out["dragDidNotSeek"] is True
    assert out["pxZoomedIn"] is True
    assert out["tStartAfterDrag"] > 0
    assert out["activesCount"] >= 2
    assert out["listenersAfterDestroy"] == 0


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
