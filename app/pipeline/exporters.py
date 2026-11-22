from __future__ import annotations
from dataclasses import dataclass
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


GRID = 8                    # divisions per quarter note: 32nd-note resolution
MEASURE_UNITS = 4 * GRID    # 4/4
FILL_GAP_UNITS = 2          # a gap up to a 16th before the next note is read as legato, not a rest

# (units, Guitar Pro value, MusicXML type, dotted), longest first
_DURATIONS: list[tuple[int, int, str, bool]] = [
    (32, 1, "whole", False),
    (24, 2, "half", True),
    (16, 2, "half", False),
    (12, 4, "quarter", True),
    (8, 4, "quarter", False),
    (6, 8, "eighth", True),
    (4, 8, "eighth", False),
    (3, 16, "16th", True),
    (2, 16, "16th", False),
    (1, 32, "32nd", False),
]


@dataclass
class _Event:
    """One written note/chord or rest inside a measure."""
    start: int                      # units from the start of the measure
    dur: tuple[int, int, str, bool]
    slice: TabSlice | None          # None = rest
    tie_from_prev: bool = False     # continuation of a note split by a barline / odd length
    tie_to_next: bool = False


def _split_units(n: int) -> list[tuple[int, int, str, bool]]:
    out = []
    while n > 0:
        d = next(d for d in _DURATIONS if d[0] <= n)
        out.append(d)
        n -= d[0]
    return out


def _merge_slices(a: TabSlice, b: TabSlice) -> TabSlice:
    """Two slices quantized onto the same grid point -> one chord (one note per string)."""
    used = {n.string_idx for n in a.notes}
    extra = [n for n in b.notes if n.string_idx not in used]
    notes = sorted(a.notes + extra, key=lambda n: n.string_idx)
    return TabSlice(start=a.start, end=max(a.end, b.end), notes=notes)


def _layout(slices: list[TabSlice], bpm: float) -> list[list[_Event]]:
    """Slices (seconds) -> measures of events on a 32nd grid: onsets quantized, each note lasting
    until the next onset (short gaps filled, longer ones written as rests), notes crossing a
    barline split and tied, every measure summing to 4/4."""
    unit = (60.0 / bpm) / GRID
    items: list[list] = []  # [onset, end, slice]
    for sl in sorted(slices, key=lambda x: x.start):
        on = max(0, int(round(sl.start / unit)))
        # onsets on the 32nd grid; lengths on the 16th grid (a measured 0.4-beat staccato note
        # reads as an eighth + rest, not a dotted 16th + 32nd rest)
        raw = sl.end / unit - on
        end = on + (1 if raw < 1.5 else 2 * max(1, int(round(raw / 2))))
        if items and on <= items[-1][0]:
            items[-1][2] = _merge_slices(items[-1][2], sl)
            items[-1][1] = max(items[-1][1], end)
            continue
        items.append([on, end, sl])
    for i in range(len(items) - 1):
        nxt = items[i + 1][0]
        items[i][1] = min(items[i][1], nxt)
        if nxt - items[i][1] <= FILL_GAP_UNITS:
            items[i][1] = nxt

    last = max((it[1] for it in items), default=0)
    n_measures = max(1, -(-last // MEASURE_UNITS))
    total = n_measures * MEASURE_UNITS

    segments: list[tuple[int, int, TabSlice | None]] = []
    cur = 0
    for on, end, sl in items:
        if on > cur:
            segments.append((cur, on, None))
        segments.append((on, end, sl))
        cur = end
    if cur < total:
        segments.append((cur, total, None))

    measures: list[list[_Event]] = [[] for _ in range(n_measures)]
    for a, b, sl in segments:
        pieces: list[tuple[int, int, tuple[int, int, str, bool]]] = []
        t = a
        while t < b:
            m = t // MEASURE_UNITS
            seg_end = min(b, (m + 1) * MEASURE_UNITS)
            for d in _split_units(seg_end - t):
                pieces.append((m, t - m * MEASURE_UNITS, d))
                t += d[0]
        for k, (m, pos, d) in enumerate(pieces):
            measures[m].append(
                _Event(
                    start=pos,
                    dur=d,
                    slice=sl,
                    tie_from_prev=sl is not None and k > 0,
                    tie_to_next=sl is not None and k < len(pieces) - 1,
                )
            )
    return measures


def export_musicxml(
    slices: list[TabSlice],
    params: TabParams,
    output_path: Path,
    title: str = "EscriTab Transcription",
) -> Path:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    num_strings = len(preset.pitches)

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

    for m_idx, events in enumerate(_layout(slices, params.bpm)):
        measure = ET.SubElement(part, "measure", number=str(m_idx + 1))

        if m_idx == 0:
            attrs = ET.SubElement(measure, "attributes")
            ET.SubElement(attrs, "divisions").text = str(GRID)
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

        for ev in events:
            units, _, xml_type, dotted = ev.dur
            if ev.slice is None:
                note_el = ET.SubElement(measure, "note")
                rest = ET.SubElement(note_el, "rest")
                if units == MEASURE_UNITS:
                    rest.set("measure", "yes")
                ET.SubElement(note_el, "duration").text = str(units)
                ET.SubElement(note_el, "voice").text = "1"
                ET.SubElement(note_el, "type").text = xml_type
                if dotted:
                    ET.SubElement(note_el, "dot")
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

                notations = ET.SubElement(note_el, "notations")
                if ev.tie_from_prev:
                    ET.SubElement(notations, "tied", type="stop")
                if ev.tie_to_next:
                    ET.SubElement(notations, "tied", type="start")
                technical = ET.SubElement(notations, "technical")
                # MusicXML convention: string 1 is highest pitch string, N is lowest pitch string
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

    measures = _layout(slices, params.bpm)
    while len(song.measureHeaders) < len(measures):
        header = guitarpro.models.MeasureHeader(number=len(song.measureHeaders) + 1)
        song.measureHeaders.append(header)
        track.measures.append(guitarpro.models.Measure(track=track, header=header))

    for m_idx, events in enumerate(measures):
        voice = track.measures[m_idx].voices[0]
        voice.beats.clear()
        for ev in events:
            _, gp_value, _, dotted = ev.dur
            duration = guitarpro.models.Duration(value=gp_value, isDotted=dotted)
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

    # one column per attack, in measure order (tied continuations are not re-written)
    cols: list[tuple[int, dict[int, str]]] = []
    for m_idx, events in enumerate(_layout(slices, params.bpm)):
        for ev in events:
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
                # row r=0 is highest string (string_idx = num_strings - 1)
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
