"""Lyrics search on Genius (https://docs.genius.com).

The API searches songs and gives each one's page URL, but not the lyrics text: that is read
from the song page (the ``data-lyrics-container`` blocks), like the usual Genius clients do.
The token comes from ``GENIUS_TOKEN_ACCESS`` (environment or the project's ``.env``).
"""
from __future__ import annotations

import html
import os
import re
from html.parser import HTMLParser
from pathlib import Path

import httpx

API = "https://api.genius.com"
TIMEOUT = 15.0
# a browser-like agent: the song pages refuse obvious bots
PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class GeniusError(Exception):
    """Something the user should read (no token, Genius down, song without lyrics)."""


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_token() -> str | None:
    return os.environ.get("GENIUS_TOKEN_ACCESS") or _read_env_file(ENV_FILE).get("GENIUS_TOKEN_ACCESS") or None


def is_configured() -> bool:
    return bool(get_token())


def _api_get(path: str, params: dict | None = None) -> dict:
    token = get_token()
    if not token:
        raise GeniusError("Token do Genius não configurado (GENIUS_TOKEN_ACCESS no .env).")
    try:
        res = httpx.get(f"{API}{path}", params=params, timeout=TIMEOUT,
                        headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise GeniusError(f"Não foi possível falar com o Genius: {exc}") from exc
    if res.status_code == 401:
        raise GeniusError("O Genius recusou o token (GENIUS_TOKEN_ACCESS inválido ou expirado).")
    if res.status_code >= 400:
        raise GeniusError(f"O Genius respondeu com erro HTTP {res.status_code}.")
    return res.json().get("response", {})


def search(query: str, limit: int = 10) -> list[dict]:
    """Songs matching ``query`` (title and/or artist), best first."""
    query = query.strip()
    if not query:
        return []
    hits = _api_get("/search", {"q": query, "per_page": min(max(limit, 1), 20)}).get("hits", [])
    results = []
    for hit in hits:
        if hit.get("type") != "song":
            continue
        s = hit.get("result") or {}
        results.append({
            "id": s.get("id"),
            "title": s.get("title") or "",
            "artist": s.get("artist_names") or (s.get("primary_artist") or {}).get("name") or "",
            "full_title": s.get("full_title") or "",
            "thumbnail": s.get("song_art_image_thumbnail_url") or "",
            "url": s.get("url") or "",
            "year": ((s.get("release_date_components") or {}).get("year")),
            "lyrics_state": s.get("lyrics_state") or "",
        })
    return results[:limit]


class _LyricsParser(HTMLParser):
    """Text of every ``data-lyrics-container`` block, ``<br>`` as line breaks, skipping the
    blocks Genius marks ``data-exclude-from-selection`` (contributor count, "Lyrics" header...)."""

    VOID = {"br", "img", "input", "hr", "meta", "link", "source", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0          # open tags inside the current lyrics container (0 = outside)
        self.skip_depth = 0     # >0 while inside an excluded block
        self.blocks: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.depth == 0:
            if a.get("data-lyrics-container") == "true" and tag not in self.VOID:
                self.depth = 1
                self.blocks.append([])
            return
        if tag == "br":
            if not self.skip_depth:
                self.blocks[-1].append("\n")
            return
        if tag in self.VOID:
            return
        self.depth += 1
        if self.skip_depth:
            self.skip_depth += 1
        elif a.get("data-exclude-from-selection") == "true":
            self.skip_depth = 1

    def handle_endtag(self, tag):
        if self.depth == 0 or tag in self.VOID:
            return
        self.depth -= 1
        if self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.depth and not self.skip_depth:
            # newlines in the HTML source are just whitespace: only <br> breaks a line
            self.blocks[-1].append(re.sub(r"[\r\n]+", " ", data))


def extract_lyrics(page_html: str) -> str:
    parser = _LyricsParser()
    parser.feed(page_html)
    text = "\n".join("".join(block) for block in parser.blocks)
    text = html.unescape(text).replace(" ", " ").replace(" ", " ")
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # translated pages open with a title header ([Letra de "X"], [Paroles de « X »]): not a section
    text = re.sub(r'^\[[^\]\n]*["“«][^\]\n]*\]\n+', "", text)
    # a section header always starts a new stanza
    text = re.sub(r"(?<!\n)\n(\[[^\]\n]+\])", r"\n\n\1", text)
    return text


def fetch_lyrics(song_id: int) -> dict:
    """The song's lyrics as plain text ([Verse] headers kept), plus title and artist."""
    song = _api_get(f"/songs/{int(song_id)}").get("song") or {}
    url = song.get("url")
    if not url:
        raise GeniusError("Música não encontrada no Genius.")
    try:
        res = httpx.get(url, headers=PAGE_HEADERS, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise GeniusError(f"Não foi possível abrir a página da letra: {exc}") from exc
    if res.status_code >= 400:
        raise GeniusError(f"A página da letra respondeu com erro HTTP {res.status_code}.")
    text = extract_lyrics(res.text)
    if not text:
        if song.get("instrumental"):
            raise GeniusError("O Genius marca esta música como instrumental (sem letra).")
        raise GeniusError("Não encontrei a letra na página do Genius.")
    language = song.get("language")
    return {
        "id": song.get("id"),
        "title": song.get("title") or "",
        "artist": song.get("artist_names") or "",
        "url": url,
        "language": language if isinstance(language, str) else None,
        "text": text,
    }
