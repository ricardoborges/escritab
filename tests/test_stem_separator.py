from pathlib import Path
import numpy as np
import soundfile as sf
from app.pipeline.stem_separator import separate_stems


def test_separate_stems_isolated_stem_shortcut(tmp_path: Path):
    sr = 22050
    audio = 0.5 * np.sin(2 * np.pi * 440.0 * np.linspace(0, 0.5, sr // 2))
    input_wav = tmp_path / "input_riff.wav"
    sf.write(input_wav, audio, sr)

    stems, device = separate_stems(
        input_audio_path=input_wav,
        job_dir=tmp_path,
        is_isolated_stem=True,
    )
    assert device == "bypass"
    assert "guitar" in stems
    assert (tmp_path / stems["guitar"]).exists()


def test_separate_stems_fallback_from_cuda_oom_to_cpu(tmp_path: Path, monkeypatch):
    sr = 22050
    audio = 0.5 * np.sin(2 * np.pi * 440.0 * np.linspace(0, 0.5, sr // 2))
    input_wav = tmp_path / "input_song.wav"
    sf.write(input_wav, audio, sr)

    calls = []

    def fake_run_demucs(input_path: Path, out_dir: Path, model_name: str, device: str, segment: int, progress=None):
        calls.append((device, segment))
        if device == "cuda":
            raise RuntimeError("CUDA out of memory")
        target = out_dir / model_name / input_path.stem
        target.mkdir(parents=True, exist_ok=True)
        for s in ("guitar", "other", "bass", "drums", "vocals", "piano"):
            sf.write(target / f"{s}.wav", audio, sr)

    monkeypatch.setattr("app.pipeline.stem_separator._invoke_demucs", fake_run_demucs)
    monkeypatch.setattr("torch.cuda.is_available", lambda: True)

    stems, device = separate_stems(
        input_audio_path=input_wav,
        job_dir=tmp_path,
        is_isolated_stem=False,
        model_name="htdemucs_6s",
    )
    assert device == "cpu"
    assert ("cuda", 7) in calls
    assert ("cuda", 4) in calls
    assert ("cpu", 7) in calls
    assert set(stems.keys()) == {"guitar", "other", "bass", "drums", "vocals", "piano"}
    for filename in stems.values():
        assert (tmp_path / filename).exists()
