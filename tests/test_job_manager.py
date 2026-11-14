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
