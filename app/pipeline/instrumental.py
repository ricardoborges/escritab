"""Names the instrumental stretches the lyrics leave untagged (Solo, Breakdown, Interlúdio...).

Works on the full-song Dettato MIDI and the harmony chords, both already computed, so it is cheap
enough to run on every lyrics request. Each stretch is compared with the song as a whole:

  * Solo: a lead line (a guitar "Solo/Riff" part or a lead-like track) plays through most of it;
  * Breakdown: the drums are still at full strength, there is no lead and the harmony sits on one
    or two chords (the typical chug on the low string);
  * Interlúdio: clearly quieter than the rest of the song (fewer notes and drum hits);
  * anything else stays "Instrumental".

These are guesses: the result carries ``why`` so the interface can say what it was based on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

BIN_S = 0.5
SOLO_LEAD_COVER = 0.45        # share of the stretch's bins with lead notes sounding
BREAKDOWN_DRUMS = 0.8         # drum hits per second vs the song's typical (median of active bins)
BREAKDOWN_MAX_LEAD = 0.2
BREAKDOWN_MAX_CHORDS = 2
QUIET_ACTIVITY = 0.5          # notes + drum hits per second vs the song's typical
MIN_STRETCH_S = 2.0


@dataclass
class SongActivity:
    """Per-bin note activity of the whole song, split into lead, other pitched and drums."""
    lead: np.ndarray = field(default_factory=lambda: np.zeros(0))
    pitched: np.ndarray = field(default_factory=lambda: np.zeros(0))
    drums: np.ndarray = field(default_factory=lambda: np.zeros(0))

    def typical(self, arr: np.ndarray) -> float:
        active = arr[arr > 0]
        return float(np.median(active)) if len(active) else 0.0


def build_activity(tracks: list[dict[str, Any]], duration: float) -> SongActivity:
    """tracks: [{"role": str|None, "is_drum": bool, "onsets": [s], "spans": [(start, end)]}]."""
    n = max(1, int(np.ceil(duration / BIN_S)))
    lead = np.zeros(n)
    pitched = np.zeros(n)
    drums = np.zeros(n)
    for t in tracks:
        onsets = np.asarray(t.get("onsets", []), dtype=float)
        idx = np.clip((onsets / BIN_S).astype(int), 0, n - 1)
        if t.get("is_drum"):
            np.add.at(drums, idx, 1)
            continue
        np.add.at(pitched, idx, 1)
        if t.get("role") in ("lead", "lead_candidate"):
            for start, end in t.get("spans", []):  # a held solo note keeps the bins "on"
                a, b = int(start / BIN_S), int(np.ceil(end / BIN_S))
                lead[max(0, a):min(n, max(a + 1, b))] = 1
    return SongActivity(lead=lead, pitched=pitched, drums=drums)


def classify(start: float, end: float, act: SongActivity, chords: list[dict[str, Any]]) -> Optional[dict[str, str]]:
    """{"name", "why"} for an instrumental stretch, or None when there is nothing to go on."""
    if end - start < MIN_STRETCH_S or not len(act.pitched):
        return None
    a, b = int(start / BIN_S), max(int(start / BIN_S) + 1, int(np.ceil(end / BIN_S)))
    lead_cover = float(act.lead[a:b].mean()) if b > a else 0.0
    if lead_cover >= SOLO_LEAD_COVER:
        return {"name": "Solo", "why": f"guitarra solo tocando {round(lead_cover * 100)}% do trecho"}

    typ_drums = act.typical(act.drums)
    typ_all = act.typical(act.pitched + act.drums)
    drums = float(act.drums[a:b].mean())
    activity = float((act.pitched[a:b] + act.drums[a:b]).mean())
    inside = {c["label"] for c in chords if c["end"] > start + 0.3 and c["start"] < end - 0.3 and c.get("label") not in (None, "N")}
    if (typ_drums and drums >= BREAKDOWN_DRUMS * typ_drums and lead_cover <= BREAKDOWN_MAX_LEAD
            and 0 < len(inside) <= BREAKDOWN_MAX_CHORDS):
        chords_txt = " e ".join(sorted(inside))
        return {"name": "Breakdown", "why": f"bateria cheia, sem solo, harmonia parada em {chords_txt}"}
    if typ_all and activity <= QUIET_ACTIVITY * typ_all:
        return {"name": "Interlúdio", "why": "trecho bem mais calmo que o resto da música"}
    return None
