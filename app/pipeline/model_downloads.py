"""Model weights downloaded on first use, with progress the UI can show.

Whisper, Demucs, Dettato and BS-RoFormer fetch their weights the first time they run (hundreds of
MB to ~1.6 GB). Each ``ensure_*`` below downloads them beforehand, into the same cache the library
reads from, while recording the progress in a process-wide registry; ``GET /api/downloads`` serves
it so the processing screen can show a download bar instead of a step that seems stuck.

The ``ensure_*`` helpers never raise: when something goes wrong here the library still downloads
the weights on its own (only without the progress bar).
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

CHUNK = 1 << 20
KEEP_FINISHED_S = 8.0  # a finished download stays listed for a moment, so the UI shows it at 100%

_lock = threading.Lock()
_downloads: dict[str, dict[str, Any]] = {}


# ---------- registry ----------

def _start(label: str, filename: str, total: Optional[int]) -> str:
    did = uuid.uuid4().hex[:12]
    with _lock:
        _downloads[did] = {
            "id": did, "label": label, "filename": filename, "downloaded": 0,
            "total": total or None, "started": time.time(), "finished": None, "error": None,
        }
    return did


def _progress(did: str, downloaded: int, total: Optional[int] = None) -> None:
    with _lock:
        d = _downloads.get(did)
        if d is not None:
            d["downloaded"] = int(downloaded)
            if total:
                d["total"] = int(total)


def _finish(did: str, error: Optional[str] = None) -> None:
    with _lock:
        d = _downloads.get(did)
        if d is not None:
            d["finished"] = time.time()
            d["error"] = error
            if error is None and d["total"]:
                d["downloaded"] = d["total"]


def active_downloads() -> list[dict[str, Any]]:
    """Running downloads plus the ones finished in the last few seconds, with the speed."""
    now = time.time()
    out = []
    with _lock:
        for did, d in list(_downloads.items()):
            if d["finished"] is not None and now - d["finished"] > KEEP_FINISHED_S:
                del _downloads[did]
                continue
            elapsed = max((d["finished"] or now) - d["started"], 1e-3)
            out.append({
                "id": d["id"], "label": d["label"], "filename": d["filename"],
                "downloaded": d["downloaded"], "total": d["total"],
                "done": d["finished"] is not None, "error": d["error"],
                "speed": d["downloaded"] / elapsed,  # bytes/s
            })
    return out


# ---------- plain HTTP download ----------

def download_file(
    url: str, dest: Path, label: str, check: Optional[Callable[[str], bool]] = None
) -> Path:
    """Streams ``url`` to ``dest`` (through a .part file) recording the progress.
    ``check(sha256_hex)`` validates the file before it takes the final name."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    did = _start(label, dest.name, None)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "escritab"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0) or None
            _progress(did, 0, total)
            sha = hashlib.sha256()
            done = 0
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                _progress(did, done)
        if check is not None and not check(sha.hexdigest()):
            raise RuntimeError(f"checksum mismatch for {dest.name}")
        tmp.replace(dest)
    except Exception as exc:
        _finish(did, str(exc))
        tmp.unlink(missing_ok=True)
        raise
    _finish(did)
    return dest


def _safely(what: str, fn: Callable[[], Any]) -> None:
    try:
        fn()
    except Exception as exc:  # the library downloads it on its own
        log.warning("pre-download of %s failed: %s", what, exc)


# ---------- Whisper ----------

def whisper_root() -> Path:
    default = os.path.join(os.path.expanduser("~"), ".cache")  # same as whisper.load_model
    return Path(os.getenv("XDG_CACHE_HOME", default)) / "whisper"


def ensure_whisper(name: str) -> None:
    def run() -> None:
        import whisper

        url = whisper._MODELS.get(name)
        if not url:
            return
        dest = whisper_root() / os.path.basename(url)
        if dest.is_file():
            return  # whisper verifies the checksum itself
        expected = url.split("/")[-2]
        download_file(url, dest, f"Whisper {name}", check=lambda h: h == expected)

    _safely(f"whisper {name}", run)


# ---------- Demucs ----------

def ensure_demucs(model_name: str) -> None:
    def run() -> None:
        import torch
        import yaml
        from demucs import pretrained

        bag_file = pretrained.REMOTE_ROOT / f"{model_name}.yaml"
        if not bag_file.is_file():
            return
        sigs = yaml.safe_load(bag_file.read_text())["models"]
        urls = pretrained._parse_remote_files(pretrained.REMOTE_ROOT / "files.txt")
        ckpt_dir = Path(torch.hub.get_dir()) / "checkpoints"
        for i, sig in enumerate(sigs):
            url = urls.get(sig)
            if not url:
                continue
            dest = ckpt_dir / os.path.basename(url)
            if dest.is_file():
                continue
            # torch.hub's check_hash: the part after the last "-" is a sha256 prefix
            prefix = dest.stem.rsplit("-", 1)[-1] if "-" in dest.stem else ""
            part = f" ({i + 1}/{len(sigs)})" if len(sigs) > 1 else ""
            download_file(url, dest, f"Demucs {model_name}{part}", check=lambda h, p=prefix: h.startswith(p))

    _safely(f"demucs {model_name}", run)


# ---------- Dettato (Hugging Face Hub) ----------

def _tracked_tqdm(label: str, filename: str):
    """A tqdm class for hf_hub_download that reports to the registry (and prints nothing)."""
    from tqdm.std import tqdm

    class Tracked(tqdm):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["disable"] = False
            kwargs["file"] = open(os.devnull, "w")  # noqa: SIM115 - closed in close()
            super().__init__(*args, **kwargs)
            self._did = _start(label, filename, self.total)

        def update(self, n: float = 1) -> Optional[bool]:
            r = super().update(n)
            _progress(self._did, int(self.n), int(self.total) if self.total else None)
            return r

        def close(self) -> None:
            if not self.disable:
                _finish(self._did)
                fh = self.fp
                super().close()
                try:
                    fh.close()
                except Exception:
                    pass

    return Tracked


def ensure_dettato() -> None:
    def run() -> None:
        if os.environ.get("DETTATO_WEIGHTS"):
            return
        from dettato.hub import REPOS
        from huggingface_hub import hf_hub_download, try_to_load_from_cache

        for stage, repo in REPOS.items():
            for filename in ("config.json", "model.safetensors"):
                cached = try_to_load_from_cache(repo, filename)
                if isinstance(cached, str):
                    continue
                label = f"Dettato ({stage})"
                hf_hub_download(repo, filename, tqdm_class=_tracked_tqdm(label, filename))

    _safely("dettato", run)
