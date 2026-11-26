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
