from __future__ import annotations
from pathlib import Path
import xml.etree.ElementTree as ET
import guitarpro
from app.models import TUNINGS, TabParams, TabSlice

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


def _duration_to_xml_type(quarters: float) -> str:
    if quarters >= 3.0:
        return "whole"
    if quarters >= 1.5:
        return "half"
    if quarters >= 0.75:
        return "quarter"
    if quarters >= 0.375:
        return "eighth"
    return "16th"


def export_musicxml(
    slices: list[TabSlice],
    params: TabParams,
    output_path: Path,
    title: str = "EscriTab Transcription",
) -> Path:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    num_strings = len(preset.pitches)
    divisions = 4  # 4 divisions per quarter note (16th-note resolution)
    quarter_sec = 60.0 / params.bpm
    measure_sec = 4.0 * quarter_sec  # 4/4 measure

    score = ET.Element("score-partwise", version="3.1")
    work = ET.SubElement(score, "work")
    ET.SubElement(work, "work-title").text = title

    part_list = ET.SubElement(score, "part-list")
    score_part = ET.SubElement(part_list, "score-part", id="P1")
    ET.SubElement(score_part, "part-name").text = f"Guitar ({preset.name})"
    # Without a midi-instrument, players (AlphaTab) fall back to program 0 = piano.
    score_inst = ET.SubElement(score_part, "score-instrument", id="P1-I1")
    ET.SubElement(score_inst, "instrument-name").text = "Distortion Guitar"
    midi_inst = ET.SubElement(score_part, "midi-instrument", id="P1-I1")
    ET.SubElement(midi_inst, "midi-channel").text = "1"
    ET.SubElement(midi_inst, "midi-program").text = str(GUITAR_MIDI_PROGRAM + 1)  # MusicXML is 1-based

    part = ET.SubElement(score, "part", id="P1")

    max_time = max((s.end for s in slices), default=measure_sec)
    total_measures = max(1, int(max_time // measure_sec) + 1)

    for m_idx in range(total_measures):
        m_start = m_idx * measure_sec
        m_end = (m_idx + 1) * measure_sec
        measure = ET.SubElement(part, "measure", number=str(m_idx + 1))

        if m_idx == 0:
            attrs = ET.SubElement(measure, "attributes")
            ET.SubElement(attrs, "divisions").text = str(divisions)
            key = ET.SubElement(attrs, "key")
            ET.SubElement(key, "fifths").text = "0"
            time_el = ET.SubElement(attrs, "time")
            ET.SubElement(time_el, "beats").text = "4"
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

        m_slices = [s for s in slices if m_start <= s.start < m_end]
        if not m_slices:
            note_el = ET.SubElement(measure, "note")
            ET.SubElement(note_el, "rest")
            ET.SubElement(note_el, "duration").text = str(divisions * 4)
            ET.SubElement(note_el, "type").text = "whole"
            continue

        for sl in m_slices:
            dur_sec = max(quarter_sec * 0.25, sl.end - sl.start)
            quarters = dur_sec / quarter_sec
            dur_divs = max(1, min(divisions * 4, int(round(quarters * divisions))))
            xml_type = _duration_to_xml_type(quarters)

            for n_idx, fn in enumerate(sl.notes):
                note_el = ET.SubElement(measure, "note")
                if n_idx > 0:
                    ET.SubElement(note_el, "chord")
                step, alter, octave = _midi_to_step_alter_octave(fn.pitch)
                pitch_el = ET.SubElement(note_el, "pitch")
                ET.SubElement(pitch_el, "step").text = step
                if alter != 0:
                    ET.SubElement(pitch_el, "alter").text = str(alter)
                ET.SubElement(pitch_el, "octave").text = str(octave)

                ET.SubElement(note_el, "duration").text = str(dur_divs)
                ET.SubElement(note_el, "voice").text = "1"
                ET.SubElement(note_el, "type").text = xml_type

                notations = ET.SubElement(note_el, "notations")
                technical = ET.SubElement(notations, "technical")
                # MusicXML convention: string 1 is highest pitch string, N is lowest pitch string
                xml_string_num = num_strings - fn.string_idx
                ET.SubElement(technical, "string").text = str(xml_string_num)
                ET.SubElement(technical, "fret").text = str(fn.fret)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(score, space="  ")
    xml_bytes = ET.tostring(score, encoding="utf-8", xml_declaration=True)
    output_path.write_bytes(xml_bytes)
    return output_path


def export_gp5(
    slices: list[TabSlice],
    params: TabParams,
    output_path: Path,
    title: str = "EscriTab Transcription",
) -> Path:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    num_strings = len(preset.pitches)
    song = guitarpro.models.Song()
    song.title = title
    song.tempo = int(round(params.bpm))

    track = song.tracks[0]
    track.name = f"Guitar ({preset.id})"
    track.fretCount = params.max_frets
    # pyguitarpro strings: index 1 is highest string, N is lowest string
    track.strings = [
        guitarpro.models.GuitarString(number=i + 1, value=pitch)
        for i, pitch in enumerate(reversed(preset.pitches))
    ]

    quarter_sec = 60.0 / params.bpm
    measure_sec = 4.0 * quarter_sec
    max_time = max((s.end for s in slices), default=measure_sec)
    total_measures = max(1, int(max_time // measure_sec) + 1)

    while len(song.measureHeaders) < total_measures:
        header = guitarpro.models.MeasureHeader(number=len(song.measureHeaders) + 1)
        song.measureHeaders.append(header)
        measure = guitarpro.models.Measure(track=track, header=header)
        track.measures.append(measure)

    for m_idx in range(total_measures):
        m_start = m_idx * measure_sec
        m_end = (m_idx + 1) * measure_sec
        measure = track.measures[m_idx]
        voice = measure.voices[0]
        voice.beats.clear()

        m_slices = [s for s in slices if m_start <= s.start < m_end]
        if not m_slices:
            beat = guitarpro.models.Beat(
                voice=voice,
                status=guitarpro.models.BeatStatus.rest,
                duration=guitarpro.models.Duration(value=1),
            )
            voice.beats.append(beat)
            continue

        for sl in m_slices:
            dur_sec = max(quarter_sec * 0.25, sl.end - sl.start)
            quarters = dur_sec / quarter_sec
            if quarters >= 3.0:
                dur_val = 1
            elif quarters >= 1.5:
                dur_val = 2
            elif quarters >= 0.75:
                dur_val = 4
            elif quarters >= 0.375:
                dur_val = 8
            else:
                dur_val = 16

            beat = guitarpro.models.Beat(
                voice=voice,
                status=guitarpro.models.BeatStatus.normal,
                duration=guitarpro.models.Duration(value=dur_val),
            )
            for fn in sl.notes:
                gp_string_num = num_strings - fn.string_idx
                note = guitarpro.models.Note(
                    beat=beat,
                    value=fn.fret,
                    velocity=fn.velocity,
                    string=gp_string_num,
                    type=guitarpro.models.NoteType.normal,
                )
                beat.notes.append(note)
            voice.beats.append(beat)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    guitarpro.write(song, str(output_path), version=(5, 1, 0))
    return output_path


def export_ascii_tab(
    slices: list[TabSlice],
    params: TabParams,
    output_path: Path,
    title: str = "EscriTab Transcription",
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

    quarter_sec = 60.0 / params.bpm
    measure_sec = 4.0 * quarter_sec
    columns_per_system = 16

    cols: list[tuple[int, dict[int, int]]] = []
    for sl in slices:
        m_num = int(sl.start // measure_sec) + 1
        col_map = {fn.string_idx: fn.fret for fn in sl.notes}
        cols.append((m_num, col_map))

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
            for r in range(num_strings):
                # row r=0 is highest string (string_idx = num_strings - 1)
                s_idx = (num_strings - 1) - r
                if s_idx in col_map:
                    fret_str = str(col_map[s_idx])
                    cell = f"-{fret_str}-" if len(fret_str) == 1 else f"-{fret_str}"
                else:
                    cell = "---"
                rows[r] += cell + "-"
        for r in range(num_strings):
            rows[r] += "|"
        systems.append("\n".join(rows))

    full_text = "\n".join(header_lines) + "\n\n".join(systems) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(full_text, encoding="utf-8")
    return output_path
