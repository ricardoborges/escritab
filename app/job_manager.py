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
