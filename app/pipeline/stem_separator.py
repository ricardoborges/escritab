from __future__ import annotations
from pathlib import Path
import shutil
import subprocess
import sys
import librosa
import soundfile as sf
import torch


STEM_NAMES_6S = ("guitar", "other", "bass", "drums", "vocals", "piano")
STEM_NAMES_4S = ("other", "bass", "drums", "vocals")


def _invoke_demucs(
    input_path: Path,
    out_dir: Path,
    model_name: str,
    device: str,
    segment: int,
) -> None:
    cmd = [
        sys.executable,
        "-m",
        "demucs.separate",
        "-n",
        model_name,
        "-d",
        device,
        "--segment",
        str(segment),
        "-o",
        str(out_dir),
        str(input_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Demucs failed ({device}, seg={segment}): {proc.stderr}")


def _collect_demucs_outputs(
    demucs_out: Path,
    model_name: str,
    input_stem: str,
    job_dir: Path,
) -> dict[str, str]:
    track_dir = demucs_out / model_name / input_stem
    if not track_dir.exists():
        candidates = list((demucs_out / model_name).glob("*"))
        if not candidates:
            raise FileNotFoundError(f"Demucs output directory not found under {demucs_out}")
        track_dir = candidates[0]

    stems_map: dict[str, str] = {}
    expected = STEM_NAMES_6S if "6s" in model_name else STEM_NAMES_4S
    for stem in expected:
        src = track_dir / f"{stem}.wav"
        if src.exists():
            dst_name = f"stem_{stem}.wav"
            dst = job_dir / dst_name
            shutil.move(str(src), str(dst))
            stems_map[stem] = dst_name
    shutil.rmtree(demucs_out, ignore_errors=True)
    return stems_map


def separate_stems(
    input_audio_path: Path,
    job_dir: Path,
    is_isolated_stem: bool = False,
    model_name: str = "htdemucs_6s",
) -> tuple[dict[str, str], str]:
    job_dir.mkdir(parents=True, exist_ok=True)

    if is_isolated_stem:
        dst_name = "stem_guitar.wav"
        dst_path = job_dir / dst_name
        audio, sr = librosa.load(str(input_audio_path), sr=None, mono=False)
        if audio.ndim == 2:
            audio = audio.T
        sf.write(str(dst_path), audio, sr)
        return {"guitar": dst_name}, "bypass"

    demucs_tmp = job_dir / "_demucs_tmp"
    demucs_tmp.mkdir(parents=True, exist_ok=True)

    attempts: list[tuple[str, int]] = []
    if torch.cuda.is_available():
        attempts.extend([("cuda", 7), ("cuda", 4)])
    attempts.append(("cpu", 7))

    last_err: Exception | None = None
    for device, segment in attempts:
        try:
            _invoke_demucs(input_audio_path, demucs_tmp, model_name, device, segment)
            if device == "cuda" and torch.cuda.is_available():
                torch.cuda.empty_cache()
            stems_map = _collect_demucs_outputs(
                demucs_tmp, model_name, input_audio_path.stem, job_dir
            )
            return stems_map, device
        except Exception as exc:
            last_err = exc
            if device == "cuda" and torch.cuda.is_available():
                torch.cuda.empty_cache()
            continue

    raise RuntimeError(f"All stem separation attempts failed: {last_err}")
