# Design Spec: Piano Roll Multi-Instrumento para Leitura e Estudo de Notas

- **Data:** 2026-11-27
- **Escopo:** Visualizador interativo de Piano Roll da música inteira na aba **MIDI**, com seleção de um ou mais instrumentos (cores distintas por instrumento), nomes das notas nas barras, visor ao vivo de notas sob a agulha, destaque das notas do tom, posições no braço da guitarra, audição isolada ao clicar e sincronismo duplo com o player de áudio (`WaveSurfer`) e o player de instrumentos virtuais (`MidiPlayer`).

---

## 1. Problema e Motivação

Hoje, quando o Dettato transcreve o MIDI completo da música (`full_song.mid`), a aba **MIDI** mostra apenas o resumo numérico de cada faixa (`1297 notas · B1–B4`) e os botões `M`/`S` do player com SoundFonts. O usuário não consegue **enxergar nem ler as notas transcritas da música inteira**, comparar visualmente instrumentos (ex.: *Electric Guitar - Base* vs. *Solo/Riff* vs. *Synth Lead* vs. *Electric Bass*), nem acompanhar nota por nota em câmera lenta para aprender a música lendo o Piano Roll.

---

## 2. Objetivos e Não-Objetivos

### Objetivos
- **Visualização da música inteira com 60 FPS:** exibir todas as notas de um ou mais instrumentos selecionados do `full_song.mid`, com uma cor distinta e consistente para cada faixa.
- **Foco pedagógico em leitura de notas:**
  1. Teclado/régua vertical fixa à esquerda com nomes das notas (`C4`, `F#4`, `G4`), diferenciando teclas brancas/pretas, oitavas (`C`) e iluminando as teclas ativas em tempo real.
  2. Nome da nota escrito dentro da própria barra horizontal quando o nível de zoom comportar.
  3. Visor **"Notas agora"** acima do canvas listando as notas que cruzam a agulha (ou a nota clicada), com nome científico + português (`E4 · Mi4`), cor do instrumento e sugestão de `corda/casa` na guitarra.
  4. Destaque opcional das linhas horizontais pertencentes à escala/tom da música (quando `harmony.json` existir) e marcadores superiores das **Partes** (`Intro`, `Solo 1`, `Refrão`...) + grade de batidas/compassos (`job.beats`).
  5. **Audição ao clicar:** clicar em uma barra de nota ou tecla lateral reproduz aquela nota isoladamente (usando a SoundFont carregada no `MidiPlayer` quando disponível, com fallback automático para síntese WebAudio local).
- **Sincronismo duplo de agulha (`WaveSurfer` + `MidiPlayer`):** acompanhar automaticamente o `MidiPlayer` quando ele estiver tocando (com interpolação suave a 60 FPS entre atualizações de ticks) ou o player principal de áudio/stems (`projWave`, inclusive com velocidade reduzida de 25%–100% e loop de trecho).

### Não-Objetivos (YAGNI)
- Edição destrutiva de notas MIDI (arrastar/apagar/desenhar notas no `full_song.mid`).
- Substituir o editor/player de tablatura individual (`AlphaTab`) da visão de Parte.

---

## 3. Arquitetura e Fluxo de Dados

### 3.1. Backend: Endpoint de Notas Multi-Faixa

1. **`dettato_transcriber.multitrack_notes_payload(midi_path: Path, tracks: list[MidiTrackInfo], duration: float = 0.0) -> dict[str, Any]`** ([dettato_transcriber.py](file:///d:/dev/github/music/escritab/app/pipeline/dettato_transcriber.py)):
   - Lê `full_song.mid` via `pretty_midi.PrettyMIDI`.
   - Mantém cache leve em memória por `(str(midi_path), mtime, len(tracks))` para respostas instantâneas ao alternar projetos/abas.
   - Para cada `MidiTrackInfo` em `tracks` (alinhado por índice com `pm.instruments`), ordena as notas por `(start, pitch)` e serializa como lista compacta `[round(start, 4), round(end, 4), int(pitch), int(velocity)]`.
   - Calcula `duration = round(max(duration, pm.get_end_time()), 4)`.
2. **`GET /api/projects/{project_id}/multitrack/notes`** ([main.py](file:///d:/dev/github/music/escritab/app/main.py)):
   - Carrega o projeto; se `not job.multitrack` ou `full_song.mid` não existir no diretório do job, retorna HTTP `404` (`"MIDI completo ainda não foi gerado."`).
   - Retorna o JSON `{ "duration": float, "tracks": [...] }`.

---

### 3.2. Paleta por Instrumento e Integração com o Mixer

- **Paleta determinística de 10 cores (`TRACK_COLORS`):**
  1. `#3b82f6` (Azul)
  2. `#f59e0b` (Âmbar)
  3. `#10b981` (Esmeralda)
  4. `#a855f7` (Violeta)
  5. `#f43f5e` (Coral)
  6. `#06b6d4` (Ciano)
  7. `#84cc16` (Lima)
  8. `#f97316` (Laranja)
  9. `#ec4899` (Magenta)
  10. `#eab308` (Dourado)
- Cada faixa recebe `TRACK_COLORS[track.index % TRACK_COLORS.length]`.
- Na lista de faixas `#multitrackTracks` ([app.js](file:///d:/dev/github/music/escritab/app/static/app.js#L2905-L2920)), cada linha ganha um indicador colorido `<span class="track-color-dot">` com a mesma cor usada no chip seletor e nas barras do Piano Roll.
- **Seleção inicial de faixas no Piano Roll:**
  - Por padrão, seleciona todas as faixas **melódicas com notas** (`!t.is_drum && t.note_count > 0`), mantendo `Drums` desmarcada inicialmente para não comprimir o zoom vertical com notas GM `35..81` (mas permitindo marcá-la com um clique).
  - Atalhos rápidos na barra de filtros: `Melódicas`, `Guitarras / Solos` (`t.is_guitar || t.role === "lead_candidate"`), `Limpar`, além de `Alt+Clique` (ou duplo clique) num chip para isolar apenas aquele instrumento.

---

### 3.3. Componente `PianoRoll` (`app/static/piano_roll.js`)

Classe autocontida `window.PianoRoll` com métodos estáticos puros (testáveis em Node.js sem DOM) e renderizador `<canvas>` 2D responsivo (`devicePixelRatio` nítido em telas HiDPI).

#### Indexação e Busca Rápida (`O(log N)`)
- Ao receber `tracks`, pré-calcula para cada faixa um vetor monotônico `maxEndPrefix[i] = max(maxEndPrefix[i - 1], notes[i][1])`.
- `PianoRoll.visibleNoteRange(notes, maxEndPrefix, tStart, tEnd)`:
  - `firstIdx`: busca binária pelo primeiro `i` com `maxEndPrefix[i] >= tStart` (garante que notas longas iniciadas antes de `tStart` mas ainda soando dentro da janela nunca sumam ao rolar!).
  - `lastIdx`: busca binária pelo primeiro `j` com `notes[j][0] > tEnd`.
- `PianoRoll.activeNotesAt(tracks, selectedSet, t)`: retorna todas as notas visíveis com `start <= t < end`, ordenadas da mais aguda para a mais grave.
- `PianoRoll.pitchRange(tracks, selectedSet)`: calcula `[minPitch, maxPitch]` apenas sobre as faixas selecionadas, adiciona margem de `±2` semitons (mínimo de `14` semitons de altura total, dentro de `[12..108]`) para aproveitar toda a altura vertical útil do canvas.
- `PianoRoll.guitarPositions(pitch, tuningPitches, maxFrets = 24)`: retorna pares `{ string, fret }` (`1` = corda mais aguda, `N` = mais grave) ordenados por conforto de leitura, exibindo a sugestão principal no visor **"Notas agora"** (ex.: `2ª c.5`) e todas as opções no tooltip.

#### Camadas Visuais do `<canvas>`
1. **Faixa superior (`28px`):** régua de tempo (`0:00`, `0:15`... ou compassos) + blocos translúcidos das **Partes** do projeto (`Intro`, `Solo (1)`...), destacando a Parte selecionada no player principal.
2. **Grade de fundo:**
   - Linhas horizontais por semitom: teclas brancas ligeiramente mais claras que teclas pretas; linha divisória acentuada em todo `C` (`C2`, `C3`, `C4`...).
   - Quando `[x] Destacar tom` está ativo e o projeto possui análise harmônica (`key.tonic`, `key.scale` / `flats`), aplica um matiz sutil nas linhas pertencentes à escala (e sutilmente mais marcado na tônica) e usa bemóis/sustenidos coerentes com a armadura do tom nos nomes das notas (`Bb3` vs. `A#3`).
   - Linhas verticais nos `job.beats` visíveis (a cada 4 batidas com traço mais forte; em zoom muito aberto, reduz a densidade automaticamente para não poluir).
3. **Barras de notas:**
   - Retângulos arredondados com preenchimento na cor da faixa (transparência leve ajustada por `velocity`), borda de `1px` mais clara.
   - Se `widthPx >= 26` e `rowHeight >= 11`, escreve o nome da nota (`E4`, `F#4`, ou sigla da peça de bateria `Kick`, `Snare`, `HH` para `is_drum`) dentro da barra com contraste legível.
   - Notas cruzadas pela agulha (`start <= playhead < end`) ou selecionadas por clique recebem brilho/contorno branco-dourado.
   - Sobreposição de múltiplos instrumentos na mesma nota e mesmo tempo: deslocamento vertical interno sutil (`2px`) + borda nítida, desenhando faixas de solo/melodia por cima das faixas de base/baixo.
4. **Teclado lateral esquerdo fixo (`56px`):**
   - Desenha teclas brancas/pretas alinhadas às linhas da grade; escreve todos os nomes de notas quando `rowHeight >= 13` (ou todas as notas naturais / `C` e `G` se o range vertical for muito amplo, ex.: baixo `B0` + synth `G6` juntos).
   - Ilumina cada tecla ativa com a cor do instrumento que está tocando aquela altura no momento.
5. **Agulha vertical (Playhead):** linha vertical contínua atravessando régua e grade na posição `t`.

#### Interações de Estudo
- **Clique em uma barra de nota:** seleciona a nota, exibe seus detalhes completos na barra **"Notas agora"** e dispara a audição da nota (`onAuditionNote({ track, pitch, velocity, duration })`).
- **Clique em uma tecla lateral esquerda:** dispara a audição daquela altura (`velocity = 96`, `duration = 0.45s`).
- **Clique na régua superior:** clicar num bloco de Parte seleciona/busca o início da Parte; clicar em outro ponto da régua (ou em área vazia da grade sem arrastar) faz `seek(t)`.
- **Arrastar horizontalmente (drag) ou `Shift + Roda` / scrollbar:** navega no tempo (desativando temporariamente o auto-follow se o usuário rolar longe da agulha, ou mantendo conforme o toggle `[x] Seguir`).
- **`Ctrl + Roda do mouse` e botões `-` / `+` / `Trecho` / `Tudo`:** altera `pxPerSec` mantendo o ponto sob o cursor (ou a agulha) ancorado; `Trecho` ajusta `tStart..tEnd` exatamente para a Parte selecionada no topo (`sel.cur`).

---

### 3.4. Sincronia Dupla e Audição (`midi_player.js` + `app.js`)

1. **Melhorias em `app/static/midi_player.js`:**
   - Rastreia `this._anchorTick = 0` e `this._anchorPerf = 0` sempre que `this.position` muda (`play`, `seek`, `_startClock`, `pause`, `stop`).
   - Novo método `currentSeconds()`: quando `this.playing` é `false`, retorna `this.seconds(this.position)`; quando `true`, retorna `Math.min(this.seconds(this.total || Infinity), this.seconds(this._anchorTick) + (performance.now() - this._anchorPerf) / 1000)`, entregando movimento contínuo a 60 FPS para o Piano Roll.
   - Novo método `auditionNote(trackIndex, pitch, velocity = 96, durationMs = 420)`: localiza o canal da faixa (ou canal `0`), verifica se `this.synth` já tem a SoundFont da rota carregada, toca `midiNoteOn` + `setTimeout -> midiNoteOff` e retorna `true` (retorna `false` se o motor/SoundFont ainda não estiver carregado, sem travar nem iniciar download pesado).
2. **Orquestração em `app/static/app.js` e `app/static/index.html`:**
   - Dentro de `#mixerBox` (logo abaixo de `#midiPlayer`), adiciona `#pianoRollBox` contendo:
     - Cabeçalho + chips de instrumentos + botões rápidos (`Melódicas`, `Guitarras/Solos`, `Limpar`)
     - Barra de ferramentas (`-`, `+`, `Trecho`, `Tudo`, `[x] Seguir`, `[x] Destacar tom`)
     - Visor ao vivo `#prNowBar` ("Notas agora" / dica de clique)
     - Canvas `#pianoRollCanvas` + tooltip flutuante `#prTooltip`
   - **Fonte da agulha:**
     - Enquanto `sound.player && sound.player.playing`, um loop `requestAnimationFrame` atualiza `pianoRoll.setTime(sound.player.currentSeconds(), true)`.
     - Quando o `MidiPlayer` está parado, `projWave.onTime` e `projWave.ws.on("seeking")` atualizam `pianoRoll.setTime(t, projWave.ws.isPlaying())`.
     - `onSeek(t)` no Piano Roll: se `sound.player && sound.player.playing`, converte `t` para ticks (`MidiPlayer.ticksAtSeconds(t)`) e chama `sound.player.seek(ticks)`; caso contrário, chama `projWave.ws.setTime(t)` (e se `sound.player && sound.player.total`, também sincroniza `sound.player.position` parado).
   - **Fallback WebAudio de audição:** se `sound.player.auditionNote(...)` retornar `false`, usa `playPitches([pitch])` para notas melódicas ou um pulso percussivo WebAudio curto para `is_drum`.

---

## 4. Casos de Borda e Tratamento de Erros

- **Projeto sem MIDI completo:** `#pianoRollBox` permanece oculto junto com `#mixerBox`.
- **Re-transcrição do MIDI completo (`runMultitrack`):** limpa o cache do Piano Roll do projeto atual e busca `/multitrack/notes` novamente.
- **Aba `MIDI` inicialmente oculta (`offsetParent === null`):** `ResizeObserver` re-mede e redesenha o `<canvas>` assim que o usuário abre a aba `MIDI`, evitando canvas de largura `0`.
- **Nenhuma faixa marcada nos chips:** desenha estado vazio claro no centro do canvas orientando a clicar em um instrumento acima.
- **Faixas com muitas notas (`10.000+`):** `visibleNoteRange` limita a iteração apenas à janela visível, e em zoom de "Música toda" oculta textos internos menores que `26px` para manter nitidez e 60 FPS.

---

## 5. Estratégia de Testes

1. **Backend (`tests/test_piano_roll.py`):**
   - `GET /api/projects/{id}/multitrack/notes` retorna `404` antes de transcrever e `200` após `multitrack`, preservando todas as faixas (incluindo `is_drum`), metadados (`role`, `is_guitar`, `program`) e tuplas `[start, end, pitch, velocity]` ordenadas.
   - Cache por `mtime` invalida corretamente quando `full_song.mid` é regravado.
2. **Frontend / Lógica do `PianoRoll` e `MidiPlayer` via Node.js (`tests/test_piano_roll.py`):**
   - `PianoRoll.visibleNoteRange`: verifica inclusão de notas longas iniciadas antes de `tStart` (`maxEndPrefix`) e corte exato após `tEnd`.
   - `PianoRoll.pitchRange` e `PianoRoll.activeNotesAt`: enquadramento vertical por instrumentos selecionados, exclusão de faixas desmarcadas e ordenação aguda → grave.
   - `PianoRoll.noteLabel` e `PianoRoll.guitarPositions`: enarmonia `#`/`b`, rótulos GM de bateria e cálculo de `corda/casa` para afinações de 6 e 7 cordas.
   - `MidiPlayer.prototype.ticksAtSeconds`: inversão exata de `seconds(ticks)` em mapas multi-tempo para `seek(t)` a partir do clique no Piano Roll.
   - Verificação de IDs únicos em `index.html` e inclusão de `/static/piano_roll.js`.
