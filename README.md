# EscriTab

EscriTab is a web app (FastAPI + JS) for learning songs section by section. You mark the parts of a
song (intro, riff, solo…) on a DAW-style player and practice them in a loop, slowed down without
changing the pitch. For each part, the app transcribes the notes (Dettato, Basic Pitch, or CREPE for
single-note solos, on stems separated with Demucs) and generates a playable tablature. It also shows
the lyrics with chords (Whisper), live harmony, and the most likely scales for figuring out a solo by
ear.

It turns audio (MP3/WAV/FLAC, including songs generated with Suno) into **multi-instrument MIDI** and
**guitar tablature**, with a local web studio to listen to, cut, and export the parts.

## How it works

1. **Upload** a song: this creates a project.
2. **Stem separation** (optional) with [Demucs](https://github.com/facebookresearch/demucs).
   Optionally, [BS-RoFormer](https://github.com/lucidrains/BS-RoFormer) removes the vocals first and
   `htdemucs_6s` separates the instrumental. The vocal stem comes out much cleaner, which helps with
   lyrics alignment. This is about 6× slower. The public viperx weights
   (`model_bs_roformer_ep_317_sdr_12.9755`, ~640 MB) are downloaded on first use to
   `workspace/models/roformer` (or to the folder set in `ESCRITAB_MODELS`).
3. **MIDI transcription** with [Dettato](https://github.com/kobimusic/dettato), which produces one
   track per detected instrument, plus drums. The legacy Basic Pitch engine is still available for
   short excerpts.
   For **single-note solos and riffs**, use the *monophonic solo / riff* engine. It tracks one note
   at a time with [CREPE](https://github.com/maxrmorrison/torchcrepe) on the guitar stem
   (`htdemucs_6s`) and:
   - measures and compensates for the recording's tuning (bands a few cents off A440);
   - splits notes at pick attacks and pitch jumps (hammer-on, pull-off, slide);
   - keeps bends and vibrato as a single note, with the bend marked in the tab (`7b9`).
4. **Guitar role separation**: rhythm and lead/riff.
5. **Fretboard optimization** (Viterbi) to pick the string and fret for each note, in 6- and
   7-string tunings (Standard E, Drop D, Eb, D Standard, Drop C, Drop B, 7-string Standard and
   Drop A).
6. **Export** to Guitar Pro (`.gp5`), MusicXML and ASCII tab, with rhythm quantized down to 32nd
   notes, rests, ties across bar lines and bends, plus an AlphaTab player and a MIDI player with
   SoundFonts in the browser.

The heavy analysis runs once per project and is cached. Creating or moving a "part" (a section of the
song) only slices the notes that were already transcribed.

## Requirements

- **Python 3.11** (exactly 3.11, because of the TensorFlow used by the legacy engine)
- [uv](https://docs.astral.sh/uv/)
- **NVIDIA GPU** recommended. On Windows and Linux, PyTorch is installed with CUDA 12.8 (RTX 30xx or
  newer). It also runs on CPU, just slower.
- `git`, because Dettato is installed straight from GitHub

## Installation

```bash
uv sync
```

To include the legacy Basic Pitch engine (installs TensorFlow 2.15, which runs on CPU only on
Windows):

```bash
uv sync --extra legacy
```

Without that extra, the `basic_pitch` engine falls back to librosa's pYIN.

On the first transcription, the Dettato weights are downloaded from Hugging Face.

## Running

```bash
uv run python run.py
```

Then open <http://127.0.0.1:8000>.

Projects and generated files are stored in `workspace/`.

### SoundFonts (optional)

The browser players use `.sf2` / `.sf3` files. By default they are read from the `./soundfonts`
folder, which can be a regular folder or a link/junction. To use other folders, set
`ESCRITAB_SOUNDFONTS` (separate multiple folders with `;` on Windows or `:` on Linux/macOS):

```powershell
$env:ESCRITAB_SOUNDFONTS = "D:\SoundFonts;E:\More SoundFonts"
uv run python run.py
```

### Batch processing (CLI)

`cli.py` imports every song in a folder as a project and runs the chosen pipeline steps. The
projects show up in the web studio afterwards, with the analysis already cached.

```bash
uv run python cli.py D:\Music                       # stems + full-song MIDI + harmony
uv run python cli.py D:\Music -r --steps all --qtab-dir D:\qtabs
uv run python cli.py D:\Music --dry-run             # just list what would be processed
```

Steps, in order: `stems`, `multitrack`, `harmony`, `lyrics`, `parts`, `tabs`, `qtab` (or `all`).
A song that was already imported (same file content) reuses its project, and steps that are already
done are skipped, so an interrupted run can simply be started again (`--force` redoes them). A
failed step doesn't stop the batch; a summary is printed at the end. See `python cli.py --help`.

## Tests

```bash
uv run pytest
```

## Structure

```
app/
  main.py            FastAPI API and frontend server
  projects.py        projects, parts and analysis cache
  soundfonts.py      local SoundFont library
  pipeline/
    stem_separator.py      Demucs (and the RoFormer + Demucs chain)
    roformer_separator.py  BS-RoFormer: vocals vs. instrumental
    dettato_transcriber.py multi-instrument transcription
    midi_transcriber.py    Basic Pitch / pYIN (legacy)
    lead_transcriber.py    monophonic solo/riff (CREPE + onsets + bends)
    guitar_roles.py        rhythm vs. lead
    fretboard.py           fingering optimizer (Viterbi)
    harmony.py             key, chords per beat, progressions
    lyrics.py              lyrics aligned with Whisper, chord sheet
    scales.py              likely scales for a solo (ear-training aid)
    exporters.py           GP5, MusicXML, ASCII tab
  static/            SPA (HTML/JS/CSS, AlphaTab, MIDI player)
tests/               pytest tests
run.py               entry point (uvicorn on 127.0.0.1:8000)
cli.py               batch processing of a folder (app/batch.py)
```
