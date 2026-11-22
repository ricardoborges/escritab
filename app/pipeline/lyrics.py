"""Lyrics: the user's text aligned to the vocals with Whisper word timestamps, and the chord sheet.

The user pastes the lyrics (text and line breaks are right, timing unknown); Whisper on the vocals
stem hears the words with timestamps (timing right, text often wrong when sung). A global alignment
(Needleman-Wunsch with fuzzy word similarity) gives each pasted word the time of the word Whisper
heard; words without a match (screams, growls, misheard words) are placed between their neighbours.
The chord sheet puts every chord change of the harmony analysis above the word sung at that moment.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Callable, Optional

import numpy as np

WHISPER_SR = 16000
SECTION_RE = re.compile(r"^\s*\[([^\]]{1,60})\]\s*$")
SECTION_PT = [
    ("pre-chorus", "Pré-refrão"), ("pre chorus", "Pré-refrão"), ("prechorus", "Pré-refrão"),
    ("post-chorus", "Pós-refrão"), ("chorus", "Refrão"), ("refrain", "Refrão"), ("hook", "Refrão"),
    ("verse", "Verso"), ("bridge", "Ponte"), ("intro", "Intro"), ("outro", "Outro"),
    ("interlude", "Interlúdio"), ("instrumental", "Instrumental"), ("guitar solo", "Solo"),
    ("solo", "Solo"), ("breakdown", "Breakdown"), ("break", "Break"), ("end", "Final"),
]

# alignment scores
MATCH_EXACT = 2.0
MATCH_CLOSE = 1.2
MATCH_WEAK = 0.3
MISMATCH = -1.0
GAP = -0.4
WORD_PACE_S = 0.35          # duration given to a word placed without a match
MAX_PACE_S = 1.2            # slower than this, unmatched words hug their anchor instead of spreading
LINE_MAX_GAP_S = 6.0        # two words of one sung line are never this far apart
INSTRUMENTAL_MIN_S = 4.0    # a gap between sung lines longer than this gets its own chords line
CHORDS_PER_ROW = 8


class LyricsError(Exception):
    pass


# ---------- text ----------

def norm_word(w: str) -> str:
    """Lowercase, no accents, letters and digits only: "Coração," -> "coracao"."""
    w = unicodedata.normalize("NFKD", w.lower())
    return "".join(ch for ch in w if ch.isalnum() and not unicodedata.combining(ch))


def section_label(tag: str) -> str:
    t = tag.strip()
    low = t.lower()
    for key, pt in SECTION_PT:
        if low.startswith(key):
            rest = t[len(key):].strip(" :-")
            return f"{pt} {rest}".strip()
    return t


def parse_lyrics(text: str) -> list[dict[str, Any]]:
    """Pasted lyrics -> sung lines: {section, new_section, para, words: [str]}.

    "[Verse 1]" style tags name the section of the lines below them; blank lines mark paragraphs."""
    lines: list[dict[str, Any]] = []
    section: Optional[str] = None
    new_section = False
    para = False
    for raw in text.replace("\r\n", "\n").split("\n"):
        m = SECTION_RE.match(raw)
        if m:
            section, new_section = section_label(m.group(1)), True
            continue
        words = raw.split()
        if not words:
            para = bool(lines)
            continue
        lines.append({"section": section, "new_section": new_section, "para": para and not new_section, "words": words})
        new_section = para = False
    return lines


# ---------- Whisper ----------

def transcribe_words(
    audio: np.ndarray, model_name: str = "turbo", language: Optional[str] = None, prompt: Optional[str] = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Whisper with word timestamps on 16 kHz mono audio -> ([{text, start, end, prob}], info).

    The model is released afterwards: the GPU is shared with Demucs and Dettato."""
    import torch
    import whisper

    from app.pipeline import model_downloads

    device ="cuda" if torch.cuda.is_available() else "cpu"
    attempts = [(model_name, device)]
    if device == "cuda":
        attempts += [("small", "cuda"), ("small", "cpu")]
    last_exc: Optional[Exception] = None
    for name, dev in attempts:
        model = None
        try:
            if dev == "cuda":
                torch.cuda.empty_cache()
            model_downloads.ensure_whisper(name)  # first run: download with progress for the UI
            model = whisper.load_model(name, device=dev)
            result = model.transcribe(
                audio.astype(np.float32),
                language=language or None,
                word_timestamps=True,
                condition_on_previous_text=False,
                initial_prompt=prompt or None,
                hallucination_silence_threshold=2.0,
                fp16=(dev == "cuda"),
                verbose=None,
            )
        except torch.cuda.OutOfMemoryError as exc:  # try a smaller model / the CPU
            last_exc = exc
            continue
        finally:
            del model
            if dev == "cuda":
                torch.cuda.empty_cache()
        words = [
            {"text": w["word"].strip(), "start": float(w["start"]), "end": float(w["end"]),
             "prob": round(float(w.get("probability", 0.0)), 3)}
            for seg in result.get("segments", [])
            for w in seg.get("words", [])
            if w["word"].strip()
        ]
        segments = [
            {"start": float(s["start"]), "end": float(s["end"]), "words": [w["word"].strip() for w in s.get("words", [])]}
            for s in result.get("segments", [])
        ]
        return words, {"model": name, "device": dev, "language": result.get("language"), "segments": segments}
    raise LyricsError(f"Sem memória de GPU para o Whisper: {last_exc}")


# ---------- alignment ----------

def align_tokens(a: list[str], b: list[str]) -> list[Optional[int]]:
    """Global alignment of lyric tokens a to heard tokens b: for each a[i], the matched index in b."""
    n, m = len(a), len(b)
    if not n or not m:
        return [None] * n
    cache: dict[tuple[str, str], float] = {}

    def sim(x: str, y: str) -> float:
        if not x or not y:
            return MISMATCH
        if x == y:
            return MATCH_EXACT
        key = (x, y)
        if key not in cache:
            r = SequenceMatcher(None, x, y).ratio()
            cache[key] = MATCH_CLOSE if r >= 0.75 else MATCH_WEAK if r >= 0.5 else MISMATCH
        return cache[key]

    score = np.zeros((n + 1, m + 1))
    move = np.zeros((n + 1, m + 1), dtype=np.int8)  # 0 diag, 1 up (skip a), 2 left (skip b)
    score[1:, 0] = GAP * np.arange(1, n + 1)
    score[0, 1:] = GAP * np.arange(1, m + 1)
    move[1:, 0] = 1
    move[0, 1:] = 2
    for i in range(1, n + 1):
        ai = a[i - 1]
        prev, cur = score[i - 1], score[i]
        for j in range(1, m + 1):
            d = prev[j - 1] + sim(ai, b[j - 1])
            u = prev[j] + GAP
            left = cur[j - 1] + GAP
            # on a tie, skip this lyric word rather than match it: with a repeated word ("line ...
            # line") the earlier occurrence gets the match and the words after it are then placed
            # next to their anchors instead of being squeezed in between
            if d > u and d >= left:
                cur[j], move[i, j] = d, 0
            elif u >= left:
                cur[j], move[i, j] = u, 1
            else:
                cur[j], move[i, j] = left, 2
    out: list[Optional[int]] = [None] * n
    i, j = n, m
    while i > 0 and j > 0:
        mv = move[i, j]
        if mv == 0:
            if sim(a[i - 1], b[j - 1]) > 0:
                out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif mv == 1:
            i -= 1
        else:
            j -= 1
    return out


def _drop_line_outliers(words: list[dict[str, Any]], line_of: list[int]) -> int:
    """Within a line, matches far from the rest (a word Whisper "heard" during a solo) are
    unmatched, so _fill_times puts them next to their line. Keeps the biggest cluster of
    matches (the first one on a tie). Returns how many matches were dropped. In place."""
    dropped = 0
    by_line: dict[int, list[int]] = {}
    for i, li in enumerate(line_of):
        if words[i]["matched"]:
            by_line.setdefault(li, []).append(i)
    for idx in by_line.values():
        clusters = [[idx[0]]]
        for prev, cur in zip(idx, idx[1:]):
            if words[cur]["start"] - words[prev]["end"] > LINE_MAX_GAP_S:
                clusters.append([cur])
            else:
                clusters[-1].append(cur)
        if len(clusters) == 1:
            continue
        keep = max(clusters, key=len)
        for c in clusters:
            if c is not keep:
                for i in c:
                    words[i]["matched"] = False
                    dropped += 1
    return dropped


def repair_lines(lines: list[dict[str, Any]]) -> bool:
    """Saved timed lines with the outlier fix applied (for lyrics aligned before it). In place;
    True when something changed."""
    flat = [w for line in lines for w in line["words"]]
    line_of = [li for li, line in enumerate(lines) for _ in line["words"]]
    if not flat or not any(w["matched"] for w in flat) or not _drop_line_outliers(flat, line_of):
        return False
    _fill_times(flat, line_of)
    for line in lines:
        for w in line["words"]:
            w["start"], w["end"] = round(w["start"], 3), round(w["end"], 3)
        ws = line["words"]
        line["start"], line["end"] = ws[0]["start"], ws[-1]["end"]
        line["matched_share"] = round(sum(w["matched"] for w in ws) / len(ws), 2)
    return True


def _fill_times(
    words: list[dict[str, Any]], line_of: list[int]
) -> None:
    """Give unmatched words a time between their matched neighbours (in place)."""
    n = len(words)
    anchors = [i for i, w in enumerate(words) if w["matched"]]
    if not anchors:
        raise LyricsError("Nenhuma palavra da letra foi reconhecida na voz. Confira se a letra é desta música.")
    # runs of unmatched words: (first, last, prev anchor, next anchor)
    i = 0
    while i < n:
        if words[i]["matched"]:
            i += 1
            continue
        j = i
        while j + 1 < n and not words[j + 1]["matched"]:
            j += 1
        prev = i - 1 if i > 0 else None
        nxt = j + 1 if j + 1 < n else None
        run = list(range(i, j + 1))
        if prev is None:
            t = words[nxt]["start"] - WORD_PACE_S * len(run)
            for k in run:
                words[k]["start"], words[k]["end"] = max(0.0, t), max(0.0, t + WORD_PACE_S)
                t += WORD_PACE_S
        elif nxt is None:
            t = words[prev]["end"]
            for k in run:
                words[k]["start"], words[k]["end"] = t, t + WORD_PACE_S
                t += WORD_PACE_S
        else:
            t0, t1 = words[prev]["end"], words[nxt]["start"]
            if t1 - t0 <= MAX_PACE_S * len(run):
                weights = np.array([max(1, len(words[k]["text"])) for k in run], dtype=float)
                edges = t0 + (t1 - t0) * np.concatenate([[0], np.cumsum(weights) / weights.sum()])
                for idx, k in enumerate(run):
                    words[k]["start"], words[k]["end"] = float(edges[idx]), float(edges[idx + 1])
            else:
                # a long gap (solo, instrumental): words of the previous anchor's line follow it,
                # the others are sung just before the next anchor
                after = [k for k in run if line_of[k] == line_of[prev]]
                before = [k for k in run if k not in after]
                t = t0
                for k in after:
                    words[k]["start"], words[k]["end"] = t, t + WORD_PACE_S
                    t += WORD_PACE_S
                t = t1 - WORD_PACE_S * len(before)
                for k in before:
                    words[k]["start"], words[k]["end"] = t, t + WORD_PACE_S
                    t += WORD_PACE_S
        i = j + 1
    for k in range(1, n):  # keep the order
        if words[k]["start"] < words[k - 1]["start"]:
            words[k]["start"] = words[k - 1]["start"]
        words[k]["end"] = max(words[k]["end"], words[k]["start"] + 0.05)


def align_lyrics(text: str, heard: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pasted lyrics + Whisper words -> timed lines {section, new_section, para, start, end, words, matched_share}."""
    parsed = parse_lyrics(text)
    if not parsed:
        raise LyricsError("A letra está vazia.")
    flat: list[dict[str, Any]] = []
    line_of: list[int] = []
    for li, line in enumerate(parsed):
        for w in line["words"]:
            flat.append({"text": w, "start": 0.0, "end": 0.0, "matched": False})
            line_of.append(li)
    mapping = align_tokens([norm_word(w["text"]) for w in flat], [norm_word(h["text"]) for h in heard])
    for w, j in zip(flat, mapping):
        if j is not None:
            w["start"], w["end"], w["matched"] = heard[j]["start"], heard[j]["end"], True
    _drop_line_outliers(flat, line_of)
    _fill_times(flat, line_of)
    lines = []
    for li, line in enumerate(parsed):
        ws = [
            {"text": w["text"], "start": round(w["start"], 3), "end": round(w["end"], 3), "matched": w["matched"]}
            for w, l in zip(flat, line_of) if l == li
        ]
        lines.append({
            "section": line["section"], "new_section": line["new_section"], "para": line["para"],
            "start": ws[0]["start"], "end": ws[-1]["end"], "words": ws,
            "matched_share": round(sum(w["matched"] for w in ws) / len(ws), 2),
        })
    return lines


def lines_from_whisper(info: dict[str, Any], heard: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """No lyrics pasted: one line per Whisper segment (a draft to correct)."""
    lines = []
    k = 0
    for seg in info.get("segments", []):
        ws = []
        for _ in seg["words"]:
            if k >= len(heard):
                break
            h = heard[k]
            ws.append({"text": h["text"], "start": round(h["start"], 3), "end": round(h["end"], 3), "matched": True})
            k += 1
        if ws:
            lines.append({"section": None, "new_section": False, "para": False, "start": ws[0]["start"],
                          "end": ws[-1]["end"], "words": ws, "matched_share": 1.0})
    return lines


def lines_to_text(lines: list[dict[str, Any]]) -> str:
    """Timed lines back to editable text (with the section tags)."""
    out: list[str] = []
    for line in lines:
        if line.get("new_section") and line.get("section"):
            if out:
                out.append("")
            out.append(f"[{line['section']}]")
        elif line.get("para"):
            out.append("")
        out.append(" ".join(w["text"] for w in line["words"]))
    return "\n".join(out)


# ---------- song structure ----------

PART_GAP_S = 8.0          # a pause in the singing this long between blocks is an instrumental part
TAGGED_GAP_S = 2.0        # ... or this long when an empty tag ([Solo], [Intro]) names it
CONFIRMED_GAP_S = 3.5     # ... or this long when the music says it is a solo / breakdown (classify)
UNTAGGED_SPLIT_S = 3.0    # no tags nor blank lines: a pause this long between lines starts a new block
LEAD_IN_S = 0.3           # a section starts a little before its first sung word
SNAP_BEFORE_S = 1.5       # a chord change this close before the first word is the section's start
SNAP_PICKUP_S = 0.25      # ... or this close after it (the word was sung just ahead of the beat)
REPEAT_SIM = 0.75         # blocks this similar (text) are the same section: the chorus
MIN_SECTION_S = 0.5
INTRO_MIN_S = 2.0         # the voice starting this late: what comes before it is the intro


def _outline(text: str) -> list[tuple[Optional[str], int]]:
    """Tags in order with the number of sung lines under each (0 = an instrumental tag like [Solo]).
    Lines before the first tag come as (None, n)."""
    out: list[list[Any]] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        m = SECTION_RE.match(raw)
        if m:
            out.append([section_label(m.group(1)), 0])
        elif raw.split():
            if not out:
                out.append([None, 0])
            out[-1][1] += 1
    return [(name, n) for name, n in out]


def _blocks(text: str, lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Sung lines grouped into blocks {name, lines, gap_names} (+ empty tags after the last block).

    By the tags when there are any, else by blank lines, else by pauses in the singing."""
    outline = _outline(text) if text else []
    if any(name for name, _ in outline) and sum(n for _, n in outline) == len(lines):
        blocks: list[dict[str, Any]] = []
        pending: list[str] = []
        k = 0
        for name, n in outline:
            if n == 0:
                pending.append(name)
                continue
            blocks.append({"name": name, "lines": lines[k : k + n], "gap_names": pending})
            pending, k = [], k + n
        return blocks, pending
    groups: list[list[dict[str, Any]]] = []
    for i, line in enumerate(lines):
        if not groups or line.get("para") or line.get("new_section"):
            groups.append([line])
        else:
            groups[-1].append(line)
    if len(groups) == 1 and len(lines) > 4:  # one long run (a Whisper draft): split at the pauses
        groups = [[lines[0]]]
        for prev, line in zip(lines, lines[1:]):
            if line["start"] - prev["end"] >= UNTAGGED_SPLIT_S:
                groups.append([line])
            else:
                groups[-1].append(line)
    return [{"name": None, "lines": g, "gap_names": []} for g in groups], []


def _block_text(block: dict[str, Any]) -> str:
    return " ".join(norm_word(w["text"]) for line in block["lines"] for w in line["words"])


def _name_blocks(blocks: list[dict[str, Any]]) -> None:
    """Untagged blocks: like a tagged block -> its name; repeated -> "Refrão"; the rest "Verso N"."""
    texts = [_block_text(b) for b in blocks]

    def similar(i: int, j: int) -> bool:
        return SequenceMatcher(None, texts[i], texts[j], autojunk=False).ratio() >= REPEAT_SIM

    for b in blocks:
        b["source"] = "tag" if b["name"] else None
    tagged = [i for i, b in enumerate(blocks) if b["name"]]
    verse = 0
    for i, b in enumerate(blocks):
        if b["name"]:
            continue
        twin = next((j for j in tagged if similar(i, j)), None)
        if twin is not None:
            b["name"], b["source"] = blocks[twin]["name"], "repeat"
        elif any(similar(i, j) for j in range(len(blocks)) if j != i and not blocks[j]["source"] == "tag"):
            b["name"], b["source"] = "Refrão", "repeat"
        else:
            verse += 1
            b["name"], b["source"] = f"Verso {verse}", "guess"
    if verse == 1:  # a single guessed verse needs no number
        for b in blocks:
            if b["source"] == "guess":
                b["name"] = "Verso"


def _snap(t: float, lo: float, hi: float, changes: list[float]) -> Optional[float]:
    inside = [c for c in changes if lo <= c <= hi]
    return min(inside, key=lambda c: abs(c - t)) if inside else None


def retag_lines(text: str, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The timed lines with the section tags / stanza breaks of ``text`` (same words, new tags).

    Raises LyricsError when the words changed: then the lyrics must be aligned again."""
    parsed = parse_lyrics(text)
    same = len(parsed) == len(lines) and all(
        [norm_word(w) for w in p["words"]] == [norm_word(w["text"]) for w in line["words"]]
        for p, line in zip(parsed, lines)
    )
    if not same:
        raise LyricsError("As palavras da letra mudaram: alinhe a letra de novo com a voz.")
    return [{**line, "section": p["section"], "new_section": p["new_section"], "para": p["para"]}
            for p, line in zip(parsed, lines)]


def infer_sections(
    text: str, lines: list[dict[str, Any]], chords: list[dict[str, Any]], duration: float,
    classify: Optional[Callable[[float, float], Optional[dict[str, str]]]] = None,
) -> list[dict[str, Any]]:
    """The song's parts from the timed lyrics: [{name, start, end, kind, source}] covering 0..duration.

    kind: "sung" or "instrumental"; source: "tag" (named by the lyrics), "repeat" (same text as
    another block: the chorus), "gap" (a long pause in the singing), "guess" (an untagged block) or
    "inferred" (an untagged instrumental stretch that ``classify(start, end)`` named, e.g. "Solo";
    it then also carries ``why``)."""
    if not lines:
        return []
    blocks, tail_names = _blocks(text, lines)
    _name_blocks(blocks)
    changes = sorted({round(c["start"], 3) for c in chords if c.get("start", 0) > 0})
    duration = max(duration, lines[-1]["end"])
    index_of = {id(line): i for i, line in enumerate(lines)}
    for b in blocks:
        b["first"], b["last"] = b["lines"][0]["start"], b["lines"][-1]["end"]
        b["range"] = [index_of[id(b["lines"][0])], index_of[id(b["lines"][-1])] + 1]

    def sung(b: dict[str, Any], start: float) -> dict[str, Any]:
        """A sung section; ``lines`` = [first, last + 1] indices of its lyric lines."""
        return {"name": b["name"], "kind": "sung", "source": b["source"], "start": start, "lines": b["range"]}

    def start_of(b: dict[str, Any], floor: float) -> float:
        t = b["first"]
        snapped = _snap(t, max(floor, t - SNAP_BEFORE_S), t + SNAP_PICKUP_S, changes)
        return snapped if snapped is not None else max(floor, t - LEAD_IN_S)

    def tail_of(b: dict[str, Any], ceil: float) -> float:
        t = b["last"]
        snapped = _snap(t, t, min(ceil, t + SNAP_BEFORE_S), changes)
        return snapped if snapped is not None else min(ceil, t + 0.5)

    def is_part(gap: float, names: list[str]) -> bool:
        return gap >= (TAGGED_GAP_S if names else PART_GAP_S)

    def instrumental(names: list[str], default: str) -> dict[str, Any]:
        return {"name": " / ".join(names) or default, "kind": "instrumental", "source": "tag" if names else "gap"}

    sections: list[dict[str, Any]] = []
    first = blocks[0]
    sung_intro = (first["name"] or "").lower().startswith("intro")  # the lyrics call the first block the intro
    if not sung_intro and (first["first"] >= INTRO_MIN_S or is_part(first["first"], first["gap_names"])):
        sections.append({**instrumental(first["gap_names"], "Intro"), "start": 0.0})
        sections.append(sung(first, start_of(first, 0.0)))
    else:
        sections.append(sung(first, 0.0))
    for cur, nxt in zip(blocks, blocks[1:]):
        gap = nxt["first"] - cur["last"]
        confirmed = (not nxt["gap_names"] and classify is not None and CONFIRMED_GAP_S <= gap < PART_GAP_S
                     and classify(cur["last"], nxt["first"]) is not None)
        if confirmed or is_part(gap, nxt["gap_names"]):
            sections.append({**instrumental(nxt["gap_names"], "Instrumental"), "start": tail_of(cur, nxt["first"]),
                             "nameless": not nxt["gap_names"]})
            floor = sections[-1]["start"]
        else:
            floor = cur["last"]
        sections.append(sung(nxt, start_of(nxt, floor)))
    last = blocks[-1]
    if is_part(duration - last["last"], tail_names):
        sections.append({**instrumental(tail_names, "Outro"), "start": tail_of(last, duration)})
    for s, nxt in zip(sections, sections[1:] + [None]):
        s["end"] = nxt["start"] if nxt else duration
        s["start"], s["end"] = round(s["start"], 2), round(s["end"], 2)
    sections = [s for s in sections if round(s["end"] - s["start"], 3) >= MIN_SECTION_S]
    # untagged instrumental stretches in the middle: Solo / Breakdown / ... when the music says so
    for s in sections:
        if s.pop("nameless", False) and classify:
            guess = classify(s["start"], s["end"])
            if guess:
                s.update(name=guess["name"], source="inferred", why=guess["why"])
    # the same name more than once (Refrão, Refrão): number the occurrences
    counts: dict[str, int] = {}
    for s in sections:
        counts[s["name"]] = counts.get(s["name"], 0) + 1
    seen: dict[str, int] = {}
    for s in sections:
        if counts[s["name"]] > 1:
            seen[s["name"]] = seen.get(s["name"], 0) + 1
            s["name"] = f"{s['name']} ({seen[s['name']]})"
    return sections


# ---------- voice onsets (Whisper starts a line too early after a pause) ----------

ONSET_HOP_S = 0.01
ONSET_SILENCE_S = 0.2       # this much quiet before a voiced frame makes it an onset
ONSET_DB_BELOW_PEAK = 32.0  # "voiced": within this many dB of the loud part of the vocals
ONSET_LOOK_BEFORE_S = 0.4   # an onset may sit this much before Whisper's start...
ONSET_LOOK_AFTER_S = 1.6    # ...or this much after it


def voice_activity(vocals: np.ndarray, sr: int) -> dict[str, Any]:
    """{"onsets": times where the voice comes back after a pause, "voiced": per-frame bool,
    "hop": frame length in s}, from the loudness of the vocals."""
    import librosa

    hop = max(1, int(sr * ONSET_HOP_S))
    rms = librosa.feature.rms(y=vocals, frame_length=hop * 4, hop_length=hop)[0]
    if not len(rms) or float(rms.max()) <= 0:
        return {"onsets": [], "voiced": np.zeros(0, bool), "hop": hop / sr}
    db = librosa.amplitude_to_db(rms, ref=float(np.percentile(rms, 98)) or float(rms.max()))
    voiced = db > -ONSET_DB_BELOW_PEAK
    need = int(ONSET_SILENCE_S / ONSET_HOP_S)
    onsets, quiet = [], need  # the song start counts as a pause
    for i, v in enumerate(voiced):
        if v:
            if quiet >= need:
                onsets.append(round(i * hop / sr, 3))
            quiet = 0
        else:
            quiet += 1
    return {"onsets": onsets, "voiced": voiced, "hop": hop / sr}


ONSET_MAX_VOICED_SKIP = 0.15  # the stretch skipped over must be (almost) silent


def refine_line_starts(lines: list[dict[str, Any]], activity: dict[str, Any]) -> int:
    """Moves a line's first words to the voice onset when Whisper put them in the silence before
    it (typical after an instrumental stretch). Only when the time skipped over is silent in the
    vocals, so a pause inside a sung phrase is never mistaken for the start. In place; returns how
    many lines moved."""
    onsets, voiced, hop = activity["onsets"], activity["voiced"], activity["hop"]
    moved = 0
    prev_end = 0.0
    for line in lines:
        start = line["start"]
        cand = [o for o in onsets if start - ONSET_LOOK_BEFORE_S <= o <= start + ONSET_LOOK_AFTER_S and o >= prev_end]
        onset = min(cand, key=lambda o: (o < start, abs(o - start))) if cand else None  # prefer the one after
        if onset is not None and onset > start + 0.05:
            a, b = int(start / hop), int(onset / hop)
            skipped = voiced[a:b]
            if len(skipped) and float(skipped.mean()) <= ONSET_MAX_VOICED_SKIP:
                for w in line["words"]:
                    if w["start"] >= onset:
                        break
                    w["start"] = onset
                    w["end"] = max(w["end"], onset + 0.1)
                for x, y in zip(line["words"], line["words"][1:]):  # keep the words in order
                    if y["start"] < x["end"]:
                        y["start"] = x["end"]
                        y["end"] = max(y["end"], y["start"] + 0.05)
                line["start"] = line["words"][0]["start"]
                line["end"] = max(line["end"], line["words"][-1]["end"])
                moved += 1
        prev_end = line["end"]
    return moved


# ---------- a stanza moved into its part (the user changed the part's bounds) ----------

FIT_MIN_SPAN_S = 0.6  # the stretch the words are squeezed into is at least this long
FIT_TOLERANCE_S = 0.35  # a pickup sung this little before the part (or a tail after it) stays


def _remap_times(lines: list[dict[str, Any]], a: float, b: float, a2: float, b2: float) -> None:
    """Every word / line time in [a, b] mapped linearly onto [a2, b2] (in place)."""
    scale = (b2 - a2) / (b - a) if b > a else 0.0

    def f(t: float) -> float:
        return round(a2 + (t - a) * scale, 3) if a - 1e-6 <= t <= b + 1e-6 else t

    for line in lines:
        for w in line["words"]:
            w["start"], w["end"] = f(w["start"]), f(w["end"])
        line["start"], line["end"] = line["words"][0]["start"], line["words"][-1]["end"]


def fit_lines_to_range(lines: list[dict[str, Any]], first: int, last: int, start: float, end: float) -> int:
    """Pulls the lyric lines ``lines[first:last]`` (one stanza) inside [start, end]: the words sung
    before ``start`` are squeezed into the beginning of the range, the ones after ``end`` into its
    end; the words already inside keep their time. In place; returns how many lines changed."""
    block = lines[first:last]
    if not block or end - start < FIT_MIN_SPAN_S:
        return 0
    before = [l for l in block if l["start"] < start - 1e-3]
    after = [l for l in block if l["end"] > end + 1e-3]
    if before and before[0]["start"] >= start - FIT_TOLERANCE_S:
        before = []
    if after and after[-1]["end"] <= end + FIT_TOLERANCE_S:
        after = []
    if before:
        # squeeze [first word, b] onto [start, b]; b = end of the last early line (>= start + min span)
        nxt = next((l["start"] for l in block if l["start"] >= start - 1e-3), None)
        b = max(before[-1]["end"], start + FIT_MIN_SPAN_S)
        if nxt is not None:
            b = min(b, nxt)
        b = min(max(b, start + 0.1), end)
        _remap_times(before, before[0]["start"], b, start, b)
    if after:
        prv = next((l["end"] for l in reversed(block) if l["end"] <= end + 1e-3), None)
        a = min(after[0]["start"], end - FIT_MIN_SPAN_S)
        if prv is not None:
            a = max(a, prv)
        a = max(min(a, end - 0.1), start)
        _remap_times(after, a, after[-1]["end"], a, end)
    return len({id(l) for l in before + after})


# ---------- chord sheet ----------

def _chord_fn(c: dict[str, Any], field: list[dict[str, Any]]) -> str:
    if not c.get("diatonic") or not c.get("degree"):
        return "X"
    return field[c["degree"] - 1]["function"]


def _sheet_chord(c: dict[str, Any], field: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"label": c["label"], "numeral": c["numeral"], "fn": _chord_fn(c, field), "start": c["start"], **extra}


def _chord_rows(chords: list[dict[str, Any]], field, start: float, end: float) -> list[dict[str, Any]]:
    """Chords sounding in [start, end) as rows of a chords-only (instrumental) block."""
    inside = [c for c in chords if c["end"] > start + 0.3 and c["start"] < end - 0.3]
    picked: list[dict[str, Any]] = []
    for c in inside:
        if not picked or picked[-1]["label"] != c["label"]:
            picked.append(_sheet_chord(c, field, start=max(c["start"], start)))
    rows = []
    for k in range(0, len(picked), CHORDS_PER_ROW):
        row = picked[k : k + CHORDS_PER_ROW]
        row_end = picked[k + CHORDS_PER_ROW]["start"] if k + CHORDS_PER_ROW < len(picked) else end
        rows.append({"kind": "chords", "start": row[0]["start"], "end": row_end, "chords": row})
    return rows


def chord_sheet(
    lines: list[dict[str, Any]], chords: list[dict[str, Any]], field: list[dict[str, Any]], duration: float
) -> list[dict[str, Any]]:
    """Sheet items in time order: section headers, chords-only rows and lyric lines with chords above.

    Each lyric chord has `pos`, the character column (in the line text = words joined by spaces)."""
    items: list[dict[str, Any]] = []
    chords = sorted(chords, key=lambda c: c["start"])

    def chord_at(t: float) -> Optional[dict[str, Any]]:
        for c in chords:
            if c["start"] <= t < c["end"]:
                return c
        return None

    def gap_rows(t0: float, t1: float) -> None:
        if t1 - t0 >= INSTRUMENTAL_MIN_S:
            items.extend(_chord_rows(chords, field, t0, t1))

    cursor = 0.0
    for li, line in enumerate(lines):
        nxt_start = lines[li + 1]["start"] if li + 1 < len(lines) else duration
        if line.get("new_section") and line.get("section"):
            gap_rows(cursor, line["start"])
            items.append({"kind": "section", "name": line["section"], "start": line["start"]})
        else:
            gap_rows(cursor, line["start"])
        offsets, text_parts, pos = [], [], 0
        for w in line["words"]:
            offsets.append(pos)
            text_parts.append(w["text"])
            pos += len(w["text"]) + 1
        text = " ".join(text_parts)
        window_end = min(nxt_start, line["end"] + 1.5)
        placed: list[dict[str, Any]] = []

        def place(c: dict[str, Any], t: float) -> None:
            ws = line["words"]
            if t > ws[-1]["end"] + 0.3:
                col = len(text) + 1
            else:
                idx = 0
                for k, w in enumerate(ws):
                    if w["start"] <= t + 0.12:
                        idx = k
                col = offsets[idx]
            if placed:
                if placed[-1]["label"] == c["label"]:
                    return
                col = max(col, placed[-1]["pos"] + len(placed[-1]["label"]) + 1)
            placed.append(_sheet_chord(c, field, pos=col, start=round(t, 3)))

        first = chord_at(line["start"])
        changes = [c for c in chords if line["start"] < c["start"] < window_end]
        if first and not (changes and changes[0]["start"] - line["start"] < 0.3):
            place(first, line["start"])
        for c in changes:
            place(c, c["start"])
        items.append({
            "kind": "lyric", "start": line["start"], "end": max(line["end"], window_end if placed else line["end"]),
            "para": bool(line.get("para")), "text": text,
            "words": [{"offset": o, "len": len(w["text"]), "start": w["start"], "end": w["end"], "matched": w["matched"]}
                      for o, w in zip(offsets, line["words"])],
            "chords": placed, "matched_share": line.get("matched_share", 1.0),
        })
        cursor = window_end
    gap_rows(cursor, duration)
    return items
