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
from typing import Any, Optional

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

    device = "cuda" if torch.cuda.is_available() else "cpu"
    attempts = [(model_name, device)]
    if device == "cuda":
        attempts += [("small", "cuda"), ("small", "cpu")]
    last_exc: Optional[Exception] = None
    for name, dev in attempts:
        model = None
        try:
            if dev == "cuda":
                torch.cuda.empty_cache()
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
