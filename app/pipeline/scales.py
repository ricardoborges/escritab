"""Which scales a solo is probably built on, best first: an ear-training aid.

Evidence, per pitch class: what the excerpt plays (the part's transcribed notes when they exist,
plus the chroma of the excerpt's audio). Each candidate scale (root x type) is scored as a simple
generative model: in-scale notes share most of the probability, out-of-scale notes a little, so a
5-note pentatonic that covers everything beats the 7-note scale containing it, and a 7-note scale
wins as soon as its extra notes really sound. Scales sharing the same notes (A minor pentatonic /
C major pentatonic) only differ by their root, which comes from the song's key, the chords under
the excerpt and how much the line lands on it; the UI says which scales are equivalent.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import numpy as np

from app.models import NoteEvent
from app.pipeline.harmony import FLAT_PARENTS, chord_pcs, note_name, note_name_pt

# id -> (intervals, nome, relative-major offset (spelling), prior, description)
SCALE_TYPES: dict[str, tuple[tuple[int, ...], str, int, float, str]] = {
    "minor_pentatonic": (
        (0, 3, 5, 7, 10), "pentatônica menor", 3, 1.0,
        "A escala mais usada em solos de rock, blues e metal. Cinco notas, sem semitons: difícil errar.",
    ),
    "major_pentatonic": (
        (0, 2, 4, 7, 9), "pentatônica maior", 0, 0.75,
        "Som aberto e alegre: country, rock sulista, pop. Mesmas notas da pentatônica menor três casas abaixo.",
    ),
    "blues": (
        (0, 3, 5, 6, 7, 10), "blues", 3, 0.6,
        "Pentatônica menor com a b5 (blue note), usada de passagem entre a 4ª e a 5ª.",
    ),
    "aeolian": (
        (0, 2, 3, 5, 7, 8, 10), "menor natural (eólio)", 3, 0.85,
        "Pentatônica menor + 2ª e b6. Soa triste ou épica; base dos solos melódicos de metal.",
    ),
    "ionian": (
        (0, 2, 4, 5, 7, 9, 11), "maior (jônio)", 0, 0.8,
        "A escala maior. Estável e resolvida; com a 4ª e a 7ª fica mais melódica que a pentatônica.",
    ),
    "dorian": (
        (0, 2, 3, 5, 7, 9, 10), "dórico", 10, 0.55,
        "Menor com a 6ª maior: menos sombria, com groove (Santana, funk, jazz).",
    ),
    "mixolydian": (
        (0, 2, 4, 5, 7, 9, 10), "mixolídio", 5, 0.55,
        "Maior com a 7ª menor: rock, blues em tom maior, hard rock.",
    ),
    "harmonic_minor": (
        (0, 2, 3, 5, 7, 8, 11), "menor harmônica", 3, 0.4,
        "Menor com a 7ª maior (sensível): som neoclássico, \"árabe\", muito usado no metal.",
    ),
    "phrygian": (
        (0, 1, 3, 5, 7, 8, 10), "frígio", 8, 0.4,
        "Menor com a b2: escuro e tenso, típico de thrash e metal.",
    ),
    "phrygian_dominant": (
        (0, 1, 4, 5, 7, 8, 10), "frígio dominante", 8, 0.3,
        "Frígio com a 3ª maior (5º modo da menor harmônica): flamenco, metal neoclássico.",
    ),
    "lydian": (
        (0, 2, 4, 6, 7, 9, 11), "lídio", 7, 0.25,
        "Maior com a #4: luminoso, flutuante, de trilha sonora (Satriani, Vai).",
    ),
}
DEGREE_LABELS = ["1", "b2", "2", "b3", "3", "4", "b5", "5", "b6", "6", "b7", "7"]

OUT_PROB = 0.07         # total probability a scale leaves for notes outside it
SAMPLE_SIZE = 40.0      # how many "notes" the profile is worth: sharpens the likelihood
OUTSIDE_SHOW = 0.05     # a pitch class above this share that a scale misses is reported
TOP_N = 6
DISPLAY_TEMPERATURE = 3.0  # the likelihood is very peaked: soften it for the percentages shown


def scale_pcs(root: int, type_id: str) -> list[int]:
    return [(root + i) % 12 for i in SCALE_TYPES[type_id][0]]


def parse_scale_id(scale_id: Optional[str]) -> Optional[tuple[int, str]]:
    """"11:minor_pentatonic" -> (11, "minor_pentatonic"); None for anything invalid."""
    if not scale_id or ":" not in scale_id:
        return None
    root_s, type_id = scale_id.split(":", 1)
    try:
        root = int(root_s) % 12
    except ValueError:
        return None
    return (root, type_id) if type_id in SCALE_TYPES else None


def _uses_flats(root: int, type_id: str) -> bool:
    return (root + SCALE_TYPES[type_id][2]) % 12 in FLAT_PARENTS


def scale_label(root: int, type_id: str) -> str:
    return f"{note_name(root, _uses_flats(root, type_id))} {SCALE_TYPES[type_id][1]}"


def note_profile(notes: Iterable[NoteEvent]) -> np.ndarray:
    """Pitch-class weight of a note list: duration (capped, a held note is still one note) x velocity."""
    p = np.zeros(12)
    for n in notes:
        p[n.pitch % 12] += min(n.end - n.start, 1.0) * (0.3 + n.velocity / 127.0)
    return p


def _norm(p: np.ndarray) -> np.ndarray:
    s = float(p.sum())
    return p / s if s > 0 else np.full(12, 1 / 12)


def chord_root_weights(chords: list[dict], start: float, end: float) -> tuple[np.ndarray, list[tuple[set[int], float]]]:
    """Time each chord root sounds inside [start, end), and (chord pcs, seconds) of each chord."""
    w = np.zeros(12)
    spans: list[tuple[set[int], float]] = []
    for c in chords:
        ov = min(c["end"], end) - max(c["start"], start)
        if ov <= 0:
            continue
        w[c["root"]] += ov
        spans.append((chord_pcs(c["root"], c["quality"]), ov))
    return w, spans


def suggest_scales(
    profile: np.ndarray,
    key_tonic: Optional[int] = None,
    key_family: Optional[str] = None,
    chord_roots: Optional[np.ndarray] = None,
    chord_spans: Optional[list[tuple[set[int], float]]] = None,
    top_n: int = TOP_N,
) -> list[dict[str, Any]]:
    """Rank root x scale type for a pitch-class profile (any scale, 12 values)."""
    p = _norm(np.asarray(profile, dtype=float))
    roots = _norm(chord_roots) if chord_roots is not None and chord_roots.sum() > 0 else None
    chord_time = sum(s for _, s in chord_spans) if chord_spans else 0.0

    cands = []
    for type_id, (ivs, _, _, prior, _) in SCALE_TYPES.items():
        k = len(ivs)
        for root in range(12):
            pcs = {(root + i) % 12 for i in ivs}
            inside = np.array([pc in pcs for pc in range(12)])
            q = np.where(inside, (1 - OUT_PROB) / k, OUT_PROB / (12 - k))
            loglik = SAMPLE_SIZE * float(np.sum(p * np.log(q)))
            # root evidence: the song's tonic, the chords under the solo, the line landing on it
            r = 0.0
            if key_tonic is not None and root == key_tonic:
                r += 2.0
                minorish = 3 in ivs
                if (key_family == "minor") == minorish and key_family is not None:
                    r += 0.8
            if roots is not None:
                r += 3.0 * float(roots[root])
            r += 4.0 * float(p[root])
            fit = None
            if chord_spans:
                fit = sum(s for c, s in chord_spans if c <= pcs) / chord_time if chord_time else None
                r += 1.5 * (fit or 0.0)
            score = loglik + r + float(np.log(prior))
            coverage = float(p[inside].sum())
            cands.append((score, root, type_id, coverage, fit, pcs))

    cands.sort(key=lambda c: -c[0])
    # one entry per set of notes: its other names (relative / modes) are listed on that entry
    distinct, seen = [], []
    for c in cands:
        if c[5] not in seen:
            seen.append(c[5])
            distinct.append(c)
    scores = np.array([c[0] for c in distinct]) / DISPLAY_TEMPERATURE
    probs = np.exp(scores - scores.max())
    probs /= probs.sum()

    out: list[dict[str, Any]] = []
    for (score, root, type_id, coverage, fit, pcs), prob in zip(distinct[:top_n], probs[:top_n]):
        ivs, tname, _, _, desc = SCALE_TYPES[type_id]
        flats = _uses_flats(root, type_id)
        labels = list(DEGREE_LABELS)
        if type_id == "lydian":
            labels[6] = "#4"
        same = [
            f"{note_name_pt(r2, _uses_flats(r2, t2))} {SCALE_TYPES[t2][1]}"
            for _, r2, t2, _, _, pcs2 in cands
            if pcs2 == pcs and (r2, t2) != (root, type_id)
        ]
        outside = [
            {"pc": pc, "name": note_name(pc, flats), "share": round(float(p[pc]), 3)}
            for pc in np.argsort(-p) if pc not in pcs and p[pc] >= OUTSIDE_SHOW
        ]
        out.append({
            "id": f"{root}:{type_id}",
            "root": root,
            "type": type_id,
            "name": f"{note_name_pt(root, flats)} {tname}",
            "short": scale_label(root, type_id),
            "description": desc,
            "notes": [
                {"pc": (root + i) % 12, "name": note_name(root + i, flats), "degree": labels[i],
                 "share": round(float(p[(root + i) % 12]), 3)}
                for i in ivs
            ],
            "probability": round(float(prob), 3),
            "coverage": round(coverage, 3),
            "chord_fit": None if fit is None else round(float(fit), 3),
            "outside": [dict(o, pc=int(o["pc"])) for o in outside],
            "same_notes_as": same[:3],
        })
    return out
