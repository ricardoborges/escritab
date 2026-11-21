"""Which compute device the neural models (Dettato, Demucs) will actually run on."""
from __future__ import annotations

import functools
import shutil
import subprocess
from typing import Any


def _nvidia_smi_gpu() -> tuple[str | None, int | None]:
    """Detect an NVIDIA GPU even when the installed PyTorch has no CUDA support."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None, None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip().splitlines()
        if not out:
            return None, None
        name, mem = [x.strip() for x in out[0].split(",")[:2]]
        return name, int(float(mem))
    except Exception:
        return None, None


@functools.lru_cache(maxsize=1)
def get_compute_info() -> dict[str, Any]:
    import torch

    cuda = bool(torch.cuda.is_available())
    info: dict[str, Any] = {
        "device": "cuda" if cuda else "cpu",
        "torch_version": torch.__version__,
        "torch_cuda": torch.version.cuda,  # None on CPU-only builds
        "gpu_name": None,
        "gpu_memory_mb": None,
        "hint": None,
    }
    if cuda:
        props = torch.cuda.get_device_properties(0)
        info["gpu_name"] = props.name
        info["gpu_memory_mb"] = int(props.total_memory // (1024 * 1024))
        return info

    name, mem = _nvidia_smi_gpu()
    info["gpu_name"], info["gpu_memory_mb"] = name, mem
    if name and torch.version.cuda is None:
        info["hint"] = (
            "GPU NVIDIA detectada, mas o PyTorch instalado é a versão só-CPU. Instale a build CUDA: "
            "pip install --force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu128"
        )
    elif name:
        info["hint"] = "GPU NVIDIA detectada, mas o CUDA não está disponível (verifique o driver)."
    return info
