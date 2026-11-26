"""Batch processing: every song in a folder becomes a project and goes through the chosen steps.

The projects land in the same workspace as the web studio, so they open there afterwards with
the heavy analysis already cached. A song already imported (same file content) reuses its
project, and steps whose output already exists are skipped, so an interrupted run can simply be
started again.

    uv run python cli.py D:\\Musicas
    uv run python cli.py D:\\Musicas -r --steps all --qtab-dir D:\\qtabs
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import threading
import time
import traceback
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

AUDIO_EXTENSIONS = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".aif", ".aiff")
# pipeline order: harmony reads the stems and the full-song MIDI, parts read the lyrics...
STEPS = ("stems", "multitrack", "harmony", "lyrics", "parts", "tabs", "qtab")
DEFAULT_STEPS = ("stems", "multitrack", "harmony")
STEP_HELP = {
    "stems": "separa as stems (Demucs / RoFormer)",
    "multitrack": "MIDI multi-instrumento da música inteira (Dettato)",
    "harmony": "tom e acordes",
    "lyrics": "letra ouvida pelo Whisper (sem texto colado)",
    "parts": "uma parte por seção da letra",
    "tabs": "tablatura de cada parte (sem partes: uma parte com a música inteira)",
    "qtab": "exporta o .qtab (backing + acordes) para --qtab-dir",
}
INDEX_FILE = "batch_index.json"


class StepSkipped(Exception):
    """A step that cannot run for this song (nothing to do, missing input): not a failure."""


# ---------- discovery ----------

def find_audio_files(root: Path, recursive: bool, extensions: tuple[str, ...] = AUDIO_EXTENSIONS) -> list[Path]:
    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
    pattern = "**/*" if recursive else "*"
    files = [p for p in root.glob(pattern) if p.is_file() and p.suffix.lower() in exts]
    return sorted(files, key=lambda p: str(p).lower())


def parse_steps(spec: str) -> list[str]:
    names = [s.strip().lower() for s in spec.split(",") if s.strip()]
    if not names:
        raise argparse.ArgumentTypeError("nenhuma etapa informada")
    if "all" in names:
        return list(STEPS)
    unknown = [n for n in names if n not in STEPS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"etapa desconhecida: {', '.join(unknown)} (opções: {', '.join(STEPS)}, all)"
        )
    return [s for s in STEPS if s in names]  # always run in pipeline order


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- import index (file content -> project) ----------

class ImportIndex:
    """sha256 of each imported file -> project id, so a re-run reuses the project."""

    def __init__(self, workspace_root: Path) -> None:
        self.path = workspace_root / INDEX_FILE
        try:
            self.data: dict[str, str] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, digest: str, workspace_root: Path) -> str | None:
        job_id = self.data.get(digest)
        if job_id and (workspace_root / job_id / "job.json").exists():
            return job_id
        return None  # unknown, or the project was deleted in the studio

    def put(self, digest: str, job_id: str) -> None:
        self.data[digest] = job_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)


# ---------- options and results ----------

@dataclass
class Options:
    steps: list[str]
    separation: str = "htdemucs_6s"
    isolated_stem: bool = False
    split_guitar_roles: bool = True
    language: str | None = None
    whisper_model: str = "turbo"
    tuning: str = "standard_e"
    qtab_dir: Path | None = None
    qtab_stems: list[str] = field(default_factory=list)
    force: bool = False
    verbose: bool = False


@dataclass
class SongResult:
    path: Path
    job_id: str | None = None
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failed


# ---------- console output ----------

_out_lock = threading.Lock()
_status: "Status | None" = None


def _log(msg: str) -> None:
    with _out_lock:
        if _status is not None:
            _status.clear_line()
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _progress(fraction: float | None = None, detail: str | None = None) -> None:
    """Report progress of the running step (no-op outside a step)."""
    if _status is not None:
        _status.update(fraction, detail)


def _fmt_mb(n: float) -> str:
    return f"{n / 1e6:.0f} MB"


def _downloads_text() -> str:
    try:
        from app.pipeline import model_downloads
    except Exception:
        return ""
    items = []
    for d in model_downloads.active_downloads():
        if d["done"]:
            continue
        size = f"{_fmt_mb(d['downloaded'])}/{_fmt_mb(d['total'])}" if d["total"] else _fmt_mb(d["downloaded"])
        items.append(f"baixando {d['label']} {size} ({d['speed'] / 1e6:.1f} MB/s)")
    return " · ".join(items)


class Status:
    """A live line under the log while a long step runs: spinner, elapsed time, progress, downloads.

    In a terminal it redraws in place; when the output goes to a file or pipe it prints a line every
    PLAIN_EVERY_S seconds (or each 10% of progress) instead."""

    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    PLAIN_EVERY_S = 30.0

    def __init__(self, label: str) -> None:
        self.label = label
        self.fraction: float | None = None
        self.detail: str | None = None
        self.t0 = time.monotonic()
        self.tty = sys.stdout.isatty()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._last_plain = self.t0
        self._last_decile = 0
        self._drawn = False

    def __enter__(self) -> "Status":
        global _status
        _status = self
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        global _status
        self._stop.set()
        self._thread.join()
        with _out_lock:
            self.clear_line()
            _status = None

    def update(self, fraction: float | None, detail: str | None) -> None:
        if fraction is not None:
            self.fraction = max(0.0, min(1.0, fraction))
        if detail is not None:
            self.detail = detail

    def text(self, spin: str = "…") -> str:
        parts = [f"  {spin} {self.label}"]
        if self.detail:
            parts.append(self.detail)
        if self.fraction is not None:
            filled = int(self.fraction * 20)
            parts.append(f"[{'█' * filled}{'░' * (20 - filled)}] {self.fraction:4.0%}")
        parts.append(_fmt_duration(time.monotonic() - self.t0))
        downloads = _downloads_text()
        if downloads:
            parts.append(downloads)
        return " · ".join(parts)

    def clear_line(self) -> None:  # the caller holds _out_lock
        if self.tty and self._drawn:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()
            self._drawn = False

    def _run(self) -> None:
        i = 0
        while not self._stop.wait(0.25 if self.tty else 1.0):
            i += 1
            with _out_lock:
                if self.tty:
                    width = max(20, shutil.get_terminal_size((100, 20)).columns - 1)
                    line = self.text(self.SPINNER[i % len(self.SPINNER)])
                    sys.stdout.write("\r\033[2K" + line[:width])
                    sys.stdout.flush()
                    self._drawn = True
                    continue
                now = time.monotonic()
                decile = int(self.fraction * 10) if self.fraction is not None else 0
                if now - self._last_plain >= self.PLAIN_EVERY_S or decile > self._last_decile:
                    self._last_plain, self._last_decile = now, decile
                    print(f"[{time.strftime('%H:%M:%S')}] {self.text()}", flush=True)


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s" if m else f"{s}s"


# ---------- steps ----------
# Each step returns a short description of what it did, raises StepSkipped when there is nothing
# to do, or raises anything else on failure. Heavy modules are imported here, not at the top, so
# `--help` and `--dry-run` answer at once.

def _step_stems(job, job_dir: Path, opts: Options) -> str:
    from app.pipeline.stem_separator import separate_stems

    if job.stems and not opts.force:
        raise StepSkipped(f"já separadas ({', '.join(job.stems)})")
    stems, device = separate_stems(
        input_audio_path=job_dir / job.input_path, job_dir=job_dir,
        is_isolated_stem=job.is_isolated_stem, model_name=opts.separation,
        progress=lambda fraction, what: _progress(fraction, what),
    )
    job.stems, job.separation_device = stems, device
    for cache in job_dir.glob("stem_notes_*.json"):  # transcriptions of the old stems are stale
        cache.unlink()
    return f"{', '.join(stems)} ({device})"


def _step_multitrack(job, job_dir: Path, opts: Options) -> str:
    from app.pipeline import dettato_transcriber

    if job.multitrack and not opts.force:
        raise StepSkipped(f"já transcrito ({len(job.multitrack)} faixas)")
    if not dettato_transcriber.is_available():
        raise RuntimeError("Dettato não está instalado (uv sync).")
    full_midi = job_dir / "full_song.mid"
    tracks, info = dettato_transcriber.transcribe_multitrack(
        job_dir / job.input_path,
        full_midi,
        split_guitar_roles=opts.split_guitar_roles,
        bpm=job.detected_bpm,
        beats=job.beats or None,
    )
    job.multitrack = tracks
    job.artifacts["multitrack_midi"] = full_midi.name
    job.artifacts["multitrack_json"] = full_midi.with_suffix(".json").name
    return f"{len(tracks)} faixas ({info.get('device', '?')})"


def _step_harmony(job, job_dir: Path, opts: Options) -> str:
    from app import projects

    harm = projects.load_harmony(job_dir)
    if harm and not opts.force:
        raise StepSkipped(f"já analisada ({harm['key']['name']})")
    result = projects.compute_harmony(job, job_dir)
    return f"{result['key']['name']}, {len(result.get('chords', []))} acordes"


def _step_lyrics(job, job_dir: Path, opts: Options) -> str:
    from app import projects

    if (job_dir / projects.LYRICS_FILE).exists() and not opts.force:
        raise StepSkipped("letra já alinhada")
    try:
        data = projects.align_project_lyrics(job, job_dir, "", opts.language, opts.whisper_model)
    except projects.PartError as exc:  # e.g. an instrumental: nothing sung
        raise StepSkipped(str(exc)) from exc
    return f"{data['stats']['lines']} linhas, idioma {data['language']}"


def _step_parts(job, job_dir: Path, opts: Options) -> str:
    from app import projects

    try:
        created = projects.parts_from_lyrics(job, job_dir)
    except projects.PartError as exc:
        raise StepSkipped(str(exc)) from exc
    if not created:
        raise StepSkipped(f"nenhuma parte nova ({len(job.parts)} existentes)")
    return f"{len(created)} partes: {', '.join(p.name for p in created)}"


def _step_tabs(job, job_dir: Path, opts: Options) -> str:
    from app import projects
    from app.models import TabParams, TranscriptionParams
    from app.pipeline import dettato_transcriber

    if not job.parts:
        projects.new_part(job, "Música inteira", 0.0, job.duration)
    todo = [p for p in job.parts if opts.force or not p.artifacts]
    if not todo:
        raise StepSkipped(f"todas as {len(job.parts)} partes já têm tablatura")
    use_multitrack = bool(job.multitrack and dettato_transcriber.default_tab_tracks(job.multitrack))
    if not use_multitrack and "guitar" not in job.stems:
        raise StepSkipped("sem faixas de guitarra no MIDI completo nem stem de guitarra")
    bpm = job.detected_bpm if 20.0 < job.detected_bpm < 320.0 else 120.0
    made, errors = [], []
    for i, part in enumerate(todo, 1):
        _progress((i - 1) / len(todo), f"parte {i}/{len(todo)}: {part.name}")
        tparams = TranscriptionParams(
            engine="dettato", source="multitrack" if use_multitrack else "stem",
            stem="guitar", tuning=opts.tuning, bpm=bpm,
        )
        tab = TabParams(tuning=opts.tuning, bpm=bpm)
        try:
            projects.generate_part(job, job_dir, part, tparams, tab)
            made.append(part.name)
            _log(f"      · {part.name}: tablatura gerada")
        except projects.PartError as exc:
            errors.append(f"{part.name}: {exc}")
    if errors:
        for e in errors:
            _log(f"      · {e}")
    if not made:
        raise StepSkipped("nenhuma parte com notas de guitarra")
    return f"{len(made)}/{len(todo)} partes ({'MIDI completo' if use_multitrack else 'stem guitar'})"


def _step_qtab(job, job_dir: Path, opts: Options) -> str:
    from app import projects, qtab

    if opts.qtab_dir is None:
        raise StepSkipped("informe --qtab-dir")
    out = opts.qtab_dir / qtab.qtab_filename(job, 1.0)
    if out.exists() and not opts.force:
        raise StepSkipped(f"já existe: {out.name}")
    stems = [s for s in opts.qtab_stems if s in job.stems]
    try:
        data = qtab.build_qtab(job, job_dir, stems, 1.0)
    except projects.PartError as exc:
        raise StepSkipped(str(exc)) from exc
    opts.qtab_dir.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return str(out)


STEP_FUNCS: dict[str, Callable] = {
    "stems": _step_stems,
    "multitrack": _step_multitrack,
    "harmony": _step_harmony,
    "lyrics": _step_lyrics,
    "parts": _step_parts,
    "tabs": _step_tabs,
    "qtab": _step_qtab,
}


# ---------- one song ----------

def _find_uploaded_project(path: Path, digest: str, manager) -> str | None:
    """A project with this exact file that the index doesn't know (uploaded in the web studio)."""
    size = path.stat().st_size
    for job in manager.list_jobs():
        source = manager.get_job_dir(job.job_id) / job.input_path
        try:
            if source.stat().st_size == size and file_sha256(source) == digest:
                return job.job_id
        except OSError:
            continue
    return None


def _open_project(path: Path, manager, index: ImportIndex, opts: Options, digest: str):
    """The song's project: the one it was imported into before, or a new one."""
    from app import projects

    job_id = index.get(digest, manager.workspace_root)
    if not job_id:
        with Status("procurando projeto já existente com este arquivo"):
            job_id = _find_uploaded_project(path, digest, manager)
    if job_id:
        index.put(digest, job_id)
        job = manager.get_job(job_id)
        job_dir = manager.get_job_dir(job_id)
        changed = projects.ensure_project(job, job_dir)
        # a name still derived the old way ("Song v4"): use today's rule, never a name typed by hand
        if job.name == projects.default_name(job.original_filename, strip_version=False):
            new_name = projects.default_name(job.original_filename)
            if new_name != job.name:
                _log(f"  nome: {job.name} → {new_name}")
                job.name, changed = new_name, True
        if changed:
            manager.save_job(job)
        return job, job_dir, False

    _log("  importando: duração, BPM e forma de onda…")
    job = manager.create_job(
        original_filename=path.name, is_isolated_stem=opts.isolated_stem, audio_bytes=path.read_bytes()
    )
    job.mode = "dettato" if "multitrack" in opts.steps else "classic"
    job.name = projects.default_name(path.name)
    job_dir = manager.get_job_dir(job.job_id)
    try:
        projects.analyze_audio(job, job_dir)
    except Exception:
        import shutil

        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    manager.save_job(job)
    index.put(digest, job.job_id)
    return job, job_dir, True


def _error_text(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[-1][:300] if text else type(exc).__name__


def process_song(
    path: Path, manager, index: ImportIndex, opts: Options, seen: dict[str, Path] | None = None
) -> SongResult:
    """Import (or reopen) one song and run the steps. `seen` maps the files' sha256 to the first
    file of this run with that content, so a duplicate is reported instead of processed twice."""
    result = SongResult(path=path)
    t0 = time.monotonic()
    try:
        with Status("calculando a assinatura do arquivo"):
            digest = file_sha256(path)
        if seen is not None:
            if digest in seen:
                result.job_id = index.get(digest, manager.workspace_root)
                result.skipped.append("duplicada")
                _log(f"  – mesmo conteúdo de {seen[digest].name}: pulada")
                return result
            seen[digest] = path
        job, job_dir, created = _open_project(path, manager, index, opts, digest)
    except Exception as exc:
        result.failed["import"] = f"não foi possível ler o áudio: {_error_text(exc)}"
        _log(f"  ✗ import: {result.failed['import']}")
        result.seconds = time.monotonic() - t0
        return result
    result.job_id = job.job_id
    _log(f"  projeto {job.job_id} ({'novo' if created else 'existente'}) · "
         f"{_fmt_duration(job.duration)} · {job.detected_bpm:g} BPM")

    for step in opts.steps:
        st = time.monotonic()
        try:
            with Status(f"{step}: {STEP_HELP[step]}"):
                desc = STEP_FUNCS[step](job, job_dir, opts)
        except StepSkipped as exc:
            result.skipped.append(step)
            _log(f"  – {step}: {exc}")
            continue
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            result.failed[step] = _error_text(exc)
            _log(f"  ✗ {step}: {result.failed[step]}")
            if opts.verbose:
                traceback.print_exc()
            manager.save_job(job)  # keep whatever the earlier steps produced
            continue
        result.done.append(step)
        manager.save_job(job)
        _log(f"  ✓ {step}: {desc} [{_fmt_duration(time.monotonic() - st)}]")
    result.seconds = time.monotonic() - t0
    return result



# ---------- CLI ----------

def build_parser() -> argparse.ArgumentParser:
    steps_help = "\n".join(f"  {name:<11} {STEP_HELP[name]}" for name in STEPS)
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="EscriTab em lote: importa todas as músicas de um diretório como projetos e "
                    "roda as etapas escolhidas do pipeline. Os projetos aparecem no estúdio web.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"etapas (na ordem em que rodam):\n{steps_help}\n\n"
               f"padrão: --steps {','.join(DEFAULT_STEPS)}\n"
               "Músicas já importadas (mesmo conteúdo) reutilizam o projeto; etapas já feitas são puladas "
               "(use --force para refazer).",
    )
    parser.add_argument("directory", type=Path, help="diretório com as músicas")
    parser.add_argument("-r", "--recursive", action="store_true", help="inclui subdiretórios")
    parser.add_argument("-s", "--steps", type=parse_steps, default=list(DEFAULT_STEPS),
                        help="etapas separadas por vírgula, ou 'all'")
    parser.add_argument("--ext", default=",".join(e.lstrip(".") for e in AUDIO_EXTENSIONS),
                        help="extensões aceitas (padrão: %(default)s)")
    parser.add_argument("--separation", default="htdemucs_6s",
                        choices=["htdemucs_6s", "htdemucs", "roformer_htdemucs_6s"],
                        help="modelo de separação de stems (padrão: %(default)s)")
    parser.add_argument("--isolated-stem", action="store_true",
                        help="os arquivos já são a guitarra isolada (não separa stems)")
    parser.add_argument("--no-split-guitar-roles", action="store_true",
                        help="não divide as guitarras do MIDI completo em base e solo/riff")
    parser.add_argument("--language", choices=["pt", "en", "es", "fr", "it", "de", "ja"],
                        help="idioma da letra para o Whisper (padrão: detectar)")
    parser.add_argument("--whisper-model", default="turbo", choices=["turbo", "medium", "small"])
    parser.add_argument("--tuning", default="standard_e", help="afinação das tablaturas (padrão: %(default)s)")
    parser.add_argument("--qtab-dir", type=Path, help="pasta onde salvar os .qtab (etapa qtab)")
    parser.add_argument("--qtab-stems", default="",
                        help="stems do backing do .qtab, ex.: drums,bass (padrão: mix original)")
    parser.add_argument("--workspace", type=Path, default=Path("workspace/jobs"),
                        help="pasta dos projetos (padrão: %(default)s)")
    parser.add_argument("-f", "--force", action="store_true", help="refaz etapas já concluídas")
    parser.add_argument("-n", "--dry-run", action="store_true", help="só lista o que seria processado")
    parser.add_argument("--limit", type=int, default=0, help="processa no máximo N músicas")
    parser.add_argument("--fail-fast", action="store_true", help="para na primeira música com erro")
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra o traceback dos erros")
    return parser


def _print_summary(results: list[SongResult], total_s: float, interrupted: bool) -> None:
    ok = sum(r.ok for r in results)
    print()
    print("=" * 72)
    print(f"Resumo: {ok}/{len(results)} músicas sem erro em {_fmt_duration(total_s)}"
          + (" (interrompido)" if interrupted else ""))
    for r in results:
        mark = "✓" if r.ok else "✗"
        parts = []
        if r.done:
            parts.append("feito: " + ",".join(r.done))
        if r.skipped:
            parts.append("pulado: " + ",".join(r.skipped))
        if r.failed:
            parts.append("erro: " + ",".join(r.failed))
        print(f" {mark} {r.path.name} [{r.job_id or '-'}] {'; '.join(parts)} ({_fmt_duration(r.seconds)})")
    print("=" * 72)


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252: keep ✓ / accents
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = build_parser().parse_args(argv)

    root: Path = args.directory
    if not root.is_dir():
        print(f"Diretório não encontrado: {root}", file=sys.stderr)
        return 2
    from app.models import TUNINGS

    if args.tuning not in TUNINGS:
        print(f"Afinação desconhecida: {args.tuning} (opções: {', '.join(TUNINGS)})", file=sys.stderr)
        return 2
    if "qtab" in args.steps and args.qtab_dir is None:
        print("A etapa qtab precisa de --qtab-dir.", file=sys.stderr)
        return 2

    files = find_audio_files(root, args.recursive, tuple(args.ext.split(",")))
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        print(f"Nenhuma música encontrada em {root}" + ("" if args.recursive else " (use -r para subpastas)"))
        return 0

    print(f"{len(files)} música(s) em {root} · etapas: {', '.join(args.steps)}")
    if args.dry_run:
        for f in files:
            print(f"  {f.relative_to(root)}")
        return 0

    if not args.verbose:  # library chatter (deprecations, audioread fallbacks) drowns the progress
        warnings.filterwarnings("ignore")
    with Status("carregando bibliotecas (torch, librosa…)"):
        from app import projects  # noqa: F401  (the heavy imports, once, up front)
        from app.job_manager import JobManager

    manager = JobManager(workspace_root=args.workspace)
    index = ImportIndex(manager.workspace_root)
    opts = Options(
        steps=args.steps, separation=args.separation, isolated_stem=args.isolated_stem,
        split_guitar_roles=not args.no_split_guitar_roles, language=args.language,
        whisper_model=args.whisper_model, tuning=args.tuning, qtab_dir=args.qtab_dir,
        qtab_stems=[s.strip() for s in args.qtab_stems.split(",") if s.strip()], force=args.force,
        verbose=args.verbose,
    )

    results: list[SongResult] = []
    interrupted = False
    seen: dict[str, Path] = {}
    t0 = time.monotonic()
    try:
        for i, path in enumerate(files, 1):
            print()
            eta = ""
            worked = [r.seconds for r in results if r.done]
            if worked:
                left = sum(worked) / len(worked) * (len(files) - i + 1)
                eta = f" · decorrido {_fmt_duration(time.monotonic() - t0)} · restam ~{_fmt_duration(left)}"
            _log(f"[{i}/{len(files)}] {path.relative_to(root)}{eta}")
            result = process_song(path, manager, index, opts, seen)
            results.append(result)
            if args.fail_fast and not result.ok:
                break
    except KeyboardInterrupt:
        interrupted = True
        print("\nInterrompido (Ctrl+C). Rode de novo para continuar de onde parou.")
    _print_summary(results, time.monotonic() - t0, interrupted)
    if interrupted:
        return 130
    return 0 if all(r.ok for r in results) else 1
