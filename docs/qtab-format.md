# Formato `.qtab` (v1)

Pacote que o EscriTab exporta para o VstCam / Quantifier Studio: um backing track com os acordes
(e a letra) sincronizados. O VstCam carrega o áudio como backing e desenha a faixa de acordes sobre
a câmera, usando a posição do transporte do backing como relógio único.

Gerado por `GET /api/projects/{id}/export.qtab?stems=&rate=&start=&end=&label=`
(botão **Exportar para VstCam** no player). O código está em `app/qtab.py`. Para exportar, a música
precisa ter a análise harmônica pronta (acordes detectados).

## Regra principal: uma linha do tempo só

**Todo tempo no pacote está em segundos na linha do tempo do áudio exportado.** `0` é a primeira
amostra de `backing.flac`, já recortada e já desacelerada. Quem lê o pacote compara o tempo direto
com a posição de reprodução, sem aplicar `range` nem `rate`. Esses dois campos são só informativos.

## Conteúdo do zip

| Arquivo | Conteúdo |
|---|---|
| `manifest.json` | metadados (abaixo) |
| `backing.flac` | áudio estéreo (ou mono), PCM 16 bits, na taxa de amostragem original |
| `chords.json` | acordes com função harmônica |
| `sections.json` | partes da música (Intro, Verso, Solo...) |
| `lyrics.json` | linhas da letra alinhada (`[]` sem letra) |

O leitor deve usar `manifest.audio.file` e `manifest.files.*`, não os nomes fixos.

### `manifest.json`

```json
{
  "format": "qtab",
  "version": 1,
  "generator": "EscriTab",
  "created_at": "2026-11-22T12:00:00+00:00",
  "title": "Minha música",
  "audio": { "file": "backing.flac", "source": "bass+drums+other+piano+vocals",
             "sample_rate": 44100, "channels": 2, "duration": 183.42 },
  "rate": 0.75,
  "range": { "start": 0.0, "end": 137.57 },
  "bpm": 90.0,
  "beats": [0.41, 1.08, 1.74],
  "key": { "tonic": 11, "tonic_name": "B", "name": "Si menor", "mode": "aeolian" },
  "functions": {
    "T": { "name": "tônica",        "color": "#10b981" },
    "S": { "name": "subdominante",  "color": "#3b82f6" },
    "D": { "name": "dominante",     "color": "#f59e0b" },
    "X": { "name": "fora do campo", "color": "#ec4899" }
  },
  "files": { "chords": "chords.json", "sections": "sections.json", "lyrics": "lyrics.json" },
  "counts": { "chords": 58, "sections": 6, "lyrics": 24 }
}
```

- `audio.source`: `"mix"` (música original) ou os stems somados, unidos por `+`.
- `rate`: velocidade aplicada (0.25 a 1.0), com o tom preservado.
- `range`: trecho exportado, em segundos da música **original**. Só informativo.
- `bpm` e `beats`: já ajustados para a velocidade e o recorte.
- `key`: tonalidade detectada (`tonic`: classe de altura, 0 = Dó). Pode ser `null`.
- `functions`: as cores da faixa de acordes do EscriTab, por função harmônica.

### `chords.json`

```json
[{ "start": 0.0, "end": 8.2, "label": "Bm", "function": "T",
   "numeral": "i", "root": 11, "quality": "min", "bass": null }]
```

- Lista ordenada e contígua (o fim de um acorde é o início do próximo). Acordes que tocam as
  bordas do trecho são cortados nele.
- `label`: o nome a desenhar (`C#5/G#`, `B/D#`, `G7M`...).
- `function`: `T`, `S`, `D` ou `X` (fora do campo harmônico). A cor fica em
  `manifest.functions[function].color`. Na UI do EscriTab o bloco tem fundo dessa cor com ~22% de
  opacidade e uma borda esquerda de 2px na cor cheia.
- `numeral`: grau em algarismo romano em relação à tonalidade (`i`, `bVI7M`...).
- `root`, `bass`: classe de altura (0 = Dó); `bass` é `null` sem inversão.

### `sections.json`

```json
[{ "id": "a1b2c3", "name": "Refrão", "start": 10.0, "end": 24.5 }]
```

### `lyrics.json`

```json
[{ "start": 10.51, "end": 11.74, "section": "Verso 1", "text": "Sangue nel cielo",
   "words": [{ "text": "Sangue", "start": 10.51, "end": 10.86 }] }]
```

## Versões

`version` sobe quando um campo muda de significado ou é removido. Campos novos podem aparecer sem
mudar a versão, então o leitor deve ignorar campos que não conhece.
