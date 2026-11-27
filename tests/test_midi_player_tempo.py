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
      }}, 90);
    """
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    res = json.loads(proc.stdout.strip())
    assert res["maxDiff"] <= 1
    assert abs(res["deltaPlaying"] - 0.12) < 0.01
    assert res["noSynthOk"] is True
    assert res["withSynthOk"] is True
    assert ["on", 2, 64, 100] in res["events"]
    assert ["off", 2, 64] in res["events"]

