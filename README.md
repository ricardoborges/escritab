# EscriTab

Transforma áudio (MP3/WAV/FLAC, inclusive músicas geradas no Suno) em **MIDI multi-instrumento** e
**tablatura de guitarra**, com um estúdio web local para ouvir, recortar e exportar as partes.

## Como funciona

1. **Upload** da música: cria um projeto.
2. **Separação de stems** (opcional) com [Demucs](https://github.com/facebookresearch/demucs).
3. **Transcrição para MIDI** com [Dettato](https://github.com/kobimusic/dettato), que gera uma faixa
   por instrumento detectado, mais a bateria. O motor legado Basic Pitch continua disponível para
   trechos curtos.
4. **Separação de papéis da guitarra**: base (rítmica) e solo/riff.
5. **Otimização no braço** (Viterbi) para escolher corda e casa de cada nota, em afinações de 6 e
   7 cordas (Standard E, Drop D, Eb, D Standard, Drop C, Drop B, 7 cordas Standard e Drop A).
6. **Exportação** em Guitar Pro (`.gp5`), MusicXML e tablatura ASCII, com player AlphaTab e player
   MIDI com SoundFonts no navegador.

A análise pesada roda uma única vez por projeto e fica em cache. Criar ou mover uma "parte" (um
trecho da música) só recorta as notas já transcritas.

## Requisitos

- **Python 3.11** (exatamente 3.11, por causa do TensorFlow usado pelo motor legado)
- [uv](https://docs.astral.sh/uv/)
- **GPU NVIDIA** recomendada. No Windows e no Linux o PyTorch é instalado com CUDA 12.8 (RTX 30xx ou
  mais nova). Também roda em CPU, só que mais devagar.
- `git`, porque o Dettato é instalado direto do GitHub

## Instalação

```bash
uv sync
```

Para incluir o motor legado Basic Pitch (instala o TensorFlow 2.15, que roda só em CPU no Windows):

```bash
uv sync --extra legacy
```

Sem esse extra, o motor `basic_pitch` usa o pYIN do librosa como alternativa.

Na primeira transcrição, os pesos do Dettato são baixados do Hugging Face.

## Executar

```bash
uv run python run.py
```

Depois abra <http://127.0.0.1:8000>.

Os projetos e arquivos gerados ficam em `workspace/`.

### SoundFonts (opcional)

Os players do navegador usam arquivos `.sf2` / `.sf3`. Por padrão eles são lidos da pasta
`./soundfonts`, que pode ser uma pasta comum ou um link/junction. Para usar outras pastas, defina
`ESCRITAB_SOUNDFONTS` (separe várias pastas com `;` no Windows ou `:` no Linux/macOS):

```powershell
$env:ESCRITAB_SOUNDFONTS = "D:\SoundFonts;E:\Mais SoundFonts"
uv run python run.py
```

## Testes

```bash
uv run pytest
```

## Estrutura

```
app/
  main.py            API FastAPI e servidor do frontend
  projects.py        projetos, partes e cache da análise
  soundfonts.py      biblioteca local de SoundFonts
  pipeline/
    stem_separator.py      Demucs
    dettato_transcriber.py transcrição multi-instrumento
    midi_transcriber.py    Basic Pitch / pYIN (legado)
    guitar_roles.py        base x solo
    fretboard.py           otimizador de digitação (Viterbi)
    exporters.py           GP5, MusicXML, tab ASCII
  static/            SPA (HTML/JS/CSS, AlphaTab, player MIDI)
tests/               testes pytest
run.py               ponto de entrada (uvicorn em 127.0.0.1:8000)
```
