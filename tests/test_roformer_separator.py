import re
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from bs_roformer import BSRoformer

from app.pipeline import roformer_separator
from app.pipeline.stem_separator import separate_stems

TINY = dict(
    dim=32, depth=2, stereo=True, num_stems=1, time_transformer_depth=1, freq_transformer_depth=1,
    dim_head=16, heads=2, stft_n_fft=256, stft_hop_length=64, stft_win_length=256,
    dim_freqs_in=129, freqs_per_bands=(8,) * 16 + (1,), flash_attn=False,
)


def _legacy_state_dict(model: BSRoformer) -> dict:
    """What a checkpoint trained before hyper-connections / value residuals looks like."""
    return {
        re.sub(r"\.branch\.", ".", k): v
        for k, v in model.state_dict().items()
        if "to_value_residual_mix" not in k
    }


def test_legacy_checkpoint_loads_and_matches_plain_attention():
    torch.manual_seed(0)
    ref = BSRoformer(**TINY, num_residual_streams=1).eval()
    with torch.no_grad():  # the network the legacy weights describe: no value-residual mixing
        for name, mod in ref.named_modules():
            if name.endswith("to_value_residual_mix"):
                mod.weight.zero_()
                mod.bias.fill_(-50.0)

    model = roformer_separator.build_model(TINY, _legacy_state_dict(ref))

    x = torch.randn(1, 2, 4096)
    with torch.no_grad():
        assert torch.allclose(model(x), ref(x), atol=1e-5)


def test_mismatched_checkpoint_is_rejected():
    torch.manual_seed(0)
    sd = _legacy_state_dict(BSRoformer(**TINY, num_residual_streams=1))
    sd.pop(next(k for k in sd if "to_qkv" in k))
    try:
        roformer_separator.build_model(TINY, sd)
    except RuntimeError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("a checkpoint missing real weights must not load silently")


def test_roformer_chain_keeps_roformer_vocals(tmp_path: Path, monkeypatch):
    sr = 22050
    audio = (0.3 * np.sin(2 * np.pi * 220.0 * np.arange(sr // 2) / sr)).astype(np.float32)
    song = tmp_path / "song.wav"
    sf.write(song, audio, sr)

    def fake_vocals(input_path, vocals_path, instrumental_path):
        sf.write(vocals_path, np.full(1000, 0.25, np.float32), sr)  # marker: the RoFormer vocals
        sf.write(instrumental_path, audio, sr)

    def fake_demucs(input_path, out_dir, model_name, device, segment):
        assert input_path.name == "_roformer_instrumental.wav"  # Demucs runs on the instrumental
        target = out_dir / model_name / input_path.stem
        target.mkdir(parents=True, exist_ok=True)
        for s in ("guitar", "other", "bass", "drums", "vocals", "piano"):
            sf.write(target / f"{s}.wav", audio, sr)

    monkeypatch.setattr(roformer_separator, "separate_vocals", fake_vocals)
    monkeypatch.setattr("app.pipeline.stem_separator._invoke_demucs", fake_demucs)
    monkeypatch.setattr("torch.cuda.is_available", lambda: False)

    stems, device = separate_stems(song, tmp_path, model_name="roformer_htdemucs_6s")

    assert "BS-RoFormer" in device
    assert set(stems) == {"guitar", "other", "bass", "drums", "vocals", "piano"}
    vocals, _ = sf.read(tmp_path / stems["vocals"])
    assert len(vocals) == 1000 and np.allclose(vocals, 0.25, atol=1e-3)  # not Demucs' residue
    assert not (tmp_path / "_roformer_instrumental.wav").exists()
    assert not (tmp_path / "_roformer_vocals.wav").exists()
