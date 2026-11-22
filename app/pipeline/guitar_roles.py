"""Split a transcribed guitar part into rhythm ("base") and lead ("solo / riff") by musical role.

Dettato's grouper names tracks by timbre and fuses every electric guitar into one track, so rhythm
chords and solo lines come out mixed (and some lead lines land in a mis-named track such as
"violin" or "synth lead"). This module re-labels each guitar note from its local context:

  * notes are grouped into onset events (notes starting within EVENT_S of each other);
  * inside an event, top notes a wide gap above the rest are peeled off as lead (a solo played
    over a chugging rhythm lands in the same events as the rhythm);
  * the rhythm "roof" at time t is the typical top note of the chord events around t;
  * chord events whose bottom sits at or below the roof are rhythm; double stops above it are lead;
  * single notes are lead when they sit above the roof (or, with no chords around, above
    LEAD_MIN_PITCH); low single notes are rhythm (palm-muted chugs, pedal notes, bass-string riffs);
  * a short majority filter over the single notes keeps phrases together.

It also flags *lead candidates*: non-guitar tracks that play like a lead guitar (monophonic, guitar
register), which is where Dettato tends to put intro melodies and solos.
"""
from __future__ import annotations

import numpy as np

EVENT_S = 0.035          # notes starting this close together form one event (chord / double stop)
CONTEXT_S = 2.0          # half-width of the window that defines the local rhythm roof
SMOOTH_S = 0.6           # half-width of the majority filter over single-note labels
LEAD_MIN_PITCH = 60      # C4: with no rhythm chords around, single notes from here up are lead
ROOF_MARGIN = 1          # semitones above the rhythm roof a single note needs to count as lead
MIN_CHORD_EVENTS = 3     # chord events needed in the window for the roof to be trusted
PEEL_GAP = 8             # semitones: a gap this wide inside an event separates a lead note from the chord below
PEEL_MIN_PITCH = 59      # B3: peeled notes must sit at least this high

# programs that are never lead-guitar candidates: pianos/keys, bass, voices, ensembles, pads, FX, percussion
_NOT_LEAD = set(range(0, 8)) | set(range(32, 40)) | {52, 53, 54} | set(range(88, 128)) | {44, 45, 48, 49, 50, 51}


def _events(onsets: np.ndarray) -> np.ndarray:
    """Event id per note (input sorted by onset)."""
    ev = np.zeros(len(onsets), np.int64)
    if len(onsets) == 0:
        return ev
    start = onsets[0]
    cur = 0
    for i in range(1, len(onsets)):
        if onsets[i] - start > EVENT_S:
            cur += 1
            start = onsets[i]
        ev[i] = cur
    return ev


def _peel(pitches: np.ndarray) -> int:
    """Within one onset event (pitches sorted ascending): how many top notes are a lead line played over
    the rhythm (0 = none). Power chords stack in 4ths / 5ths / octaves from the low strings; a lead note
    sits a wide gap above the rhythm's top while the rhythm keeps chugging underneath."""
    if len(pitches) < 2:
        return 0
    gaps = np.diff(pitches)
    g = int(np.argmax(gaps))
    upper = pitches[g + 1:]
    if gaps[g] >= PEEL_GAP and len(upper) <= 2 and upper[0] >= PEEL_MIN_PITCH:
        return len(upper)
    return 0


def split_roles(notes: np.ndarray) -> np.ndarray:
    """notes: (N, >=3) array [onset, offset, pitch, ...]. -> bool array, True = lead, False = rhythm."""
    n = len(notes)
    if n == 0:
        return np.zeros(0, bool)
    order = np.lexsort((notes[:, 2], notes[:, 0]))      # by onset, then pitch
    on = notes[order, 0].astype(np.float64)
    pitch = notes[order, 2].astype(np.int64)
    ev = _events(on)
    n_ev = int(ev.max()) + 1
    starts = np.searchsorted(ev, np.arange(n_ev))
    ends = np.append(starts[1:], n)

    lead = np.zeros(n, bool)          # per note (sorted order)
    peeled = np.zeros(n, bool)        # lead notes found by peeling: strong evidence, never smoothed away
    ev_time = on[starts]
    r_size = np.zeros(n_ev, np.int64)  # rhythm part of each event (after peeling)
    r_top = np.full(n_ev, -1)
    r_bot = np.full(n_ev, 999)
    for e in range(n_ev):
        a, b = starts[e], ends[e]
        ps = np.sort(pitch[a:b])
        k = _peel(ps) if b - a >= 2 else 0
        if k:
            idx = np.arange(a, b)[np.argsort(pitch[a:b], kind="stable")][-k:]
            lead[idx] = peeled[idx] = True
            ps = ps[:-k]
        r_size[e] = len(ps)
        if len(ps):
            r_top[e], r_bot[e] = ps[-1], ps[0]

    chord = r_size >= 2
    c_time, c_top = ev_time[chord], r_top[chord]
    lo = np.searchsorted(c_time, ev_time - CONTEXT_S)
    hi = np.searchsorted(c_time, ev_time + CONTEXT_S)

    single_notes = []
    for e in range(n_ev):
        if r_size[e] == 0:
            continue
        roof = float(np.percentile(c_top[lo[e]:hi[e]], 60)) if hi[e] - lo[e] >= MIN_CHORD_EVENTS else None
        rest = [i for i in range(starts[e], ends[e]) if not peeled[i]]
        if r_size[e] >= 2:
            # a chord whose lowest note is above the rhythm roof is a lead double stop / harmony line
            is_lead = roof is not None and r_bot[e] > roof + ROOF_MARGIN and r_size[e] <= 3
        else:
            p = r_top[e]
            is_lead = p >= LEAD_MIN_PITCH if roof is None else p > roof + ROOF_MARGIN
            single_notes.append(rest[0])
        lead[rest] = is_lead

    # majority filter over the upper voice (single notes + peeled lead notes): phrases stay in one part
    single = np.array(single_notes, np.int64)
    if len(single) > 2:
        voice = np.sort(np.concatenate([single, np.nonzero(peeled)[0]]))
        vt, vl = on[voice], lead[voice].astype(np.float64)
        csum = np.concatenate([[0.0], np.cumsum(vl)])
        st = on[single]
        a = np.searchsorted(vt, st - SMOOTH_S)
        b = np.searchsorted(vt, st + SMOOTH_S, side="right")
        frac = (csum[b] - csum[a]) / np.maximum(b - a, 1)
        # never pull a clearly low note (bass strings) into the lead
        lead[single] = (frac > 0.5) & (pitch[single] >= LEAD_MIN_PITCH - 7)

    out = np.zeros(n, bool)
    out[order] = lead
    return out


def _first_per_event(on: np.ndarray, ev: np.ndarray, n_ev: int) -> np.ndarray:
    first = np.full(n_ev, np.inf)
    np.minimum.at(first, ev, on)
    return first


def chord_ratio(notes: np.ndarray, ignore_octaves: bool = False) -> float:
    """Share of notes that start together with another note (0 = monophonic line)."""
    if len(notes) < 2:
        return 0.0
    order = np.argsort(notes[:, 0])
    on = notes[order, 0]
    pitches = notes[order, 2].astype(np.int64)
    close = np.zeros(len(on), bool)
    for i in range(len(on) - 1):
        j = i + 1
        while j < len(on) and on[j] - on[i] < EVENT_S:
            if not (ignore_octaves and (pitches[j] - pitches[i]) % 12 == 0):
                close[i] = close[j] = True
            j += 1
    return float(close.mean())


def is_lead_candidate(program: int, is_drum: bool, notes: np.ndarray) -> bool:
    """A non-guitar track that plays like a lead guitar: mostly monophonic, in the guitar's upper register."""
    if is_drum or program in _NOT_LEAD or 24 <= program <= 31 or len(notes) < 20:
        return False
    med = float(np.median(notes[:, 2]))
    return chord_ratio(notes, ignore_octaves=True) <= 0.45 and 55 <= med <= 84
