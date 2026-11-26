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
FILL_GAP_UNITS = 6                    # a gap up to a 16th before the next note is read as legato, not a rest


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
    tie_from_prev: bool = False     # continuation of a note split by a barline / odd length
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
    if end_beat in triplet_beats:
        cand = int(round((b_end * GRID) / 4)) * 4
        min_step = 4 if on_beat in triplet_beats else 3
        return max(on_u + min_step, cand)
    if on_beat in triplet_beats:
        # Crossed from a triplet beat into a binary beat: snap end onto the binary beat's 16th/32nd grid
        rel = b_end * GRID - end_beat * GRID
        rel_len = 3 if 1.5 <= rel < 4.5 else 6 * int(round(rel / 6.0))
        return max((on_beat + 1) * GRID, end_beat * GRID + rel_len)
    # Binary: lengths on the 16th grid (6 units), or 32nd (3 units) when genuinely short (< 4.5 units)
    length = 3 if raw < 4.5 else 6 * max(1, int(round(raw / 6.0)))
    cand = on_u + length
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
    # Without a midi-instrument, players (AlphaTab) fall back to program 0 = piano.
    score_inst = ET.SubElement(score_part, "score-instrument", id="P1-I1")
    ET.SubElement(score_inst, "instrument-name").text = "Distortion Guitar"
    midi_inst = ET.SubElement(score_part, "midi-instrument", id="P1-I1")
    ET.SubElement(midi_inst, "midi-channel").text = "1"
    ET.SubElement(midi_inst, "midi-program").text = str(GUITAR_MIDI_PROGRAM + 1)  # MusicXML is 1-based

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
    # pyguitarpro strings: index 1 is highest string, N is lowest string
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

    # one column per attack, in measure order (tied continuations are not re-written)
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
