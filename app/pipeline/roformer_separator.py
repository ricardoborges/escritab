"""Vocal / instrumental separation with BS-RoFormer (https://github.com/lucidrains/BS-RoFormer).

BS-RoFormer is the current state of the art for vocals: far less vocal bleed than Demucs. There is
no public BS-RoFormer that outputs a guitar stem, so it is chained in front of Demucs: RoFormer
removes the vocals from the mix, then htdemucs_6s splits the clean instrumental into guitar / bass
/ drums / piano / other. Vocal bleed in the guitar stem is what makes the monophonic lead engine
follow the singer's melody, so this matters most for solos.

Weights: viperx's public checkpoint (vocals SDR 12.97 on MUSDB18-HQ), trained with an earlier
version of lucidrains' code. The current package (1.4.x) wraps each attention / feed-forward block
in a hyper-connection (`.branch.` in parameter names) and adds a learned value-residual mix; with
one residual stream, renamed keys and the value-residual gate closed, the network is exactly the
one the weights were trained for.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

MODEL_NAME = "model_bs_roformer_ep_317_sdr_12.9755"
MODEL_URLS = {
    ".ckpt": "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/"
    f"{MODEL_NAME}.ckpt",
    ".yaml": "https://raw.githubusercontent.com/TRvlvr/application_data/main/mdx_model_data/"
    f"mdx_c_configs/{MODEL_NAME}.yaml",
}
SAMPLE_RATE = 44100
CHUNK = 352800          # 8 s, the training chunk size
NUM_OVERLAP = 2         # windows per chunk length (50%: half the time of 4, -30 dB difference)

_model: Any = None
_cfg: dict | None = None
_lock = threading.Lock()


def models_dir() -> Path:
    return Path(os.environ.get("ESCRITAB_MODELS", "workspace/models")) / "roformer"


def is_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("bs_roformer") is not None


def _ensure_files() -> tuple[Path, Path]:
    d = models_dir()
    d.mkdir(parents=True, exist_ok=True)
    paths = {}
    for ext, url in MODEL_URLS.items():
        p = d / f"{MODEL_NAME}{ext}"
        if not p.exists() or p.stat().st_size == 0:
            from app.pipeline import model_downloads

            log.info("downloading %s", url)
            model_downloads.download_file(url, p, f"BS-RoFormer ({ext[1:]})")
        paths[ext] = p
    return paths[".ckpt"], paths[".yaml"]


def _remap_state_dict(sd: dict, model_keys: set[str]) -> dict:
    """Checkpoint names (pre hyper-connection) -> current names: insert `.branch.` after each
    transformer block index when the plain name does not exist in the model."""
    out = {}
    for k, v in sd.items():
        if k not in model_keys:
            k = re.sub(r"(layers\.\d+\.\d+\.layers\.\d+\.\d+)\.", r"\1.branch.", k, count=1)
        out[k] = v
    return out


def build_model(model_cfg: dict, state_dict: dict) -> Any:
    """lucidrains BSRoformer with the architecture of the checkpoint (one residual stream), its
    weights under the current parameter names, and the value-residual gate closed."""
    import torch
    from bs_roformer import BSRoformer

    model = BSRoformer(**{**model_cfg, "num_residual_streams": 1})
    sd = _remap_state_dict(state_dict, set(model.state_dict()))
    missing, unexpected = model.load_state_dict(sd, strict=False)
    if unexpected or any("to_value_residual_mix" not in k for k in missing):
        raise RuntimeError(
            "BS-RoFormer checkpoint does not match the installed bs-roformer: "
            f"missing={missing[:4]} unexpected={unexpected[:4]}"
        )
    # close the value-residual gate (added after these weights were trained): sigmoid(-50) ~ 0
    # -> v.lerp(value_residual, 0) == v, the original attention
    with torch.no_grad():
        for name, mod in model.named_modules():
            if name.endswith("to_value_residual_mix"):
                mod.weight.zero_()
                mod.bias.fill_(-50.0)
    return model.eval()


def load_model(device: str | None = None) -> tuple[Any, dict]:
    global _model, _cfg
    with _lock:
        if _model is not None:
            return _model, _cfg
        import torch
        import yaml

        ckpt, cfg_path = _ensure_files()
        cfg = yaml.load(cfg_path.read_text(encoding="utf-8"), Loader=yaml.FullLoader)
        sd = torch.load(ckpt, map_location="cpu", weights_only=True)
        model = build_model(cfg["model"], sd.get("state_dict", sd))
        device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        _model = model.to(device).eval()
        _cfg = cfg
        log.info("BS-RoFormer loaded on %s", device)
        return _model, _cfg


def unload() -> None:
    """Free the GPU memory before Demucs runs (6 GB cards cannot hold both)."""
    global _model
    with _lock:
        if _model is None:
            return
        import torch

        _model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _window(n: int, fade: int) -> np.ndarray:
    w = np.ones(n, np.float32)
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    w[:fade] = ramp
    w[-fade:] = ramp[::-1]
    return w


def separate_vocals_array(mix: np.ndarray, progress=None) -> np.ndarray:
    """mix: (2, T) float32 at 44.1 kHz -> vocals (2, T). Overlap-add over 8 s windows."""
    import torch

    model, _ = load_model()
    device = next(model.parameters()).device
    total = mix.shape[1]
    step = CHUNK // NUM_OVERLAP
    fade = CHUNK // 10
    pad = CHUNK - step
    x = np.pad(mix, ((0, 0), (pad, pad + CHUNK)), mode="constant")
    out = np.zeros_like(x)
    weight = np.zeros(x.shape[1], np.float32)
    win = _window(CHUNK, fade)
    starts = range(0, x.shape[1] - CHUNK + 1, step)
    use_amp = device.type == "cuda"
    with torch.inference_mode():
        for i, s in enumerate(starts):
            chunk = torch.from_numpy(x[:, s:s + CHUNK]).to(device)[None]
            with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=use_amp):
                y = model(chunk)
            y = y[0].float().cpu().numpy()
            n = min(CHUNK, y.shape[-1])
            out[:, s:s + n] += y[:, :n] * win[:n]
            weight[s:s + n] += win[:n]
            if progress:
                progress(i + 1, len(starts))
    if device.type == "cuda":
        torch.cuda.empty_cache()
    out /= np.maximum(weight, 1e-6)
    return out[:, pad:pad + total]


def separate_vocals(input_path: Path, vocals_path: Path, instrumental_path: Path) -> None:
    """Write the vocals and the instrumental (mix - vocals) of an audio file, 44.1 kHz stereo."""
    import librosa
    import soundfile as sf

    mix, _ = librosa.load(str(input_path), sr=SAMPLE_RATE, mono=False)
    if mix.ndim == 1:
        mix = np.stack([mix, mix])
    mix = mix[:2].astype(np.float32)
    vocals = separate_vocals_array(mix)
    sf.write(str(vocals_path), vocals.T, SAMPLE_RATE)
    sf.write(str(instrumental_path), (mix - vocals).T, SAMPLE_RATE)
