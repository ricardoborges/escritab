# EscriTab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build EscriTab, a local Python FastAPI web application that separates guitar stems from AI-generated music (Suno/MP3/WAV/FLAC) using Demucs (`htdemucs_6s`), cleans and transcribes the selected stem to MIDI via Basic Pitch with high-gain harmonic suppression, optimizes guitar fretboard fingerings across 8 tunings using a Viterbi solver, and renders an interactive AlphaTab.js player with `.gp5`, `.musicxml`, `.mid`, and `.txt` exports.

**Architecture:** A modular FastAPI backend manages stateful jobs persisted on disk under `workspace/jobs/<job_id>/job.json` so users can re-run MIDI transcription or fretboard optimization with different parameters without re-running Demucs stem separation. The processing pipeline is split into 5 focused modules (`stem_separator`, `audio_cleaner`, `midi_transcriber`, `fretboard`, `exporters`) consumed by REST endpoints and a vanilla HTML5/CSS3/JS single-page frontend integrating native multi-stem audio players and AlphaTab.js.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, PyTorch + Demucs (`htdemucs_6s`), ONNX Runtime / Basic Pitch, Librosa, SciPy, SoundFile, PrettyMIDI, PyGuitarPro (`guitarpro`), AlphaTab.js (via CDN), Pytest.

**Spec:** `docs/superpowers/specs/2026-11-14-escritab-design.md`

## Global Constraints

- **OS & Shell:** Windows 11 with PowerShell terminal. All commands must be valid PowerShell.
- **Python Environment:** Python 3.11 (`python` / `pytest`).
- **Workspace Persistence:** Every uploaded job is stored in `workspace/jobs/<job_id>/` with metadata in `job.json` and generated artifacts (`guitar_clean.mid`, `tab.musicxml`, `tab.gp5`, `tab.txt`).
- **GPU Management:** Detect `torch.cuda.is_available()`. On 6 GB VRAM GPUs, use `segment=7` for Demucs, call `torch.cuda.empty_cache()` after separation, retry with reduced segment on OOM, and fall back to CPU automatically if CUDA fails.
- **Supported Tunings (low to high MIDI pitches):**
  - `standard_e`: `[40, 45, 50, 55, 59, 64]` (`E2, A2, D3, G3, B3, E4`)
  - `drop_d`: `[38, 45, 50, 55, 59, 64]` (`D2, A2, D3, G3, B3, E4`)
  - `eb_standard`: `[39, 44, 49, 54, 58, 63]` (`Eb2, Ab2, Db3, Gb3, Bb3, Eb4`)
  - `d_standard`: `[38, 43, 48, 53, 57, 62]` (`D2, G2, C3, F3, A3, D4`)
  - `drop_c`: `[36, 43, 48, 53, 57, 62]` (`C2, G2, C3, F3, A3, D4`)
  - `drop_b`: `[35, 42, 47, 52, 56, 61]` (`B1, F#2, B2, E3, G#3, C#4`)
  - `seven_standard`: `[35, 40, 45, 50, 55, 59, 64]` (`B1, E2, A2, D3, G3, B3, E4`)
  - `seven_drop_a`: `[33, 40, 45, 50, 55, 59, 64]` (`A1, E2, A2, D3, G3, B3, E4`)
- **Zero-Note Guard:** `/api/jobs/{job_id}/transcribe` must return HTTP `422` with a helpful message if 0 notes remain after filtering.

---

### Task 1: Project Scaffolding, Dependencies, Models & Job Manager

**Files:**
- Create: `requirements.txt`
- Create: `app/__init__.py`
- Create: `app/pipeline/__init__.py`
- Create: `app/models.py`
- Create: `app/job_manager.py`
- Test: `tests/test_job_manager.py`

**Interfaces:**
- Consumes: None (foundational data layer)
- Produces:
  - `app.models.TUNINGS: dict[str, TuningPreset]`
  - `app.models.TuningPreset`, `app.models.TranscriptionParams`, `app.models.TabParams`, `app.models.NoteEvent`, `app.models.FrettedNote`, `app.models.TabSlice`, `app.models.JobMetadata`
  - `app.job_manager.JobManager(workspace_root: Path)` with methods `create_job(original_filename: str, is_isolated_stem: bool, audio_bytes: bytes) -> JobMetadata`, `get_job(job_id: str) -> JobMetadata`, `save_job(job: JobMetadata) -> None`, `list_jobs() -> list[JobMetadata]`, `get_job_dir(job_id: str) -> Path`

- [ ] **Step 1: Install required Python packages and create `requirements.txt`**

Create `requirements.txt`:

```text
fastapi>=0.115.0
uvicorn>=0.30.0
pydantic>=2.7.0
python-multipart>=0.0.9
numpy>=1.26.0
scipy>=1.13.0
soundfile>=0.12.1
librosa>=0.10.2
pretty_midi>=0.2.10
pyguitarpro>=0.9.3
demucs>=4.0.1
basic-pitch>=0.3.0
onnxruntime-gpu>=1.18.0
pytest>=8.0.0
httpx>=0.27.0
```

Run in PowerShell:
```powershell
python -m pip install librosa pretty_midi pyguitarpro demucs
python -m pip install basic-pitch --no-deps
```

- [ ] **Step 2: Write the failing test in `tests/test_job_manager.py`**

```python
from pathlib import Path
import pytest
from app.models import TUNINGS, TranscriptionParams, TabParams
from app.job_manager import JobManager


def test_tunings_catalog_complete():
    expected_keys = {
        "standard_e",
        "drop_d",
        "eb_standard",
        "d_standard",
        "drop_c",
        "drop_b",
        "seven_standard",
        "seven_drop_a",
    }
    assert set(TUNINGS.keys()) == expected_keys
    assert TUNINGS["standard_e"].pitches == [40, 45, 50, 55, 59, 64]
    assert TUNINGS["drop_d"].pitches == [38, 45, 50, 55, 59, 64]
    assert TUNINGS["seven_drop_a"].pitches == [33, 40, 45, 50, 55, 59, 64]
    assert TUNINGS["drop_d"].is_drop is True
    assert TUNINGS["standard_e"].is_drop is False


def test_job_manager_lifecycle(tmp_path: Path):
    manager = JobManager(workspace_root=tmp_path)
    dummy_bytes = b"RIFF_DUMMY_WAV_DATA"

    job = manager.create_job(
        original_filename="metal_riff.wav",
        is_isolated_stem=True,
        audio_bytes=dummy_bytes,
    )
    assert job.job_id
    assert job.original_filename == "metal_riff.wav"
    assert job.is_isolated_stem is True

    job_dir = manager.get_job_dir(job.job_id)
    assert (job_dir / "job.json").exists()
    assert (job_dir / "input_metal_riff.wav").read_bytes() == dummy_bytes

    job.detected_bpm = 140.0
    job.selected_stem = "guitar"
    job.transcription_params = TranscriptionParams(onset_threshold=0.6)
    job.tab_params = TabParams(tuning="drop_d", bpm=140.0)
    manager.save_job(job)

    reloaded = manager.get_job(job.job_id)
    assert reloaded.detected_bpm == 140.0
    assert reloaded.selected_stem == "guitar"
    assert reloaded.transcription_params.onset_threshold == 0.6
    assert reloaded.tab_params.tuning == "drop_d"

    all_jobs = manager.list_jobs()
    assert len(all_jobs) == 1
    assert all_jobs[0].job_id == job.job_id


def test_job_manager_missing_job_raises(tmp_path: Path):
    manager = JobManager(workspace_root=tmp_path)
    with pytest.raises(FileNotFoundError):
        manager.get_job("nonexistent-id")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_job_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 4: Implement `app/__init__.py`, `app/pipeline/__init__.py`, `app/models.py`, and `app/job_manager.py`**

Create `app/__init__.py` and `app/pipeline/__init__.py` (empty files).

Create `app/models.py`:

```python
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class TuningPreset(BaseModel):
    id: str
    name: str
    pitches: list[int]  # Low string to high string (MIDI note numbers)
    string_names: list[str]  # Low string to high string labels
    is_drop: bool = False


TUNINGS: dict[str, TuningPreset] = {
    "standard_e": TuningPreset(
        id="standard_e",
        name="Standard E (6 cordas - EADGBE)",
        pitches=[40, 45, 50, 55, 59, 64],
        string_names=["E2", "A2", "D3", "G3", "B3", "E4"],
        is_drop=False,
    ),
    "drop_d": TuningPreset(
        id="drop_d",
        name="Drop D (6 cordas - DADGBE)",
        pitches=[38, 45, 50, 55, 59, 64],
        string_names=["D2", "A2", "D3", "G3", "B3", "E4"],
        is_drop=True,
    ),
    "eb_standard": TuningPreset(
        id="eb_standard",
        name="Eb Standard / Meio Tom Abaixo (6 cordas)",
        pitches=[39, 44, 49, 54, 58, 63],
        string_names=["Eb2", "Ab2", "Db3", "Gb3", "Bb3", "Eb4"],
        is_drop=False,
    ),
    "d_standard": TuningPreset(
        id="d_standard",
        name="D Standard (6 cordas - DGCFAD)",
        pitches=[38, 43, 48, 53, 57, 62],
        string_names=["D2", "G2", "C3", "F3", "A3", "D4"],
        is_drop=False,
    ),
    "drop_c": TuningPreset(
        id="drop_c",
        name="Drop C (6 cordas - CGCFAD)",
        pitches=[36, 43, 48, 53, 57, 62],
        string_names=["C2", "G2", "C3", "F3", "A3", "D4"],
        is_drop=True,
    ),
    "drop_b": TuningPreset(
        id="drop_b",
        name="Drop B (6 cordas - B F# B E G# C#)",
        pitches=[35, 42, 47, 52, 56, 61],
        string_names=["B1", "F#2", "B2", "E3", "G#3", "C#4"],
        is_drop=True,
    ),
    "seven_standard": TuningPreset(
        id="seven_standard",
        name="7 Cordas Standard (BEADGBE)",
        pitches=[35, 40, 45, 50, 55, 59, 64],
        string_names=["B1", "E2", "A2", "D3", "G3", "B3", "E4"],
        is_drop=False,
    ),
    "seven_drop_a": TuningPreset(
        id="seven_drop_a",
        name="7 Cordas Drop A (AEADGBE)",
        pitches=[33, 40, 45, 50, 55, 59, 64],
        string_names=["A1", "E2", "A2", "D3", "G3", "B3", "E4"],
        is_drop=True,
    ),
}


class TranscriptionParams(BaseModel):
    stem: str = "guitar"
    bandpass_enabled: bool = True
    lowcut_hz: float = 75.0
    highcut_hz: float = 5500.0
    noise_gate_db: float = -45.0
    onset_threshold: float = Field(default=0.5, ge=0.1, le=0.95)
    frame_threshold: float = Field(default=0.3, ge=0.1, le=0.9)
    minimum_note_length_ms: float = Field(default=60.0, ge=15.0, le=300.0)
    suppress_harmonics: bool = True
    harmonic_velocity_ratio: float = Field(default=0.75, ge=0.2, le=1.0)
    quantize_grid: Optional[Literal["off", "1/16", "1/32"]] = "off"
    tuning: str = "standard_e"
    max_frets: int = 24
    bpm: float = 120.0


class TabParams(BaseModel):
    tuning: str = "standard_e"
    max_frets: int = Field(default=24, ge=12, le=27)
    fret_preference: Literal["auto", "low", "mid", "high"] = "auto"
    bpm: float = Field(default=120.0, gt=20.0, lt=320.0)


class NoteEvent(BaseModel):
    pitch: int
    start: float
    end: float
    velocity: int = 80


class FrettedNote(BaseModel):
    string_idx: int  # 0 = lowest pitched string (e.g. E2), N-1 = highest string (e.g. E4)
    fret: int
    pitch: int
    start: float
    end: float
    velocity: int = 80


class TabSlice(BaseModel):
    start: float
    end: float
    notes: list[FrettedNote]


class SeparateRequest(BaseModel):
    model: Literal["htdemucs_6s", "htdemucs"] = "htdemucs_6s"


class JobMetadata(BaseModel):
    job_id: str
    created_at: str
    original_filename: str
    input_path: str
    is_isolated_stem: bool = False
    detected_bpm: float = 120.0
    separation_device: Optional[str] = None
    stems: dict[str, str] = Field(default_factory=dict)
    selected_stem: Optional[str] = None
    transcription_params: Optional[TranscriptionParams] = None
    tab_params: Optional[TabParams] = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    stats: dict[str, int | float | str] = Field(default_factory=dict)
```

Create `app/job_manager.py`:

```python
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import re
import uuid
from app.models import JobMetadata


def _sanitize_filename(filename: str) -> str:
    base = Path(filename).name
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)


class JobManager:
    def __init__(self, workspace_root: Path | str = Path("workspace/jobs")) -> None:
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def get_job_dir(self, job_id: str) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "", job_id)
        return self.workspace_root / safe_id

    def create_job(
        self,
        original_filename: str,
        is_isolated_stem: bool,
        audio_bytes: bytes,
    ) -> JobMetadata:
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.get_job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)

        safe_name = _sanitize_filename(original_filename)
        input_file = job_dir / f"input_{safe_name}"
        input_file.write_bytes(audio_bytes)

        job = JobMetadata(
            job_id=job_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            original_filename=original_filename,
            input_path=input_file.name,
            is_isolated_stem=is_isolated_stem,
        )
        self.save_job(job)
        return job

    def save_job(self, job: JobMetadata) -> None:
        job_dir = self.get_job_dir(job.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        meta_file = job_dir / "job.json"
        meta_file.write_text(job.model_dump_json(indent=2), encoding="utf-8")

    def get_job(self, job_id: str) -> JobMetadata:
        meta_file = self.get_job_dir(job_id) / "job.json"
        if not meta_file.exists():
            raise FileNotFoundError(f"Job '{job_id}' not found.")
        return JobMetadata.model_validate_json(meta_file.read_text(encoding="utf-8"))

    def list_jobs(self) -> list[JobMetadata]:
        jobs: list[JobMetadata] = []
        if not self.workspace_root.exists():
            return jobs
        for child in self.workspace_root.iterdir():
            meta_file = child / "job.json"
            if child.is_dir() and meta_file.exists():
                try:
                    jobs.append(
                        JobMetadata.model_validate_json(
                            meta_file.read_text(encoding="utf-8")
                        )
                    )
                except Exception:
                    continue
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs
```

- [ ] **Step 5: Run test to verify it passes and commit**

Run: `pytest tests/test_job_manager.py -v`
Expected: PASS (3 passed)

Commit:
```powershell
git add requirements.txt app/__init__.py app/pipeline/__init__.py app/models.py app/job_manager.py tests/test_job_manager.py
git commit -m "feat: add models, tuning presets, and job manager"
```

---

### Task 2: Audio Pre-processing, Mixing, Band-Pass Filter, Noise Gate & BPM Detection

**Files:**
- Create: `app/pipeline/audio_cleaner.py`
- Test: `tests/test_audio_cleaner.py`

**Interfaces:**
- Consumes: Audio files on disk (`.wav`, `.mp3`, `.flac`)
- Produces:
  - `apply_bandpass_filter(audio: np.ndarray, sr: int, lowcut_hz: float = 75.0, highcut_hz: float = 5500.0, order: int = 4) -> np.ndarray`
  - `apply_noise_gate(audio: np.ndarray, sr: int, threshold_db: float = -45.0, frame_ms: float = 20.0) -> np.ndarray`
  - `mix_stem_files(stem_paths: list[Path], output_path: Path) -> Path`
  - `prepare_clean_audio(stem_paths: list[Path], output_path: Path, bandpass_enabled: bool = True, lowcut_hz: float = 75.0, highcut_hz: float = 5500.0, noise_gate_db: float = -45.0) -> Path`
  - `detect_bpm(audio_path: Path, default_bpm: float = 120.0) -> float`

- [ ] **Step 1: Write the failing test in `tests/test_audio_cleaner.py`**

```python
from pathlib import Path
import numpy as np
import soundfile as sf
from app.pipeline.audio_cleaner import (
    apply_bandpass_filter,
    apply_noise_gate,
    detect_bpm,
    mix_stem_files,
    prepare_clean_audio,
)


def test_bandpass_filter_attenuates_sub_bass_and_ultra_highs():
    sr = 44100
    duration = 1.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    sub_bass = np.sin(2 * np.pi * 30.0 * t)       # 30 Hz (below 75 Hz)
    guitar_tone = np.sin(2 * np.pi * 440.0 * t)   # 440 Hz (in passband)
    high_hiss = np.sin(2 * np.pi * 12000.0 * t)   # 12 kHz (above 5500 Hz)

    filtered_sub = apply_bandpass_filter(sub_bass, sr, lowcut_hz=75.0, highcut_hz=5500.0)
    filtered_guitar = apply_bandpass_filter(guitar_tone, sr, lowcut_hz=75.0, highcut_hz=5500.0)
    filtered_high = apply_bandpass_filter(high_hiss, sr, lowcut_hz=75.0, highcut_hz=5500.0)

    rms_sub = np.sqrt(np.mean(filtered_sub**2))
    rms_guitar = np.sqrt(np.mean(filtered_guitar**2))
    rms_high = np.sqrt(np.mean(filtered_high**2))

    assert rms_guitar > 0.65
    assert rms_sub < 0.25
    assert rms_high < 0.15


def test_noise_gate_silences_quiet_tail():
    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    signal = np.concatenate([
        0.5 * np.sin(2 * np.pi * 220.0 * t[: sr // 2]),   # loud first half (-6 dB)
        0.001 * np.sin(2 * np.pi * 220.0 * t[sr // 2 :]), # quiet tail (-60 dB)
    ])
    gated = apply_noise_gate(signal, sr, threshold_db=-40.0)
    assert np.max(np.abs(gated[: sr // 2])) > 0.4
    assert np.max(np.abs(gated[int(sr * 0.6) :])) == 0.0


def test_mix_stems_and_prepare_clean_audio(tmp_path: Path):
    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    stem1 = 0.6 * np.sin(2 * np.pi * 330.0 * t)
    stem2 = 0.6 * np.sin(2 * np.pi * 440.0 * t)

    p1 = tmp_path / "guitar.wav"
    p2 = tmp_path / "other.wav"
    sf.write(p1, stem1, sr)
    sf.write(p2, stem2, sr)

    mixed_path = tmp_path / "mixed.wav"
    mix_stem_files([p1, p2], mixed_path)
    assert mixed_path.exists()
    mixed_audio, _ = sf.read(mixed_path)
    assert np.max(np.abs(mixed_audio)) <= 1.0

    clean_path = tmp_path / "clean.wav"
    prepare_clean_audio([p1, p2], clean_path, bandpass_enabled=True, noise_gate_db=-45.0)
    assert clean_path.exists()


def test_detect_bpm_returns_valid_tempo(tmp_path: Path):
    sr = 22050
    duration = 4.0
    audio = np.zeros(int(sr * duration), dtype=np.float32)
    # Place impulses every 0.5s -> 120 BPM
    for sec in np.arange(0.0, duration, 0.5):
        idx = int(sec * sr)
        audio[idx : min(idx + 400, len(audio))] = 0.9
    wav_path = tmp_path / "click.wav"
    sf.write(wav_path, audio, sr)

    bpm = detect_bpm(wav_path, default_bpm=120.0)
    assert 60.0 <= bpm <= 240.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_audio_cleaner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipeline.audio_cleaner'`

- [ ] **Step 3: Implement `app/pipeline/audio_cleaner.py`**

Create `app/pipeline/audio_cleaner.py`:

```python
from __future__ import annotations
from pathlib import Path
import librosa
import numpy as np
from scipy.signal import butter, sosfiltfilt
import soundfile as sf


def load_mono_audio(path: Path, target_sr: int | None = None) -> tuple[np.ndarray, int]:
    audio, sr = librosa.load(str(path), sr=target_sr, mono=True)
    return audio.astype(np.float32), int(sr)


def apply_bandpass_filter(
    audio: np.ndarray,
    sr: int,
    lowcut_hz: float = 75.0,
    highcut_hz: float = 5500.0,
    order: int = 4,
) -> np.ndarray:
    nyquist = 0.5 * sr
    low = max(10.0, lowcut_hz) / nyquist
    high = min(nyquist - 50.0, highcut_hz) / nyquist
    if low >= high or low <= 0.0 or high >= 1.0:
        return audio
    sos = butter(order, [low, high], btype="bandpass", output="sos")
    filtered = sosfiltfilt(sos, audio)
    return filtered.astype(np.float32)


def apply_noise_gate(
    audio: np.ndarray,
    sr: int,
    threshold_db: float = -45.0,
    frame_ms: float = 20.0,
) -> np.ndarray:
    if threshold_db <= -100.0 or audio.size == 0:
        return audio
    threshold_linear = 10.0 ** (threshold_db / 20.0)
    frame_len = max(64, int(sr * (frame_ms / 1000.0)))
    output = audio.copy()
    for start in range(0, len(output), frame_len):
        end = min(len(output), start + frame_len)
        frame = output[start:end]
        rms = float(np.sqrt(np.mean(frame**2) + 1e-12))
        if rms < threshold_linear:
            output[start:end] = 0.0
    return output


def mix_stem_files(stem_paths: list[Path], output_path: Path) -> Path:
    if not stem_paths:
        raise ValueError("At least one stem path is required.")
    signals: list[np.ndarray] = []
    base_sr: int | None = None
    for p in stem_paths:
        sig, sr = load_mono_audio(p, target_sr=base_sr)
        if base_sr is None:
            base_sr = sr
        signals.append(sig)

    max_len = max(len(s) for s in signals)
    mixed = np.zeros(max_len, dtype=np.float32)
    for s in signals:
        mixed[: len(s)] += s

    peak = float(np.max(np.abs(mixed))) if mixed.size > 0 else 0.0
    if peak > 0.98:
        mixed = mixed * (0.95 / peak)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), mixed, base_sr or 44100)
    return output_path


def prepare_clean_audio(
    stem_paths: list[Path],
    output_path: Path,
    bandpass_enabled: bool = True,
    lowcut_hz: float = 75.0,
    highcut_hz: float = 5500.0,
    noise_gate_db: float = -45.0,
) -> Path:
    mixed_path = output_path.parent / "_temp_mixed.wav"
    mix_stem_files(stem_paths, mixed_path)
    audio, sr = load_mono_audio(mixed_path)
    if mixed_path.exists():
        mixed_path.unlink()

    if bandpass_enabled:
        audio = apply_bandpass_filter(audio, sr, lowcut_hz=lowcut_hz, highcut_hz=highcut_hz)

    audio = apply_noise_gate(audio, sr, threshold_db=noise_gate_db)

    peak = float(np.max(np.abs(audio))) if audio.size > 0 else 0.0
    if peak > 0.98:
        audio = audio * (0.95 / peak)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), audio, sr)
    return output_path


def detect_bpm(audio_path: Path, default_bpm: float = 120.0) -> float:
    try:
        audio, sr = librosa.load(str(audio_path), sr=22050, mono=True, duration=60.0)
        if audio.size < sr:
            return default_bpm
        tempo, _ = librosa.beat.beat_track(y=audio, sr=sr)
        bpm_val = float(np.asarray(tempo).flat[0])
        if not np.isfinite(bpm_val) or bpm_val <= 30.0 or bpm_val >= 300.0:
            return default_bpm
        return round(bpm_val, 1)
    except Exception:
        return default_bpm
```

- [ ] **Step 4: Run test to verify it passes and commit**

Run: `pytest tests/test_audio_cleaner.py -v`
Expected: PASS (4 passed)

Commit:
```powershell
git add app/pipeline/audio_cleaner.py tests/test_audio_cleaner.py
git commit -m "feat: add audio cleaner with bandpass filter, noise gate, stem mixing, and BPM detection"
```

---

### Task 3: Stem Separator with Demucs & GPU/CPU Fallback

**Files:**
- Create: `app/pipeline/stem_separator.py`
- Test: `tests/test_stem_separator.py`

**Interfaces:**
- Consumes: `input_audio_path: Path`, `job_dir: Path`, `is_isolated_stem: bool`, `model_name: str`
- Produces:
  - `separate_stems(input_audio_path: Path, job_dir: Path, is_isolated_stem: bool = False, model_name: str = "htdemucs_6s") -> tuple[dict[str, str], str]` returning `(stems_filename_map, device_used)`

- [ ] **Step 1: Write the failing test in `tests/test_stem_separator.py`**

```python
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

    def fake_run_demucs(input_path: Path, out_dir: Path, model_name: str, device: str, segment: int):
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_stem_separator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipeline.stem_separator'`

- [ ] **Step 3: Implement `app/pipeline/stem_separator.py`**

Create `app/pipeline/stem_separator.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes and commit**

Run: `pytest tests/test_stem_separator.py -v`
Expected: PASS (2 passed)

Commit:
```powershell
git add app/pipeline/stem_separator.py tests/test_stem_separator.py
git commit -m "feat: add Demucs stem separator with CUDA VRAM management and CPU fallback"
```

---

### Task 4: MIDI Transcriber & Metal Harmonic Post-Processor

**Files:**
- Create: `app/pipeline/midi_transcriber.py`
- Test: `tests/test_midi_transcriber.py`

**Interfaces:**
- Consumes: `audio_path: Path`, `output_midi_path: Path`, `params: TranscriptionParams`
- Produces:
  - `postprocess_notes(notes: list[NoteEvent], params: TranscriptionParams) -> tuple[list[NoteEvent], dict[str, int]]`
  - `notes_to_midi_file(notes: list[NoteEvent], output_midi_path: Path, bpm: float = 120.0) -> Path`
  - `midi_file_to_notes(midi_path: Path) -> list[NoteEvent]`
  - `transcribe_audio_to_midi(audio_path: Path, output_midi_path: Path, params: TranscriptionParams) -> tuple[list[NoteEvent], dict[str, int | float | str]]`

- [ ] **Step 1: Write the failing test in `tests/test_midi_transcriber.py`**

```python
from pathlib import Path
import numpy as np
import soundfile as sf
from app.models import NoteEvent, TranscriptionParams
from app.pipeline.midi_transcriber import (
    midi_file_to_notes,
    notes_to_midi_file,
    postprocess_notes,
    transcribe_audio_to_midi,
)


def test_postprocess_removes_short_notes_and_spurious_harmonics():
    raw_notes = [
        # Fundamental E2 (pitch 40) with velocity 100
        NoteEvent(pitch=40, start=0.00, end=0.50, velocity=100),
        # Spurious 2nd harmonic E3 (pitch 52 = 40+12) within 20ms, velocity 60 (< 75% of 100)
        NoteEvent(pitch=52, start=0.015, end=0.45, velocity=60),
        # Spurious 3rd harmonic B3 (pitch 59 = 40+19) within 10ms, velocity 55 (< 75% of 100)
        NoteEvent(pitch=59, start=0.010, end=0.40, velocity=55),
        # Genuine high melody note with high velocity (90 >= 75% of 100)
        NoteEvent(pitch=64, start=0.00, end=0.50, velocity=90),
        # Ghost note shorter than 60ms (duration 30ms)
        NoteEvent(pitch=45, start=0.60, end=0.63, velocity=80),
    ]
    params = TranscriptionParams(
        minimum_note_length_ms=60.0,
        suppress_harmonics=True,
        harmonic_velocity_ratio=0.75,
        quantize_grid="off",
        tuning="standard_e",
    )
    cleaned, stats = postprocess_notes(raw_notes, params)
    pitches = [n.pitch for n in cleaned]
    assert pitches == [40, 64]
    assert stats["harmonics_removed"] == 2
    assert stats["short_notes_removed"] == 1


def test_postprocess_polyphony_cap_and_quantization():
    # 8 simultaneous notes on a 6-string guitar -> only top 6 by velocity survive
    raw_notes = [
        NoteEvent(pitch=40 + i, start=0.02, end=0.26, velocity=50 + i * 5)
        for i in range(8)
    ]
    params = TranscriptionParams(
        minimum_note_length_ms=50.0,
        suppress_harmonics=False,
        quantize_grid="1/16",
        bpm=120.0,  # 1/16 note = 0.125s
        tuning="standard_e",
    )
    cleaned, _ = postprocess_notes(raw_notes, params)
    assert len(cleaned) == 6
    # Highest velocities should be kept (i=2..7 -> pitches 42..47)
    assert sorted(n.pitch for n in cleaned) == [42, 43, 44, 45, 46, 47]
    # Quantized start should snap 0.02 -> 0.0 and end 0.26 -> 0.25
    for n in cleaned:
        assert abs(n.start - 0.0) < 1e-6
        assert abs(n.end - 0.25) < 1e-6


def test_midi_roundtrip_and_audio_transcription(tmp_path: Path):
    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    # Synthesize a clear A2 (110 Hz) followed by E3 (164.81 Hz)
    audio = np.concatenate([
        0.8 * np.sin(2 * np.pi * 110.0 * t[: sr // 2]),
        0.8 * np.sin(2 * np.pi * 164.81 * t[sr // 2 :]),
    ]).astype(np.float32)
    wav_path = tmp_path / "test_guitar.wav"
    sf.write(wav_path, audio, sr)

    midi_path = tmp_path / "guitar_clean.mid"
    params = TranscriptionParams(
        onset_threshold=0.3,
        frame_threshold=0.2,
        minimum_note_length_ms=50.0,
    )
    notes, stats = transcribe_audio_to_midi(wav_path, midi_path, params)
    assert len(notes) >= 1
    assert midi_path.exists()
    assert stats["total_notes"] == len(notes)

    reloaded = midi_file_to_notes(midi_path)
    assert len(reloaded) == len(notes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_midi_transcriber.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipeline.midi_transcriber'`

- [ ] **Step 3: Implement `app/pipeline/midi_transcriber.py`**

Create `app/pipeline/midi_transcriber.py`:

```python
from __future__ import annotations
from pathlib import Path
import librosa
import numpy as np
import pretty_midi
from app.models import NoteEvent, TUNINGS, TranscriptionParams


def _tuning_pitch_bounds(tuning_id: str, max_frets: int = 24) -> tuple[int, int]:
    preset = TUNINGS.get(tuning_id, TUNINGS["standard_e"])
    min_pitch = min(preset.pitches)
    max_pitch = max(preset.pitches) + max_frets
    return min_pitch, max_pitch


def _run_basic_pitch_or_fallback(
    audio_path: Path,
    params: TranscriptionParams,
) -> list[NoteEvent]:
    min_pitch, max_pitch = _tuning_pitch_bounds(params.tuning, params.max_frets)
    min_freq = float(librosa.midi_to_hz(min_pitch))
    max_freq = float(librosa.midi_to_hz(max_pitch))

    try:
        from basic_pitch.inference import predict
        from basic_pitch import ICASSP_2022_MODEL_PATH

        _, midi_data, note_events = predict(
            str(audio_path),
            model_or_model_path=ICASSP_2022_MODEL_PATH,
            onset_threshold=params.onset_threshold,
            frame_threshold=params.frame_threshold,
            minimum_note_length=params.minimum_note_length_ms,
            minimum_frequency=min_freq,
            maximum_frequency=max_freq,
        )
        extracted: list[NoteEvent] = []
        for start_s, end_s, pitch, amplitude, _ in note_events:
            vel = int(np.clip(round(float(amplitude) * 127.0), 1, 127))
            extracted.append(
                NoteEvent(
                    pitch=int(pitch),
                    start=float(start_s),
                    end=float(end_s),
                    velocity=vel,
                )
            )
        if extracted:
            return extracted
    except Exception:
        pass

    # Fallback deterministic Librosa pYIN + onset note extractor
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    if y.size == 0:
        return []
    hop_length = 256
    f0, voiced_flag, voiced_probs = librosa.pyin(
        y,
        fmin=min_freq,
        fmax=max_freq,
        sr=sr,
        hop_length=hop_length,
    )
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    max_rms = float(np.max(rms)) if rms.size > 0 else 1.0
    times = librosa.times_like(f0, sr=sr, hop_length=hop_length)
    frame_dt = float(hop_length / sr)

    notes: list[NoteEvent] = []
    cur_pitch: int | None = None
    cur_start: float = 0.0
    cur_vels: list[int] = []

    for idx, (freq, is_voiced, prob) in enumerate(zip(f0, voiced_flag, voiced_probs)):
        amp_norm = float(rms[idx] / (max_rms + 1e-9)) if idx < len(rms) else 0.0
        active = (
            bool(is_voiced)
            and np.isfinite(freq)
            and float(prob) >= params.frame_threshold * 0.5
            and amp_norm >= params.onset_threshold * 0.25
        )
        if active:
            midi_p = int(round(float(librosa.hz_to_midi(freq))))
            vel = int(np.clip(round(amp_norm * 120.0), 20, 127))
            if cur_pitch is None:
                cur_pitch = midi_p
                cur_start = float(times[idx])
                cur_vels = [vel]
            elif midi_p == cur_pitch:
                cur_vels.append(vel)
            else:
                notes.append(
                    NoteEvent(
                        pitch=cur_pitch,
                        start=cur_start,
                        end=float(times[idx]),
                        velocity=int(np.mean(cur_vels)),
                    )
                )
                cur_pitch = midi_p
                cur_start = float(times[idx])
                cur_vels = [vel]
        else:
            if cur_pitch is not None:
                notes.append(
                    NoteEvent(
                        pitch=cur_pitch,
                        start=cur_start,
                        end=float(times[idx]) + frame_dt,
                        velocity=int(np.mean(cur_vels)),
                    )
                )
                cur_pitch = None
                cur_vels = []

    if cur_pitch is not None and len(times) > 0:
        notes.append(
            NoteEvent(
                pitch=cur_pitch,
                start=cur_start,
                end=float(times[-1]) + frame_dt,
                velocity=int(np.mean(cur_vels)),
            )
        )
    return notes


def postprocess_notes(
    notes: list[NoteEvent],
    params: TranscriptionParams,
) -> tuple[list[NoteEvent], dict[str, int]]:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    num_strings = len(preset.pitches)
    min_pitch, max_pitch = _tuning_pitch_bounds(params.tuning, params.max_frets)
    min_len_s = params.minimum_note_length_ms / 1000.0

    short_removed = 0
    out_of_range_adjusted = 0
    duration_filtered: list[NoteEvent] = []

    for n in sorted(notes, key=lambda x: (x.start, x.pitch)):
        if (n.end - n.start) < min_len_s:
            short_removed += 1
            continue
        pitch = n.pitch
        if pitch < min_pitch:
            while pitch < min_pitch:
                pitch += 12
            out_of_range_adjusted += 1
        elif pitch > max_pitch:
            while pitch > max_pitch:
                pitch -= 12
            out_of_range_adjusted += 1
        duration_filtered.append(
            NoteEvent(pitch=pitch, start=n.start, end=n.end, velocity=n.velocity)
        )

    # Harmonic suppression (+12 octave and +19 octave+fifth within 35ms onset window)
    harmonics_removed = 0
    if params.suppress_harmonics and duration_filtered:
        kept: list[NoteEvent] = []
        for idx, candidate in enumerate(duration_filtered):
            is_spurious = False
            for other in duration_filtered:
                if other is candidate:
                    continue
                if abs(candidate.start - other.start) <= 0.035:
                    interval = candidate.pitch - other.pitch
                    if interval in (12, 19):
                        if candidate.velocity < (
                            other.velocity * params.harmonic_velocity_ratio
                        ):
                            is_spurious = True
                            break
            if is_spurious:
                harmonics_removed += 1
            else:
                kept.append(candidate)
    else:
        kept = duration_filtered

    # Group simultaneous onsets (within 30ms) to enforce max polyphony = num_strings
    polyphony_capped: list[NoteEvent] = []
    i = 0
    while i < len(kept):
        group = [kept[i]]
        j = i + 1
        while j < len(kept) and (kept[j].start - group[0].start) <= 0.030:
            group.append(kept[j])
            j += 1
        # Deduplicate identical pitches in the same onset slice (keep loudest)
        by_pitch: dict[int, NoteEvent] = {}
        for g in group:
            if g.pitch not in by_pitch or g.velocity > by_pitch[g.pitch].velocity:
                by_pitch[g.pitch] = g
        unique_group = list(by_pitch.values())
        if len(unique_group) > num_strings:
            unique_group.sort(key=lambda x: x.velocity, reverse=True)
            unique_group = unique_group[:num_strings]
        unique_group.sort(key=lambda x: x.pitch)
        polyphony_capped.extend(unique_group)
        i = j

    # Optional rhythmic quantization
    final_notes: list[NoteEvent] = []
    if params.quantize_grid in ("1/16", "1/32") and params.bpm > 0:
        subdiv = 4.0 if params.quantize_grid == "1/16" else 8.0
        step = (60.0 / params.bpm) / subdiv
        for n in polyphony_capped:
            q_start = round(n.start / step) * step
            q_end = max(q_start + step, round(n.end / step) * step)
            final_notes.append(
                NoteEvent(
                    pitch=n.pitch,
                    start=round(q_start, 6),
                    end=round(q_end, 6),
                    velocity=n.velocity,
                )
            )
    else:
        final_notes = polyphony_capped

    stats = {
        "short_notes_removed": short_removed,
        "harmonics_removed": harmonics_removed,
        "out_of_range_adjusted": out_of_range_adjusted,
        "total_notes": len(final_notes),
    }
    return final_notes, stats


def notes_to_midi_file(
    notes: list[NoteEvent],
    output_midi_path: Path,
    bpm: float = 120.0,
) -> Path:
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(bpm))
    guitar_program = pretty_midi.instrument_name_to_program("Electric Guitar (distortion)")
    instrument = pretty_midi.Instrument(program=guitar_program, name="Guitar")
    for n in notes:
        instrument.notes.append(
            pretty_midi.Note(
                velocity=int(np.clip(n.velocity, 1, 127)),
                pitch=int(n.pitch),
                start=float(n.start),
                end=float(max(n.start + 0.02, n.end)),
            )
        )
    pm.instruments.append(instrument)
    output_midi_path.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(output_midi_path))
    return output_midi_path


def midi_file_to_notes(midi_path: Path) -> list[NoteEvent]:
    pm = pretty_midi.PrettyMIDI(str(midi_path))
    notes: list[NoteEvent] = []
    for inst in pm.instruments:
        if inst.is_drum:
            continue
        for n in inst.notes:
            notes.append(
                NoteEvent(
                    pitch=int(n.pitch),
                    start=float(n.start),
                    end=float(n.end),
                    velocity=int(n.velocity),
                )
            )
    notes.sort(key=lambda x: (x.start, x.pitch))
    return notes


def transcribe_audio_to_midi(
    audio_path: Path,
    output_midi_path: Path,
    params: TranscriptionParams,
) -> tuple[list[NoteEvent], dict[str, int | float | str]]:
    raw_notes = _run_basic_pitch_or_fallback(audio_path, params)
    cleaned_notes, stats = postprocess_notes(raw_notes, params)
    if cleaned_notes:
        notes_to_midi_file(cleaned_notes, output_midi_path, bpm=params.bpm)
        min_p = min(n.pitch for n in cleaned_notes)
        max_p = max(n.pitch for n in cleaned_notes)
        stats["lowest_note"] = str(librosa.midi_to_note(min_p))
        stats["highest_note"] = str(librosa.midi_to_note(max_p))
    return cleaned_notes, stats
```

- [ ] **Step 4: Run test to verify it passes and commit**

Run: `pytest tests/test_midi_transcriber.py -v`
Expected: PASS (3 passed)

Commit:
```powershell
git add app/pipeline/midi_transcriber.py tests/test_midi_transcriber.py
git commit -m "feat: add Basic Pitch MIDI transcriber and Metal harmonic post-processor"
```

---

### Task 5: Viterbi Fretboard Optimizer

**Files:**
- Create: `app/pipeline/fretboard.py`
- Test: `tests/test_fretboard.py`

**Interfaces:**
- Consumes: `notes: list[NoteEvent]`, `params: TabParams`
- Produces:
  - `optimize_fretboard(notes: list[NoteEvent], params: TabParams) -> tuple[list[TabSlice], dict[str, int]]`

- [ ] **Step 1: Write the failing test in `tests/test_fretboard.py`**

```python
from app.models import NoteEvent, TabParams
from app.pipeline.fretboard import optimize_fretboard


def test_viterbi_assigns_distinct_strings_for_chords():
    # E5 power chord in Standard E: E2 (40), B2 (47), E3 (52)
    notes = [
        NoteEvent(pitch=40, start=0.0, end=0.5, velocity=100),
        NoteEvent(pitch=47, start=0.0, end=0.5, velocity=95),
        NoteEvent(pitch=52, start=0.0, end=0.5, velocity=90),
    ]
    params = TabParams(tuning="standard_e", max_frets=24, fret_preference="low")
    slices, stats = optimize_fretboard(notes, params)
    assert len(slices) == 1
    strings_used = [n.string_idx for n in slices[0].notes]
    assert len(strings_used) == len(set(strings_used)) == 3
    # In low position: E2=(str 0, fret 0), B2=(str 1, fret 2), E3=(str 2, fret 2)
    mapping = {n.string_idx: n.fret for n in slices[0].notes}
    assert mapping == {0: 0, 1: 2, 2: 2}
    assert stats["transposed_notes"] == 0


def test_viterbi_drop_d_power_chord_barre_bonus():
    # E5 power chord in Drop D: E2 (40), B2 (47), E3 (52) -> barre on fret 2 across strings 0, 1, 2
    notes = [
        NoteEvent(pitch=40, start=0.0, end=0.5, velocity=100),
        NoteEvent(pitch=47, start=0.0, end=0.5, velocity=95),
        NoteEvent(pitch=52, start=0.0, end=0.5, velocity=90),
    ]
    params = TabParams(tuning="drop_d", max_frets=24, fret_preference="low")
    slices, _ = optimize_fretboard(notes, params)
    mapping = {n.string_idx: n.fret for n in slices[0].notes}
    assert mapping == {0: 2, 1: 2, 2: 2}


def test_viterbi_maintains_ergonomic_position_in_high_preference():
    # Melody: C4 (60), D4 (62), E4 (64) played fast with "high" fret preference
    notes = [
        NoteEvent(pitch=60, start=0.0, end=0.2, velocity=90),
        NoteEvent(pitch=62, start=0.2, end=0.4, velocity=90),
        NoteEvent(pitch=64, start=0.4, end=0.6, velocity=90),
    ]
    params = TabParams(tuning="standard_e", max_frets=24, fret_preference="high")
    slices, _ = optimize_fretboard(notes, params)
    assert len(slices) == 3
    frets = [s.notes[0].fret for s in slices]
    # Should stay in the high region (frets >= 5) rather than open string E4 (fret 0)
    assert all(f >= 5 for f in frets)
    assert max(frets) - min(frets) <= 5


def test_viterbi_seven_string_drop_a_and_out_of_range_transposition():
    # Note below A1 (pitch 33): F1 (pitch 29) -> transposed +12 to F2 (41)
    notes = [
        NoteEvent(pitch=29, start=0.0, end=0.5, velocity=100),
        NoteEvent(pitch=33, start=0.5, end=1.0, velocity=100),  # Open A1 on 7-string Drop A
    ]
    params = TabParams(tuning="seven_drop_a", max_frets=24, fret_preference="low")
    slices, stats = optimize_fretboard(notes, params)
    assert len(slices) == 2
    assert stats["transposed_notes"] == 1
    assert slices[0].notes[0].pitch == 41
    assert slices[1].notes[0].string_idx == 0
    assert slices[1].notes[0].fret == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fretboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipeline.fretboard'`

- [ ] **Step 3: Implement `app/pipeline/fretboard.py`**

Create `app/pipeline/fretboard.py`:

```python
from __future__ import annotations
import itertools
from app.models import FrettedNote, NoteEvent, TUNINGS, TabParams, TabSlice, TuningPreset


StateAssignment = tuple[tuple[int, int, NoteEvent], ...]  # ((string_idx, fret, note), ...)


def _group_into_time_slices(
    notes: list[NoteEvent],
    window_s: float = 0.030,
) -> list[list[NoteEvent]]:
    if not notes:
        return []
    sorted_notes = sorted(notes, key=lambda n: (n.start, n.pitch))
    slices: list[list[NoteEvent]] = []
    cur_group = [sorted_notes[0]]
    for n in sorted_notes[1:]:
        if (n.start - cur_group[0].start) <= window_s:
            cur_group.append(n)
        else:
            slices.append(cur_group)
            cur_group = [n]
    slices.append(cur_group)
    return slices


def _clamp_notes_to_tuning(
    slice_notes: list[NoteEvent],
    preset: TuningPreset,
    max_frets: int,
) -> tuple[list[NoteEvent], int]:
    min_pitch = min(preset.pitches)
    max_pitch = max(preset.pitches) + max_frets
    adjusted_count = 0
    by_pitch: dict[int, NoteEvent] = {}

    for n in slice_notes:
        p = n.pitch
        if p < min_pitch:
            while p < min_pitch:
                p += 12
            adjusted_count += 1
        elif p > max_pitch:
            while p > max_pitch:
                p -= 12
            adjusted_count += 1
        clamped = NoteEvent(pitch=p, start=n.start, end=n.end, velocity=n.velocity)
        if p not in by_pitch or clamped.velocity > by_pitch[p].velocity:
            by_pitch[p] = clamped

    unique = sorted(by_pitch.values(), key=lambda x: x.pitch)
    return unique, adjusted_count


def _generate_candidates_for_notes(
    notes_subset: list[NoteEvent],
    preset: TuningPreset,
    max_frets: int,
) -> list[StateAssignment]:
    per_note_options: list[list[tuple[int, int, NoteEvent]]] = []
    for note in notes_subset:
        options: list[tuple[int, int, NoteEvent]] = []
        for s_idx, open_pitch in enumerate(preset.pitches):
            fret = note.pitch - open_pitch
            if 0 <= fret <= max_frets:
                options.append((s_idx, fret, note))
        if not options:
            return []
        per_note_options.append(options)

    valid_states: list[StateAssignment] = []
    for combo in itertools.product(*per_note_options):
        strings = [item[0] for item in combo]
        if len(set(strings)) != len(strings):
            continue
        fretted = [item[1] for item in combo if item[1] > 0]
        if len(fretted) >= 2 and (max(fretted) - min(fretted)) > 5:
            continue
        valid_states.append(tuple(sorted(combo, key=lambda x: x[0])))
    return valid_states


def _get_valid_candidates_with_dropping(
    slice_notes: list[NoteEvent],
    preset: TuningPreset,
    max_frets: int,
) -> tuple[list[StateAssignment], int]:
    working = list(slice_notes)
    dropped = 0
    num_strings = len(preset.pitches)
    if len(working) > num_strings:
        working.sort(key=lambda x: x.velocity, reverse=True)
        dropped += len(working) - num_strings
        working = sorted(working[:num_strings], key=lambda x: x.pitch)

    while working:
        candidates = _generate_candidates_for_notes(working, preset, max_frets)
        if candidates:
            return candidates, dropped
        # Drop lowest velocity note and retry
        min_vel_note = min(working, key=lambda x: (x.velocity, -x.pitch))
        working.remove(min_vel_note)
        dropped += 1

    return [], dropped


def _state_cost(
    state: StateAssignment,
    preset: TuningPreset,
    preference: str,
) -> float:
    frets = [item[1] for item in state]
    fretted = [f for f in frets if f > 0]
    cost = 0.0

    # Hand span penalty
    if len(fretted) >= 2:
        span = max(fretted) - min(fretted)
        cost += span * 1.4

    # Fretboard region preference
    avg_fret = sum(frets) / len(frets)
    if preference == "low":
        if avg_fret > 7.0:
            cost += (avg_fret - 7.0) * 1.8
        cost += avg_fret * 0.35
    elif preference == "mid":
        if avg_fret < 3.0:
            cost += (3.0 - avg_fret) * 2.0
        elif avg_fret > 12.0:
            cost += (avg_fret - 12.0) * 1.8
        cost += abs(avg_fret - 7.0) * 0.4
    elif preference == "high":
        if avg_fret < 7.0:
            cost += (7.0 - avg_fret) * 2.5
        elif avg_fret > 19.0:
            cost += (avg_fret - 19.0) * 1.5
        cost += abs(avg_fret - 12.0) * 0.3
    else:  # auto
        cost += avg_fret * 0.20

    # Drop tuning power-chord barre bonus on lowest 3 strings
    if preset.is_drop and len(state) >= 2:
        low_map = {item[0]: item[1] for item in state if item[0] in (0, 1, 2)}
        if 0 in low_map and 1 in low_map and low_map[0] == low_map[1] and low_map[0] > 0:
            cost -= 3.0
            if 2 in low_map and low_map[2] == low_map[0]:
                cost -= 2.0

    return cost


def _hand_center(state: StateAssignment) -> float:
    fretted = [item[1] for item in state if item[1] > 0]
    if not fretted:
        return 0.0
    return sum(fretted) / len(fretted)


def _transition_cost(
    prev_state: StateAssignment,
    curr_state: StateAssignment,
) -> float:
    c_prev = _hand_center(prev_state)
    c_curr = _hand_center(curr_state)
    prev_start = prev_state[0][2].start
    curr_start = curr_state[0][2].start
    dt = max(0.05, curr_start - prev_start)

    # If either state is purely open strings, moving the hand has lower penalty
    if c_prev == 0.0 or c_curr == 0.0:
        dist_cost = abs(c_curr - c_prev) * 0.25
    else:
        time_weight = min(4.0, 0.25 / dt)
        dist_cost = abs(c_curr - c_prev) * (1.0 + time_weight)

    # Mild string jump penalty for consecutive single notes
    string_penalty = 0.0
    if len(prev_state) == 1 and len(curr_state) == 1:
        s_diff = abs(curr_state[0][0] - prev_state[0][0])
        string_penalty = s_diff * 0.45

    return dist_cost + string_penalty


def optimize_fretboard(
    notes: list[NoteEvent],
    params: TabParams,
) -> tuple[list[TabSlice], dict[str, int]]:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    raw_slices = _group_into_time_slices(notes, window_s=0.030)
    if not raw_slices:
        return [], {"transposed_notes": 0, "dropped_notes": 0, "total_slices": 0}

    all_candidates: list[list[StateAssignment]] = []
    total_transposed = 0
    total_dropped = 0

    for grp in raw_slices:
        clamped, transposed = _clamp_notes_to_tuning(grp, preset, params.max_frets)
        total_transposed += transposed
        cands, dropped = _get_valid_candidates_with_dropping(
            clamped, preset, params.max_frets
        )
        total_dropped += dropped
        if cands:
            all_candidates.append(cands)

    if not all_candidates:
        return [], {
            "transposed_notes": total_transposed,
            "dropped_notes": total_dropped,
            "total_slices": 0,
        }

    # Viterbi dynamic programming
    dp_costs: list[list[float]] = []
    backpointers: list[list[int]] = []

    first_costs = [
        _state_cost(st, preset, params.fret_preference) for st in all_candidates[0]
    ]
    dp_costs.append(first_costs)
    backpointers.append([-1] * len(all_candidates[0]))

    for t in range(1, len(all_candidates)):
        prev_states = all_candidates[t - 1]
        curr_states = all_candidates[t]
        prev_costs = dp_costs[t - 1]

        curr_costs: list[float] = []
        curr_back: list[int] = []

        for c_state in curr_states:
            s_cost = _state_cost(c_state, preset, params.fret_preference)
            best_prev_idx = 0
            best_val = float("inf")
            for p_idx, p_state in enumerate(prev_states):
                total = prev_costs[p_idx] + _transition_cost(p_state, c_state) + s_cost
                if total < best_val:
                    best_val = total
                    best_prev_idx = p_idx
            curr_costs.append(best_val)
            curr_back.append(best_prev_idx)

        dp_costs.append(curr_costs)
        backpointers.append(curr_back)

    # Backtrack optimal path
    last_idx = int(min(range(len(dp_costs[-1])), key=lambda i: dp_costs[-1][i]))
    chosen_states: list[StateAssignment] = [all_candidates[-1][last_idx]]
    for t in range(len(all_candidates) - 1, 0, -1):
        last_idx = backpointers[t][last_idx]
        chosen_states.append(all_candidates[t - 1][last_idx])
    chosen_states.reverse()

    tab_slices: list[TabSlice] = []
    for state in chosen_states:
        start_t = min(item[2].start for item in state)
        end_t = max(item[2].end for item in state)
        fretted_notes = [
            FrettedNote(
                string_idx=item[0],
                fret=item[1],
                pitch=item[2].pitch,
                start=item[2].start,
                end=item[2].end,
                velocity=item[2].velocity,
            )
            for item in state
        ]
        tab_slices.append(TabSlice(start=start_t, end=end_t, notes=fretted_notes))

    stats = {
        "transposed_notes": total_transposed,
        "dropped_notes": total_dropped,
        "total_slices": len(tab_slices),
    }
    return tab_slices, stats
```

- [ ] **Step 4: Run test to verify it passes and commit**

Run: `pytest tests/test_fretboard.py -v`
Expected: PASS (4 passed)

Commit:
```powershell
git add app/pipeline/fretboard.py tests/test_fretboard.py
git commit -m "feat: add Viterbi guitar fretboard optimizer supporting 6 and 7 string tunings"
```

---

### Task 6: Tab Exporters — MusicXML, Guitar Pro 5 (`.gp5`), and ASCII Tab (`.txt`)

**Files:**
- Create: `app/pipeline/exporters.py`
- Test: `tests/test_exporters.py`

**Interfaces:**
- Consumes: `slices: list[TabSlice]`, `params: TabParams`, `output_path: Path`, `title: str`
- Produces:
  - `export_musicxml(slices: list[TabSlice], params: TabParams, output_path: Path, title: str = "EscriTab Transcription") -> Path`
  - `export_gp5(slices: list[TabSlice], params: TabParams, output_path: Path, title: str = "EscriTab Transcription") -> Path`
  - `export_ascii_tab(slices: list[TabSlice], params: TabParams, output_path: Path, title: str = "EscriTab Transcription") -> Path`

- [ ] **Step 1: Write the failing test in `tests/test_exporters.py`**

```python
from pathlib import Path
import xml.etree.ElementTree as ET
import guitarpro
from app.models import FrettedNote, TabParams, TabSlice
from app.pipeline.exporters import export_ascii_tab, export_gp5, export_musicxml


def _sample_slices() -> list[TabSlice]:
    return [
        TabSlice(
            start=0.0,
            end=0.5,
            notes=[
                FrettedNote(string_idx=0, fret=2, pitch=40, start=0.0, end=0.5, velocity=100),
                FrettedNote(string_idx=1, fret=2, pitch=47, start=0.0, end=0.5, velocity=95),
            ],
        ),
        TabSlice(
            start=0.5,
            end=1.0,
            notes=[
                FrettedNote(string_idx=2, fret=4, pitch=54, start=0.5, end=1.0, velocity=90),
            ],
        ),
    ]


def test_export_musicxml_valid_structure(tmp_path: Path):
    slices = _sample_slices()
    params = TabParams(tuning="drop_d", bpm=120.0)
    xml_path = tmp_path / "tab.musicxml"
    export_musicxml(slices, params, xml_path, title="Drop D Riff")

    assert xml_path.exists()
    tree = ET.parse(xml_path)
    root = tree.getroot()
    assert root.tag == "score-partwise"

    staff_lines = root.find(".//staff-details/staff-lines")
    assert staff_lines is not None and staff_lines.text == "6"

    frets = [el.text for el in root.findall(".//technical/fret")]
    strings = [el.text for el in root.findall(".//technical/string")]
    assert frets == ["2", "2", "4"]
    # MusicXML string numbering is 1 = highest string, 6 = lowest string
    assert strings == ["6", "5", "4"]


def test_export_gp5_parses_with_pyguitarpro(tmp_path: Path):
    slices = _sample_slices()
    params = TabParams(tuning="drop_d", bpm=140.0)
    gp5_path = tmp_path / "tab.gp5"
    export_gp5(slices, params, gp5_path, title="Drop D Riff")

    assert gp5_path.exists()
    song = guitarpro.parse(str(gp5_path))
    assert song.tempo == 140
    track = song.tracks[0]
    assert len(track.strings) == 6
    # pyguitarpro strings are ordered 1 (highest) to N (lowest)
    assert track.strings[-1].value == 38  # D2 on lowest string


def test_export_ascii_tab_renders_strings_and_frets(tmp_path: Path):
    slices = _sample_slices()
    params = TabParams(tuning="drop_d", bpm=120.0)
    txt_path = tmp_path / "tab.txt"
    export_ascii_tab(slices, params, txt_path, title="Drop D Riff")

    content = txt_path.read_text(encoding="utf-8")
    assert "Drop D" in content
    assert "D2|" in content
    assert "E4|" in content
    assert "-2-" in content
    assert "-4-" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_exporters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.pipeline.exporters'`

- [ ] **Step 3: Implement `app/pipeline/exporters.py`**

Create `app/pipeline/exporters.py`:

```python
from __future__ import annotations
from pathlib import Path
import xml.etree.ElementTree as ET
import guitarpro
from app.models import TUNINGS, TabParams, TabSlice


PITCH_CLASS_NAMES = [
    ("C", 0),
    ("C", 1),
    ("D", 0),
    ("D", 1),
    ("E", 0),
    ("F", 0),
    ("F", 1),
    ("G", 0),
    ("G", 1),
    ("A", 0),
    ("A", 1),
    ("B", 0),
]


def _midi_to_step_alter_octave(midi_pitch: int) -> tuple[str, int, int]:
    pc = midi_pitch % 12
    octave = (midi_pitch // 12) - 1
    step, alter = PITCH_CLASS_NAMES[pc]
    return step, alter, octave


def _duration_to_xml_type(quarters: float) -> str:
    if quarters >= 3.0:
        return "whole"
    if quarters >= 1.5:
        return "half"
    if quarters >= 0.75:
        return "quarter"
    if quarters >= 0.375:
        return "eighth"
    return "16th"


def export_musicxml(
    slices: list[TabSlice],
    params: TabParams,
    output_path: Path,
    title: str = "EscriTab Transcription",
) -> Path:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    num_strings = len(preset.pitches)
    divisions = 4  # 4 divisions per quarter note (16th-note resolution)
    quarter_sec = 60.0 / params.bpm
    measure_sec = 4.0 * quarter_sec  # 4/4 measure

    score = ET.Element("score-partwise", version="3.1")
    work = ET.SubElement(score, "work")
    ET.SubElement(work, "work-title").text = title

    part_list = ET.SubElement(score, "part-list")
    score_part = ET.SubElement(part_list, "score-part", id="P1")
    ET.SubElement(score_part, "part-name").text = f"Guitar ({preset.name})"

    part = ET.SubElement(score, "part", id="P1")

    max_time = max((s.end for s in slices), default=measure_sec)
    total_measures = max(1, int(max_time // measure_sec) + 1)

    for m_idx in range(total_measures):
        m_start = m_idx * measure_sec
        m_end = (m_idx + 1) * measure_sec
        measure = ET.SubElement(part, "measure", number=str(m_idx + 1))

        if m_idx == 0:
            attrs = ET.SubElement(measure, "attributes")
            ET.SubElement(attrs, "divisions").text = str(divisions)
            key = ET.SubElement(attrs, "key")
            ET.SubElement(key, "fifths").text = "0"
            time_el = ET.SubElement(attrs, "time")
            ET.SubElement(time_el, "beats").text = "4"
            ET.SubElement(time_el, "beat-type").text = "4"
            clef = ET.SubElement(attrs, "clef")
            ET.SubElement(clef, "sign").text = "TAB"
            ET.SubElement(clef, "line").text = "5"

            staff_details = ET.SubElement(attrs, "staff-details")
            ET.SubElement(staff_details, "staff-lines").text = str(num_strings)
            for s_idx, pitch in enumerate(preset.pitches):
                step, alter, octave = _midi_to_step_alter_octave(pitch)
                st = ET.SubElement(staff_details, "staff-tuning", line=str(s_idx + 1))
                ET.SubElement(st, "tuning-step").text = step
                if alter != 0:
                    ET.SubElement(st, "tuning-alter").text = str(alter)
                ET.SubElement(st, "tuning-octave").text = str(octave)

            direction = ET.SubElement(measure, "direction", placement="above")
            dir_type = ET.SubElement(direction, "direction-type")
            metronome = ET.SubElement(dir_type, "metronome")
            ET.SubElement(metronome, "beat-unit").text = "quarter"
            ET.SubElement(metronome, "per-minute").text = str(int(round(params.bpm)))
            ET.SubElement(direction, "sound", tempo=str(int(round(params.bpm))))

        m_slices = [s for s in slices if m_start <= s.start < m_end]
        if not m_slices:
            note_el = ET.SubElement(measure, "note")
            ET.SubElement(note_el, "rest")
            ET.SubElement(note_el, "duration").text = str(divisions * 4)
            ET.SubElement(note_el, "type").text = "whole"
            continue

        for sl in m_slices:
            dur_sec = max(quarter_sec * 0.25, sl.end - sl.start)
            quarters = dur_sec / quarter_sec
            dur_divs = max(1, min(divisions * 4, int(round(quarters * divisions))))
            xml_type = _duration_to_xml_type(quarters)

            for n_idx, fn in enumerate(sl.notes):
                note_el = ET.SubElement(measure, "note")
                if n_idx > 0:
                    ET.SubElement(note_el, "chord")
                step, alter, octave = _midi_to_step_alter_octave(fn.pitch)
                pitch_el = ET.SubElement(note_el, "pitch")
                ET.SubElement(pitch_el, "step").text = step
                if alter != 0:
                    ET.SubElement(pitch_el, "alter").text = str(alter)
                ET.SubElement(pitch_el, "octave").text = str(octave)

                ET.SubElement(note_el, "duration").text = str(dur_divs)
                ET.SubElement(note_el, "voice").text = "1"
                ET.SubElement(note_el, "type").text = xml_type

                notations = ET.SubElement(note_el, "notations")
                technical = ET.SubElement(notations, "technical")
                # MusicXML convention: string 1 is highest pitch string, N is lowest pitch string
                xml_string_num = num_strings - fn.string_idx
                ET.SubElement(technical, "string").text = str(xml_string_num)
                ET.SubElement(technical, "fret").text = str(fn.fret)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(score, space="  ")
    xml_bytes = ET.tostring(score, encoding="utf-8", xml_declaration=True)
    output_path.write_bytes(xml_bytes)
    return output_path


def export_gp5(
    slices: list[TabSlice],
    params: TabParams,
    output_path: Path,
    title: str = "EscriTab Transcription",
) -> Path:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    num_strings = len(preset.pitches)
    song = guitarpro.models.Song()
    song.title = title
    song.tempo = int(round(params.bpm))

    track = song.tracks[0]
    track.name = f"Guitar ({preset.id})"
    track.fretCount = params.max_frets
    # pyguitarpro strings: index 1 is highest string, N is lowest string
    track.strings = [
        guitarpro.models.GuitarString(number=i + 1, value=pitch)
        for i, pitch in enumerate(reversed(preset.pitches))
    ]

    quarter_sec = 60.0 / params.bpm
    measure_sec = 4.0 * quarter_sec
    max_time = max((s.end for s in slices), default=measure_sec)
    total_measures = max(1, int(max_time // measure_sec) + 1)

    while len(song.measureHeaders) < total_measures:
        header = guitarpro.models.MeasureHeader(number=len(song.measureHeaders) + 1)
        song.measureHeaders.append(header)
        measure = guitarpro.models.Measure(track=track, header=header)
        track.measures.append(measure)

    for m_idx in range(total_measures):
        m_start = m_idx * measure_sec
        m_end = (m_idx + 1) * measure_sec
        measure = track.measures[m_idx]
        voice = measure.voices[0]
        voice.beats.clear()

        m_slices = [s for s in slices if m_start <= s.start < m_end]
        if not m_slices:
            beat = guitarpro.models.Beat(
                voice=voice,
                status=guitarpro.models.BeatStatus.rest,
                duration=guitarpro.models.Duration(value=1),
            )
            voice.beats.append(beat)
            continue

        for sl in m_slices:
            dur_sec = max(quarter_sec * 0.25, sl.end - sl.start)
            quarters = dur_sec / quarter_sec
            if quarters >= 3.0:
                dur_val = 1
            elif quarters >= 1.5:
                dur_val = 2
            elif quarters >= 0.75:
                dur_val = 4
            elif quarters >= 0.375:
                dur_val = 8
            else:
                dur_val = 16

            beat = guitarpro.models.Beat(
                voice=voice,
                status=guitarpro.models.BeatStatus.normal,
                duration=guitarpro.models.Duration(value=dur_val),
            )
            for fn in sl.notes:
                gp_string_num = num_strings - fn.string_idx
                note = guitarpro.models.Note(
                    beat=beat,
                    value=fn.fret,
                    velocity=fn.velocity,
                    string=gp_string_num,
                    type=guitarpro.models.NoteType.normal,
                )
                beat.notes.append(note)
            voice.beats.append(beat)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    guitarpro.write(song, str(output_path), version=(5, 1, 0))
    return output_path


def export_ascii_tab(
    slices: list[TabSlice],
    params: TabParams,
    output_path: Path,
    title: str = "EscriTab Transcription",
) -> Path:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    num_strings = len(preset.pitches)
    labels = [name.ljust(3) for name in reversed(preset.string_names)]

    header_lines = [
        f"# {title}",
        f"# Afinação: {preset.name} | BPM: {params.bpm:.1f}",
        "",
    ]

    quarter_sec = 60.0 / params.bpm
    measure_sec = 4.0 * quarter_sec
    columns_per_system = 16

    cols: list[tuple[int, dict[int, int]]] = []
    for sl in slices:
        m_num = int(sl.start // measure_sec) + 1
        col_map = {fn.string_idx: fn.fret for fn in sl.notes}
        cols.append((m_num, col_map))

    if not cols:
        cols.append((1, {}))

    systems: list[str] = []
    for start_idx in range(0, len(cols), columns_per_system):
        chunk = cols[start_idx : start_idx + columns_per_system]
        rows = [f"{lbl}|" for lbl in labels]
        cur_measure = chunk[0][0]
        for m_num, col_map in chunk:
            if m_num != cur_measure:
                for r in range(num_strings):
                    rows[r] += "|"
                cur_measure = m_num
            for r in range(num_strings):
                # row r=0 is highest string (string_idx = num_strings - 1)
                s_idx = (num_strings - 1) - r
                if s_idx in col_map:
                    fret_str = str(col_map[s_idx])
                    cell = f"-{fret_str}-" if len(fret_str) == 1 else f"-{fret_str}"
                else:
                    cell = "---"
                rows[r] += cell + "-"
        for r in range(num_strings):
            rows[r] += "|"
        systems.append("\n".join(rows))

    full_text = "\n".join(header_lines) + "\n\n".join(systems) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(full_text, encoding="utf-8")
    return output_path
```

- [ ] **Step 4: Run test to verify it passes and commit**

Run: `pytest tests/test_exporters.py -v`
Expected: PASS (3 passed)

Commit:
```powershell
git add app/pipeline/exporters.py tests/test_exporters.py
git commit -m "feat: add MusicXML, GP5, and ASCII tab exporters"
```

---

### Task 7: FastAPI Server, REST Endpoints & Entrypoint

**Files:**
- Create: `app/main.py`
- Create: `run.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `JobManager`, `separate_stems`, `prepare_clean_audio`, `detect_bpm`, `transcribe_audio_to_midi`, `midi_file_to_notes`, `optimize_fretboard`, `export_musicxml`, `export_gp5`, `export_ascii_tab`
- Produces:
  - FastAPI `app` instance with REST routes:
    - `GET /api/tunings`
    - `GET /api/jobs`
    - `POST /api/jobs/upload`
    - `POST /api/jobs/{job_id}/separate`
    - `POST /api/jobs/{job_id}/transcribe`
    - `POST /api/jobs/{job_id}/tab`
    - `GET /api/jobs/{job_id}/files/{filename}`

- [ ] **Step 1: Write the failing test in `tests/test_api.py`**

```python
import io
from pathlib import Path
from fastapi.testclient import TestClient
import numpy as np
import soundfile as sf
from app.main import create_app


def _make_wav_bytes(freq_hz: float = 110.0, duration_s: float = 1.0, sr: int = 22050) -> bytes:
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    audio = (0.8 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV")
    return buf.getvalue()


def test_full_api_pipeline_isolated_stem(tmp_path: Path):
    app = create_app(workspace_root=tmp_path)
    client = TestClient(app)

    # 1. GET /api/tunings
    res_tunings = client.get("/api/tunings")
    assert res_tunings.status_code == 200
    assert len(res_tunings.json()) == 8

    # 2. POST /api/jobs/upload
    wav_bytes = _make_wav_bytes(freq_hz=110.0, duration_s=1.2)
    res_upload = client.post(
        "/api/jobs/upload",
        files={"file": ("suno_guitar.wav", wav_bytes, "audio/wav")},
        data={"is_isolated_stem": "true"},
    )
    assert res_upload.status_code == 200
    job = res_upload.json()
    job_id = job["job_id"]
    assert job["is_isolated_stem"] is True

    # 3. POST /api/jobs/{job_id}/separate
    res_sep = client.post(f"/api/jobs/{job_id}/separate", json={"model": "htdemucs_6s"})
    assert res_sep.status_code == 200
    sep_data = res_sep.json()
    assert "guitar" in sep_data["stems"]

    # 4. POST /api/jobs/{job_id}/transcribe
    res_trans = client.post(
        f"/api/jobs/{job_id}/transcribe",
        json={
            "stem": "guitar",
            "onset_threshold": 0.3,
            "frame_threshold": 0.2,
            "minimum_note_length_ms": 40.0,
            "suppress_harmonics": True,
            "tuning": "drop_d",
            "bpm": 120.0,
        },
    )
    assert res_trans.status_code == 200
    trans_data = res_trans.json()
    assert trans_data["stats"]["total_notes"] >= 1

    # 5. POST /api/jobs/{job_id}/tab
    res_tab = client.post(
        f"/api/jobs/{job_id}/tab",
        json={
            "tuning": "drop_d",
            "max_frets": 24,
            "fret_preference": "low",
            "bpm": 120.0,
        },
    )
    assert res_tab.status_code == 200
    tab_data = res_tab.json()
    assert "musicxml" in tab_data["artifacts"]
    assert "gp5" in tab_data["artifacts"]
    assert "txt" in tab_data["artifacts"]
    assert "ascii_preview" in tab_data

    # 6. GET /api/jobs/{job_id}/files/tab.gp5
    res_file = client.get(f"/api/jobs/{job_id}/files/tab.gp5")
    assert res_file.status_code == 200
    assert len(res_file.content) > 50


def test_transcribe_returns_422_on_silent_audio(tmp_path: Path):
    app = create_app(workspace_root=tmp_path)
    client = TestClient(app)

    buf = io.BytesIO()
    sf.write(buf, np.zeros(22050, dtype=np.float32), 22050, format="WAV")

    res_upload = client.post(
        "/api/jobs/upload",
        files={"file": ("silent.wav", buf.getvalue(), "audio/wav")},
        data={"is_isolated_stem": "true"},
    )
    job_id = res_upload.json()["job_id"]
    client.post(f"/api/jobs/{job_id}/separate", json={"model": "htdemucs_6s"})

    res_trans = client.post(
        f"/api/jobs/{job_id}/transcribe",
        json={"stem": "guitar", "onset_threshold": 0.7, "frame_threshold": 0.5},
    )
    assert res_trans.status_code == 422
    assert "Nenhuma nota" in res_trans.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Implement `app/main.py` and `run.py`**

Create `app/main.py`:

```python
from __future__ import annotations
from pathlib import Path
import re
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from app.job_manager import JobManager
from app.models import (
    SeparateRequest,
    TUNINGS,
    TabParams,
    TranscriptionParams,
)
from app.pipeline.audio_cleaner import detect_bpm, prepare_clean_audio
from app.pipeline.exporters import export_ascii_tab, export_gp5, export_musicxml
from app.pipeline.fretboard import optimize_fretboard
from app.pipeline.midi_transcriber import (
    midi_file_to_notes,
    transcribe_audio_to_midi,
)
from app.pipeline.stem_separator import separate_stems


def create_app(workspace_root: Path | str = Path("workspace/jobs")) -> FastAPI:
    app = FastAPI(
        title="EscriTab — Suno / Audio to Guitar Tab",
        version="1.0.0",
    )
    manager = JobManager(workspace_root=workspace_root)
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    @app.get("/api/tunings")
    def list_tunings():
        return list(TUNINGS.values())

    @app.get("/api/jobs")
    def list_jobs():
        return manager.list_jobs()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        try:
            return manager.get_job(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/jobs/upload")
    async def upload_audio(
        file: UploadFile = File(...),
        is_isolated_stem: bool = Form(False),
    ):
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="Arquivo de áudio vazio.")

        filename = file.filename or "audio.wav"
        job = manager.create_job(
            original_filename=filename,
            is_isolated_stem=is_isolated_stem,
            audio_bytes=audio_bytes,
        )
        job_dir = manager.get_job_dir(job.job_id)
        input_path = job_dir / job.input_path
        job.detected_bpm = detect_bpm(input_path, default_bpm=120.0)
        if is_isolated_stem:
            stems_map, device = separate_stems(
                input_audio_path=input_path,
                job_dir=job_dir,
                is_isolated_stem=True,
            )
            job.stems = stems_map
            job.separation_device = device
            job.selected_stem = "guitar"
        manager.save_job(job)
        return job

    @app.post("/api/jobs/{job_id}/separate")
    def separate_job_stems(job_id: str, req: SeparateRequest = SeparateRequest()):
        try:
            job = manager.get_job(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        job_dir = manager.get_job_dir(job_id)
        input_path = job_dir / job.input_path
        try:
            stems_map, device = separate_stems(
                input_audio_path=input_path,
                job_dir=job_dir,
                is_isolated_stem=job.is_isolated_stem,
                model_name=req.model,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Falha ao separar stems: {exc}"
            ) from exc

        job.stems = stems_map
        job.separation_device = device
        if "guitar" in stems_map:
            job.selected_stem = "guitar"
        elif stems_map:
            job.selected_stem = next(iter(stems_map.keys()))
        manager.save_job(job)

        stem_urls = {
            name: f"/api/jobs/{job_id}/files/{fname}"
            for name, fname in stems_map.items()
        }
        return {
            "job": job,
            "device": device,
            "stems": stems_map,
            "stem_urls": stem_urls,
        }

    @app.post("/api/jobs/{job_id}/transcribe")
    def transcribe_job(job_id: str, params: TranscriptionParams):
        try:
            job = manager.get_job(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        job_dir = manager.get_job_dir(job_id)
        requested_parts = [s.strip() for s in params.stem.split("+") if s.strip()]
        stem_paths: list[Path] = []
        for part in requested_parts:
            if part not in job.stems:
                raise HTTPException(
                    status_code=400,
                    detail=f"Stem '{part}' não encontrada neste job.",
                )
            stem_paths.append(job_dir / job.stems[part])

        if not stem_paths:
            raise HTTPException(status_code=400, detail="Nenhuma stem selecionada.")

        cleaned_wav = job_dir / "guitar_preprocessed.wav"
        prepare_clean_audio(
            stem_paths=stem_paths,
            output_path=cleaned_wav,
            bandpass_enabled=params.bandpass_enabled,
            lowcut_hz=params.lowcut_hz,
            highcut_hz=params.highcut_hz,
            noise_gate_db=params.noise_gate_db,
        )

        midi_path = job_dir / "guitar_clean.mid"
        notes, stats = transcribe_audio_to_midi(cleaned_wav, midi_path, params)
        if not notes:
            raise HTTPException(
                status_code=422,
                detail="Nenhuma nota detectada no MIDI com os filtros atuais. Reduza o Onset Threshold ou verifique se a stem selecionada contém áudio de guitarra.",
            )

        job.selected_stem = params.stem
        job.transcription_params = params
        job.artifacts["midi"] = midi_path.name
        job.artifacts["preprocessed_wav"] = cleaned_wav.name
        job.stats.update(stats)
        manager.save_job(job)

        return {
            "job": job,
            "stats": stats,
            "midi_url": f"/api/jobs/{job_id}/files/{midi_path.name}",
            "preprocessed_audio_url": f"/api/jobs/{job_id}/files/{cleaned_wav.name}",
        }

    @app.post("/api/jobs/{job_id}/tab")
    def generate_tab(job_id: str, params: TabParams):
        try:
            job = manager.get_job(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        job_dir = manager.get_job_dir(job_id)
        midi_name = job.artifacts.get("midi", "guitar_clean.mid")
        midi_path = job_dir / midi_name
        if not midi_path.exists():
            raise HTTPException(
                status_code=400,
                detail="Arquivo MIDI ainda não foi gerado para este job. Execute a transcrição primeiro.",
            )

        notes = midi_file_to_notes(midi_path)
        if not notes:
            raise HTTPException(
                status_code=422, detail="O arquivo MIDI não possui notas válidas."
            )

        slices, tab_stats = optimize_fretboard(notes, params)
        title = Path(job.original_filename).stem

        xml_path = export_musicxml(slices, params, job_dir / "tab.musicxml", title=title)
        gp5_path = export_gp5(slices, params, job_dir / "tab.gp5", title=title)
        txt_path = export_ascii_tab(slices, params, job_dir / "tab.txt", title=title)

        job.tab_params = params
        job.artifacts["musicxml"] = xml_path.name
        job.artifacts["gp5"] = gp5_path.name
        job.artifacts["txt"] = txt_path.name
        job.stats.update(tab_stats)
        manager.save_job(job)

        return {
            "job": job,
            "stats": job.stats,
            "artifacts": {
                "musicxml": f"/api/jobs/{job_id}/files/{xml_path.name}",
                "gp5": f"/api/jobs/{job_id}/files/{gp5_path.name}",
                "txt": f"/api/jobs/{job_id}/files/{txt_path.name}",
                "midi": f"/api/jobs/{job_id}/files/{midi_path.name}",
            },
            "ascii_preview": txt_path.read_text(encoding="utf-8"),
        }

    @app.get("/api/jobs/{job_id}/files/{filename}")
    def get_job_file(job_id: str, filename: str):
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "", Path(filename).name)
        job_dir = manager.get_job_dir(job_id)
        file_path = job_dir / safe_name
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
        return FileResponse(path=str(file_path), filename=safe_name)

    @app.get("/", response_class=HTMLResponse)
    def index_page():
        index_file = static_dir / "index.html"
        if index_file.exists():
            return index_file.read_text(encoding="utf-8")
        return "<h1>EscriTab Backend Active</h1>"

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    return app


app = create_app()
```

Create `run.py`:

```python
import uvicorn


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
```

- [ ] **Step 4: Run test to verify it passes and commit**

Run: `pytest tests/test_api.py -v`
Expected: PASS (2 passed)

Commit:
```powershell
git add app/main.py run.py tests/test_api.py
git commit -m "feat: add FastAPI server endpoints and entrypoint"
```

---

### Task 8: Single-Page Web UI with Stem Auditioning & AlphaTab.js Player

**Files:**
- Create: `app/static/index.html`
- Create: `app/static/styles.css`
- Create: `app/static/app.js`
- Modify: `tests/test_api.py` (add UI static serving test)

**Interfaces:**
- Consumes: REST API (`/api/tunings`, `/api/jobs`, `/api/jobs/upload`, `/api/jobs/{job_id}/separate`, `/api/jobs/{job_id}/transcribe`, `/api/jobs/{job_id}/tab`, `/api/jobs/{job_id}/files/{filename}`) and AlphaTab.js CDN (`https://cdn.jsdelivr.net/npm/@coderline/alphatab@latest/dist/alphaTab.min.js`)
- Produces: Complete 4-step studio Web UI served at `GET /`

- [ ] **Step 1: Add failing test for static UI in `tests/test_api.py`**

Append to `tests/test_api.py`:

```python
def test_root_serves_web_ui_and_assets(tmp_path: Path):
    app = create_app(workspace_root=tmp_path)
    client = TestClient(app)

    res_html = client.get("/")
    assert res_html.status_code == 200
    assert "AlphaTab" in res_html.text or "alphaTab" in res_html.text
    assert "EscriTab" in res_html.text

    res_css = client.get("/static/styles.css")
    assert res_css.status_code == 200

    res_js = client.get("/static/app.js")
    assert res_js.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py::test_root_serves_web_ui_and_assets -v`
Expected: FAIL (since `app/static/index.html` does not exist yet)

- [ ] **Step 3: Implement `app/static/index.html`, `app/static/styles.css`, and `app/static/app.js`**

Create `app/static/index.html`:

```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EscriTab — Suno / Áudio para Tablatura de Guitarra</title>
  <link rel="stylesheet" href="/static/styles.css" />
  <script src="https://cdn.jsdelivr.net/npm/@coderline/alphatab@latest/dist/alphaTab.min.js"></script>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <span class="brand-badge">ESCRITAB</span>
      <h1>Estúdio IA: Suno / Áudio &rarr; Stems &rarr; MIDI &rarr; Tablatura</h1>
    </div>
    <div class="job-selector">
      <label for="recentJobsSelect">Sessões Salvas:</label>
      <select id="recentJobsSelect">
        <option value="">-- Nova Sessão --</option>
      </select>
    </div>
  </header>

  <main class="workspace-grid">
    <!-- ETAPA 1: UPLOAD -->
    <section class="card" id="step1Card">
      <div class="card-header">
        <span class="step-num">1</span>
        <h2>Upload do Áudio ou Stem</h2>
      </div>
      <form id="uploadForm" class="controls-stack">
        <div class="file-drop" id="fileDropZone">
          <input type="file" id="audioFileInput" accept=".mp3,.wav,.flac,.ogg,.m4a" required />
          <p id="fileNameDisplay">Arraste um MP3/WAV/FLAC do Suno ou clique para selecionar</p>
        </div>
        <label class="checkbox-row">
          <input type="checkbox" id="isolatedStemCheck" />
          <span>Este arquivo já é uma pista de guitarra isolada (pular Demucs)</span>
        </label>
        <div class="row-inline">
          <label for="demucsModelSelect">Modelo Demucs:</label>
          <select id="demucsModelSelect">
            <option value="htdemucs_6s" selected>htdemucs_6s (6 Stems - Isola Guitarra)</option>
            <option value="htdemucs">htdemucs (4 Stems)</option>
          </select>
          <button type="submit" class="btn-primary" id="uploadBtn">Enviar e Preparar</button>
        </div>
      </form>
      <div id="step1Status" class="status-banner hidden"></div>
    </section>

    <!-- ETAPA 2: STEMS -->
    <section class="card" id="step2Card">
      <div class="card-header">
        <span class="step-num">2</span>
        <h2>Audição e Seleção de Stems</h2>
      </div>
      <div class="controls-stack">
        <div id="stemsContainer" class="stems-list">
          <p class="muted">Faça o upload na Etapa 1 para ouvir as pistas separadas.</p>
        </div>
        <div class="row-inline">
          <label for="selectedStemSelect">Pista para Transcrever:</label>
          <select id="selectedStemSelect">
            <option value="guitar">Guitar (Guitarra Isolada)</option>
            <option value="guitar+other">Guitar + Other (Mixar Base + Solo/Synth)</option>
            <option value="other">Other</option>
            <option value="bass">Bass (Baixo)</option>
          </select>
        </div>
      </div>
    </section>

    <!-- ETAPA 3: TRANSCRIÇÃO MIDI E LIMPEZA HARMÔNICA -->
    <section class="card" id="step3Card">
      <div class="card-header">
        <span class="step-num">3</span>
        <h2>Transcrição MIDI &amp; Limpeza Metal</h2>
      </div>
      <div class="controls-grid">
        <div class="control-group">
          <label for="onsetSlider">Onset Threshold (Ataque): <strong id="onsetVal">0.50</strong></label>
          <input type="range" id="onsetSlider" min="0.20" max="0.85" step="0.05" value="0.50" />
          <small>Aumente se a distorção gerar notas fantasmas demais.</small>
        </div>
        <div class="control-group">
          <label for="frameSlider">Frame Threshold (Sustentação): <strong id="frameVal">0.30</strong></label>
          <input type="range" id="frameSlider" min="0.15" max="0.70" step="0.05" value="0.30" />
        </div>
        <div class="control-group">
          <label for="minLenSlider">Duração Mínima (ms): <strong id="minLenVal">60</strong> ms</label>
          <input type="range" id="minLenSlider" min="20" max="200" step="10" value="60" />
        </div>
        <div class="control-group">
          <label for="gateSlider">Noise Gate (dB): <strong id="gateVal">-45</strong> dB</label>
          <input type="range" id="gateSlider" min="-70" max="-20" step="5" value="-45" />
        </div>
        <div class="control-group">
          <label for="quantizeSelect">Quantização Rítmica:</label>
          <select id="quantizeSelect">
            <option value="off">Desativada (Tempo Natural)</option>
            <option value="1/16">1/16 (Semicolcheia)</option>
            <option value="1/32">1/32 (Fusa)</option>
          </select>
        </div>
        <div class="control-group checkboxes">
          <label class="checkbox-row">
            <input type="checkbox" id="bandpassCheck" checked />
            <span>Filtro Passa-Faixa Guitarra (75 Hz – 5.5 kHz)</span>
          </label>
          <label class="checkbox-row">
            <input type="checkbox" id="suppressHarmonicsCheck" checked />
            <span>Suprimir Harmônicos de Distorção (Oitavas/Quintas Espúrias)</span>
          </label>
        </div>
      </div>
      <div class="row-inline top-gap">
        <button type="button" class="btn-primary" id="transcribeBtn" disabled>Gerar MIDI Limpo</button>
        <span id="midiStatsBadge" class="badge hidden"></span>
      </div>
    </section>

    <!-- ETAPA 4: AFINAÇÃO E OTIMIZAÇÃO DE BRAÇO (VITERBI) -->
    <section class="card" id="step4Card">
      <div class="card-header">
        <span class="step-num">4</span>
        <h2>Afinação &amp; Otimizador de Braço (Viterbi)</h2>
      </div>
      <div class="controls-grid">
        <div class="control-group">
          <label for="tuningSelect">Afinação da Guitarra:</label>
          <select id="tuningSelect"></select>
        </div>
        <div class="control-group">
          <label for="fretPrefSelect">Região Preferencial do Braço:</label>
          <select id="fretPrefSelect">
            <option value="auto">Automática (Equilibrada)</option>
            <option value="low">Primeiras Casas / Riffs Graves (0 – 7)</option>
            <option value="mid">Região Média (3 – 12)</option>
            <option value="high">Região Aguda / Solos (7 – 19)</option>
          </select>
        </div>
        <div class="control-group">
          <label for="bpmInput">Andamento (BPM):</label>
          <input type="number" id="bpmInput" min="40" max="280" step="0.5" value="120" />
        </div>
        <div class="control-group">
          <label for="maxFretsInput">Número de Casas:</label>
          <input type="number" id="maxFretsInput" min="18" max="27" value="24" />
        </div>
      </div>
      <div class="row-inline top-gap">
        <button type="button" class="btn-accent" id="generateTabBtn" disabled>Otimizar Digitação e Gerar Tablatura</button>
        <div id="exportLinks" class="export-group hidden">
          <a id="dlGp5" class="btn-outline" download>Baixar .GP5 (Guitar Pro)</a>
          <a id="dlXml" class="btn-outline" download>Baixar .MusicXML</a>
          <a id="dlMid" class="btn-outline" download>Baixar .MID</a>
          <a id="dlTxt" class="btn-outline" download>Baixar .TXT (ASCII)</a>
        </div>
      </div>
    </section>
  </main>

  <!-- PLAYER INTERATIVO ALPHATAB + ASCII PREVIEW -->
  <section class="card player-section" id="playerSection">
    <div class="player-toolbar">
      <h2>Visualizador Interativo de Tablatura (AlphaTab)</h2>
      <div class="player-controls">
        <button type="button" id="playPauseBtn" class="btn-primary" disabled>&#9654; Play / Pause</button>
        <button type="button" id="stopBtn" class="btn-outline" disabled>&#9632; Stop</button>
        <label class="checkbox-row inline-ctrl">
          <input type="checkbox" id="loopCheck" />
          <span>Loop</span>
        </label>
        <label class="checkbox-row inline-ctrl">
          <input type="checkbox" id="metronomeCheck" />
          <span>Metrônomo</span>
        </label>
        <label class="inline-ctrl">
          Velocidade:
          <select id="speedSelect">
            <option value="0.25">0.25x</option>
            <option value="0.5">0.50x</option>
            <option value="0.75">0.75x</option>
            <option value="1" selected>1.00x</option>
            <option value="1.25">1.25x</option>
          </select>
        </label>
      </div>
    </div>

    <div id="alphaTabViewport" class="alphatab-viewport">
      <div id="alphaTabContainer"></div>
    </div>

    <details class="ascii-drawer">
      <summary>Ver Tablatura em Texto (ASCII Tab)</summary>
      <pre id="asciiPreviewBox">Gere uma tablatura na Etapa 4 para visualizar aqui.</pre>
    </details>
  </section>

  <script src="/static/app.js"></script>
</body>
</html>
```

Create `app/static/styles.css`:

```css
:root {
  --bg-dark: #0e1117;
  --bg-card: #161b22;
  --bg-input: #0d1117;
  --border: #30363d;
  --text-main: #e6edf3;
  --text-muted: #8b949e;
  --primary: #3b82f6;
  --primary-hover: #2563eb;
  --accent: #f59e0b;
  --accent-hover: #d97706;
  --success: #10b981;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  background-color: var(--bg-dark);
  color: var(--text-main);
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  line-height: 1.5;
  padding-bottom: 3rem;
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 2rem;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: center;
  gap: 0.85rem;
}

.brand-badge {
  background: linear-gradient(135deg, var(--primary), var(--accent));
  color: #fff;
  font-weight: 800;
  font-size: 0.8rem;
  padding: 0.25rem 0.6rem;
  border-radius: 6px;
  letter-spacing: 0.08em;
}

.topbar h1 {
  font-size: 1.15rem;
  font-weight: 600;
}

.workspace-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(460px, 1fr));
  gap: 1.25rem;
  padding: 1.5rem 2rem;
}

.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1.25rem;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.65rem;
}

.step-num {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--primary);
  color: #fff;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 0.9rem;
}

.card-header h2 {
  font-size: 1.05rem;
}

.controls-stack {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}

.controls-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 0.9rem;
}

.control-group {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.control-group small {
  color: var(--text-muted);
  font-size: 0.75rem;
}

.file-drop {
  border: 2px dashed var(--border);
  border-radius: 8px;
  padding: 1.1rem;
  text-align: center;
  background: var(--bg-input);
}

input[type="text"],
input[type="number"],
select {
  background: var(--bg-input);
  color: var(--text-main);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.45rem 0.65rem;
  font-size: 0.9rem;
}

.row-inline {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
}

.top-gap {
  margin-top: 1rem;
}

.checkbox-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.88rem;
  cursor: pointer;
}

.btn-primary,
.btn-accent,
.btn-outline {
  border: none;
  border-radius: 6px;
  padding: 0.55rem 1rem;
  font-weight: 600;
  font-size: 0.9rem;
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}

.btn-primary {
  background: var(--primary);
  color: #fff;
}
.btn-primary:hover:not(:disabled) {
  background: var(--primary-hover);
}

.btn-accent {
  background: var(--accent);
  color: #111;
}
.btn-accent:hover:not(:disabled) {
  background: var(--accent-hover);
}

.btn-outline {
  background: transparent;
  color: var(--text-main);
  border: 1px solid var(--border);
}
.btn-outline:hover:not(:disabled) {
  border-color: var(--primary);
}

button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.stems-list {
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
  max-height: 220px;
  overflow-y: auto;
}

.stem-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  background: var(--bg-input);
  padding: 0.45rem 0.75rem;
  border-radius: 6px;
  border: 1px solid var(--border);
}

.stem-row strong {
  min-width: 80px;
  text-transform: uppercase;
  font-size: 0.82rem;
  color: var(--accent);
}

.stem-row audio {
  height: 32px;
  flex: 1;
}

.status-banner {
  margin-top: 0.85rem;
  padding: 0.6rem 0.85rem;
  border-radius: 6px;
  background: rgba(59, 130, 246, 0.15);
  border: 1px solid var(--primary);
  font-size: 0.88rem;
}

.badge {
  background: rgba(16, 185, 129, 0.15);
  border: 1px solid var(--success);
  color: #a7f3d0;
  padding: 0.35rem 0.7rem;
  border-radius: 6px;
  font-size: 0.85rem;
}

.hidden {
  display: none !important;
}

.player-section {
  margin: 0 2rem;
}

.player-toolbar {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--border);
}

.player-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.85rem;
}

.alphatab-viewport {
  background: #ffffff;
  color: #111111;
  border-radius: 8px;
  min-height: 360px;
  max-height: 620px;
  overflow-y: auto;
  padding: 1rem;
}

.at-cursor-bar {
  background: rgba(245, 158, 11, 0.2);
}

.at-cursor-beat {
  background: #3b82f6;
  width: 3px;
}

.ascii-drawer {
  margin-top: 1rem;
}

.ascii-drawer summary {
  cursor: pointer;
  color: var(--primary);
  font-weight: 600;
  margin-bottom: 0.5rem;
}

#asciiPreviewBox {
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1rem;
  overflow-x: auto;
  font-family: "Cascadia Mono", Consolas, monospace;
  font-size: 0.85rem;
  color: #d1d5db;
}
```

Create `app/static/app.js`:

```javascript
let currentJobId = null;
let alphaTabApi = null;

const els = {
  recentJobsSelect: document.getElementById("recentJobsSelect"),
  uploadForm: document.getElementById("uploadForm"),
  audioFileInput: document.getElementById("audioFileInput"),
  isolatedStemCheck: document.getElementById("isolatedStemCheck"),
  demucsModelSelect: document.getElementById("demucsModelSelect"),
  uploadBtn: document.getElementById("uploadBtn"),
  step1Status: document.getElementById("step1Status"),
  stemsContainer: document.getElementById("stemsContainer"),
  selectedStemSelect: document.getElementById("selectedStemSelect"),
  onsetSlider: document.getElementById("onsetSlider"),
  onsetVal: document.getElementById("onsetVal"),
  frameSlider: document.getElementById("frameSlider"),
  frameVal: document.getElementById("frameVal"),
  minLenSlider: document.getElementById("minLenSlider"),
  minLenVal: document.getElementById("minLenVal"),
  gateSlider: document.getElementById("gateSlider"),
  gateVal: document.getElementById("gateVal"),
  quantizeSelect: document.getElementById("quantizeSelect"),
  bandpassCheck: document.getElementById("bandpassCheck"),
  suppressHarmonicsCheck: document.getElementById("suppressHarmonicsCheck"),
  transcribeBtn: document.getElementById("transcribeBtn"),
  midiStatsBadge: document.getElementById("midiStatsBadge"),
  tuningSelect: document.getElementById("tuningSelect"),
  fretPrefSelect: document.getElementById("fretPrefSelect"),
  bpmInput: document.getElementById("bpmInput"),
  maxFretsInput: document.getElementById("maxFretsInput"),
  generateTabBtn: document.getElementById("generateTabBtn"),
  exportLinks: document.getElementById("exportLinks"),
  dlGp5: document.getElementById("dlGp5"),
  dlXml: document.getElementById("dlXml"),
  dlMid: document.getElementById("dlMid"),
  dlTxt: document.getElementById("dlTxt"),
  playPauseBtn: document.getElementById("playPauseBtn"),
  stopBtn: document.getElementById("stopBtn"),
  loopCheck: document.getElementById("loopCheck"),
  metronomeCheck: document.getElementById("metronomeCheck"),
  speedSelect: document.getElementById("speedSelect"),
  alphaTabContainer: document.getElementById("alphaTabContainer"),
  alphaTabViewport: document.getElementById("alphaTabViewport"),
  asciiPreviewBox: document.getElementById("asciiPreviewBox"),
};

function setStatus(msg, isError = false) {
  els.step1Status.textContent = msg;
  els.step1Status.classList.remove("hidden");
  els.step1Status.style.borderColor = isError ? "#ef4444" : "#3b82f6";
}

function bindSliders() {
  els.onsetSlider.addEventListener("input", () => {
    els.onsetVal.textContent = Number(els.onsetSlider.value).toFixed(2);
  });
  els.frameSlider.addEventListener("input", () => {
    els.frameVal.textContent = Number(els.frameSlider.value).toFixed(2);
  });
  els.minLenSlider.addEventListener("input", () => {
    els.minLenVal.textContent = els.minLenSlider.value;
  });
  els.gateSlider.addEventListener("input", () => {
    els.gateVal.textContent = els.gateSlider.value;
  });
}

async function loadTunings() {
  const res = await fetch("/api/tunings");
  const tunings = await res.json();
  els.tuningSelect.innerHTML = "";
  for (const t of tunings) {
    const opt = document.createElement("option");
    opt.value = t.id;
    opt.textContent = t.name;
    els.tuningSelect.appendChild(opt);
  }
}

async function loadRecentJobs() {
  const res = await fetch("/api/jobs");
  const jobs = await res.json();
  els.recentJobsSelect.innerHTML = '<option value="">-- Nova Sessão --</option>';
  for (const j of jobs) {
    const opt = document.createElement("option");
    opt.value = j.job_id;
    opt.textContent = `${j.original_filename} (${j.job_id.slice(0, 6)})`;
    els.recentJobsSelect.appendChild(opt);
  }
}

function renderStems(job) {
  els.stemsContainer.innerHTML = "";
  const stemEntries = Object.entries(job.stems || {});
  if (stemEntries.length === 0) {
    els.stemsContainer.innerHTML = '<p class="muted">Nenhuma stem extraída ainda.</p>';
    return;
  }
  for (const [stemName, filename] of stemEntries) {
    const row = document.createElement("div");
    row.className = "stem-row";
    const label = document.createElement("strong");
    label.textContent = stemName;
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.src = `/api/jobs/${job.job_id}/files/${filename}`;
    row.appendChild(label);
    row.appendChild(audio);
    els.stemsContainer.appendChild(row);
  }
  els.transcribeBtn.disabled = false;
}

function initAlphaTab(musicXmlUrl) {
  if ( typeof alphaTab === "undefined" ) {
    return;
  }
  if (alphaTabApi) {
    alphaTabApi.destroy();
    els.alphaTabContainer.innerHTML = "";
  }
  alphaTabApi = new alphaTab.AlphaTabApi(els.alphaTabContainer, {
    core: {
      file: musicXmlUrl,
      fontDirectory: "https://cdn.jsdelivr.net/npm/@coderline/alphatab@latest/dist/font/",
    },
    player: {
      enablePlayer: true,
      enableCursor: true,
      enableUserInteraction: true,
      soundFont: "https://cdn.jsdelivr.net/npm/@coderline/alphatab@latest/dist/soundfont/sonivox.sf2",
      scrollElement: els.alphaTabViewport,
    },
  });

  els.playPauseBtn.disabled = false;
  els.stopBtn.disabled = false;
}

els.uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = els.audioFileInput.files[0];
  if (!file) return;

  els.uploadBtn.disabled = true;
  setStatus("Enviando áudio e detectando BPM...");

  try {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("is_isolated_stem", els.isolatedStemCheck.checked ? "true" : "false");

    const upRes = await fetch("/api/jobs/upload", { method: "POST", body: formData });
    if (!upRes.ok) throw new Error(await upRes.text());
    let job = await upRes.json();
    currentJobId = job.job_id;
    els.bpmInput.value = job.detected_bpm || 120;

    if (!job.is_isolated_stem) {
      setStatus("Separando stems com Demucs (GPU/CPU)... Isso pode levar alguns instantes.");
      const sepRes = await fetch(`/api/jobs/${currentJobId}/separate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: els.demucsModelSelect.value }),
      });
      if (!sepRes.ok) throw new Error(await sepRes.text());
      const sepData = await sepRes.json();
      job = sepData.job;
      setStatus(`Stems separadas com sucesso (Dispositivo: ${sepData.device}).`);
    } else {
      setStatus("Stem isolada carregada! Pronta para transcrição MIDI.");
    }

    renderStems(job);
    await loadRecentJobs();
    els.recentJobsSelect.value = currentJobId;
  } catch (err) {
    setStatus(`Erro: ${err.message}`, true);
  } finally {
    els.uploadBtn.disabled = false;
  }
});

els.transcribeBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  els.transcribeBtn.disabled = true;
  els.midiStatsBadge.classList.remove("hidden");
  els.midiStatsBadge.textContent = "Transcrevendo áudio para MIDI...";

  try {
    const payload = {
      stem: els.selectedStemSelect.value,
      bandpass_enabled: els.bandpassCheck.checked,
      noise_gate_db: Number(els.gateSlider.value),
      onset_threshold: Number(els.onsetSlider.value),
      frame_threshold: Number(els.frameSlider.value),
      minimum_note_length_ms: Number(els.minLenSlider.value),
      suppress_harmonics: els.suppressHarmonicsCheck.checked,
      quantize_grid: els.quantizeSelect.value,
      tuning: els.tuningSelect.value,
      max_frets: Number(els.maxFretsInput.value),
      bpm: Number(els.bpmInput.value),
    };

    const res = await fetch(`/api/jobs/${currentJobId}/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Erro na transcrição");
    }

    const st = data.stats;
    els.midiStatsBadge.textContent = `${st.total_notes} notas (${st.lowest_note || ""} a ${st.highest_note || ""}) | Harmônicos removidos: ${st.harmonics_removed}`;
    els.generateTabBtn.disabled = false;
  } catch (err) {
    els.midiStatsBadge.textContent = `Aviso: ${err.message}`;
  } finally {
    els.transcribeBtn.disabled = false;
  }
});

els.generateTabBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  els.generateTabBtn.disabled = true;

  try {
    const payload = {
      tuning: els.tuningSelect.value,
      max_frets: Number(els.maxFretsInput.value),
      fret_preference: els.fretPrefSelect.value,
      bpm: Number(els.bpmInput.value),
    };

    const res = await fetch(`/api/jobs/${currentJobId}/tab`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Erro ao gerar tablatura");

    els.dlGp5.href = data.artifacts.gp5;
    els.dlXml.href = data.artifacts.musicxml;
    els.dlMid.href = data.artifacts.midi;
    els.dlTxt.href = data.artifacts.txt;
    els.exportLinks.classList.remove("hidden");

    els.asciiPreviewBox.textContent = data.ascii_preview;
    initAlphaTab(data.artifacts.musicxml);
  } catch (err) {
    alert(`Erro ao gerar tablatura: ${err.message}`);
  } finally {
    els.generateTabBtn.disabled = false;
  }
});

els.recentJobsSelect.addEventListener("change", async () => {
  const jobId = els.recentJobsSelect.value;
  if (!jobId) return;
  const res = await fetch(`/api/jobs/${jobId}`);
  if (!res.ok) return;
  const job = await res.json();
  currentJobId = job.job_id;
  els.bpmInput.value = job.detected_bpm || 120;
  renderStems(job);
  if (job.artifacts && job.artifacts.midi) {
    els.generateTabBtn.disabled = false;
  }
  if (job.artifacts && job.artifacts.musicxml) {
    const xmlUrl = `/api/jobs/${job.job_id}/files/${job.artifacts.musicxml}`;
    initAlphaTab(xmlUrl);
  }
});

els.playPauseBtn.addEventListener("click", () => {
  if (alphaTabApi) alphaTabApi.playPause();
});
els.stopBtn.addEventListener("click", () => {
  if (alphaTabApi) alphaTabApi.stop();
});
els.loopCheck.addEventListener("change", () => {
  if (alphaTabApi) alphaTabApi.isLooping = els.loopCheck.checked;
});
els.metronomeCheck.addEventListener("change", () => {
  if (alphaTabApi) alphaTabApi.metronomeVolume = els.metronomeCheck.checked ? 1 : 0;
});
els.speedSelect.addEventListener("change", () => {
  if (alphaTabApi) alphaTabApi.playbackSpeed = Number(els.speedSelect.value);
});

bindSliders();
loadTunings();
loadRecentJobs();
```

- [ ] **Step 4: Run full test suite to verify everything passes and commit**

Run: `pytest -v`
Expected: PASS (all tests across `test_job_manager.py`, `test_audio_cleaner.py`, `test_stem_separator.py`, `test_midi_transcriber.py`, `test_fretboard.py`, `test_exporters.py`, `test_api.py` pass)

Commit:
```powershell
git add app/static/index.html app/static/styles.css app/static/app.js tests/test_api.py
git commit -m "feat: add SPA studio frontend with multi-stem auditioning and AlphaTab player"
```
