from __future__ import annotations
from pathlib import Path
import re
from typing import Literal
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from app.job_manager import JobManager
import shutil
from app.models import (
    JobMetadata,
    LyricsRequest,
    MultitrackRequest,
    PartCreate,
    PartGenerate,
    PartUpdate,
    ProjectUpdate,
    SeparateRequest,
    TUNINGS,
)
from app import projects
from app.projects import PartError
from app.pipeline import dettato_transcriber
from app.pipeline.stem_separator import separate_stems
from app.system_info import get_compute_info
from app import soundfonts


def create_app(workspace_root: Path | str = Path("workspace/jobs")) -> FastAPI:
    app = FastAPI(
        title="EscriTab — aprenda a música por partes",
        version="1.0.0",
    )
    manager = JobManager(workspace_root=workspace_root)
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    @app.get("/api/tunings")
    def list_tunings():
        return list(TUNINGS.values())

    @app.get("/api/system")
    def system_info():
        return get_compute_info()

    @app.get("/api/soundfonts")
    def list_soundfonts():
        return {
            "dirs": [str(d) for d in soundfonts.soundfont_dirs()],
            "soundfonts": soundfonts.public(soundfonts.list_soundfonts()),
        }

    @app.get("/api/soundfonts/{sf_id}")
    def get_soundfont(sf_id: str):
        path = soundfonts.path_for(sf_id)
        if path is None:
            raise HTTPException(status_code=404, detail="SoundFont não encontrada.")
        path = soundfonts.servable_path(path, Path(workspace_root).parent / "soundfont_cache")
        return FileResponse(path=str(path), media_type="application/octet-stream",
                            headers={"Cache-Control": "max-age=86400"})

    def load(project_id: str) -> tuple[JobMetadata, Path]:
        try:
            job = manager.get_job(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Projeto não encontrado.") from exc
        return job, manager.get_job_dir(project_id)

    def safe_file(folder: Path, filename: str):
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "", Path(filename).name)
        file_path = folder / safe_name
        if not safe_name or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
        return FileResponse(path=str(file_path), filename=safe_name)

    def part_error(exc: PartError) -> HTTPException:
        return HTTPException(status_code=exc.status, detail=str(exc))

    # ---------- projects ----------

    @app.get("/api/projects")
    def list_projects():
        return [
            {
                "job_id": j.job_id,
                "name": j.name or projects.default_name(j.original_filename),
                "original_filename": j.original_filename,
                "created_at": j.created_at,
                "mode": j.mode,
                "duration": j.duration,
                "parts": [p.name for p in j.parts],
                "has_multitrack": bool(j.multitrack),
                "has_stems": bool(j.stems),
            }
            for j in manager.list_jobs()
        ]

    @app.post("/api/projects")
    async def create_project(
        file: UploadFile = File(...),
        name: str = Form(""),
        is_isolated_stem: bool = Form(False),
        mode: Literal["dettato", "classic"] = Form("dettato"),
    ):
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="Arquivo de áudio vazio.")
        filename = file.filename or "audio.wav"
        job = manager.create_job(
            original_filename=filename, is_isolated_stem=is_isolated_stem, audio_bytes=audio_bytes
        )
        job.mode = mode
        job.name = name.strip()[:80] or projects.default_name(filename)
        job_dir = manager.get_job_dir(job.job_id)
        try:
            projects.analyze_audio(job, job_dir)
        except Exception as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"Não foi possível ler o áudio: {exc}") from exc
        if is_isolated_stem:
            job.stems, job.separation_device = separate_stems(
                input_audio_path=job_dir / job.input_path, job_dir=job_dir, is_isolated_stem=True
            )
        manager.save_job(job)
        return job

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        job, job_dir = load(project_id)
        if projects.ensure_project(job, job_dir):
            manager.save_job(job)
        return job

    @app.patch("/api/projects/{project_id}")
    def update_project(project_id: str, req: ProjectUpdate):
        job, _ = load(project_id)
        if req.name is not None:
            job.name = req.name.strip()
        if req.detected_bpm is not None:
            job.detected_bpm = round(req.detected_bpm, 1)
        manager.save_job(job)
        return job

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str):
        _, job_dir = load(project_id)
        shutil.rmtree(job_dir, ignore_errors=True)
        return {"deleted": project_id}

    @app.get("/api/projects/{project_id}/peaks")
    def get_peaks(project_id: str):
        job, job_dir = load(project_id)
        if not (job_dir / projects.PEAKS_FILE).exists():
            projects.analyze_audio(job, job_dir)
            manager.save_job(job)
        return FileResponse(path=str(job_dir / projects.PEAKS_FILE), media_type="application/json")

    @app.get("/api/projects/{project_id}/files/{filename}")
    def get_project_file(project_id: str, filename: str):
        _, job_dir = load(project_id)
        return safe_file(job_dir, filename)

    # ---------- analysis (once per project) ----------

    @app.post("/api/projects/{project_id}/separate")
    def separate_project_stems(project_id: str, req: SeparateRequest = SeparateRequest()):
        job, job_dir = load(project_id)
        try:
            stems_map, device = separate_stems(
                input_audio_path=job_dir / job.input_path,
                job_dir=job_dir,
                is_isolated_stem=job.is_isolated_stem,
                model_name=req.model,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Falha ao separar stems: {exc}") from exc
        job.stems = stems_map
        job.separation_device = device
        for cache in job_dir.glob("stem_notes_*.json"):  # new stems: old stem transcriptions are stale
            cache.unlink()
        manager.save_job(job)
        return {"job": job, "device": device}

    @app.post("/api/projects/{project_id}/multitrack")
    def transcribe_full_song(project_id: str, req: MultitrackRequest = MultitrackRequest()):
        """Full-song, multi-instrument MIDI (one track per instrument + drums) with Dettato.

        Runs on the original upload so the drum model gets the full sample rate."""
        job, job_dir = load(project_id)
        if not dettato_transcriber.is_available():
            raise HTTPException(
                status_code=503, detail="Dettato não está instalado. Rode: pip install -r requirements.txt"
            )
        full_midi = job_dir / "full_song.mid"
        try:
            tracks, info = dettato_transcriber.transcribe_multitrack(
                job_dir / job.input_path, full_midi, split_guitar_roles=req.split_guitar_roles
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Falha na transcrição multi-instrumento: {exc}") from exc
        job.multitrack = tracks
        job.artifacts["multitrack_midi"] = full_midi.name
        job.artifacts["multitrack_json"] = full_midi.with_suffix(".json").name
        manager.save_job(job)
        return {
            "job": job,
            "default_tab_tracks": dettato_transcriber.default_tab_tracks(tracks),
            "info": info,
        }

    @app.get("/api/projects/{project_id}/harmony")
    def get_harmony(project_id: str):
        """The cached harmony analysis, or null when it has not been run yet."""
        _, job_dir = load(project_id)
        return {"harmony": projects.load_harmony(job_dir)}

    @app.post("/api/projects/{project_id}/harmony")
    def analyze_harmony(project_id: str):
        job, job_dir = load(project_id)
        if projects.ensure_project(job, job_dir):
            manager.save_job(job)
        try:
            result = projects.compute_harmony(job, job_dir)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Falha na análise harmônica: {exc}") from exc
        return {"harmony": result}

    @app.get("/api/projects/{project_id}/lyrics")
    def get_lyrics(project_id: str):
        """Saved lyrics (timed lines) and the chord sheet; nulls when no lyrics were loaded."""
        job, job_dir = load(project_id)
        return projects.lyrics_payload(job, job_dir)

    @app.post("/api/projects/{project_id}/lyrics")
    def align_lyrics(project_id: str, req: LyricsRequest):
        job, job_dir = load(project_id)
        if projects.ensure_project(job, job_dir):
            manager.save_job(job)
        try:
            projects.align_project_lyrics(job, job_dir, req.text, req.language, req.model)
        except PartError as exc:
            raise part_error(exc) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Falha ao alinhar a letra: {exc}") from exc
        return projects.lyrics_payload(job, job_dir)

    # ---------- parts ----------

    @app.post("/api/projects/{project_id}/parts")
    def create_part(project_id: str, req: PartCreate):
        job, _ = load(project_id)
        try:
            part = projects.new_part(job, req.name, req.start, req.end)
        except PartError as exc:
            raise part_error(exc) from exc
        manager.save_job(job)
        return {"job": job, "part": part}

    @app.post("/api/projects/{project_id}/parts/from-lyrics")
    def create_parts_from_lyrics(project_id: str):
        """One part per section of the aligned lyrics ([Verse], [Chorus]... or inferred)."""
        job, job_dir = load(project_id)
        try:
            created = projects.parts_from_lyrics(job, job_dir)
        except PartError as exc:
            raise part_error(exc) from exc
        manager.save_job(job)
        return {"job": job, "created": len(created)}

    @app.patch("/api/projects/{project_id}/parts/{part_id}")
    def update_part(project_id: str, part_id: str, req: PartUpdate):
        job, job_dir = load(project_id)
        try:
            part = projects.find_part(job, part_id)
            if req.name is not None:
                part.name = req.name.strip()
            if req.start is not None or req.end is not None:
                start, end = projects.check_range(
                    job,
                    part.start if req.start is None else req.start,
                    part.end if req.end is None else req.end,
                )
                if (start, end) != (part.start, part.end):
                    part.start, part.end = start, end
                    projects.clear_outputs(job_dir, part)  # the old tab no longer matches the excerpt
                    job.parts.sort(key=lambda p: (p.start, p.created_at))
        except PartError as exc:
            raise part_error(exc) from exc
        manager.save_job(job)
        return {"job": job, "part": part}

    @app.delete("/api/projects/{project_id}/parts/{part_id}")
    def delete_part(project_id: str, part_id: str):
        job, job_dir = load(project_id)
        try:
            part = projects.find_part(job, part_id)
        except PartError as exc:
            raise part_error(exc) from exc
        projects.clear_outputs(job_dir, part)
        job.parts.remove(part)
        manager.save_job(job)
        return {"job": job}

    @app.post("/api/projects/{project_id}/parts/{part_id}/generate")
    def generate_part(project_id: str, part_id: str, req: PartGenerate):
        """Transcribe the excerpt (slicing the project's analysis) and build its tab."""
        job, job_dir = load(project_id)
        try:
            part = projects.find_part(job, part_id)
            ascii_tab = projects.generate_part(job, job_dir, part, req.transcription, req.tab)
        except PartError as exc:
            manager.save_job(job)  # outputs may have been cleared
            raise part_error(exc) from exc
        manager.save_job(job)
        return {"job": job, "part": part, "ascii_preview": ascii_tab}

    @app.get("/api/projects/{project_id}/parts/{part_id}/scales")
    def get_part_scales(project_id: str, part_id: str, stem: str | None = None):
        """Scales the excerpt is probably built on (best first), to help figuring a solo out by ear."""
        job, job_dir = load(project_id)
        try:
            part = projects.find_part(job, part_id)
            return projects.part_scales(job, job_dir, part, stem)
        except PartError as exc:
            raise part_error(exc) from exc

    @app.get("/api/projects/{project_id}/parts/{part_id}/files/{filename}")
    def get_part_file(project_id: str, part_id: str, filename: str):
        _, job_dir = load(project_id)
        return safe_file(projects.part_dir(job_dir, part_id), filename)

    @app.get("/", response_class=HTMLResponse)
    def index_page():
        index_file = static_dir / "index.html"
        if index_file.exists():
            return index_file.read_text(encoding="utf-8")
        return "<h1>EscriTab Backend Active</h1>"

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    return app


app = create_app()
