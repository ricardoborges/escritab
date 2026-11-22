from pathlib import Path
import xml.etree.ElementTree as ET
import guitarpro
from app.models import FrettedNote, TabParams, TabSlice
from app.pipeline.exporters import export_ascii_tab, export_gp5, export_musicxml


def _sample_slices() -> list[TabSlice]:
    return [
        TabSlice(
            start=0.0,
            end=0.5,
            notes=[
                FrettedNote(string_idx=0, fret=2, pitch=40, start=0.0, end=0.5, velocity=100),
                FrettedNote(string_idx=1, fret=2, pitch=47, start=0.0, end=0.5, velocity=95),
            ],
        ),
        TabSlice(
            start=0.5,
            end=1.0,
            notes=[
                FrettedNote(string_idx=2, fret=4, pitch=54, start=0.5, end=1.0, velocity=90),
            ],
        ),
    ]


def test_export_musicxml_valid_structure(tmp_path: Path):
    slices = _sample_slices()
    params = TabParams(tuning="drop_d", bpm=120.0)
    xml_path = tmp_path / "tab.musicxml"
    export_musicxml(slices, params, xml_path, title="Drop D Riff")

    assert xml_path.exists()
    tree = ET.parse(xml_path)
    root = tree.getroot()
    assert root.tag == "score-partwise"

    staff_lines = root.find(".//staff-details/staff-lines")
    assert staff_lines is not None and staff_lines.text == "6"

    frets = [el.text for el in root.findall(".//technical/fret")]
    strings = [el.text for el in root.findall(".//technical/string")]
    assert frets == ["2", "2", "4"]
    # MusicXML string numbering is 1 = highest string, 6 = lowest string
    assert strings == ["6", "5", "4"]


def test_export_gp5_parses_with_pyguitarpro(tmp_path: Path):
    slices = _sample_slices()
    params = TabParams(tuning="drop_d", bpm=140.0)
    gp5_path = tmp_path / "tab.gp5"
    export_gp5(slices, params, gp5_path, title="Drop D Riff")

    assert gp5_path.exists()
    song = guitarpro.parse(str(gp5_path))
    assert song.tempo == 140
    track = song.tracks[0]
    assert len(track.strings) == 6
    # pyguitarpro strings are ordered 1 (highest) to N (lowest)
    assert track.strings[-1].value == 38  # D2 on lowest string


def test_export_ascii_tab_renders_strings_and_frets(tmp_path: Path):
    slices = _sample_slices()
    params = TabParams(tuning="drop_d", bpm=120.0)
    txt_path = tmp_path / "tab.txt"
    export_ascii_tab(slices, params, txt_path, title="Drop D Riff")

    content = txt_path.read_text(encoding="utf-8")
    assert "Drop D" in content
    assert "D2|" in content
    assert "E4|" in content
    assert "-2-" in content
    assert "-4-" in content


def _riff_with_rests_and_bend() -> list[TabSlice]:
    # 120 BPM: a quarter = 0.5 s. Eighth, eighth rest, dotted quarter crossing nothing, then a
    # bent half note that crosses the barline (starts on beat 4 of measure 1).
    def sl(start, end, string_idx, fret, pitch, bend=0.0):
        n = FrettedNote(string_idx=string_idx, fret=fret, pitch=pitch, start=start, end=end, bend=bend)
        return TabSlice(start=start, end=end, notes=[n])

    return [
        sl(0.00, 0.20, 3, 7, 62),
        sl(0.50, 1.20, 3, 9, 64),
        sl(1.50, 2.50, 2, 7, 57, bend=2.0),
    ]


def test_layout_writes_rests_and_full_measures():
    from app.pipeline.exporters import MEASURE_UNITS, _layout

    measures = _layout(_riff_with_rests_and_bend(), bpm=120.0)
    assert len(measures) == 2
    for events in measures:
        assert sum(ev.dur[0] for ev in events) == MEASURE_UNITS
    first = measures[0]
    assert first[0].slice is not None and first[0].dur[2] == "eighth"
    assert first[1].slice is None  # the gap after the short note is a rest, not a longer note
    tied = [ev for ev in measures[1] if ev.slice is not None]
    assert tied and tied[0].tie_from_prev  # the half note continues over the barline


def test_bends_in_every_format(tmp_path: Path):
    slices = _riff_with_rests_and_bend()
    params = TabParams(tuning="standard_e", bpm=120.0)

    txt = export_ascii_tab(slices, params, tmp_path / "t.txt").read_text(encoding="utf-8")
    assert "7b9" in txt

    root = ET.parse(export_musicxml(slices, params, tmp_path / "t.musicxml")).getroot()
    assert [b.text for b in root.findall(".//technical/bend/bend-alter")] == ["2"]
    assert root.findall(".//note/rest")
    assert root.findall(".//notations/tied[@type='stop']")

    song = guitarpro.parse(str(export_gp5(slices, params, tmp_path / "t.gp5")))
    bends = [
        n.effect.bend
        for m in song.tracks[0].measures
        for b in m.voices[0].beats
        for n in b.notes
        if n.effect.bend
    ]
    assert len(bends) == 1 and max(p.value for p in bends[0].points) == 4
