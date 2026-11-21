"""Harmony analysis: key / mode, chords per beat, harmonic field, recurring progressions.

No trained chord model: a chroma per beat (CQT of the audio, optionally blended with the notes of
the Dettato MIDI), cosine matching against chord templates, Viterbi smoothing so the chord does not
flicker on every beat, the best-fitting diatonic collection for the mode and Krumhansl-Schmuckler
plus chord evidence for the tonic. A study guide (expect roughly 60-75% of the chords right), not a
lead sheet: the UI shows confidences and explains what each result means.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Optional

import librosa
import numpy as np

from app.models import NoteEvent

SR = 22050
HOP = 512

SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
PT_LETTERS = {"C": "Dó", "D": "Ré", "E": "Mi", "F": "Fá", "G": "Sol", "A": "Lá", "B": "Si"}
# parent major keys written with flats (F, Bb, Eb, Ab, Db); everything else with sharps
FLAT_PARENTS = {5, 10, 3, 8, 1}

MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
# Krumhansl-Kessler key profiles
KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# quality -> (interval: weight, chord-name suffix, score penalty)
QUALITIES: dict[str, tuple[dict[int, float], str, float]] = {
    "maj": ({0: 1.0, 4: 1.0, 7: 1.0}, "", 0.0),
    "min": ({0: 1.0, 3: 1.0, 7: 1.0}, "m", 0.0),
    "5": ({0: 1.0, 7: 1.0}, "5", 0.0),
    "dim": ({0: 1.0, 3: 1.0, 6: 1.0}, "°", 0.03),
    "sus4": ({0: 1.0, 5: 1.0, 7: 1.0}, "sus4", 0.04),
    "7": ({0: 1.0, 4: 1.0, 7: 1.0, 10: 0.8}, "7", 0.04),
    "m7": ({0: 1.0, 3: 1.0, 7: 1.0, 10: 0.8}, "m7", 0.04),
    "maj7": ({0: 1.0, 4: 1.0, 7: 1.0, 11: 0.8}, "7M", 0.04),
    "m7b5": ({0: 1.0, 3: 1.0, 6: 1.0, 10: 0.8}, "m7(b5)", 0.05),
}
MINOR_LIKE = {"min", "dim", "m7", "m7b5"}

MODES = [
    # (id, nome, família)
    ("ionian", "jônio", "major"),
    ("dorian", "dórico", "minor"),
    ("phrygian", "frígio", "minor"),
    ("lydian", "lídio", "major"),
    ("mixolydian", "mixolídio", "major"),
    ("aeolian", "eólio", "minor"),
    ("locrian", "lócrio", "minor"),
]
# degree index (0-based, inside the mode) of the chord that gives each mode its colour
CHARACTERISTIC_DEGREE = {"dorian": 3, "phrygian": 1, "lydian": 1, "mixolydian": 6, "locrian": 0}
MODE_INFO = {
    "ionian": (
        "Escala maior. Soa estável, aberta e \"resolvida\".",
        "É a referência: os outros modos são descritos pelo que mudam em relação a ela.",
    ),
    "dorian": (
        "Menor com a 6ª maior. Soa menor, mas menos sombrio, com um toque de groove ou jazz.",
        "O acorde característico é o IV maior (num tom menor comum ele seria menor).",
    ),
    "phrygian": (
        "Menor com a 2ª menor. Soa escuro, tenso, \"espanhol\". Muito usado em metal.",
        "O acorde característico é o bII, meio tom acima da tônica (ex.: Mi → Fá).",
    ),
    "lydian": (
        "Maior com a 4ª aumentada. Soa luminoso, flutuante, de trilha sonora.",
        "O acorde característico é o II maior.",
    ),
    "mixolydian": (
        "Maior com a 7ª menor. Soa maior, mas \"rock\" ou blues, sem a tensão da sensível.",
        "O acorde característico é o bVII maior (ex.: em Ré, o Dó).",
    ),
    "aeolian": (
        "Escala menor natural. Soa triste, sombria ou épica.",
        "Os acordes bVI e bVII maiores dão o som típico de rock e metal em tom menor.",
    ),
    "locrian": (
        "Menor com 2ª menor e 5ª diminuta. Soa instável, quase sem repouso. Raro.",
        "A própria tônica é um acorde diminuto.",
    ),
}
FUNCTIONS = {
    "major": ["T", "S", "T", "S", "D", "T", "D"],
    "minor": ["T", "S", "T", "S", "D", "S", "D"],
}
FUNCTION_PT = {
    "T": "Tônica: repouso, sensação de \"casa\"",
    "S": "Subdominante: afastamento, prepara o movimento",
    "D": "Dominante: tensão, pede para voltar à tônica",
}
NUMERALS = ["I", "bII", "II", "bIII", "III", "IV", "bV", "V", "bVI", "VI", "bVII", "VII"]

TRANSITION_SELF = 0.8
SOFTMAX_BETA = 30.0
DIATONIC_BONUS = 0.02
BASS_BONUS = 0.15
SILENCE_RATIO = 0.08


# ---------- names ----------

def note_name(pc: int, flats: bool) -> str:
    return (FLAT_NAMES if flats else SHARP_NAMES)[pc % 12]


def note_name_pt(pc: int, flats: bool) -> str:
    name = note_name(pc, flats)
    return PT_LETTERS[name[0]] + name[1:]


def chord_name(root: int, quality: str, flats: bool) -> str:
    return note_name(root, flats) + QUALITIES[quality][1]


def roman(root: int, tonic: int, quality: str, implied: Optional[str] = None) -> str:
    """Chromatic roman numeral relative to the tonic: bIII, bVI, bVII... lowercase for minor chords."""
    num = NUMERALS[(root - tonic) % 12]
    q = implied or quality
    accidental, base = (num[0], num[1:]) if num[0] in "b#" else ("", num)
    if q in MINOR_LIKE:
        base = base.lower()
    suffix = {"dim": "°", "m7b5": "ø", "7": "7", "m7": "7", "maj7": "7M", "sus4": "sus4"}.get(q, "")
    if quality == "5" and implied is None:
        suffix = "5"
    return accidental + base + suffix


def triad_quality(third: int, fifth: int) -> str:
    return {(4, 7): "maj", (3, 7): "min", (3, 6): "dim"}.get((third, fifth), "maj")


def tetrad_quality(third: int, fifth: int, seventh: int) -> tuple[str, str]:
    """(internal quality or "", chord suffix)."""
    table = {
        (4, 7, 11): ("maj7", "7M"), (4, 7, 10): ("7", "7"), (3, 7, 10): ("m7", "m7"),
        (3, 6, 10): ("m7b5", "m7(b5)"), (3, 7, 11): ("", "m(7M)"), (3, 6, 9): ("", "°7"),
        (4, 8, 11): ("", "+7M"),
    }
    return table.get((third, fifth, seventh), ("", "7"))


# ---------- features ----------

def beat_grid(beats: list[float], duration: float) -> np.ndarray:
    """Beat boundaries from 0 to the end. Falls back to a 0.5 s grid without beats."""
    b = [t for t in beats if 0.0 < t < duration]
    if len(b) < 4:
        b = list(np.arange(0.5, duration, 0.5))
    edges = np.array([0.0, *b, duration])
    return edges[np.diff(edges, prepend=-1.0) > 0.05]


def _sync(feature: np.ndarray, edges: np.ndarray, frame_times: np.ndarray) -> np.ndarray:
    """Median of each column group falling inside each [edge_i, edge_i+1)."""
    idx = np.searchsorted(frame_times, edges)
    out = np.zeros((feature.shape[0], len(edges) - 1))
    for i in range(len(edges) - 1):
        a, b = idx[i], max(idx[i + 1], idx[i] + 1)
        seg = feature[:, a:b]
        if seg.size:
            out[:, i] = np.median(seg, axis=1)
    return out


def audio_features(y: np.ndarray, bass_y: Optional[np.ndarray], edges: np.ndarray) -> dict[str, Any]:
    """Beat-synchronous chroma, bass chroma and energy, plus the tuning offset (cents)."""
    tuning = float(librosa.estimate_tuning(y=y, sr=SR)) if y.size > SR else 0.0
    # treble only (from C3): the bass and its harmonics would drown the third of the chord;
    # the bass gets its own chroma, used for the root.
    chroma = librosa.feature.chroma_cqt(
        y=y, sr=SR, hop_length=HOP, tuning=tuning, bins_per_octave=36,
        fmin=librosa.note_to_hz("C3"), n_octaves=5,
    )
    by = bass_y if bass_y is not None else y
    bass = librosa.feature.chroma_cqt(
        y=by, sr=SR, hop_length=HOP, tuning=tuning, bins_per_octave=36,
        fmin=librosa.note_to_hz("C1"), n_octaves=3 if bass_y is not None else 2,
    )
    rms = librosa.feature.rms(y=y, hop_length=HOP)
    n = chroma.shape[1]
    times = librosa.frames_to_time(np.arange(n), sr=SR, hop_length=HOP)
    return {
        "chroma": _sync(chroma, edges, times),
        "bass": _sync(bass[:, :n], edges, times),
        "energy": _sync(rms[:, :n], edges, times)[0],
        "tuning_cents": int(round(tuning * 100)),
    }


def midi_chroma(notes: Iterable[NoteEvent], edges: np.ndarray) -> np.ndarray:
    """Pitch-class weight per beat: overlap duration x velocity."""
    out = np.zeros((12, len(edges) - 1))
    starts, ends = edges[:-1], edges[1:]
    for n in notes:
        a = np.searchsorted(ends, n.start, side="right")
        b = np.searchsorted(starts, n.end, side="left")
        for i in range(a, min(b, len(starts))):
            ov = min(n.end, ends[i]) - max(n.start, starts[i])
            if ov > 0:
                out[n.pitch % 12, i] += ov * (n.velocity / 127.0)
    top = out.max(axis=0, keepdims=True)
    return np.divide(out, top, out=np.zeros_like(out), where=top > 0)


# ---------- key ----------

def _norm_rows(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=0, keepdims=True)
    return np.divide(x, n, out=np.zeros_like(x), where=n > 0)


def best_collection(profile: np.ndarray) -> tuple[int, float]:
    """Parent major key (pc) of the diatonic set holding the most energy, and that share."""
    p = profile / (profile.sum() or 1.0)
    scores = [float(sum(p[(r + i) % 12] for i in MAJOR_SCALE)) for r in range(12)]
    r = int(np.argmax(scores))
    return r, scores[r]


def ks_scores(profile: np.ndarray) -> dict[tuple[int, str], float]:
    out = {}
    for t in range(12):
        rolled = np.roll(profile, -t)
        out[(t, "major")] = float(np.corrcoef(rolled, KS_MAJOR)[0, 1])
        out[(t, "minor")] = float(np.corrcoef(rolled, KS_MINOR)[0, 1])
    return out


def mode_of(tonic: int, parent: int) -> int:
    return MAJOR_SCALE.index((tonic - parent) % 12)


def scale_pcs(tonic: int, parent: int) -> list[int]:
    m = mode_of(tonic, parent)
    return [(parent + MAJOR_SCALE[(m + i) % 7]) % 12 for i in range(7)]


# ---------- chords ----------

def chord_vocab() -> list[tuple[int, str]]:
    return [(r, q) for q in QUALITIES for r in range(12)]


def template_matrix(vocab: list[tuple[int, str]]) -> np.ndarray:
    t = np.zeros((len(vocab), 12))
    for k, (r, q) in enumerate(vocab):
        for iv, w in QUALITIES[q][0].items():
            t[k, (r + iv) % 12] = w
    return t / np.linalg.norm(t, axis=1, keepdims=True)


def chord_pcs(root: int, quality: str) -> set[int]:
    return {(root + iv) % 12 for iv in QUALITIES[quality][0]}


def decode_chords(
    chroma: np.ndarray, bass: np.ndarray, energy: np.ndarray, collection: set[int]
) -> tuple[list[Optional[tuple[int, str]]], np.ndarray]:
    """Viterbi path of (root, quality) per beat (None = silence) and the per-beat posterior."""
    vocab = chord_vocab()
    temps = template_matrix(vocab)
    c = _norm_rows(chroma)
    scores = temps @ c  # (n_chords, n_beats)
    bass_n = bass / np.maximum(bass.max(axis=0, keepdims=True), 1e-9)
    for k, (r, q) in enumerate(vocab):
        scores[k] += BASS_BONUS * bass_n[r] - QUALITIES[q][2]
        if chord_pcs(r, q) <= collection:
            scores[k] += DIATONIC_BONUS
    med = float(np.median(energy[energy > 0])) if np.any(energy > 0) else 0.0
    quiet = (energy < SILENCE_RATIO * med) | (np.linalg.norm(chroma, axis=0) < 1e-6)
    n_score = np.where(quiet, 3.0, -1.0)
    scores = np.vstack([scores, n_score[None, :]])
    probs = np.exp(SOFTMAX_BETA * (scores - scores.max(axis=0, keepdims=True)))
    probs /= probs.sum(axis=0, keepdims=True)
    trans = librosa.sequence.transition_loop(len(vocab) + 1, TRANSITION_SELF)
    path = librosa.sequence.viterbi(probs, trans)
    labels = [None if k == len(vocab) else vocab[k] for k in path]
    return labels, probs[path, np.arange(len(path))]


# ---------- analysis ----------

def _segments(labels, posterior, edges) -> list[dict[str, Any]]:
    segs: list[dict[str, Any]] = []
    for i, lab in enumerate(labels):
        if segs and segs[-1]["chord"] == lab:
            segs[-1]["end_i"] = i + 1
            segs[-1]["post"].append(float(posterior[i]))
        else:
            segs.append({"chord": lab, "start_i": i, "end_i": i + 1, "post": [float(posterior[i])]})
    for s in segs:
        s["start"] = round(float(edges[s["start_i"]]), 3)
        s["end"] = round(float(edges[s["end_i"]]), 3)
    return segs


def _explain_outside(root: int, quality: str, tonic: int, family: str, nxt: Optional[dict], flats: bool) -> str:
    pcs = chord_pcs(root, quality)
    dominant_like = quality in ("maj", "7")
    if family == "minor" and dominant_like and root == (tonic + 7) % 12:
        return ("V maior: vem da escala menor harmônica (7º grau elevado em meio tom, a \"sensível\"). "
                "Cria uma tensão forte que pede a volta para a tônica.")
    if dominant_like and nxt is not None and nxt["root"] == (root + 5) % 12 and nxt.get("diatonic"):
        return (f"Dominante secundária (V/{nxt['numeral_base']}): pega emprestada a tensão de dominante "
                f"para valorizar a chegada no {nxt['label']}.")

    def borrowed(mode_ids: tuple[str, ...]) -> Optional[str]:
        for idx, (mid, mname, _) in enumerate(MODES):
            if mid in mode_ids and pcs <= set(scale_pcs(tonic, (tonic - MAJOR_SCALE[idx]) % 12)):
                fam = "menor" if mid == "aeolian" else "maior" if mid == "ionian" else mname
                return (f"Empréstimo modal: o acorde vem do campo de {note_name_pt(tonic, flats)} {fam} "
                        "(mesma tônica, outra escala). Muda a cor sem mudar o centro.")
        return None

    text = borrowed(("ionian", "aeolian"))
    if text:
        return text
    if nxt is not None and (nxt["root"] - root) % 12 in (1, 11):
        return (f"Aproximação cromática: chega ao {nxt['label']} por meio tom. "
                "É uma nota de passagem em forma de acorde, muito comum em riffs.")
    return borrowed(("dorian", "mixolydian", "phrygian", "lydian", "locrian")) or (
        "Cromático: fora do campo harmônico. Pode ser uma passagem, uma tensão de efeito ou um erro de detecção.")


def find_loops(seq: list[str], max_len: int = 8) -> list[tuple[tuple[str, ...], list[int]]]:
    """Recurring chord sequences: (pattern, start indices of non-overlapping occurrences), best first."""
    found: list[tuple[float, tuple[str, ...], list[int]]] = []
    for n in range(2, min(max_len, len(seq) // 2) + 1):
        positions: dict[tuple[str, ...], list[int]] = {}
        for i in range(len(seq) - n + 1):
            pat = tuple(seq[i : i + n])
            if len(set(pat)) < 2:
                continue
            occ = positions.setdefault(pat, [])
            if not occ or i >= occ[-1] + n:
                occ.append(i)
        for pat, occ in positions.items():
            # a pattern that is just a shorter one repeated (A B A B) is reported as the shorter one
            if any(n % k == 0 and pat == pat[:k] * (n // k) for k in range(2, n)):
                continue
            if len(occ) >= (3 if n == 2 else 2):
                found.append((len(occ) * n, pat, occ))
    found.sort(key=lambda f: (-f[0], len(f[1])))
    chosen: list[tuple[tuple[str, ...], list[int]]] = []
    for _, pat, occ in found:
        # skip rotations, pieces and shifted copies (sharing most of their chords in a row) of a chosen loop
        if any(_common_run(c + c, pat) >= max(2, 0.6 * min(len(c), len(pat))) for c, _ in chosen):
            continue
        chosen.append((pat, occ))
        if len(chosen) == 4:
            break
    return chosen


def _common_run(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    """Length of the longest run of chords appearing, in order, in both sequences."""
    best = 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, z in enumerate(b, 1):
            if x == z:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def analyze(
    y: np.ndarray,
    beats: list[float],
    duration: float,
    bass_y: Optional[np.ndarray] = None,
    midi_notes: Optional[list[NoteEvent]] = None,
    source: str = "mix",
) -> dict[str, Any]:
    edges = beat_grid(beats, duration)
    feats = audio_features(y, bass_y, edges)
    chroma = feats["chroma"]
    if midi_notes:
        chroma = 0.5 * chroma + 0.5 * midi_chroma(midi_notes, edges)
    bass, energy = feats["bass"], feats["energy"]

    # pass 1: diatonic collection from the energy-weighted chroma (+ bass, which marks roots)
    w = energy / (energy.max() or 1.0)
    profile = (chroma * w).sum(axis=1) + 0.5 * (bass * w).sum(axis=1)
    parent, fit = best_collection(profile)
    collection = set(scale_pcs(parent, parent))

    labels, posterior = decode_chords(chroma, bass, energy, collection)
    segs = _segments(labels, posterior, edges)
    chord_segs = [s for s in segs if s["chord"] is not None]

    # pass 2: tonic = KS correlation + how long / where chords rooted on it sound
    ks = ks_scores(profile)
    total = sum(s["end"] - s["start"] for s in chord_segs) or 1.0
    root_time = Counter()
    for s in chord_segs:
        root_time[s["chord"][0]] += s["end"] - s["start"]
    first = chord_segs[0]["chord"][0] if chord_segs else None
    last = chord_segs[-1]["chord"][0] if chord_segs else None
    cand = {}
    for t in collection:
        m = MODES[mode_of(t, parent)]
        cand[t] = (
            ks[(t, m[2])] + 0.8 * root_time[t] / total
            + (0.1 if t == last else 0.0) + (0.05 if t == first else 0.0)
        )
    ranked = sorted(cand, key=cand.get, reverse=True)
    tonic = ranked[0]
    mode_id, mode_pt, family = MODES[mode_of(tonic, parent)]
    flats = parent in FLAT_PARENTS
    scale = scale_pcs(tonic, parent)
    margin = cand[ranked[0]] - cand[ranked[1]] if len(ranked) > 1 else 1.0
    confidence = float(np.clip(0.35 + margin + (fit - 0.7), 0.05, 0.99))

    # harmonic field
    field = []
    char_deg = CHARACTERISTIC_DEGREE.get(mode_id)
    for d in range(7):
        r, third, fifth, sev = (scale[(d + k) % 7] for k in (0, 2, 4, 6))
        tq = triad_quality((third - r) % 12, (fifth - r) % 12)
        tetq, tet_suffix = tetrad_quality((third - r) % 12, (fifth - r) % 12, (sev - r) % 12)
        secs = root_time[r]
        fn = FUNCTIONS[family][d]
        field.append({
            "degree": d + 1,
            "root": r,
            "numeral": roman(r, tonic, tq),
            "triad": chord_name(r, tq, flats),
            "tetrad": note_name(r, flats) + tet_suffix,
            "tetrad_numeral": roman(r, tonic, tetq) if tetq else roman(r, tonic, tq) + tet_suffix,
            "quality": tq,
            "function": fn,
            "function_pt": FUNCTION_PT[fn],
            "seconds": round(secs, 1),
            "share": round(secs / total, 3),
            "characteristic": d == char_deg,
        })
    field_by_root = {f["root"]: f for f in field}

    # chord timeline
    chords: list[dict[str, Any]] = []
    for s in chord_segs:
        r, q = s["chord"]
        diatonic = chord_pcs(r, q) <= set(scale)
        # a power chord has no third: the field tells which triad it stands for
        implied = field_by_root[r]["quality"] if q == "5" and diatonic else None
        label = chord_name(r, q, flats)
        bass_seg = bass[:, s["start_i"] : s["end_i"]].mean(axis=1)
        b = int(np.argmax(bass_seg))
        slash = None
        if b != r and b in chord_pcs(r, q) and bass_seg[b] > 1.5 * bass_seg[r]:
            slash = b
            label += "/" + note_name(b, flats)
        num = roman(r, tonic, q, implied)
        chords.append({
            "start": s["start"], "end": s["end"], "label": label, "root": r, "quality": q,
            "bass": slash, "numeral": num, "numeral_base": num.rstrip("57ø°M").replace("sus4", "") or num,
            "implied": chord_name(r, implied, flats) if implied else None,
            "diatonic": bool(diatonic),
            "degree": field_by_root[r]["degree"] if diatonic and r in field_by_root else None,
            "confidence": round(float(np.mean(s["post"])), 2),
        })
    for i, c in enumerate(chords):
        if not c["diatonic"]:
            nxt = chords[i + 1] if i + 1 < len(chords) else None
            c["explain"] = _explain_outside(c["root"], c["quality"], tonic, family, nxt, flats)

    # chords outside the field, aggregated
    outside: dict[str, dict[str, Any]] = {}
    for c in chords:
        if c["diatonic"]:
            continue
        o = outside.setdefault(c["label"], {
            "label": c["label"], "numeral": c["numeral"], "count": 0, "seconds": 0.0,
            "explain": c["explain"], "first": c["start"],
        })
        o["count"] += 1
        o["seconds"] = round(o["seconds"] + c["end"] - c["start"], 1)

    # recurring progressions (bass / slash ignored so C and C/E count as the same chord)
    # (silences dropped, so the same chord on both sides of a gap is merged)
    merged: list[dict[str, Any]] = []
    for c in chords:
        name = chord_name(c["root"], c["quality"], flats)
        if merged and merged[-1]["name"] == name:
            merged[-1]["end"] = c["end"]
        else:
            merged.append({"name": name, "numeral": c["numeral"], "start": c["start"], "end": c["end"]})
    seq = [m["name"] for m in merged]
    loops = []
    for pat, occ in find_loops(seq):
        n = len(pat)
        loops.append({
            "chords": list(pat),
            "numerals": [m["numeral"] for m in merged[occ[0] : occ[0] + n]],
            "count": len(occ),
            "starts": [merged[i]["start"] for i in occ],
            "ends": [merged[i + n - 1]["end"] for i in occ],
        })

    rel = None
    if mode_id == "aeolian":
        rel = f"{note_name_pt((tonic + 3) % 12, flats)} maior"
    elif mode_id == "ionian":
        rel = f"{note_name_pt((tonic + 9) % 12, flats)} menor"
    tonic_pt = note_name_pt(tonic, flats)
    key_name = f"{tonic_pt} {'maior' if mode_id == 'ionian' else 'menor' if mode_id == 'aeolian' else mode_pt}"
    character, characteristic = MODE_INFO[mode_id]
    diatonic_time = sum(c["end"] - c["start"] for c in chords if c["diatonic"])
    alternatives = []
    for t in ranked[1:3]:
        mid, mpt, _ = MODES[mode_of(t, parent)]
        alternatives.append(
            f"{note_name_pt(t, flats)} {'maior' if mid == 'ionian' else 'menor' if mid == 'aeolian' else mpt}"
        )
    return {
        "version": 1,
        "source": source,
        "used_midi": bool(midi_notes),
        "tuning_cents": feats["tuning_cents"],
        "key": {
            "tonic": tonic,
            "tonic_name": note_name(tonic, flats),
            "name": key_name,
            "mode": mode_id,
            "mode_pt": mode_pt,
            "family": family,
            "confidence": round(confidence, 2),
            "scale": [note_name(p, flats) for p in scale],
            "relative": rel,
            "alternatives": alternatives,
            "character": character,
            "characteristic": characteristic,
        },
        "field": field,
        "chords": chords,
        "loops": loops,
        "outside": sorted(outside.values(), key=lambda o: -o["seconds"]),
        "stats": {
            "distinct_chords": len(set(seq)),
            "chord_changes": len(chords),
            "diatonic_share": round(diatonic_time / total, 3),
        },
    }
