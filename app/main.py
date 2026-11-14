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
