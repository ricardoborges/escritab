# Design Spec: Precisão Rítmica, Mapa de Beats e Tercinas (MIDI & Tablatura)

- **Data:** 2026-11-25
- **Escopo:** Frente 1 (P0) — Alinhamento métrico ao mapa real `job.beats`, gravação de mapa de tempo dinâmico nos MIDIs (`full_song.mid` e `guitar.mid`), suporte a compasso de anacruse (*pickup*) e detecção automática de tercinas (`3:2`) na tablatura.

---

## 1. Problema Atual

1. **Desalinhamento entre áudio e compassos da tablatura (`exporters._layout`):**
   - `_layout(slices, bpm)` assume um metrônomo rígido `unit = (60 / bpm) / 8` ancorado em `t = 0.0`.
   - Na prática, o primeiro beat de uma música raramente cai em `0.000s` (ex.: `0.279s` por silêncio de masterização) e partes criadas pela letra ou manualmente iniciam fora da cabeça do tempo (`22.38s`, `40.05s`), além de sofrerem pequenas flutuações de andamento ao longo da gravação.
   - Isso desloca notas simples em frações de semicolcheia/fusa, gerando ligaduras espúrias (`semínima + semicolcheia pontuada`) e barras de compasso defasadas do pulso real.
2. **MIDIs exportados sem mapa de tempo real (`dettato_transcriber._write_midi` e `midi_transcriber.notes_to_midi_file`):**
   - `full_song.mid` é sempre gravado com `initial_tempo = 120.0` fixo, ignorando `job.detected_bpm` e `job.beats`. Ao abrir o arquivo em DAWs, Guitar Pro ou editores de partitura, os compassos não correspondem à música.
   - `MidiPlayer.seconds(ticks)` em `app/static/midi_player.js` assume um único BPM constante durante toda a música.
3. **Ausência de tercinas (*triplets*) e quebras rítmicas de difícil leitura:**
   - `GRID = 8` (divisão binária até fusa) não divide um tempo por `3` nem por `6`. Levadas em *shuffle*, galopes de metal e solos com tercinas/sextinas são distorcidos em combinações binárias pontuadas com ligaduras.
   - `_split_units` usa decomposição gulosa sem respeitar o meio do compasso `4/4` (tempo 3), produzindo figuras síncopas difíceis de ler.

---

## 2. Objetivos e Não-Objetivos

### Objetivos
- **Fonte única de verdade temporal (`BeatMap`):** mapear bidirecionalmente `segundos ↔ beats` a partir de `job.beats` (com fallback transparente para grade uniforme em `bpm` quando não houver beats suficientes).
- **Mapa de tempo dinâmico nos MIDIs (`full_song.mid` e `guitar.mid`):** preservar os timestamps exatos em segundos (sincronia total com o áudio original) enquanto posiciona cada beat real em múltiplos exatos de `ticks_per_beat = 480` via eventos `set_tempo`.
- **Compasso de anacruse (*pickup*) até o 1º beat interno da parte:** alinhar o Compasso 1 em `4/4` ao primeiro beat da parte recortada, acomodando notas anteriores em um compasso inicial curto de anacruse (`implicit="yes"` no MusicXML e fórmula ajustada no GP5).
- **Grade `GRID = 24` com detecção automática de tercinas por beat:** representar subdivisões binárias (até fusa) e ternárias (`1/8T` e `1/16T`) na mesma grade inteira, ativando tercinas apenas nos tempos em que os ataques se encaixam claramente em `1/3` e `2/3` do beat, garantindo fechamento matemático exato em todos os compassos.
- **Compatibilidade total:** manter `beats: Sequence[float] | None = None` opcional em todas as APIs internas para não quebrar fluxos nem testes existentes que fornecem apenas `bpm`.

### Não-Objetivos (YAGNI para esta etapa)
- Inferência automática de *downbeat* global da música inteira ou detecção de fórmulas de compasso diferentes de `4/4` (como `3/4`, `6/8`, `7/8`) além do compasso inicial de anacruse.
- Separação polifônica em múltiplas vozes (`voice 1` / `voice 2`) ou reforma do otimizador Viterbi de casas (`fretboard.py`, reservado para a Frente 2).

---

## 3. Arquitetura e Componentes

### 3.1. Novo módulo `app/pipeline/rhythm.py`

Responsável exclusivamente pela conversão temporal (`BeatMap`), construção de objetos `PrettyMIDI` com mapa de tempo e quantização rítmica em `GRID = 24`.

#### `BeatMap`
- **Construção:**
  - `BeatMap.from_beats(beats: Sequence[float] | None, bpm: float, duration: float | None = None)` — linha do tempo da música ou trecho já iniciando em `0.0s`.
  - `BeatMap.for_excerpt(beats: Sequence[float] | None, bpm: float, start: float, end: float)` — recorta e desloca os beats do projeto para a linha do tempo local `[0.0, end - start]` da parte.
- **Sanitização de beats:**
  - Filtra valores finitos, ordena e remove duplicatas ou intervalos anômalos (`dt < 0.15s` → `> 400 BPM`, ou `dt > 2.5s` → `< 24 BPM`).
  - Se restarem `< 2` beats válidos no trecho, cai em modo uniforme com passo `step_s = 60.0 / bpm` ancorado no único beat disponível (se houver) ou em `t = 0.0`.
- **Ancoragem do Compasso 1 em partes (`for_excerpt`):**
  - Seja `b_rel = b_abs - start`. Todo beat com `b_rel >= -BEAT_EDGE_TOL_S` (`BEAT_EDGE_TOL_S = 0.08s`) e `b_rel <= (end - start) + step_s` é considerado; o primeiro deles (`b0`) recebe coordenada **`beat = 0.0`** (cabeça do Compasso 1).
  - Isso evita que um corte manual feito poucas dezenas de milissegundos após a batida transforme o acorde do tempo 1 em anacruse, ao mesmo tempo em que remove silêncios/offsets iniciais (ex.: `b0 = 0.279s` faz notas atacadas em `0.28s` caírem exatamente em `beat = 0.0`).
  - Para `t < b0` e `t > b_last`, extrapola linearmente usando a mediana `med_dt` dos intervalos locais de beat (`bpm_local = 60.0 / med_dt`).
- **Métodos principais:**
  - `time_to_beat(t: float | np.ndarray) -> float | np.ndarray`: interpolação contínua por partes (`np.interp` nos beats internos + extrapolação linear nas bordas).
  - `beat_to_time(b: float | np.ndarray) -> float | np.ndarray`: operação inversa exata.
  - `bpm: float`: andamento nominal (`round(60.0 / med_dt, 1)` quando há beats válidos, senão o `bpm` recebido).

#### `build_pretty_midi(beats: Sequence[float] | None, bpm: float, max_time: float, resolution: int = 480) -> pretty_midi.PrettyMIDI`
- Quando `beats` é `None` ou tem `< 2` pontos válidos, retorna `pretty_midi.PrettyMIDI(resolution=resolution, initial_tempo=float(bpm))`.
- Quando há `>= 2` beats válidos em `[0, max_time]`:
  1. Se `beats[0] >= 0.25 * med_dt`, aloca `lead_beats = max(1, int(round(beats[0] / med_dt)))` beats de pré-roll entre `t = 0.0` (`tick = 0`) e `beats[0]` (`tick = lead_beats * resolution`), com `tempo_0 = round(beats[0] * 1e6 / lead_beats)`.
  2. Se `0.0 <= beats[0] < 0.25 * med_dt`, ancora `tick = 0` em `t = 0.0` e `tick = resolution` em `beats[1]`, evitando criar um micro-compasso inicial com BPM extremo por causa de poucos milissegundos de padding de codec.
  3. Para cada par consecutivo `(beats[k], beats[k+1])`, emite `mido.MetaMessage("set_tempo", ...)` com `tempo = clamp(round((beats[k+1] - beats[k]) * 1e6), 150_000, 3_000_000)` (`20` a `400` BPM).
  4. Estende um evento final `end_of_track` cobrindo `max_time + med_dt` e instancia `pretty_midi.PrettyMIDI(mido_object=template)`.
  5. Resultado: `pm.time_to_tick(note.start)` preserva o segundo exato do áudio (erro `< 1 ms`) e crava cada `beats[k]` em um múltiplo inteiro de `480` ticks.

---

### 3.2. Grade `GRID = 24`, Anacruse e Detecção Automática de Tercinas

Constantes em `exporters.py` / `rhythm.py`:
- `GRID = 24` (unidades por semínima)
- `BEATS_PER_BAR = 4`
- `MEASURE_UNITS = BEATS_PER_BAR * GRID` (`96` unidades em `4/4`)
- `FILL_GAP_UNITS = 6` (gaps de até uma semicolcheia até o próximo ataque são ligados como legato, sem pausa curta espúria — equivalente exato ao antigo `FILL_GAP_UNITS = 2` em `GRID = 8`).

#### Tabela de Figuras Suportadas (`DurSpec`)
`class DurSpec(NamedTuple)` com `(units: int, gp_value: int, xml_type: str, dotted: bool = False, tuplet: tuple[int, int] | None = None)` — mantendo indexação posicional `dur[0]` / `dur[2]` compatível com `_Event.dur`.

- **Figuras binárias (`tuplet = None`, múltiplos de `3`):**
  - `(96, 1, "whole", False)`
  - `(72, 2, "half", True)`
  - `(48, 2, "half", False)`
  - `(36, 4, "quarter", True)`
  - `(24, 4, "quarter", False)`
  - `(18, 8, "eighth", True)`
  - `(12, 8, "eighth", False)`
  - `(9, 16, "16th", True)`
  - `(6, 16, "16th", False)`
  - `(3, 32, "32nd", False)`
- **Figuras ternárias (`tuplet = (3, 2)`, múltiplos de `4` dentro de `24`):**
  - `(16, 4, "quarter", False, (3, 2))` — semínima em tercina (2/3 de beat)
  - `(8, 8, "eighth", False, (3, 2))` — colcheia em tercina (`1/8T`, 1/3 de beat)
  - `(4, 16, "16th", False, (3, 2))` — semicolcheia em tercina/sextina (`1/16T`, 1/6 de beat)

#### Classificação Binário vs. Tercina por Beat
Para cada beat inteiro `b_idx` cobrindo `[b_idx, b_idx + 1.0)`:
1. Coletam-se as fases `f = beat_onset - b_idx` em `[0, 1)` dos ataques que caem nesse tempo (ignorando ataques muito próximos das bordas `< 0.08` ou `> 0.92`, que pertencem à cabeça do tempo).
2. Se não houver ataques internos, ou todos estiverem perto de `0.5` (`|f - 0.5| <= 0.09`), o beat é **binário**.
3. Caso contrário, compara-se a distância quadrática média dos ataques internos para:
   - Grade binária de semicolcheia (`{0.0, 0.25, 0.5, 0.75}`): `rms_bin16`
   - Grade ternária (`{0.0, 1/6, 1/3, 0.5, 2/3, 5/6}`): `rms_trip`
4. O beat é marcado como **ternário** somente se:
   - Pelo menos um ataque estiver a `<= 0.085` de `{1/3, 2/3, 1/6, 5/6}` (com preferência para `1/3` e `2/3`), **e**
   - `rms_trip + 0.035 < rms_bin16`.

#### Quantização e Fechamento Sem Restos
1. **Onsets:** cada `TabSlice` tem `b_on = beat_map.time_to_beat(sl.start)`. Se o beat `floor(b_on)` for ternário, quantiza `on` para múltiplos de `4` unidades (`round(b_on * 6) * 4`); se for binário, quantiza para múltiplos de `3` unidades (`round(b_on * 8) * 3`). Slices que colidem no mesmo `on` são fundidos com `_merge_slices`.
2. **Durações:** truncadas no próximo `on` (preenchendo gaps `<= FILL_GAP_UNITS = 6` quando compatíveis com a grade do beat); a última nota tem seu `end` quantizado na grade do próprio beat final (múltiplo de `6` em beats binários — ou `3` se `< 4.5` — e múltiplo de `4` em beats ternários).
3. **Compasso de anacruse (`Measure 0`):**
   - Se `min_on < 0`, define `pickup_beats = ceil(-min_on / GRID)` (limitado a `1..3` se `min_on >= -3 * GRID`, ou compassos extras anteriores caso maior) e desloca a origem da grade em `+pickup_beats * GRID`, gerando um primeiro compasso de tamanho `pickup_units = pickup_beats * GRID` (`is_pickup = True`) seguido de compassos regulares de `96` unidades (`4/4`).
   - Se `min_on >= 0`, não há compasso de anacruse: todos os compassos têm `96` unidades (`4/4`).
4. **Invariante entre beats binários e ternários:**
   - Durante a decomposição de cada segmento `(a, b, slice)` dentro de um compasso:
     - Qualquer cruzamento entre um beat binário e um beat ternário corta o segmento na fronteira exata do beat (`k * 24`), ligando (`tie`) os dois lados.
     - Dentro de um compasso regular `4/4` (`96` unid.) em região puramente binária, segmentos que começam em `pos > 0` e atravessam o meio do compasso (`pos < 48 < seg_end`, exceto `0..96`, `0..72` e `24..96`) são divididos em `48` (com `tie` se for nota) para respeitar a barreira visual do 3º tempo.
     - Segmentos dentro de um beat ternário (`24` unid., extremos múltiplos de `4`) decompõem-se em `{24 (semínima normal quando ocupa o beat inteiro), 16, 8, 4}`, fechando sempre `24` unidades exatas.

---

### 3.3. Integração nos Módulos Existentes

1. **[dettato_transcriber.py](file:///d:/dev/github/music/escritab/app/pipeline/dettato_transcriber.py):**
   - `transcribe_multitrack(..., bpm: float = 120.0, beats: Sequence[float] | None = None)` passa `bpm` e `beats` para `_write_midi`, que usa `rhythm.build_pretty_midi(beats, bpm, max_time)`.
   - Atualizado nas chamadas em [main.py](file:///d:/dev/github/music/escritab/app/main.py#L233-L235) e [batch.py](file:///d:/dev/github/music/escritab/app/batch.py#L285-L287) passando `bpm=job.detected_bpm, beats=job.beats`.
2. **[midi_transcriber.py](file:///d:/dev/github/music/escritab/app/pipeline/midi_transcriber.py):**
   - `postprocess_notes(notes, params, beats: Sequence[float] | None = None)`: quando `params.quantize_grid in ("1/16", "1/32")`, quantiza `start` e `end` no espaço de *beats* via `BeatMap` (`subdiv = 4` ou `8` por beat) e converte de volta para segundos (`beat_to_time`), deduplicando notas idênticas que colidam na mesma posição.
   - `notes_to_midi_file(notes, output_midi_path, bpm=120.0, beats=None)` e `finalize_notes_to_midi(..., beats=None)` usam `rhythm.build_pretty_midi`.
3. **[projects.py](file:///d:/dev/github/music/escritab/app/projects.py):**
   - Em `slice_notes(notes, start, end, beats=None)`: mantém o comportamento padrão quando `beats=None`; quando `beats` está presente, limita `ONSET_TOLERANCE_S` a `min(0.15, 0.35 * med_beat_dt)` e só puxa para `0.0` notas atacadas em `[start - tol, start)` sem colapsar notas internas.
   - Em `generate_part`: extrai `excerpt_beats` relativos ao corte `[part.start, part.end)` (via `BeatMap.for_excerpt`), passa os beats locais para `finalize_notes_to_midi` e para `export_musicxml`, `export_gp5` e `export_ascii_tab`.
4. **[exporters.py](file:///d:/dev/github/music/escritab/app/pipeline/exporters.py):**
   - `_layout_measures(slices, bpm, beats=None)` retorna `list[_Measure]` (`events: list[_Event]`, `beats: int`, `is_pickup: bool`); `_layout(slices, bpm, beats=None)` retorna `[m.events for m in _layout_measures(...)]` mantendo a assinatura `list[list[_Event]]`.
   - `export_musicxml(..., beats=None)`:
     - Usa `divisions = 24`.
     - Se `m.is_pickup`: emite `<measure number="0" implicit="yes">` com `<time><beats>{m.beats}</beats><beat-type>4</beat-type></time>`, e no compasso seguinte (`number="1"`) emite `<attributes><time><beats>4</beats><beat-type>4</beat-type></time></attributes>`.
     - Para eventos com `dur.tuplet == (3, 2)`: emite `<time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>` e marca `<notations><tuplet type="start"/></notations>` no primeiro evento do grupo ternário do beat e `<tuplet type="stop"/>` no último.
   - `export_gp5(..., beats=None)`:
     - Configura `header.timeSignature.numerator = m.beats` (ex.: `1`, `2` ou `3` na anacruse, `4` nos demais).
     - Para eventos ternários: `Duration(value=gp_val, isDotted=dotted, tuplet=Tuplet(enters=3, times=2))`.
   - `export_ascii_tab(..., beats=None)`:
     - Preserva os compassos gerados por `_layout` (incluindo o compasso inicial de anacruse) e anota células em tercina sem quebrar a largura alinhada das cordas.
5. **[midi_player.js](file:///d:/dev/github/music/escritab/app/static/midi_player.js):**
   - Adiciona `_parseTempoMap(arrayBuffer)` lendo os eventos `FF 51 03` (`set_tempo`) da Track 0 do SMF em `setSong`.
   - Atualiza `seconds(ticks)` para interpolar sobre `this.tempoMap` (caindo para `(ticks * 60) / (this.bpm * this.division)` caso o arquivo não tenha Track 0 padrão).

---

## 4. Tratamento de Erros e Casos de Borda

- **Áudios curtos ou sem batida detectável (`job.beats == []`):** `BeatMap` recua automaticamente para grade constante de `bpm`, produzindo resultado idêntico à grade uniforme.
- **Notas sustentadas cruzando anacruse → Compasso 1 ou beat binário → ternário:** divididas na fronteira exata com `tie_to_next=True` / `tie_from_prev=True`.
- **Andamentos extremos entre dois beats ruidosos do Librosa:** filtrados na sanitização (`0.15s <= dt <= 2.5s`) e limitados no SMF (`20..400 BPM`).
- **Testes existentes que importam `MEASURE_UNITS` e `_layout` (`tests/test_exporters.py`):** `_layout` continua iterável/compatível com soma de durações por compasso igual à capacidade do compasso (`m.capacity == MEASURE_UNITS` para compassos `4/4`).

---

## 5. Estratégia de Testes

1. **`tests/test_rhythm.py` (novo):**
   - `BeatMap` com offset inicial (`0.279s`), flutuação de BPM (rubato/drift) e fallback sem beats.
   - Extração de anacruse (`for_excerpt`): corte logo após batida (`<= 80 ms` → sem anacruse espúria) vs. notas reais de *pickup* antes do 1º beat (`1`, `2` e `3` beats de anacruse).
   - `build_pretty_midi`: verificação de que `pm.tick_to_time(pm.time_to_tick(t))` preserva segundos (`< 2 ms`) e de que cada `beats[k]` cai em múltiplo exato de `pm.resolution` (`480`).
2. **`tests/test_exporters.py` (expandido):**
   - Detecção automática de tercinas (`1/8T` e `1/16T`) gerando `Tuplet(enters=3, times=2)` no GP5 e `<time-modification>` + `<tuplet>` no MusicXML, sem afetar riffs binários.
   - Compasso de anacruse (`implicit="yes"` no MusicXML, `timeSignature.numerator` no GP5) e soma exata das durações em 100% dos compassos.
   - Quebra de síncopes atravessando o meio do compasso (`pos = 48`).
3. **`tests/test_dettato_transcriber.py`, `tests/test_midi_transcriber.py`, `tests/test_api.py`:**
   - Verificação de que `full_song.mid` e `guitar.mid` gravam o BPM/mapa de beats do projeto em vez de `120.0` fixo, e regressão completa de toda a suíte `pytest`.
