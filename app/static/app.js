// EscriTab — learn a song part by part.
//   #/                          projects: upload a song, list the saved ones
//   #/p/<project>               project: waveform, mark and name the parts, analysis (Dettato / Demucs)
//   #/p/<project>/part/<part>   part: practise the excerpt (loop, slower) and get its tab

/* global WaveSurfer, alphaTab, MidiPlayer */

const ROLE_TAGS = {
  rhythm: "base",
  lead: "solo/riff",
  lead_candidate: "provável solo",
};
const STEM_LABELS = {
  guitar: "Guitarra",
  other: "Outros",
  bass: "Baixo",
  drums: "Bateria",
  vocals: "Vocais",
  piano: "Piano",
};
const PART_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#06b6d4", "#ef4444", "#84cc16"];
const DRAFT_COLOR = "rgba(245, 158, 11, 0.28)";

const state = {
  view: null,
  project: null,
  partId: null,
  peaks: null,       // {projectId, duration, peaks}
  device: "cpu",
  busy: false,
  tunings: [],
};
let alphaTabApi = null;
let projWave = null;   // {ws, regions, loop, draft}
let partWave = null;

const $ = (id) => document.getElementById(id);
const els = {
  breadcrumbs: $("breadcrumbs"),
  deviceBadge: $("deviceBadge"),
  // projects
  newProjectForm: $("newProjectForm"),
  audioFileInput: $("audioFileInput"),
  fileNameDisplay: $("fileNameDisplay"),
  projectNameInput: $("projectNameInput"),
  createProjectBtn: $("createProjectBtn"),
  uploadStatus: $("uploadStatus"),
  projectList: $("projectList"),
  projectEmpty: $("projectEmpty"),
  // project
  projNameInput: $("projNameInput"),
  projMeta: $("projMeta"),
  projBpmInput: $("projBpmInput"),
  projectStatus: $("projectStatus"),
  projWave: $("projWave"),
  projPlayBtn: $("projPlayBtn"),
  projStopBtn: $("projStopBtn"),
  projTime: $("projTime"),
  projRate: $("projRate"),
  selectionBar: $("selectionBar"),
  selDot: $("selDot"),
  selLabel: $("selLabel"),
  loopCheck: $("loopCheck"),
  loopToggle: $("loopToggle"),
  selClearBtn: $("selClearBtn"),
  projRateVal: $("projRateVal"),
  snapCheck: $("snapCheck"),
  draftPanel: $("draftPanel"),
  draftName: $("draftName"),
  draftRange: $("draftRange"),
  draftPlayBtn: $("draftPlayBtn"),
  draftSaveBtn: $("draftSaveBtn"),
  draftCancelBtn: $("draftCancelBtn"),
  partList: $("partList"),
  partEmpty: $("partEmpty"),
  analysisBox: $("analysisBox"),
  analysisSummary: $("analysisSummary"),
  mtSummary: $("mtSummary"),
  multitrackBtn: $("multitrackBtn"),
  dlFullMid: $("dlFullMid"),
  splitRolesCheck: $("splitRolesCheck"),
  stemSummary: $("stemSummary"),
  separateModelSelect: $("separateModelSelect"),
  separateBtn: $("separateBtn"),
  analysisStatus: $("analysisStatus"),
  mixerBox: $("mixerBox"),
  multitrackTracks: $("multitrackTracks"),
  // part
  partNameInput: $("partNameInput"),
  partMeta: $("partMeta"),
  partDeleteBtn: $("partDeleteBtn"),
  partWave: $("partWave"),
  partPlayBtn: $("partPlayBtn"),
  partStopBtn: $("partStopBtn"),
  partTime: $("partTime"),
  partRate: $("partRate"),
  partLoopCheck: $("partLoopCheck"),
  partLoopToggle: $("partLoopToggle"),
  partRateVal: $("partRateVal"),
  partSource: $("partSource"),
  partStartInput: $("partStartInput"),
  partEndInput: $("partEndInput"),
  partRangeSaveBtn: $("partRangeSaveBtn"),
  sourcePicker: $("sourcePicker"),
  srcNone: $("srcNone"),
  srcMultitrack: $("srcMultitrack"),
  srcStem: $("srcStem"),
  trackPicks: $("trackPicks"),
  partTracks: $("partTracks"),
  selectedStemSelect: $("selectedStemSelect"),
  engineSelect: $("engineSelect"),
  onsetSlider: $("onsetSlider"),
  onsetVal: $("onsetVal"),
  frameSlider: $("frameSlider"),
  voicingSlider: $("voicingSlider"),
  voicingVal: $("voicingVal"),
  monophonicCheck: $("monophonicCheck"),
  frameVal: $("frameVal"),
  gateSlider: $("gateSlider"),
  gateVal: $("gateVal"),
  bandpassCheck: $("bandpassCheck"),
  tuningSelect: $("tuningSelect"),
  fretPrefSelect: $("fretPrefSelect"),
  maxFretsInput: $("maxFretsInput"),
  minLenSlider: $("minLenSlider"),
  minLenVal: $("minLenVal"),
  quantizeSelect: $("quantizeSelect"),
  suppressHarmonicsCheck: $("suppressHarmonicsCheck"),
  generateBtn: $("generateBtn"),
  partSummary: $("partSummary"),
  partStatus: $("partStatus"),
  playerSection: $("playerSection"),
  dlGp5: $("dlGp5"),
  dlXml: $("dlXml"),
  dlMid: $("dlMid"),
  dlTxt: $("dlTxt"),
  playPauseBtn: $("playPauseBtn"),
  stopBtn: $("stopBtn"),
  tabLoopCheck: $("tabLoopCheck"),
  metronomeCheck: $("metronomeCheck"),
  tabRate: $("tabRate"),
  tabRateVal: $("tabRateVal"),
  alphaTabContainer: $("alphaTabContainer"),
  alphaTabViewport: $("alphaTabViewport"),
  tabStatus: $("tabStatus"),
  asciiPreviewBox: $("asciiPreviewBox"),
};

// ---------- helpers ----------

function banner(el, msg, kind = "info") {
  el.textContent = msg;
  el.classList.remove("hidden", "error", "busy");
  if (kind !== "info") el.classList.add(kind);
}
function hide(el) {
  el.classList.add("hidden");
}
function show(el, visible = true) {
  el.classList.toggle("hidden", !visible);
}
function projectUrl(path = "") {
  return `/api/projects/${state.project.job_id}${path}`;
}
function fileUrl(name) {
  return projectUrl(`/files/${name}`);
}
function partFileUrl(part, name) {
  return projectUrl(`/parts/${part.part_id}/files/${name}?v=${encodeURIComponent(part.stats.total_notes || 0)}-${Date.now()}`);
}
async function api(path, options = {}) {
  const res = await fetch(path, options);
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) throw new Error((data && data.detail) || `Erro HTTP ${res.status}`);
  return data;
}
function sendJson(method, path, body) {
  return api(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}
const postJson = (path, body) => sendJson("POST", path, body);
const patchJson = (path, body) => sendJson("PATCH", path, body);

function onDevice(gpuText, cpuText) {
  return state.device === "cuda" ? gpuText : cpuText;
}
function hasStems() {
  return !!state.project && Object.keys(state.project.stems || {}).length > 0;
}
function hasMultitrack() {
  return !!state.project && (state.project.multitrack || []).length > 0;
}
function currentPart() {
  return state.project && state.project.parts.find((p) => p.part_id === state.partId);
}
function partColor(part) {
  const idx = state.project.parts.indexOf(part);
  return PART_COLORS[(idx < 0 ? 0 : idx) % PART_COLORS.length];
}
function withAlpha(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}
/** 75.25 -> "1:15.2" */
function fmtClock(sec, decimals = 1) {
  const s = Math.max(0, sec || 0);
  const m = Math.floor(s / 60);
  const rest = (s - m * 60).toFixed(decimals).padStart(decimals ? 3 + decimals : 2, "0");
  return `${m}:${rest}`;
}
function fmtRange(start, end) {
  return `${fmtClock(start)} – ${fmtClock(end)} (${(end - start).toFixed(1)} s)`;
}
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c !== null && c !== undefined) node.append(c);
  return node;
}

function setBusy(busy) {
  state.busy = busy;
  document.body.classList.toggle("is-busy", busy);
}

// ---------- routing ----------

function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "p" && parts[1]) {
    return { view: parts[2] === "part" && parts[3] ? "part" : "project", projectId: parts[1], partId: parts[3] || null };
  }
  return { view: "projects" };
}

function navigate(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

async function route() {
  const r = parseHash();
  stopAllAudio();
  try {
    if (r.view === "projects") {
      state.project = null;
      state.partId = null;
      showView("projects");
      await loadProjectList();
      return;
    }
    if (!state.project || state.project.job_id !== r.projectId) {
      banner(els.projectStatus, "Abrindo o projeto...", "busy");
      state.project = await api(`/api/projects/${r.projectId}`);
      state.peaks = null;
    }
    state.partId = r.partId;
    if (r.view === "part" && !currentPart()) {
      navigate(`#/p/${r.projectId}`);
      return;
    }
    showView(r.view);
    if (r.view === "project") {
      hide(els.projectStatus);
      if (await pipelineNeeded()) startPipeline();
      else {
        showPipeline(false);
        await renderProjectView();
      }
    } else await renderPartView();
  } catch (err) {
    showView("projects");
    await loadProjectList();
    banner(els.uploadStatus, `Não foi possível abrir: ${err.message}`, "error");
  }
}

// ---------- project: processing pipeline (shown before the player) ----------
// Upload + tempo happen in POST /api/projects; then the full-song MIDI (Dettato), the stems
// (Demucs) and the harmony, which reads both (stems: cleaner chroma + bass; MIDI: notes). Only when all of it is done the player and the tabs appear.

const PL = {
  jobs: {},    // projectId -> { steps: {id: status}, error, startedAt, stepStartedAt, skipped }
  timer: null,
};
const PL_STEPS = [
  { id: "upload", label: "Áudio recebido" },
  { id: "tempo", label: "Andamento e batidas" },
  { id: "midi", label: "Instrumentos em MIDI", hint: "Dettato" },
  { id: "stems", label: "Separar os instrumentos", hint: "Demucs" },
  { id: "harmony", label: "Tom e acordes" },
  { id: "lyrics", label: "Letra", hint: "Genius" },
  { id: "sync", label: "Sincronizar a letra com a voz", hint: "Whisper" },
  { id: "tags", label: "Marcar as partes da letra", hint: "opcional" },
  { id: "intro", label: "Marcar as partes na música" },
];
const WHISPER_LANGS = ["pt", "en", "es", "fr", "it", "de", "ja"];

/** True when the open project still lacks the MIDI or the harmony (or is being processed now). */
async function pipelineNeeded() {
  const p = state.project;
  const job = PL.jobs[p.job_id];
  if (job) return !job.finished;
  if (p.mode !== "classic" && !hasMultitrack()) return true;
  if (!hasStems()) return true;
  try {
    const data = await api(projectUrl("/harmony"));
    return !data.harmony;
  } catch {
    return false;  // cannot tell: open the player, the harmony tab has its own button
  }
}

function showPipeline(on) {
  const section = document.querySelector('[data-view="project"]');
  section.classList.toggle("is-processing", on);
  show($("pipelineCard"), on);
  if (on) {
    const p = state.project;
    els.projNameInput.value = p.name;
    els.projBpmInput.value = p.detected_bpm;
    els.projMeta.textContent = `${fmtClock(p.duration, 0)} · ${p.original_filename}`;
  }
}

function startPipeline() {
  const p = state.project;
  showPipeline(true);
  if (!PL.jobs[p.job_id]) {
    PL.jobs[p.job_id] = {
      steps: { upload: "done", tempo: "done", midi: "todo", stems: "todo", harmony: "todo", lyrics: "todo", sync: "todo", tags: "todo", intro: "todo" },
      error: null, startedAt: Date.now(), stepStartedAt: Date.now(), finished: false,
    };
    runPipeline(p.job_id);
  }
  renderPipeline();
  clearInterval(PL.timer);
  PL.timer = setInterval(renderPipelineClock, 1000);
}

async function runPipeline(projectId) {
  const job = PL.jobs[projectId];
  job.error = null;
  setBusy(true);
  try {
    const p0 = await api(`/api/projects/${projectId}`);
    // MIDI: Dettato over the whole song (old Demucs projects skip it)
    if (p0.mode === "classic" || (p0.multitrack || []).length) {
      if (job.steps.midi !== "done") job.steps.midi = p0.mode === "classic" ? "skipped" : "done";
    } else if (job.steps.midi !== "done") {
      await pipelineStep(projectId, "midi", async () => {
        const data = await postJson(`/api/projects/${projectId}/multitrack`, { split_guitar_roles: els.splitRolesCheck.checked });
        if (state.project && state.project.job_id === projectId) state.project = { ...data.job, parts: state.project.parts };
      });
    }
    // stems: Demucs over the original upload
    if (job.steps.stems !== "done") {
      const cur = state.project && state.project.job_id === projectId ? state.project : p0;
      if (Object.keys(cur.stems || {}).length) job.steps.stems = "done";
      else {
        await pipelineStep(projectId, "stems", async () => {
          const data = await postJson(`/api/projects/${projectId}/separate`, { model: "htdemucs_6s" });
          if (state.project && state.project.job_id === projectId) state.project = { ...data.job, parts: state.project.parts };
        });
      }
    }
    // harmony: always redone after a fresh MIDI / stems, so it uses them
    if (job.steps.harmony !== "done") {
      await pipelineStep(projectId, "harmony", () => postJson(`/api/projects/${projectId}/harmony`));
    }
    // lyrics: search Genius by the song name; the user confirms (or skips) in the card
    if (!["done", "skipped"].includes(job.steps.lyrics)) {
      if (await pipelineLyricsSearch(projectId)) {
        setBusy(false);
        return;  // pipelineLyricsChoice() resumes the pipeline
      }
    }
    // sync: Whisper listens to the vocals stem and times every word of the chosen lyrics
    if (job.steps.lyrics !== "done") job.steps.sync = "skipped";
    else if (!["done", "skipped"].includes(job.steps.sync)) {
      await pipelineStep(projectId, "sync", async () => {
        const cur = await api(`/api/projects/${projectId}/lyrics`);
        if (cur.lyrics) return;  // already aligned
        if (!cur.draft || !cur.draft.text.trim()) throw new Error("a letra escolhida não foi encontrada no projeto");
        const lang = WHISPER_LANGS.includes(cur.draft.language) ? cur.draft.language : null;
        const data = await postJson(`/api/projects/${projectId}/lyrics`, { text: cur.draft.text, language: lang, model: "turbo" });
        job.syncNote = `${Math.round(data.lyrics.stats.matched_share * 100)}% das palavras reconhecidas na voz`;
        L.openLyrics = projectId;
      });
    }
    const synced = job.steps.sync === "done";
    // tags: the user names each stanza (verse, chorus...) or skips; the timing is kept
    if (!synced) job.steps.tags = "skipped";
    else if (!["done", "skipped"].includes(job.steps.tags)) {
      const cur = await api(`/api/projects/${projectId}/lyrics`);
      job.tagsLyrics = cur.lyrics;
      job.steps.tags = "waiting";
      L.openLyrics = projectId;
      setBusy(false);
      if (state.project && state.project.job_id === projectId) renderPipeline();
      return;  // pipelineTagsDone() resumes the pipeline
    }
    // parts on the timeline: every section when the user tagged the stanzas, else just the intro
    // (from the start of the song to the first stanza)
    if (!synced) job.steps.intro = "skipped";
    else if (!["done", "skipped"].includes(job.steps.intro)) {
      await pipelineStep(projectId, "intro", async () => {
        const all = job.steps.tags === "done";
        const data = await postJson(`/api/projects/${projectId}/parts/from-lyrics${all ? "" : "?only_intro=true"}`, {});
        if (state.project && state.project.job_id === projectId) state.project = data.job;
        const intro = data.job.parts.find((pt) => /^intro/i.test(pt.name) && pt.start === 0);
        if (all) job.introNote = `${data.job.parts.length} partes na linha do tempo${intro ? `, com a intro (${fmtClock(intro.start, 0)}–${fmtClock(intro.end, 0)})` : ""}`;
        else job.introNote = intro ? `${intro.name}: ${fmtClock(intro.start, 0)}–${fmtClock(intro.end)}`
          : "a voz começa logo no início: sem intro instrumental";
      });
    }
    job.finished = true;
  } catch (err) {
    job.error = err.message;
  } finally {
    setBusy(false);
  }
  if (state.view === "project" && state.project && state.project.job_id === projectId) {
    if (job.finished) {
      clearInterval(PL.timer);
      delete PL.jobs[projectId];
      route();  // now the project opens normally, with player and tabs
    } else renderPipeline();
  } else if (job.finished) delete PL.jobs[projectId];
}

async function pipelineStep(projectId, id, fn) {
  const job = PL.jobs[projectId];
  job.steps[id] = "running";
  job.stepStartedAt = Date.now();
  if (state.project && state.project.job_id === projectId) renderPipeline();
  try {
    await fn();
    job.steps[id] = "done";
  } catch (err) {
    job.steps[id] = "error";
    throw err;
  }
}

/** "Ação  de Graças!" -> "acao de gracas": exact title match, ignoring case, accents and punctuation. */
function normTitle(s) {
  return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

/** Runs the Genius search; true when there are matches waiting for the user's choice. */
async function pipelineLyricsSearch(projectId) {
  const job = PL.jobs[projectId];
  const finish = (status, note) => {
    job.steps.lyrics = status;
    job.lyricsNote = note;
  };
  job.steps.lyrics = "running";
  job.stepStartedAt = Date.now();
  if (state.project && state.project.job_id === projectId) renderPipeline();
  try {
    const current = await api(`/api/projects/${projectId}/lyrics`);
    if (current.lyrics) {
      finish("done", "o projeto já tem letra");
      job.steps.sync = "done";
      job.syncNote = "já sincronizada";
      job.steps.tags = "skipped";
      job.tagsNote = "a letra já estava pronta: edite as partes na aba \"Letra e cifra\"";
      return false;
    }
    if (current.draft && current.draft.text.trim()) {
      finish("done", current.draft.source || "letra já salva no projeto");
      return false;
    }
    const p = await api(`/api/projects/${projectId}`);
    const query = geniusQueryFromName(p.name);
    job.lyricsQuery = query;
    const { results } = await api(`/api/lyrics/search?q=${encodeURIComponent(query)}&limit=20`);
    const wanted = normTitle(query);
    job.lyricsResults = results.filter((r) => normTitle(r.title) === wanted);
    if (!job.lyricsResults.length) {
      finish("skipped", `nenhuma letra no Genius com o nome exato "${query}": pesquise na aba "Letra e cifra"`);
      return false;
    }
    job.steps.lyrics = "waiting";
    if (state.project && state.project.job_id === projectId) renderPipeline();
    return true;
  } catch (err) {
    // the lyrics never block the project: say why and move on
    finish("skipped", `não foi possível pesquisar (${err.message})`);
    return false;
  }
}

/** The user picked a result (or null = skip): fetch it and open the project. */
async function pipelineLyricsChoice(projectId, result) {
  const job = PL.jobs[projectId];
  if (!job || job.steps.lyrics !== "waiting") return;
  if (result) {
    job.steps.lyrics = "running";
    job.stepStartedAt = Date.now();
    renderPipeline();
    try {
      const data = await api(`/api/lyrics/genius/${result.id}`);
      const current = await api(`/api/projects/${projectId}/lyrics`);
      if (!current.lyrics) {
        await sendJson("PUT", `/api/projects/${projectId}/lyrics/draft`, {
          text: data.text, language: data.language || null, source: `Genius: ${data.title} · ${data.artist}`,
        });
      }
      job.steps.lyrics = "done";
      job.lyricsNote = `${data.title} · ${data.artist}`;
    } catch (err) {
      job.steps.lyrics = "skipped";
      job.lyricsNote = `não foi possível baixar a letra (${err.message})`;
    }
  } else {
    job.steps.lyrics = "skipped";
    job.lyricsNote = "pulada";
  }
  runPipeline(projectId);  // goes on with the sync (or finishes, when the lyrics were skipped)
}

/** Opens the tagging modal on the synced lyrics (with each stanza's time). */
function pipelineOpenTags(projectId) {
  const job = PL.jobs[projectId];
  const ly = job && job.tagsLyrics;
  if (!ly) return;
  openTagDialog({
    text: ly.text,
    times: ly.lines.map((l) => l.start),
    onSave: (text) => pipelineTagsDone(projectId, text),
  });
}

/** Tags applied (text) or skipped (null): save them without re-running Whisper, then go on. */
async function pipelineTagsDone(projectId, text) {
  const job = PL.jobs[projectId];
  if (!job || job.steps.tags !== "waiting") return;
  if (text === null) {
    job.steps.tags = "skipped";
    job.tagsNote = "pulada: as partes são inferidas da letra";
  } else {
    job.steps.tags = "running";
    job.stepStartedAt = Date.now();
    renderPipeline();
    try {
      await sendJson("PATCH", `/api/projects/${projectId}/lyrics/sections`, { text });
      const n = parseStanzas(text).filter((s) => s.tag).length;
      job.steps.tags = "done";
      job.tagsNote = `${n} estrofe(s) marcada(s)`;
    } catch (err) {
      job.steps.tags = "waiting";
      renderPipeline();
      banner($("pipelineStatus"), `Não foi possível salvar as partes: ${err.message}`, "error");
      return;
    }
  }
  hide($("pipelineStatus"));
  runPipeline(projectId);
}

function renderPipelineLyricsChoice(job, projectId) {
  const box = $("pipelineChoice");
  box.innerHTML = "";
  const waiting = job.steps.lyrics === "waiting";
  show(box, waiting);
  if (!waiting) return;
  const list = el("ul", { class: "genius-results" });
  for (const r of job.lyricsResults) {
    const meta = [r.artist, r.year].filter(Boolean).join(" · ");
    const pending = r.lyrics_state && r.lyrics_state !== "complete";
    list.append(el("li", {},
      el("button", { type: "button", class: "genius-item", onclick: () => pipelineLyricsChoice(projectId, r) },
        r.thumbnail ? el("img", { src: r.thumbnail, alt: "", loading: "lazy", width: "44", height: "44" }) : el("span", { class: "genius-noart" }, "♪"),
        el("span", { class: "genius-text" },
          el("strong", {}, r.title),
          el("span", { class: "muted small" }, meta + (pending ? " · letra incompleta" : ""))),
        el("span", { class: "genius-use small" }, "Usar esta"))));
  }
  box.append(
    el("strong", { class: "small" }, "Qual destas é a sua música?"),
    list,
    el("div", { class: "row-inline" },
      el("button", { type: "button", class: "btn-outline btn-small", onclick: () => pipelineLyricsChoice(projectId, null) }, "Nenhuma: pular a letra")));
}

function stepDetail(id, status) {
  const p = state.project;
  if (id === "upload") return `${fmtClock(p.duration, 0)} · ${p.original_filename}`;
  if (id === "tempo") return p.detected_bpm ? `${Math.round(p.detected_bpm * 10) / 10} BPM` : "";
  if (id === "midi") {
    if (status === "done" && hasMultitrack()) return `${p.multitrack.length} faixas`;
    if (status === "skipped") return "projeto com stems (Demucs)";
    if (status === "running") return onDevice("na GPU, costuma levar 1–3 min", "na CPU, pode levar vários minutos");
    return "uma faixa por instrumento";
  }
  if (id === "stems") {
    const n = Object.keys(p.stems || {}).length;
    if (status === "done" && n) return `${n} stems: ${Object.keys(p.stems).join(", ")}`;
    if (status === "running") return onDevice("na GPU, costuma levar 1–2 min", "na CPU, pode levar vários minutos");
    return "voz, baixo, guitarra, bateria... em faixas de áudio";
  }
  if (id === "tags") {
    if (status === "waiting") return "diga qual parte da música é cada estrofe, ou pule";
    if (status === "done") return PL.jobs[p.job_id].tagsNote || "partes marcadas";
    if (status === "skipped") return PL.jobs[p.job_id].tagsNote || "pulada";
    return "verso, refrão, ponte... em cada estrofe";
  }
  if (id === "intro") {
    if (status === "done") return PL.jobs[p.job_id].introNote || "partes marcadas";
    if (status === "skipped") return "sem letra sincronizada para achar as partes";
    if (status === "running") return "criando as partes na linha do tempo...";
    return PL.jobs[p.job_id].steps.tags === "skipped" ? "a intro: do começo até a primeira estrofe"
      : "intro, versos, refrões, solo... na linha do tempo";
  }
  if (id === "sync") {
    const job = PL.jobs[p.job_id];
    if (status === "running") return onDevice("ouvindo a voz, costuma levar 1–2 min",
      "ouvindo a voz na CPU, pode levar mais de 10 min");
    if (status === "done") return job.syncNote || "letra sincronizada";
    if (status === "skipped") return "sem letra para sincronizar";
    return "cada palavra no tempo em que é cantada, com os acordes em cima";
  }
  if (id === "lyrics") {
    const job = PL.jobs[p.job_id];
    if (status === "running") return "pesquisando no Genius...";
    if (status === "waiting") return `${job.lyricsResults.length === 1 ? "1 letra encontrada" : `${job.lyricsResults.length} letras encontradas`} para "${job.lyricsQuery}": confirme abaixo`;
    if (status === "done") return job.lyricsNote || "letra escolhida";
    if (status === "skipped") return job.lyricsNote || "pulada";
    return "busca pelo nome exato da música";
  }
  if (id === "harmony") return status === "running" ? "costuma levar de 10 a 40 s" : "tom, campo harmônico e acordes";
  return "";
}

function renderPipeline() {
  const p = state.project;
  const job = p && PL.jobs[p.job_id];
  if (!job) return;
  const list = $("pipelineSteps");
  list.innerHTML = "";
  PL_STEPS.forEach((s, i) => {
    const status = job.steps[s.id];
    const mark = status === "done" ? "✓" : status === "skipped" ? "–" : status === "error" ? "!" : status === "waiting" ? "?" : String(i + 1);
    const li = el("li", { class: `pl-step is-${status}` },
      el("span", { class: "pl-mark", "aria-hidden": "true" }, mark),
      el("div", { class: "pl-body" },
        el("strong", {}, s.label, s.hint ? el("span", { class: "pl-hint" }, ` · ${s.hint}`) : null),
        el("span", { class: "muted small" }, stepDetail(s.id, status)),
        status === "running" ? el("span", { class: "pl-clock small", "data-clock": "1" }) : null,
        status === "running" ? el("div", { class: "pl-downloads hidden", "data-downloads": "1" }) : null));
    if (status === "running") li.setAttribute("aria-current", "step");
    list.append(li);
  });
  const finished = PL_STEPS.filter((s) => ["done", "skipped"].includes(job.steps[s.id])).length;
  $("pipelineBarFill").style.width = `${Math.round((finished / PL_STEPS.length) * 100)}%`;

  const note = $("pipelineNote");
  note.innerHTML = "";
  renderPipelineLyricsChoice(job, p.job_id);
  if (job.steps.tags === "waiting") {
    const box = $("pipelineChoice");
    box.innerHTML = "";
    show(box);
    box.append(
      el("strong", { class: "small" }, "A letra já está sincronizada. Quer marcar as partes de cada estrofe?"),
      el("p", { class: "muted small" }, "As partes viram a estrutura da música e os títulos da cifra. Os trechos sem letra (solo, breakdown...) são inferidos pela própria música."),
      el("div", { class: "row-inline" },
        el("button", { type: "button", class: "btn-primary btn-small", onclick: () => pipelineOpenTags(p.job_id) }, "🏷 Marcar partes"),
        el("button", { type: "button", class: "btn-outline btn-small", onclick: () => pipelineTagsDone(p.job_id, null) }, "Pular")));
    note.textContent = "Falta pouco: depois disso as partes vão para a linha do tempo e o player abre.";
  } else if (job.steps.lyrics === "waiting") {
    note.textContent = "A música já está analisada. Escolha a letra certa: em seguida ela é sincronizada com a voz. Ou pule esta etapa.";
  } else if (job.error) {
    note.append(
      el("span", { class: "pl-error" }, `Erro: ${job.error}`), " ",
      el("button", { type: "button", class: "btn-primary btn-small", onclick: () => { runPipeline(p.job_id); renderPipeline(); } }, "Tentar de novo"), " ",
      el("button", { type: "button", class: "btn-outline btn-small", onclick: () => { delete PL.jobs[p.job_id]; clearInterval(PL.timer); showPipeline(false); renderProjectView(); } }, "Abrir sem esta etapa"));
  } else {
    note.textContent = "Quando terminar, o player abre com os acordes da música. Você pode sair desta tela: o processamento continua.";
  }
  renderModelDownloads();
  renderPipelineClock();
}

function renderPipelineClock() {
  const p = state.project;
  const job = p && PL.jobs[p.job_id];
  if (!job || state.view !== "project") return;
  const fmt = (ms) => fmtClock(Math.floor(ms / 1000), 0);
  $("pipelineElapsed").textContent = job.error ? "" : `${fmt(Date.now() - job.startedAt)} no total`;
  const clock = document.querySelector("#pipelineSteps [data-clock]");
  if (clock) clock.textContent = fmt(Date.now() - job.stepStartedAt);
  if (Object.values(job.steps).includes("running")) pollModelDownloads();
  else if (PL.downloads.length) { PL.downloads = []; renderModelDownloads(); }
}

// ---------- model downloads (first run of Whisper / Demucs / Dettato: hundreds of MB) ----------

PL.downloads = [];
PL.dlPolling = false;
const DL_MIN_BYTES = 1e6;  // config files and other tiny downloads are not worth a bar

async function pollModelDownloads() {
  if (PL.dlPolling) return;
  PL.dlPolling = true;
  try {
    const { downloads } = await api("/api/downloads");
    PL.downloads = (downloads || []).filter((d) => !d.total || d.total >= DL_MIN_BYTES);
  } catch {
    PL.downloads = [];
  } finally {
    PL.dlPolling = false;
  }
  renderModelDownloads();
}

function fmtBytes(n) {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e8 ? 0 : 1)} MB`;
  return `${Math.round(n / 1e3)} KB`;
}

/** A bar per model being downloaded, under the running step of the processing card. */
function renderModelDownloads() {
  const box = document.querySelector("#pipelineSteps [data-downloads]");
  if (!box) return;
  box.innerHTML = "";
  show(box, PL.downloads.length > 0);
  for (const d of PL.downloads) {
    const pct = d.total ? Math.min(100, (d.downloaded / d.total) * 100) : null;
    const eta = d.total && d.speed > 0 && !d.done ? (d.total - d.downloaded) / d.speed : null;
    const title = d.error ? `Falha ao baixar o modelo ${d.label}`
      : d.done ? `Modelo ${d.label} baixado`
      : `Baixando o modelo ${d.label} (só na primeira vez)`;
    const info = d.error ? "tentando de outro jeito, sem barra de progresso..." : [
      d.total ? `${fmtBytes(d.downloaded)} de ${fmtBytes(d.total)}` : fmtBytes(d.downloaded),
      pct !== null ? `${Math.floor(pct)}%` : null,
      !d.done && d.speed > 0 ? `${fmtBytes(d.speed)}/s` : null,
      eta !== null ? `falta ~${fmtClock(Math.ceil(eta), 0)}` : null,
    ].filter(Boolean).join(" · ");
    box.append(el("div", { class: `pl-dl${d.done ? " is-done" : ""}${d.error ? " is-error" : ""}` },
      el("div", { class: "pl-dl-head small" },
        el("strong", {}, `⬇ ${title}`),
        el("span", { class: "muted" }, info)),
      el("div", {
        class: `pl-dl-bar${pct === null && !d.done ? " is-indeterminate" : ""}`,
        role: "progressbar", "aria-label": title, "aria-valuemin": "0", "aria-valuemax": "100",
        "aria-valuenow": pct !== null ? String(Math.floor(pct)) : null,
      }, el("span", { style: `width: ${d.done ? 100 : pct ?? 30}%` }))));
  }
}

function showView(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach((v) => show(v, v.dataset.view === view));
  renderBreadcrumbs();
  window.scrollTo({ top: 0 });
}

function renderBreadcrumbs() {
  els.breadcrumbs.innerHTML = "";
  const crumbs = [["Projetos", "#/"]];
  if (state.project && state.view !== "projects") crumbs.push([state.project.name, `#/p/${state.project.job_id}`]);
  const part = state.view === "part" && currentPart();
  if (part) crumbs.push([part.name, null]);
  crumbs.forEach(([label, href], idx) => {
    if (idx > 0) els.breadcrumbs.append(el("span", { class: "crumb-sep" }, "›"));
    const last = idx === crumbs.length - 1;
    els.breadcrumbs.append(last
      ? el("span", { class: "crumb current", "aria-current": "page" }, label)
      : el("a", { class: "crumb available", href }, label));
  });
}

function stopAllAudio() {
  destroyWave(projWave);
  destroyWave(partWave);
  projWave = partWave = null;
  if (typeof mpReset === "function") mpReset();
  if (scaleAudio) {
    try {
      scaleAudio.close();
    } catch {
      /* ignore */
    }
    scaleAudio = null;
  }
  if (alphaTabApi) {
    alphaTabApi.destroy();
    alphaTabApi = null;
    els.alphaTabContainer.innerHTML = "";
  }
}

// ---------- system / bootstrap ----------

async function loadSystemInfo() {
  try {
    const info = await api("/api/system");
    state.device = info.device;
    const mem = info.gpu_memory_mb ? ` ${Math.round(info.gpu_memory_mb / 1024)} GB` : "";
    if (info.device === "cuda") {
      els.deviceBadge.textContent = `⚡ GPU: ${info.gpu_name}${mem}`;
      els.deviceBadge.className = "device-badge gpu";
      els.deviceBadge.title = `Dettato e Demucs rodam na GPU (CUDA ${info.torch_cuda}, PyTorch ${info.torch_version}).`;
    } else {
      els.deviceBadge.textContent = info.gpu_name ? `⚠ CPU: ${info.gpu_name} sem CUDA` : "⚠ CPU (sem GPU NVIDIA)";
      els.deviceBadge.className = "device-badge cpu";
      els.deviceBadge.title = info.hint || "Dettato e Demucs vão rodar na CPU (lento).";
    }
  } catch {
    els.deviceBadge.textContent = "Dispositivo desconhecido";
  }
}

async function loadTunings() {
  state.tunings = await api("/api/tunings");
  els.tuningSelect.innerHTML = "";
  for (const t of state.tunings) els.tuningSelect.append(el("option", { value: t.id }, t.name));
}

// ---------- view: projects ----------

async function loadProjectList() {
  let projects = [];
  try {
    projects = await api("/api/projects");
  } catch (err) {
    banner(els.uploadStatus, `Erro ao listar os projetos: ${err.message}`, "error");
  }
  els.projectList.innerHTML = "";
  show(els.projectEmpty, projects.length === 0);
  for (const p of projects) {
    const badges = [];
    if (p.has_multitrack) badges.push(el("span", { class: "badge" }, "MIDI completo"));
    if (p.has_stems) badges.push(el("span", { class: "badge" }, "stems"));
    const chips = p.parts.length
      ? el("div", { class: "part-chips" }, ...p.parts.map((n) => el("span", { class: "part-chip" }, n)))
      : el("span", { class: "muted" }, "sem partes ainda");
    const del = el("button", {
      type: "button",
      class: "btn-outline btn-small danger",
      title: "Excluir projeto",
      onclick: (e) => {
        e.preventDefault();
        deleteProject(p);
      },
    }, "Excluir");
    const card = el("a", { class: "project-card", href: `#/p/${p.job_id}` },
      el("div", { class: "project-card-head" },
        el("strong", {}, p.name),
        el("span", { class: "muted" }, p.duration ? fmtClock(p.duration, 0) : ""),
        ...badges),
      el("span", { class: "muted small" }, p.original_filename),
      el("div", { class: "project-card-foot" }, chips, del));
    els.projectList.append(card);
  }
}

async function deleteProject(p) {
  if (!confirm(`Excluir o projeto "${p.name}" e todas as suas partes? Isso não pode ser desfeito.`)) return;
  try {
    await sendJson("DELETE", `/api/projects/${p.job_id}`);
    await loadProjectList();
  } catch (err) {
    banner(els.uploadStatus, `Erro ao excluir: ${err.message}`, "error");
  }
}

els.audioFileInput.addEventListener("change", () => {
  const f = els.audioFileInput.files[0];
  els.fileNameDisplay.textContent = f ? f.name : "Arraste um MP3/WAV/FLAC ou clique para selecionar";
  // fill the name from the file, unless the user already typed one of their own
  const input = els.projectNameInput;
  if (f && (!input.value || input.dataset.auto === "1")) {
    input.value = songNameFromFile(f.name);
    input.dataset.auto = "1";
  }
});
els.projectNameInput.addEventListener("input", () => { delete els.projectNameInput.dataset.auto; });

els.newProjectForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = els.audioFileInput.files[0];
  if (!file || state.busy) return;
  setBusy(true);
  els.createProjectBtn.disabled = true;
  banner(els.uploadStatus, "Enviando o áudio e detectando o andamento...", "busy");
  try {
    const form = new FormData();
    form.append("file", file);
    form.append("name", els.projectNameInput.value);
    form.append("mode", "dettato");
    const project = await api("/api/projects", { method: "POST", body: form });
    els.newProjectForm.reset();
    delete els.projectNameInput.dataset.auto;
    els.fileNameDisplay.textContent = "Arraste um MP3/WAV/FLAC ou clique para selecionar";
    hide(els.uploadStatus);
    state.project = project;
    state.peaks = null;
    navigate(`#/p/${project.job_id}`);
    // the project view runs the processing pipeline (MIDI → harmony) before showing the player
  } catch (err) {
    banner(els.uploadStatus, `Erro: ${err.message}`, "error");
  } finally {
    els.createProjectBtn.disabled = false;
    setBusy(false);
  }
});

// ---------- waveform (shared by the project and part views) ----------

async function loadPeaks() {
  if (!state.peaks || state.peaks.projectId !== state.project.job_id) {
    const data = await api(projectUrl("/peaks"));
    state.peaks = { projectId: state.project.job_id, ...data };
  }
  return state.peaks;
}

function audioSources() {
  const out = [["", "Mix original"]];
  for (const name of Object.keys(state.project.stems || {})) out.push([name, `Stem: ${STEM_LABELS[name] || name}`]);
  return out;
}
function sourceUrl(stem) {
  if (stem && stem.includes("+")) return projectUrl(`/mix?stems=${encodeURIComponent(stem.split("+").join(","))}`);
  return fileUrl(stem ? state.project.stems[stem] : state.project.input_path);
}
function fillSourceSelect(select, value) {
  select.innerHTML = "";
  for (const [v, label] of audioSources()) select.append(el("option", { value: v }, label));
  select.value = audioSources().some(([v]) => v === value) ? value : "";
  select.disabled = audioSources().length < 2;
}

async function createWave(container, stem, extra = {}) {
  const peaks = await loadPeaks();
  container.innerHTML = "";
  const ws = WaveSurfer.create({
    container,
    height: 120,
    waveColor: "#3b4a63",
    progressColor: "#5b8def",
    cursorColor: "#f59e0b",
    cursorWidth: 2,
    barWidth: 2,
    barGap: 1,
    barRadius: 2,
    autoScroll: true,
    url: sourceUrl(stem),
    peaks: [peaks.peaks],
    duration: peaks.duration,
    ...extra,
  });
  const regions = ws.registerPlugin(WaveSurfer.Regions.create());
  const w = { ws, regions, loop: null, draft: null, stem };
  ws.on("timeupdate", (t) => {
    // wrap only when crossing the end from inside the loop: a click after the end plays on from there
    const prev = w.lastT;
    w.lastT = t;
    if (w.loop && t >= w.loop.end && prev !== undefined && prev < w.loop.end && prev >= w.loop.start - 0.3) {
      ws.setTime(w.loop.start);
    }
    if (w.onTime) w.onTime(t);
  });
  // Precise click-to-seek, done here instead of trusting WaveSurfer's own: with zoom + drag
  // selection its click could be swallowed. A click = pointer moved < 4 px (a drag draws a part).
  // Region clicks stop propagation inside the shadow DOM and seek themselves.
  let down = null;
  const { signal } = (w.listeners = new AbortController());  // removed with the wave (destroyWave)
  container.addEventListener("pointerdown", (e) => {
    down = { x: e.clientX, y: e.clientY };
  }, { capture: true, signal });
  container.addEventListener("click", (e) => {
    if (!down || Math.abs(e.clientX - down.x) > 4 || Math.abs(e.clientY - down.y) > 4) return;
    const rect = ws.getWrapper().getBoundingClientRect();
    if (e.clientY < rect.top || e.clientY > rect.bottom) return;  // the horizontal scrollbar
    const t = clickTime(w, e);
    ws.setTime(t);
    if (w.onInteraction) w.onInteraction(t);
  }, { signal });
  // a click outside the looped excerpt ends the loop
  ws.on("interaction", (t) => {
    if (w.onInteraction) w.onInteraction(t);  // project view: explicit selection + loop toggle
    else if (w.loop && (t < w.loop.start || t > w.loop.end)) w.loop = null;
  });
  return w;
}

/** Song time under a mouse event on the waveform.
 *
 * Measured on the scroll container: with zoom the drawing overflows it, and the wrapper's own box
 * can stay as wide as the visible area, so its width is not the song's width. */
function clickTime(w, e) {
  const wrap = w.ws.getWrapper();
  const scroller = (wrap && wrap.parentElement) || wrap;
  const dur = w.ws.getDuration() || (state.peaks && state.peaks.duration) || 0;
  const total = Math.max(scroller.scrollWidth, wrap.scrollWidth);
  if (!total || !dur) return w.ws.getCurrentTime();
  const x = e.clientX - scroller.getBoundingClientRect().left + scroller.scrollLeft;
  return Math.min(dur, Math.max(0, (x / total) * dur));
}

/** Run fn once the waveform knows the duration (regions created before it collapse to 0). */
function whenReady(w, fn) {
  if (w.ws.getDecodedData() && w.ws.getDuration()) fn();
  else w.ws.once("ready", () => fn());
}

function destroyWave(w) {
  if (!w) return;
  if (w.listeners) w.listeners.abort();
  try {
    w.ws.destroy();
  } catch {
    /* already gone */
  }
}

/** Swap the audio (mix / stem) keeping the waveform peaks, position and loop. */
function switchSource(w, stem) {
  const t = w.ws.getCurrentTime();
  const playing = w.ws.isPlaying();
  w.stem = stem;
  w.ws.load(sourceUrl(stem), [state.peaks.peaks], state.peaks.duration).then(() => {
    w.ws.setTime(t);
    if (playing) w.ws.play();
  });
}

function playLoop(w, start, end) {
  w.loop = { start, end };
  w.ws.setTime(start);
  w.ws.play();
}

/** "compasso.tempo" (4/4 assumed) from the detected beats; "–" before the first beat. */
function barBeat(t) {
  const beats = state.project.beats || [];
  let lo = 0;
  let hi = beats.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (beats[mid] <= t + 0.02) lo = mid + 1;
    else hi = mid;
  }
  if (!lo) return "–";
  return `${Math.floor((lo - 1) / 4) + 1}.${((lo - 1) % 4) + 1}`;
}

/** Nearest beat when "snap" is on (the tab then starts on a beat, not in the middle of one). */
function snapTime(t) {
  const beats = state.project.beats || [];
  if (!els.snapCheck.checked || !beats.length) return t;
  let best = beats[0];
  for (const b of beats) {
    if (Math.abs(b - t) < Math.abs(best - t)) best = b;
    if (b > t + 2) break;
  }
  return Math.abs(best - t) < 1.5 ? best : t;
}

// ---------- view: project ----------

async function renderProjectView() {
  const p = state.project;
  PE.active = false;
  PE.disableDrag = null;
  hide(peEls.panel);
  hide(els.projectStatus);
  restoreTab();
  els.projNameInput.value = p.name;
  els.projBpmInput.value = p.detected_bpm;
  els.projMeta.textContent = `${fmtClock(p.duration, 0)} · ${p.original_filename}`;
  renderAnalysis();
  renderPartList();
  SRC.open = false;
  hide(srcEls.menu);
  setProjSource("");
  hide(els.draftPanel);
  setProjRate(100);
  sel.cur = null;
  els.loopCheck.checked = false;
  renderSelectionBar();

  projWave = await createWave(els.projWave, "", { hideScrollbar: true });
  const w = projWave;
  w.onTime = (t) => {
    els.projTime.textContent = fmtClock(t);
    $("projBarBeat").textContent = barBeat(t);
    updateNowChord(t, false, CU.project);
    syncChordLane(CU.project);
    syncPartLane();
    navPlayhead(t);
    updateSheet(t);
    lyricOverlayNow(t);
  };
  $("projTimeTotal").textContent = fmtClock(p.duration);
  $("projBpmLcd").textContent = p.detected_bpm ? String(Math.round(p.detected_bpm * 10) / 10) : "–";
  w.onTime(0);
  const playing = (on) => {
    els.projPlayBtn.classList.toggle("is-playing", on);
    els.projPlayBtn.setAttribute("aria-label", on ? "Pause" : "Play");
    $("sheetPlayBtn").innerHTML = on ? "&#10074;&#10074; Pause" : "&#9654; Play";
  };
  playing(false);
  w.ws.on("play", () => playing(true));
  w.ws.on("pause", () => playing(false));
  for (const ev of ["scroll", "zoom", "redraw", "redrawcomplete"]) w.ws.on(ev, () => {
    syncChordLane(CU.project);
    syncPartLane();
    navUpdate();
  });
  w.ws.on("seeking", (t) => {
    updateNowChord(t, false, CU.project);
    syncChordLane(CU.project);
    syncPartLane();
    navPlayhead(t);
    updateSheet(t);
    lyricOverlayNow(t);
  });
  // a click on the waveform outside the selected section deselects it (a new part being drawn stays)
  w.onInteraction = (t) => {
    const s = sel.cur;
    if (s && s.kind !== "draft" && (t < s.start || t > s.end)) setSelection(null);
  };
  whenReady(w, () => {
    if (projWave !== w) return;
    drawPartRegions();
    PE.disableDrag = w.regions.enableDragSelection({ color: DRAFT_COLOR });
    syncChordLane(CU.project);
    navAttach(w);
  });
  renderLyricOverlay();
  loadHarmony();
  loadLyrics();
  attachSelectionHint(w);
  // resizing / moving the part being drawn: live start–end next to the pointer
  w.regions.on("region-update", (region, side) => {
    if (PE.active && region.id.startsWith("part-")) {
      peRegionMoved(region, false);
      const ev = selHint.lastEvent;
      if (ev) showSelHint(ev.clientX, ev.clientY, region.start, region.end, side);
      return;
    }
    if (region !== w.draft) return;
    const ev = selHint.lastEvent;
    if (ev) showSelHint(ev.clientX, ev.clientY, snapTime(region.start), snapTime(region.end), side);
  });
  w.regions.on("region-created", (region) => {
    if (region.id.startsWith("part-") || region.id === SEL_RANGE_ID) return;
    if (w.draft && w.draft !== region) w.draft.remove();
    w.draft = region;
    snapDraft();
    showDraft();
    setSelection(draftSelection());
  });
  w.regions.on("region-updated", (region) => {
    if (PE.active && region.id.startsWith("part-")) {
      peRegionMoved(region, true);  // the edge that moved snaps to the beat (when "encaixar" is on)
      return;
    }
    if (region === w.draft) {
      snapDraft();
      showDraft();
      if (sel.cur && sel.cur.kind === "draft") setSelection(draftSelection());
    }
  });
  // one click selects the part AND puts the cursor exactly where you clicked (like a DAW);
  // looping is the "Repetir trecho" toggle's job
  w.regions.on("region-clicked", (region, e) => {
    e.stopPropagation();
    if (region === w.draft) setSelection(draftSelection());
    else if (region.id.startsWith("part-")) {
      const part = state.project.parts.find((x) => `part-${x.part_id}` === region.id);
      if (part && !(sel.cur && sel.cur.partId === part.part_id)) setSelection(partSelection(part));
    }
    w.ws.setTime(clickTime(w, e));
  });
  w.regions.on("region-double-clicked", (region) => {
    if (region.id.startsWith("part-")) navigate(`#/p/${p.job_id}/part/${region.id.slice(5)}`);
  });
}

// ---------- live start/end hint while dragging a selection on the waveform ----------

const selHint = { el: null, lastEvent: null };

function selHintEl() {
  if (!selHint.el) {
    // on <body>: position:fixed must not be trapped by a filtered / transformed ancestor
    selHint.el = el("div", { class: "sel-hint hidden", "aria-hidden": "true" });
    document.body.append(selHint.el);
  }
  return selHint.el;
}

/** Shows "start → end · length" above the waveform, following the pointer. side = the edge being moved. */
function showSelHint(x, y, start, end, side) {
  const hint = selHintEl();
  const a = Math.min(start, end);
  const b = Math.max(start, end);
  hint.innerHTML = "";
  hint.append(
    el("span", { class: side === "start" ? "is-active" : "" }, fmtClock(a, 2)),
    el("span", { class: "sel-hint-arrow" }, "→"),
    el("span", { class: side === "end" ? "is-active" : "" }, fmtClock(b, 2)),
    el("span", { class: "sel-hint-len" }, `${(b - a).toFixed(2)} s`),
  );
  hint.classList.remove("hidden");
  const waveRect = els.projWave.getBoundingClientRect();
  const w = hint.offsetWidth;
  const left = Math.min(window.innerWidth - w - 8, Math.max(8, x - w / 2));
  const top = Math.max(8, waveRect.top - hint.offsetHeight - 6);
  hint.style.left = `${left}px`;
  hint.style.top = `${top}px`;
}

function hideSelHint() {
  if (selHint.el) selHint.el.classList.add("hidden");
}

/** A drag on the bare waveform draws a new part: the regions plugin emits nothing until the drop,
 * so the times come from the pointer itself (same 3 px threshold as the plugin). */
function attachSelectionHint(w) {
  const { signal } = w.listeners;
  const container = els.projWave;
  let drag = null;
  container.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    selHint.lastEvent = e;
    // dragging the draft itself (move/resize) is reported by "region-update"
    const onDraft = w.draft && w.draft.element && e.composedPath().includes(w.draft.element);
    drag = onDraft ? null : { x: e.clientX, t0: clickTime(w, e), moved: false };
  }, { capture: true, signal });
  window.addEventListener("pointermove", (e) => {
    selHint.lastEvent = e;
    if (!drag) return;
    if (!drag.moved && Math.abs(e.clientX - drag.x) < 3) return;
    drag.moved = true;
    const t = clickTime(w, e);
    showSelHint(e.clientX, e.clientY, snapTime(Math.min(drag.t0, t)), snapTime(Math.max(drag.t0, t)), t >= drag.t0 ? "end" : "start");
  }, { signal });
  const end = () => {
    drag = null;
    hideSelHint();
  };
  window.addEventListener("pointerup", end, { signal });
  window.addEventListener("pointercancel", end, { signal });
}

function drawPartRegions() {
  const w = projWave;
  if (!w) return;
  w.regions.getRegions().filter((r) => r.id.startsWith("part-")).forEach((r) => r.remove());
  for (const part of state.project.parts) {
    const r = peRange(part);
    w.regions.addRegion({
      id: `part-${part.part_id}`,
      start: r.start,
      end: r.end,
      color: withAlpha(partColor(part), 0.22),
      content: part.name,
      drag: false,
      resize: PE.active,
    });
  }
  renderPartLane();
}

/** Clickable buttons for the parts, between the waveform and the chord lane, aligned with the song time. */
function renderPartLane() {
  const lane = $("partLane");
  const inner = $("partLaneInner");
  const parts = (state.project && state.project.parts) || [];
  inner.innerHTML = "";
  show(lane, parts.length > 0);
  const dur = (state.peaks && state.peaks.duration) || state.project.duration || 1;
  for (const part of parts) {
    const r = peRange(part);
    inner.append(el("button", {
      type: "button",
      class: "lane-part" + (PE.active && PE.focus === part.part_id ? " is-editing" : ""),
      style: `left:${(r.start / dur) * 100}%;width:${((r.end - r.start) / dur) * 100}%;--part-color:${partColor(part)}`,
      title: `${part.name} · ${fmtRange(part.start, part.end)}\nClique: seleciona e vai para o início · Clique de novo: limpa a seleção · Dois cliques: abre a parte`,
      dataset: { partId: part.part_id },
      // click selects the part (and goes to its start); a click on the selected part clears it.
      // While the boundaries are being edited a click only moves the focus.
      onclick: () => {
        const selected = !!sel.cur && sel.cur.partId === part.part_id;
        if (selected && !PE.active) {
          setSelection(null);
          return;
        }
        if (!selected) setSelection(partSelection(part));
        if (projWave) projWave.ws.setTime(peRange(part).start);
      },
      ondblclick: () => navigate(`#/p/${state.project.job_id}/part/${part.part_id}`),
    }, PE.active ? peName(part.part_id) : part.name));
  }
  styleSelection();
  syncPartLane();
  navRenderParts();
}

/** Follow the waveform's zoom and horizontal scroll (same as the chord lane). */
function syncPartLane() {
  const w = projWave;
  const wrap = w && w.ws.getWrapper();
  if (!wrap) return;
  const inner = $("partLaneInner");
  const scroller = wrap.parentElement;
  inner.style.width = `${wrap.scrollWidth}px`;
  inner.style.transform = `translateX(${-(scroller ? scroller.scrollLeft : 0)}px)`;
  syncLyricOverlay();
}

// ---------- synced lyrics drawn over the waveform ----------
// Each sung line sits at its time (it may run on until the next line starts) and follows the
// zoom and the horizontal scroll, like the part and chord lanes. The line being sung is lit.

const LO = { on: true, lines: [], nodes: [], cur: -1, laidW: 0 };
const loEls = { box: $("lyricOverlay"), inner: $("lyricOverlayInner"), btn: $("navLyricsBtn") };
const LO_MIN_PX = 26;  // narrower than this (zoomed out) the line is hidden instead of a lone "…"
try {
  LO.on = localStorage.getItem("escritab.waveLyrics") !== "off";
} catch {
  /* private mode */
}

function renderLyricOverlay() {
  const ly = lyricsIsCurrent() && L.data && L.data.lyrics;
  LO.lines = ly ? ly.lines : [];
  LO.nodes = [];
  LO.cur = -1;
  LO.laidW = 0;
  loEls.inner.innerHTML = "";
  show(loEls.btn, LO.lines.length > 0);
  loEls.btn.classList.toggle("is-on", LO.on);
  loEls.btn.setAttribute("aria-pressed", String(LO.on));
  show(loEls.box, LO.on && LO.lines.length > 0);
  if (!LO.lines.length || !state.project) return;
  const dur = (state.peaks && state.peaks.duration) || state.project.duration || 1;
  LO.lines.forEach((line, i) => {
    const next = LO.lines[i + 1];
    const until = Math.max(line.end, Math.min(next ? next.start : dur, line.end + 6));
    const text = line.words.map((w) => w.text).join(" ");
    const node = el("span", {
      class: "lo-line",
      style: `left:${(line.start / dur) * 100}%;width:${((until - line.start) / dur) * 100}%`,
    }, text);
    loEls.inner.append(node);
    LO.nodes.push({ node, frac: (until - line.start) / dur });
  });
  syncLyricOverlay();
  if (projWave) lyricOverlayNow(projWave.ws.getCurrentTime());
}

/** Follow the waveform's zoom / scroll; hide lines too narrow to read at this zoom. */
function syncLyricOverlay() {
  const w = projWave;
  const wrap = w && w.ws.getWrapper();
  if (!wrap || !LO.nodes.length) return;
  const width = wrap.scrollWidth;
  loEls.inner.style.width = `${width}px`;
  loEls.inner.style.transform = `translateX(${-(wrap.parentElement ? wrap.parentElement.scrollLeft : 0)}px)`;
  if (width !== LO.laidW) {
    LO.laidW = width;
    for (const n of LO.nodes) n.node.classList.toggle("tiny", n.frac * width < LO_MIN_PX);
  }
}

/** Light the line being sung at time t. */
function lyricOverlayNow(t) {
  if (!LO.nodes.length) return;
  let lo = 0;
  let hi = LO.lines.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (LO.lines[mid].start <= t + 0.05) lo = mid + 1;
    else hi = mid;
  }
  const idx = lo - 1 >= 0 && t <= LO.lines[lo - 1].end + 0.6 ? lo - 1 : -1;
  if (idx === LO.cur) return;
  if (LO.cur >= 0 && LO.nodes[LO.cur]) LO.nodes[LO.cur].node.classList.remove("now");
  if (idx >= 0) LO.nodes[idx].node.classList.add("now");
  LO.cur = idx;
}

loEls.btn.addEventListener("click", () => {
  LO.on = !LO.on;
  try {
    localStorage.setItem("escritab.waveLyrics", LO.on ? "on" : "off");
  } catch {
    /* private mode */
  }
  renderLyricOverlay();
});

// ---------- DAW-style navigation: time ruler + overview scrollbar with zoom handles ----------
//
// Like Reaper's arrange view: the overview under the chords shows the whole song and a window
// over the visible part. Drag the window to scroll, drag its edges to zoom, double-click for the
// whole song. Ctrl/⌘ + wheel (or pinch) over the waveform zooms around the mouse; + / − / 0 / Z keys.

const NAV_MAX_PX_PER_SEC = 1500;
const NAV_MIN_SPAN = 0.4;  // seconds visible at maximum zoom
const NAV_STEP = 1.5;      // zoom factor of one button press / key press
const nav = { w: null, raf: 0, drag: null, follow: true, rulerW: 0 };
const navEls = {
  track: $("navTrack"),
  canvas: $("navCanvas"),
  canvasPlayed: $("navCanvasPlayed"),
  played: $("navPlayed"),
  head: $("navPlayhead"),
  win: $("navWindow"),
  parts: $("navParts"),
  range: $("navRange"),
  zoom: $("navZoomLabel"),
  ruler: $("projRuler"),
  sel: $("navSelBtn"),
  follow: $("navFollowBtn"),
};

/** Scroll geometry of the waveform: total drawn width, visible width, px per second. */
function navMetrics() {
  const w = nav.w;
  if (!w || w !== projWave || !w.ws.getDecodedData()) return null;
  const wrap = w.ws.getWrapper();
  const sc = wrap && wrap.parentElement;
  const dur = w.ws.getDuration() || (state.peaks && state.peaks.duration) || 0;
  if (!sc || !dur || !sc.clientWidth) return null;
  const total = Math.max(sc.scrollWidth, wrap.scrollWidth, sc.clientWidth);
  const pps = total / dur;
  const a = sc.scrollLeft / pps;
  return { sc, dur, total, view: sc.clientWidth, pps, fit: sc.clientWidth / dur, a, b: a + sc.clientWidth / pps };
}

function navSetScroll(m, px) {
  m.sc.scrollLeft = Math.max(0, px);
}

/** Show exactly [a, b] (clamped to the song and to the zoom limits). */
function navZoomTo(a, b) {
  const m = navMetrics();
  if (!m) return;
  let span = Math.min(m.dur, Math.max(NAV_MIN_SPAN, b - a));
  let pps = Math.min(NAV_MAX_PX_PER_SEC, m.view / span);
  if (pps <= m.fit * 1.0005) {
    nav.w.ws.zoom(0);
    navSetScroll(m, 0);
  } else {
    span = m.view / pps;
    const start = Math.min(Math.max(0, a), m.dur - span);
    nav.w.ws.zoom(pps);
    navSetScroll(m, start * pps);
  }
  navUpdate();
}

/** Zoom by factor keeping time t at the same place on screen (frac = its position, 0..1). */
function navZoomAt(factor, t, frac) {
  const m = navMetrics();
  if (!m) return;
  const span = (m.b - m.a) / factor;
  if (t === undefined) {
    t = (m.a + m.b) / 2;
    frac = 0.5;
  }
  navZoomTo(t - frac * span, t - frac * span + span);
}

/** Zoom around the play cursor (kept where it is when visible, centred otherwise). */
function navZoomStep(factor) {
  const m = navMetrics();
  if (!m) return;
  const t = nav.w.ws.getCurrentTime();
  const inside = t >= m.a && t <= m.b;
  navZoomAt(factor, t, inside ? (t - m.a) / (m.b - m.a) : 0.5);
}

function navFit() {
  const m = navMetrics();
  if (m) navZoomTo(0, m.dur);
}

/** Zoom on the selected section with a little context on both sides. */
function navZoomSelection() {
  const s = sel.cur;
  if (!s) return;
  const pad = Math.max(0.3, (s.end - s.start) * 0.06);
  navZoomTo(s.start - pad, s.end + pad);
}

function navPanTo(a) {
  const m = navMetrics();
  if (!m) return;
  navSetScroll(m, Math.min(Math.max(0, a), m.dur - (m.b - m.a)) * m.pps);
  navUpdate();
}

function navUpdate() {
  if (nav.raf) return;
  nav.raf = requestAnimationFrame(() => {
    nav.raf = 0;
    navDraw();
  });
}

function navDraw() {
  const m = navMetrics();
  if (!m) return;
  navEls.win.style.left = `${(m.a / m.dur) * 100}%`;
  navEls.win.style.width = `${(Math.min(m.dur, m.b - m.a) / m.dur) * 100}%`;
  navEls.win.classList.toggle("full", m.b - m.a >= m.dur - 0.01);
  navEls.range.textContent = `${fmtClock(m.a)} – ${fmtClock(Math.min(m.dur, m.b))}`;
  const z = m.pps / m.fit;
  navEls.zoom.textContent = `${z < 10 ? z.toFixed(1) : Math.round(z)}×`;
  navEls.sel.disabled = !sel.cur;
  navDrawRuler(m);
}

function navPlayhead(t) {
  const m = navMetrics();
  if (!m) return;
  const pct = `${(Math.min(t, m.dur) / m.dur) * 100}%`;
  navEls.head.style.left = pct;
  navEls.played.style.width = pct;
  navUpdate();  // the ruler's cursor mark
}

/** Size a canvas to its CSS box in device pixels; returns the 2D context (or null when hidden). */
function fitCanvas(canvas, width, height) {
  const dpr = window.devicePixelRatio || 1;
  if (!width || !height) return null;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return ctx;
}

/** Whole-song mini waveform: drawn twice, the second copy (progress colour) clipped by the played width. */
function navDrawOverview() {
  const peaks = state.peaks && state.peaks.peaks;
  const width = navEls.track.clientWidth;
  const height = navEls.track.clientHeight;
  if (!peaks || !peaks.length) return;
  navEls.canvasPlayed.style.width = `${width}px`;
  for (const [canvas, color] of [[navEls.canvas, "#3b4a63"], [navEls.canvasPlayed, "#5b8def"]]) {
    const ctx = fitCanvas(canvas, width, height);
    if (!ctx) return;
    ctx.fillStyle = color;
    const bar = 2;
    const mid = height / 2;
    let max = 0;
    for (const p of peaks) max = Math.max(max, Math.abs(p));
    max = max || 1;
    for (let x = 0; x < width; x += bar + 1) {
      const i0 = Math.floor((x / width) * peaks.length);
      const i1 = Math.max(i0 + 1, Math.floor(((x + bar) / width) * peaks.length));
      let v = 0;
      for (let i = i0; i < i1 && i < peaks.length; i++) v = Math.max(v, Math.abs(peaks[i]));
      const h = Math.max(1, (v / max) * (height - 6));
      ctx.fillRect(x, mid - h / 2, bar, h);
    }
  }
}

/** Coloured bands of the marked parts over the overview. */
function navRenderParts() {
  navEls.parts.innerHTML = "";
  const parts = (state.project && state.project.parts) || [];
  const dur = (state.peaks && state.peaks.duration) || (state.project && state.project.duration) || 1;
  for (const part of parts) {
    navEls.parts.append(el("div", {
      class: "nav-part",
      style: `left:${(part.start / dur) * 100}%;width:${((part.end - part.start) / dur) * 100}%;--part-color:${partColor(part)}`,
    }));
  }
}

const RULER_STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];

function rulerLabel(t, step) {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  const dec = step < 1 ? 1 : 0;
  return `${m}:${s.toFixed(dec).padStart(dec ? 4 : 2, "0")}`;
}

/** Time ruler over the visible part of the waveform: labelled ticks that adapt to the zoom + beat ticks. */
function navDrawRuler(m) {
  const canvas = navEls.ruler;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const ctx = fitCanvas(canvas, width, height);
  if (!ctx) return;
  // the wave's scroll area may be narrower than the ruler (border/padding): map onto it
  const waveRect = m.sc.getBoundingClientRect();
  const off = waveRect.left - (canvas.getBoundingClientRect().left + canvas.clientLeft);
  const x = (t) => off + (t - m.a) * m.pps;
  const step = RULER_STEPS.find((s) => s * m.pps >= 70) || 600;
  const minor = step / ({ 0.2: 4, 2: 4, 15: 3, 30: 3, 60: 4, 120: 4, 300: 5 }[step] || 5);

  // beats (from the analysis), when there is room for them
  const beats = (state.project && state.project.beats) || [];
  if (beats.length > 1 && ((beats[beats.length - 1] - beats[0]) / (beats.length - 1)) * m.pps >= 7) {
    ctx.fillStyle = "rgba(91, 141, 239, 0.45)";
    for (const b of beats) {
      if (b < m.a - 1 || b > m.b + 1) continue;
      ctx.fillRect(Math.round(x(b)), height - 5, 1, 5);
    }
  }
  ctx.fillStyle = "rgba(139, 148, 158, 0.55)";
  for (let t = Math.floor(m.a / minor) * minor; t <= m.b + minor; t += minor) {
    if (t < 0) continue;
    ctx.fillRect(Math.round(x(t)), height - 4, 1, 4);
  }
  ctx.font = "600 10px ui-monospace, SFMono-Regular, Consolas, monospace";
  ctx.textBaseline = "top";
  for (let t = Math.floor(m.a / step) * step; t <= m.b + step; t += step) {
    if (t < -1e-6 || t > m.dur + 1e-6) continue;
    const px = Math.round(x(t));
    ctx.fillStyle = "rgba(139, 148, 158, 0.9)";
    ctx.fillRect(px, height - 10, 1, 10);
    ctx.fillStyle = "#c9d1d9";
    ctx.fillText(rulerLabel(t + 1e-6, step), px + 4, 4);
  }
  // play cursor
  const t = nav.w.ws.getCurrentTime();
  if (t >= m.a && t <= m.b) {
    const px = x(t);
    ctx.fillStyle = "#f59e0b";
    ctx.beginPath();
    ctx.moveTo(px - 5, height - 7);
    ctx.lineTo(px + 5, height - 7);
    ctx.lineTo(px, height);
    ctx.fill();
  }
}

/** Song time at a pointer x over the overview. */
function navTimeAt(clientX) {
  const m = navMetrics();
  const r = navEls.track.getBoundingClientRect();
  return m ? Math.min(m.dur, Math.max(0, ((clientX - r.left) / r.width) * m.dur)) : 0;
}

/** While dragging, the view must not jump to the play cursor. */
function navHoldFollow(hold) {
  // the renderer shares this options object; setOptions() would re-render the whole wave
  if (nav.w) nav.w.ws.options.autoScroll = nav.follow && !hold;
}

function navAttach(w) {
  nav.w = w;
  navHoldFollow(false);
  navDrawOverview();
  navRenderParts();
  navPlayhead(w.ws.getCurrentTime());
  navUpdate();
}

// --- overview interactions ---
navEls.track.addEventListener("pointerdown", (e) => {
  const m = navMetrics();
  if (!m || e.button !== 0) return;
  e.preventDefault();
  navEls.track.setPointerCapture(e.pointerId);
  const edge = e.target.closest(".nav-handle");
  const t = navTimeAt(e.clientX);
  if (edge) {
    nav.drag = { kind: "resize", edge: edge.dataset.edge, a: m.a, b: Math.min(m.dur, m.b) };
    navEls.track.classList.add("resizing");
  } else {
    // outside the window: jump there (centred), then keep dragging as a pan
    if (!e.target.closest(".nav-window")) navPanTo(t - (m.b - m.a) / 2);
    const m2 = navMetrics();
    nav.drag = { kind: "pan", t0: t, a0: m2.a };
    navEls.track.classList.add("dragging");
  }
  navHoldFollow(true);
});
navEls.track.addEventListener("pointermove", (e) => {
  const d = nav.drag;
  if (!d) return;
  const t = navTimeAt(e.clientX);
  if (d.kind === "pan") navPanTo(d.a0 + (t - d.t0));
  else if (d.edge === "l") navZoomTo(Math.min(t, d.b - NAV_MIN_SPAN), d.b);
  else navZoomTo(d.a, Math.max(t, d.a + NAV_MIN_SPAN));
});
function navEndDrag() {
  if (!nav.drag) return;
  nav.drag = null;
  navEls.track.classList.remove("dragging", "resizing");
  navHoldFollow(false);
}
navEls.track.addEventListener("pointerup", navEndDrag);
navEls.track.addEventListener("pointercancel", navEndDrag);
navEls.track.addEventListener("dblclick", (e) => {
  const m = navMetrics();
  if (!m) return;
  // on the window: whole song; elsewhere: zoom in there
  if (e.target.closest(".nav-window") && m.b - m.a < m.dur - 0.01) navFit();
  else navZoomAt(4, navTimeAt(e.clientX), 0.5);
});

function wheelDelta(e) {
  return e.deltaMode === 1 ? e.deltaY * 40 : e.deltaMode === 2 ? e.deltaY * 800 : e.deltaY;
}
navEls.track.addEventListener("wheel", (e) => {
  const m = navMetrics();
  if (!m) return;
  e.preventDefault();
  if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
    navPanTo(m.a + (e.deltaX / navEls.track.clientWidth) * m.dur);
    return;
  }
  const t = navTimeAt(e.clientX);
  const inside = t >= m.a && t <= m.b;
  navZoomAt(Math.exp(-wheelDelta(e) * 0.002), inside ? t : undefined, inside ? (t - m.a) / (m.b - m.a) : undefined);
}, { passive: false });

// Ctrl/⌘ + wheel (trackpad pinch too) over the waveform: zoom around the mouse
els.projWave.addEventListener("wheel", (e) => {
  if (!(e.ctrlKey || e.metaKey)) return;
  const m = navMetrics();
  if (!m) return;
  e.preventDefault();
  const t = clickTime(nav.w, e);
  const frac = (e.clientX - m.sc.getBoundingClientRect().left) / m.view;
  // a trackpad pinch arrives as ctrl + small deltas: needs a stronger factor than a mouse wheel notch
  const pinch = e.deltaMode === 0 && Math.abs(e.deltaY) < 10;
  navZoomAt(Math.exp(-wheelDelta(e) * (pinch ? 0.01 : 0.002)), t, frac);
}, { passive: false });

// --- ruler ---
navEls.ruler.addEventListener("click", (e) => {
  const m = navMetrics();
  if (!m) return;
  const t = m.a + (e.clientX - m.sc.getBoundingClientRect().left) / m.pps;
  nav.w.ws.setTime(Math.min(m.dur, Math.max(0, t)));
  if (nav.w.onInteraction) nav.w.onInteraction(t);
});
navEls.ruler.addEventListener("wheel", (e) => {
  const m = navMetrics();
  if (!m) return;
  e.preventDefault();
  const frac = (e.clientX - m.sc.getBoundingClientRect().left) / m.view;
  navZoomAt(Math.exp(-wheelDelta(e) * 0.002), m.a + frac * (m.b - m.a), frac);
}, { passive: false });

// --- buttons + keys ---
$("navInBtn").addEventListener("click", () => navZoomStep(NAV_STEP));
$("navOutBtn").addEventListener("click", () => navZoomStep(1 / NAV_STEP));
$("navFitBtn").addEventListener("click", navFit);
navEls.sel.addEventListener("click", navZoomSelection);
navEls.follow.addEventListener("click", () => {
  nav.follow = !nav.follow;
  navEls.follow.classList.toggle("is-on", nav.follow);
  navEls.follow.setAttribute("aria-pressed", String(nav.follow));
  navHoldFollow(false);
});
document.addEventListener("keydown", (e) => {
  if (state.view !== "project" || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest("input, textarea, select, [contenteditable]")) return;
  const k = e.key;
  if (k === "+" || k === "=") navZoomStep(NAV_STEP);
  else if (k === "-" || k === "_") navZoomStep(1 / NAV_STEP);
  else if (k === "0") navFit();
  else if (k === "z" || k === "Z") navZoomSelection();
  else return;
  e.preventDefault();
});

new ResizeObserver(() => {
  if (!navMetrics()) return;
  navDrawOverview();
  navUpdate();
}).observe(navEls.track);

// ---------- project: tabs (Partes / Letra / Harmonia / Análise) + sticky player ----------

const TABS = ["parts", "lyrics", "harmony", "analysis"];
const tabsEl = $("projTabs");
const playerCard = $("playerCard");

function tabStorageKey() {
  return `escritab.tab.${state.project ? state.project.job_id : ""}`;
}

/** Show one tab panel; remembered per project (localStorage is a convenience: may be unavailable). */
function openTab(name, remember = true) {
  if (!TABS.includes(name)) name = "parts";
  for (const b of tabsEl.querySelectorAll(".tab")) {
    const on = b.dataset.tab === name;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;
  }
  for (const panel of document.querySelectorAll("[data-tab-panel]")) show(panel, panel.dataset.tabPanel === name);
  if (remember) {
    try {
      localStorage.setItem(tabStorageKey(), name);
    } catch {
      /* private mode / blocked storage */
    }
  }
}

function restoreTab() {
  let name = null;
  try {
    name = localStorage.getItem(tabStorageKey());
  } catch {
    /* ignore */
  }
  openTab(name || "parts", false);
}

/** After switching, keep the tab bar just under the sticky player instead of deep inside the old panel. */
function revealTabs() {
  if (tabsEl.getBoundingClientRect().top < 0) tabsEl.scrollIntoView({ block: "start" });
}

tabsEl.addEventListener("click", (e) => {
  const b = e.target.closest(".tab");
  if (!b) return;
  openTab(b.dataset.tab);
  revealTabs();
});
tabsEl.addEventListener("keydown", (e) => {
  const order = [...tabsEl.querySelectorAll(".tab")];
  const i = order.indexOf(document.activeElement);
  if (i < 0) return;
  let j = null;
  if (e.key === "ArrowRight") j = (i + 1) % order.length;
  else if (e.key === "ArrowLeft") j = (i - 1 + order.length) % order.length;
  else if (e.key === "Home") j = 0;
  else if (e.key === "End") j = order.length - 1;
  if (j === null) return;
  e.preventDefault();
  order[j].focus();
  openTab(order[j].dataset.tab);
});

// height of the sticky player, so the tab bar scrolls to just below it
new ResizeObserver(() => {
  document.documentElement.style.setProperty("--player-h", `${playerCard.offsetHeight}px`);
}).observe(playerCard);
// shadow under the player only while it is stuck to the top
new IntersectionObserver(([entry]) => {
  playerCard.classList.toggle("is-stuck", !entry.isIntersecting && entry.boundingClientRect.top < 0);
}).observe($("playerSentinel"));

// ---------- project: selected section + loop ----------

const SEL_RANGE_ID = "sel-range";
const RANGE_COLOR = "#a78bfa";
const sel = { cur: null };  // {kind: "part" | "draft" | "range", partId?, name, start, end, color}

function partSelection(part) {
  return { kind: "part", partId: part.part_id, name: part.name, start: part.start, end: part.end, color: partColor(part) };
}
function draftSelection() {
  const r = projWave && projWave.draft;
  if (!r) return null;
  return { kind: "draft", name: els.draftName.value.trim() || "Novo trecho (sem nome)", start: r.start, end: r.end, color: "#f59e0b" };
}

function setSelection(s) {
  // selecting something else leaves the fine-tuning of a part (unsaved changes are dropped)
  if (PE.active && s && s.kind === "part") {
    if (s.partId !== PE.focus && PE.cur[s.partId]) {
      PE.focus = s.partId;
      s = { ...s, ...PE.cur[s.partId] };
      setTimeout(renderPartEdit, 0);
    }
  } else if (PE.active && s) {
    leavePartEdit(true);
  }
  sel.cur = s;
  applyLoop();
  styleSelection();
  renderSelectionBar();
  navUpdate();
}

/** The waveform loops only when a section is selected AND "Repetir trecho" is on. */
function applyLoop() {
  if (!projWave) return;
  const s = sel.cur;
  projWave.loop = s && els.loopCheck.checked ? { start: s.start, end: s.end } : null;
  if (projWave.loop && projWave.ws.isPlaying()) {
    const t = projWave.ws.getCurrentTime();
    if (t < s.start || t >= s.end) projWave.ws.setTime(s.start);
  }
}

/** Explicit "ouvir em loop": turns the toggle on and plays the selection from its start. */
function playSelectionLoop(s) {
  if (!projWave || !s) return;
  els.loopCheck.checked = true;
  setSelection(s);
  projWave.ws.setTime(s.start);
  projWave.ws.play();
}

function styleSelection() {
  const w = projWave;
  if (!w) return;
  const s = sel.cur;
  for (const part of state.project.parts) {
    const region = w.regions.getRegions().find((r) => r.id === `part-${part.part_id}`);
    if (!region) continue;
    const selected = !!s && s.kind === "part" && s.partId === part.part_id;
    region.setOptions({ color: withAlpha(partColor(part), selected ? 0.45 : s ? 0.12 : 0.22) });
    if (region.element) {
      region.element.style.boxShadow = selected ? `inset 0 0 0 2px ${partColor(part)}` : "";
      region.element.style.zIndex = selected ? "3" : "";
    }
  }
  // a progression picked in the harmony card has no part: show it as a temporary region
  const old = w.regions.getRegions().find((r) => r.id === SEL_RANGE_ID);
  if (old) old.remove();
  if (s && s.kind === "range") {
    const region = w.regions.addRegion({
      id: SEL_RANGE_ID, start: s.start, end: s.end, color: withAlpha(RANGE_COLOR, 0.35), drag: false, resize: false,
    });
    if (region && region.element) region.element.style.boxShadow = `inset 0 0 0 2px ${RANGE_COLOR}`;
  }
  for (const row of [...els.partList.querySelectorAll(".part-row"), ...$("partLaneInner").children]) {
    row.classList.toggle("selected", !!s && s.kind === "part" && row.dataset.partId === s.partId);
  }
}

function renderSelectionBar() {
  const s = sel.cur;
  els.selectionBar.classList.toggle("has-selection", !!s);
  els.selDot.style.background = s ? s.color : "";
  els.selLabel.innerHTML = "";
  if (s) {
    els.selLabel.append("Trecho selecionado: ", el("strong", {}, s.name), el("span", { class: "muted" }, ` · ${fmtRange(s.start, s.end)}`));
  } else {
    els.selLabel.append(el("strong", {}, "Nenhum trecho selecionado"),
      el("span", { class: "muted" }, " · o Play toca a música inteira. Clique numa parte ou arraste sobre a onda para selecionar."));
  }
  $("projFromStartBtn").title = s ? "Tocar do começo do trecho selecionado (tecla Home)" : "Tocar do começo da música (tecla Home)";
  els.loopCheck.disabled = !s;
  els.loopToggle.classList.toggle("is-disabled", !s);
  els.loopToggle.classList.toggle("active", !!s && els.loopCheck.checked);
  show(els.selClearBtn, !!s);
  show($("partEditBtn"), !!s && s.kind === "part" && !peEditing());
}

els.loopCheck.addEventListener("change", () => {
  applyLoop();
  renderSelectionBar();
});
els.selClearBtn.addEventListener("click", () => {
  if (sel.cur && sel.cur.kind === "draft") discardDraft();
  else setSelection(null);
});

// ---------- project: edit the parts' boundaries on the timeline ----------
// "Ajustar" turns every part's edges into handles. Dragging the boundary two parts share moves
// both of them live (waveform + part lane); the panel fine-tunes the focused part. Saving sends
// every part that changed.

const PE = {
  active: false,
  focus: null,   // part_id shown in the panel
  orig: {},      // part_id -> {start, end} saved
  cur: {},       // part_id -> {start, end} being edited
  disableDrag: null,
};
const peEls = {
  panel: $("partEditPanel"),
  name: $("peName"),
  nameInput: $("peNameInput"),
  start: $("peStart"),
  end: $("peEnd"),
  range: $("peRange"),
  neighborsRow: $("peNeighborsRow"),
  neighbors: $("peNeighbors"),
  neighborsLabel: $("peNeighborsLabel"),
  warn: $("peWarn"),
};
const PE_TOUCH_S = 0.15;  // parts this close share a boundary (they move together)
const PE_MIN_S = 0.1;

function partRegion(partId) {
  return projWave && projWave.regions.getRegions().find((r) => r.id === `part-${partId}`);
}
function peEditing() {
  return PE.active;
}
function peRange(part) {
  return PE.active && PE.cur[part.part_id] ? PE.cur[part.part_id] : part;
}
function peTimesChanged(id) {
  const a = PE.cur[id];
  const b = PE.orig[id];
  return !!a && !!b && (Math.abs(a.start - b.start) > 0.005 || Math.abs(a.end - b.end) > 0.005);
}
function peNameChanged(id) {
  const a = PE.cur[id];
  const b = PE.orig[id];
  return !!a && !!b && a.name.trim() !== b.name;
}
function peChanged(id) {
  return peTimesChanged(id) || peNameChanged(id);
}
/** The name shown for a part while editing (the typed one, or the saved one while blank). */
function peName(id) {
  const r = PE.active && PE.cur[id];
  return (r && r.name.trim()) || partById(id).name;
}
/** The parts glued to ``id``'s start / end (sharing that boundary right now). */
function peNeighbors(id) {
  const me = PE.cur[id];
  let prev = null;
  let next = null;
  for (const [other, r] of Object.entries(PE.cur)) {
    if (other === id) continue;
    if (!prev && Math.abs(r.end - me.start) < PE_TOUCH_S) prev = other;
    if (!next && Math.abs(r.start - me.end) < PE_TOUCH_S) next = other;
  }
  return { prev, next };
}
function partById(id) {
  return state.project.parts.find((p) => p.part_id === id);
}

function startPartEdit(part) {
  if (!projWave) return;
  if (projWave.draft) discardDraft();
  if (!PE.active) {
    PE.active = true;
    PE.orig = {};
    PE.cur = {};
    for (const p of state.project.parts) {
      PE.orig[p.part_id] = { start: p.start, end: p.end, name: p.name };
      PE.cur[p.part_id] = { start: p.start, end: p.end, name: p.name };
    }
    for (const p of state.project.parts) {
      const region = partRegion(p.part_id);
      if (region) region.setOptions({ resize: true });
    }
    if (PE.disableDrag) PE.disableDrag();  // no new section drawn while editing the boundaries
    PE.disableDrag = null;
    $("projWave").classList.add("pe-mode");
    show(peEls.panel);
  }
  peFocus(part.part_id);
}

function peFocus(id) {
  PE.focus = id;
  const part = partById(id);
  if (part && !(sel.cur && sel.cur.partId === id)) setSelection({ ...partSelection(part), ...PE.cur[id] });
  renderPartEdit();
}

function renderPartEdit() {
  if (!PE.active) return;
  const part = partById(PE.focus);
  const r = PE.cur[PE.focus];
  if (!part || !r) return;
  peEls.name.textContent = `"${peName(PE.focus)}"`;
  if (document.activeElement !== peEls.nameInput) peEls.nameInput.value = r.name;
  const blank = !r.name.trim();
  peEls.nameInput.classList.toggle("invalid", blank);
  if (document.activeElement !== peEls.start) peEls.start.value = fmtClock(r.start, 2);
  if (document.activeElement !== peEls.end) peEls.end.value = fmtClock(r.end, 2);
  peEls.start.classList.remove("invalid");
  peEls.end.classList.remove("invalid");
  const o = PE.orig[PE.focus];
  peEls.range.textContent = `duração ${(r.end - r.start).toFixed(2)} s`
    + (peTimesChanged(PE.focus) ? ` · antes ${fmtClock(o.start, 2)}–${fmtClock(o.end, 2)}` : "");
  const { prev, next } = peNeighbors(PE.focus);
  const names = [prev && `"${peName(prev)}"`, next && `"${peName(next)}"`].filter(Boolean);
  show(peEls.neighborsRow, true);
  peEls.neighborsLabel.textContent = names.length
    ? `Partes coladas se movem juntas (${names.join(" e ")}): sem buraco nem sobreposição`
    : "Partes coladas se movem juntas: sem buraco nem sobreposição";
  const changed = Object.keys(PE.cur).filter(peChanged);
  const withTab = changed.filter(peTimesChanged).map(partById)
    .filter((p) => p && p.artifacts.musicxml).map((p) => peName(p.part_id));
  const label = (id) => (peNameChanged(id) ? `${PE.orig[id].name} → ${peName(id)}` : peName(id));
  const parts = changed.length ? `${changed.length} parte(s) alterada(s): ${changed.map(label).join(", ")}.` : "";
  const blankAny = Object.values(PE.cur).some((x) => !x.name.trim());
  peEls.warn.textContent = [parts,
    withTab.length ? `A tablatura de ${withTab.join(", ")} vai precisar ser gerada de novo.` : "",
    blankAny ? "Toda parte precisa de um nome." : ""].filter(Boolean).join(" ");
  show(peEls.warn, changed.length > 0 || blankAny);
  $("peSaveBtn").disabled = !changed.length || blankAny;
  $("peSaveBtn").textContent = changed.length > 1 ? `Salvar ${changed.length} partes` : "Salvar";
  if (sel.cur && sel.cur.partId === PE.focus) {
    sel.cur = { ...sel.cur, start: r.start, end: r.end };
    applyLoop();
    renderSelectionBar();
  }
}

/** Moves one edge of a part (and the glued neighbour's edge with it). Returns false if invalid. */
function moveEdge(id, which, value) {
  if (!PE.active || !Number.isFinite(value) || !PE.cur[id]) return false;
  const dur = projWave.ws.getDuration() || state.project.duration || 0;
  const me = PE.cur[id];
  const glue = peEls.neighbors.checked;
  const { prev, next } = peNeighbors(id);
  if (which === "start") {
    const floor = glue && prev ? PE.cur[prev].start + PE_MIN_S : 0;
    me.start = Math.max(floor, Math.min(value, me.end - PE_MIN_S));
    if (glue && prev) PE.cur[prev].end = me.start;
  } else {
    const ceil = glue && next ? PE.cur[next].end - PE_MIN_S : dur;
    me.end = Math.min(ceil, Math.max(value, me.start + PE_MIN_S));
    if (glue && next) PE.cur[next].start = me.end;
  }
  for (const pid of [id, glue && which === "start" ? prev : null, glue && which === "end" ? next : null]) {
    if (!pid) continue;
    const region = partRegion(pid);
    const r = PE.cur[pid];
    if (region && (Math.abs(region.start - r.start) > 1e-4 || Math.abs(region.end - r.end) > 1e-4)) {
      region.setOptions({ start: r.start, end: r.end });
    }
  }
  renderPartLane();
  if (PE.focus !== id) peFocus(id);
  else renderPartEdit();
  return true;
}

/** A part region's edge was dragged on the waveform (live, or at the end with the beat snap). */
function peRegionMoved(region, done) {
  const id = region.id.slice(5);
  const r = PE.cur[id];
  if (!r) return;
  const snap = (t) => (done ? snapTime(t) : t);
  if (Math.abs(region.start - r.start) > 1e-4) moveEdge(id, "start", snap(region.start));
  if (Math.abs(region.end - r.end) > 1e-4) moveEdge(id, "end", snap(region.end));
}

function leavePartEdit(restore) {
  if (!PE.active) return;
  PE.active = false;
  for (const p of state.project.parts) {
    const region = partRegion(p.part_id);
    if (!region) continue;
    region.setOptions(restore ? { start: p.start, end: p.end, resize: false } : { resize: false });
  }
  $("projWave").classList.remove("pe-mode");
  hide(peEls.panel);
  if (projWave && !PE.disableDrag) PE.disableDrag = projWave.regions.enableDragSelection({ color: DRAFT_COLOR });
  renderPartLane();
}

function cancelPartEdit() {
  const focus = PE.focus;
  leavePartEdit(true);
  const part = focus && partById(focus);
  if (part && sel.cur && sel.cur.partId === focus) setSelection(partSelection(part));
  else renderSelectionBar();
}

async function savePartEdit() {
  if (!PE.active || state.busy) return;
  const projectId = state.project.job_id;
  const changed = Object.keys(PE.cur).filter(peChanged);
  if (!changed.length || Object.values(PE.cur).some((x) => !x.name.trim())) return;
  setBusy(true);
  $("peSaveBtn").disabled = true;
  try {
    let job = null;
    let lyricsChanged = false;
    // shrinking parts first, so a growing neighbour never overlaps them on the way
    const order = changed.sort((a, b) => {
      const grow = (id) => (PE.cur[id].end - PE.cur[id].start) - (PE.orig[id].end - PE.orig[id].start);
      return grow(a) - grow(b);
    });
    for (const id of order) {
      const body = {};
      if (peTimesChanged(id)) Object.assign(body, { start: PE.cur[id].start, end: PE.cur[id].end });
      if (peNameChanged(id)) body.name = PE.cur[id].name.trim();
      const data = await patchJson(`/api/projects/${projectId}/parts/${id}`, body);
      job = data.job;
      lyricsChanged = lyricsChanged || !!data.lyrics_changed;
    }
    if (!state.project || state.project.job_id !== projectId) return;
    const focus = PE.focus;
    state.project = job;
    if (lyricsChanged) loadLyrics();  // a stanza moved into its part's new bounds
    leavePartEdit(false);
    drawPartRegions();
    renderPartList();
    renderStructure();
    const part = partById(focus);
    if (part) setSelection(partSelection(part));
  } catch (err) {
    peEls.warn.textContent = `Não foi possível salvar: ${err.message}`;
    show(peEls.warn);
    $("peSaveBtn").disabled = false;
  } finally {
    setBusy(false);
  }
}

$("partEditBtn").addEventListener("click", () => {
  const part = sel.cur && sel.cur.kind === "part" && partById(sel.cur.partId);
  if (part) startPartEdit(part);
});
$("peSaveBtn").addEventListener("click", savePartEdit);
$("peCancelBtn").addEventListener("click", cancelPartEdit);
$("pePlayBtn").addEventListener("click", () => {
  if (!PE.active) return;
  const part = partById(PE.focus);
  if (part) playSelectionLoop({ ...partSelection(part), ...PE.cur[PE.focus] });
});
peEls.neighbors.addEventListener("change", renderPartEdit);
peEls.nameInput.addEventListener("input", () => {
  if (!PE.active || !PE.cur[PE.focus]) return;
  PE.cur[PE.focus].name = peEls.nameInput.value;
  renderPartLane();  // the new name shows on the lane right away
  renderPartEdit();
});
peEls.nameInput.addEventListener("keydown", (e) => {
  if (!PE.active) return;
  if (e.key === "Enter") {
    e.preventDefault();
    savePartEdit();
  } else if (e.key === "Escape") {  // back to the saved name
    PE.cur[PE.focus].name = PE.orig[PE.focus].name;
    peEls.nameInput.blur();
    renderPartLane();
    renderPartEdit();
  }
});
peEls.panel.addEventListener("click", (e) => {
  const nudge = e.target.closest("[data-pe]");
  if (nudge) {
    const which = nudge.dataset.pe;
    moveEdge(PE.focus, which, PE.cur[PE.focus][which] + Number(nudge.dataset.step) * (e.shiftKey ? 10 : 1));
    return;
  }
  const here = e.target.closest("[data-pe-here]");
  if (here && projWave) moveEdge(PE.focus, here.dataset.peHere, projWave.ws.getCurrentTime());
});
for (const [input, which] of [[peEls.start, "start"], [peEls.end, "end"]]) {
  const commit = () => {
    const ok = moveEdge(PE.focus, which, parseClock(input.value));
    input.classList.toggle("invalid", !ok);
    if (ok) input.value = fmtClock(PE.cur[PE.focus][which], 2);
  };
  input.addEventListener("change", commit);
  input.addEventListener("blur", () => { if (!input.classList.contains("invalid")) renderPartEdit(); });
  input.addEventListener("keydown", (e) => {
    if (!PE.active) return;
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
    } else if (e.key === "Escape") {
      input.blur();
      renderPartEdit();
    } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      const step = (e.shiftKey ? 1 : 0.1) * (e.key === "ArrowUp" ? 1 : -1);
      const cur = parseClock(input.value);
      moveEdge(PE.focus, which, (Number.isFinite(cur) ? cur : PE.cur[PE.focus][which]) + step);
      input.value = fmtClock(PE.cur[PE.focus][which], 2);
    }
  });
}

// ---------- new part that fits into the timeline ----------

/** What saving the draft with "encaixar" would do to the existing parts (same rules as the server). */
function draftFitPlan(start, end) {
  const plan = [];
  for (const p of state.project.parts) {
    if (p.end <= start + 1e-3 || p.start >= end - 1e-3) continue;
    const tab = p.artifacts && p.artifacts.musicxml ? " (a tablatura dela será refeita)" : "";
    if (p.start < start && p.end > end) {
      plan.push(`${p.name} será dividida: ${fmtClock(p.start)}–${fmtClock(start)} e ${fmtClock(end)}–${fmtClock(p.end)}${tab}`);
    } else if (p.start < start) {
      plan.push(start - p.start < 0.3 ? `${p.name} será removida` : `${p.name} vai terminar em ${fmtClock(start)}${tab}`);
    } else if (p.end > end) {
      plan.push(p.end - end < 0.3 ? `${p.name} será removida` : `${p.name} vai começar em ${fmtClock(end)}${tab}`);
    } else {
      plan.push(`${p.name} será removida (fica dentro da parte nova)`);
    }
  }
  return plan;
}

function renderDraftFit() {
  const r = projWave && projWave.draft;
  const plan = r ? draftFitPlan(r.start, r.end) : [];
  show($("draftFitBox"), plan.length > 0);
  const list = $("draftFitList");
  list.innerHTML = "";
  for (const line of plan) list.append(el("li", {}, line));
  list.classList.toggle("is-off", !$("draftFit").checked);
}
$("draftFit").addEventListener("change", renderDraftFit);

/** "+ Nova parte": a draft over the selected stretch, or 8 s from the cursor. */
$("newPartBtn").addEventListener("click", () => {
  const w = projWave;
  if (!w) return;
  if (PE.active) cancelPartEdit();
  const dur = w.ws.getDuration() || state.project.duration || 0;
  let start;
  let end;
  const t = w.ws.getCurrentTime();
  if (sel.cur && sel.cur.kind === "part") {
    // inside the selected part: from the cursor (if it is in the part) up to 8 s, within the part
    const inside = t > sel.cur.start && t < sel.cur.end - 1;
    start = inside ? snapTime(t) : sel.cur.start;
    end = Math.min(sel.cur.end, start + 8);
  } else if (sel.cur && sel.cur.kind !== "draft") {
    ({ start, end } = sel.cur);  // a stretch picked from the structure
  } else {
    start = snapTime(w.ws.getCurrentTime());
    end = Math.min(dur, snapTime(start + 8));
    if (end - start < 1) start = Math.max(0, end - 8);
  }
  if (w.draft) w.draft.remove();
  w.draft = null;
  els.draftName.value = "";
  w.regions.addRegion({ start, end, color: DRAFT_COLOR, drag: true, resize: true });  // becomes the draft
  els.draftPanel.scrollIntoView({ block: "nearest", behavior: "smooth" });
});

function snapDraft() {
  const r = projWave.draft;
  const start = snapTime(r.start);
  let end = snapTime(r.end);
  if (end - start < 0.5) end = r.end;
  r.setOptions({ start, end });
}

function showDraft() {
  renderDraftTimes();
  show(els.draftPanel);
  if (!els.draftName.value) els.draftName.focus();
}

const draftStartInput = $("draftStart");
const draftEndInput = $("draftEnd");

/** Start / end fields + length; a field being typed in is left alone. */
function renderDraftTimes() {
  const r = projWave && projWave.draft;
  if (!r) return;
  renderDraftFit();
  if (document.activeElement !== draftStartInput) draftStartInput.value = fmtClock(r.start, 2);
  if (document.activeElement !== draftEndInput) draftEndInput.value = fmtClock(r.end, 2);
  draftStartInput.classList.remove("invalid");
  draftEndInput.classList.remove("invalid");
  els.draftRange.textContent = `duração ${(r.end - r.start).toFixed(2)} s`;
}

/** "1:23.45", "83.45", "83,45" or "1:23" -> seconds; NaN if unreadable. */
function parseClock(text) {
  const s = String(text).trim().replace(",", ".");
  if (!s) return NaN;
  const m = s.match(/^(?:(\d+):)?(\d+(?:\.\d*)?|\.\d+)$/);
  if (!m) return NaN;
  return (m[1] ? Number(m[1]) * 60 : 0) + Number(m[2]);
}

/** Applies a typed start/end to the draft (exact: no beat snap). Returns false if invalid. */
function applyDraftTime(which, value) {
  const r = projWave && projWave.draft;
  if (!r) return false;
  const dur = projWave.ws.getDuration() || state.project.duration || 0;
  if (!Number.isFinite(value)) return false;
  let start = r.start;
  let end = r.end;
  if (which === "start") start = Math.max(0, Math.min(value, end - 0.1));
  else end = Math.min(dur, Math.max(value, start + 0.1));
  r.setOptions({ start, end });
  if (sel.cur && sel.cur.kind === "draft") setSelection(draftSelection());
  return true;
}

for (const [input, which] of [[draftStartInput, "start"], [draftEndInput, "end"]]) {
  const commit = () => {
    const ok = applyDraftTime(which, parseClock(input.value));
    input.classList.toggle("invalid", !ok);
    if (ok) {
      input.value = fmtClock(projWave.draft[which], 2);
      renderDraftTimes();
    }
  };
  input.addEventListener("change", commit);
  input.addEventListener("blur", () => {
    if (!input.classList.contains("invalid")) renderDraftTimes();
  });
  input.addEventListener("keydown", (e) => {
    const r = projWave && projWave.draft;
    if (!r) return;
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
    } else if (e.key === "Escape") {
      input.blur();
      renderDraftTimes();
    } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      const step = (e.shiftKey ? 1 : 0.1) * (e.key === "ArrowUp" ? 1 : -1);
      const cur = parseClock(input.value);
      applyDraftTime(which, (Number.isFinite(cur) ? cur : r[which]) + step);
      input.value = fmtClock(r[which], 2);
      renderDraftTimes();
    }
  });
}

function discardDraft() {
  if (projWave && projWave.draft) {
    projWave.draft.remove();
    projWave.draft = null;
  }
  els.draftName.value = "";
  hide(els.draftPanel);
  if (sel.cur && sel.cur.kind === "draft") setSelection(null);
}

$("nameChips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  const base = chip.textContent.trim();
  // "Solo" twice -> "Solo 2"
  const taken = new Set(state.project.parts.map((p) => p.name));
  let name = base;
  for (let i = 2; taken.has(name); i++) name = `${base} ${i}`;
  els.draftName.value = name;
  els.draftName.focus();
  if (sel.cur && sel.cur.kind === "draft") setSelection(draftSelection());
});
els.draftName.addEventListener("input", () => {
  if (sel.cur && sel.cur.kind === "draft") setSelection(draftSelection());
});

els.draftPlayBtn.addEventListener("click", () => {
  playSelectionLoop(draftSelection());
});
els.draftCancelBtn.addEventListener("click", discardDraft);
els.draftName.addEventListener("keydown", (e) => {
  if (e.key === "Enter") els.draftSaveBtn.click();
  if (e.key === "Escape") discardDraft();
});

els.draftSaveBtn.addEventListener("click", async () => {
  const r = projWave && projWave.draft;
  const name = els.draftName.value.trim();
  if (!r) return;
  const badTime = [draftStartInput, draftEndInput].find((i) => i.classList.contains("invalid"));
  if (badTime) {
    badTime.focus();
    banner(els.projectStatus, "Tempo inválido. Use m:ss.s (ex.: 1:23.5) ou segundos (ex.: 83.5).", "error");
    return;
  }
  if (!name) {
    els.draftName.focus();
    banner(els.projectStatus, "Dê um nome para a parte (ex.: Intro, Riff, Solo).", "error");
    return;
  }
  els.draftSaveBtn.disabled = true;
  try {
    const fit = !$("draftFitBox").classList.contains("hidden") && $("draftFit").checked;
    const data = await postJson(projectUrl("/parts"), { name, start: r.start, end: r.end, fit });
    state.project = data.job;
    discardDraft();
    if (data.changes && data.changes.length) {
      banner(els.projectStatus, `Parte "${name}" criada e encaixada. ${data.changes.join(" · ")}.`);
    } else hide(els.projectStatus);
    if (data.lyrics_changed) loadLyrics();  // the stanza moved into its part: new line times
    renderStructure();
    drawPartRegions();
    renderPartList();
    // the section just saved stays selected (and looping, if it was)
    const saved = state.project.parts.find((x) => x.part_id === data.part.part_id);
    if (saved) setSelection(partSelection(saved));
  } catch (err) {
    banner(els.projectStatus, `Erro ao salvar a parte: ${err.message}`, "error");
  } finally {
    els.draftSaveBtn.disabled = false;
  }
});

function renderPartList() {
  const parts = state.project.parts;
  els.partList.innerHTML = "";
  show(els.partEmpty, parts.length === 0);
  $("tabPartsCount").textContent = parts.length ? String(parts.length) : "";
  for (const part of parts) {
    const ready = !!part.artifacts.musicxml;
    const status = ready
      ? el("span", { class: "badge ok" }, `tab pronta · ${part.stats.total_notes || 0} notas`)
      : el("span", { class: "badge" }, "sem tab");
    const row = el("div", { class: "part-row", dataset: { partId: part.part_id } },
      el("span", { class: "part-dot", style: `background:${partColor(part)}` }),
      el("strong", { class: "part-name" }, part.name),
      el("span", { class: "muted" }, fmtRange(part.start, part.end)),
      status,
      el("span", { class: "part-actions" },
        el("button", {
          type: "button",
          class: "btn-outline btn-small",
          title: "Ouvir em loop",
          onclick: () => playSelectionLoop(partSelection(part)),
        }, "⟳ Ouvir"),
        el("button", {
          type: "button",
          class: "btn-outline btn-small",
          title: "Ajustar o começo e o fim",
          onclick: () => {
            startPartEdit(part);
            $("playerCard").scrollIntoView({ block: "nearest", behavior: "smooth" });
          },
        }, "✎ Ajustar"),
        el("a", { class: "btn-primary btn-small", href: `#/p/${state.project.job_id}/part/${part.part_id}` },
          ready ? "Estudar →" : "Transcrever →")));
    els.partList.append(row);
  }
  styleSelection();
  renderHarmonyParts();
  if (lyricsIsCurrent() && L.data && L.data.lyrics) renderSheetFilter();
}

els.projPlayBtn.addEventListener("click", () => {
  const w = projWave;
  if (!w) return;
  if (w.ws.isPlaying()) {
    w.ws.pause();
    return;
  }
  if (w.loop) {
    const t = w.ws.getCurrentTime();
    if (t < w.loop.start || t >= w.loop.end - 0.05) w.ws.setTime(w.loop.start);
  }
  w.ws.play();
});
// DAW-style stop: the first press stops right there (Play continues from it); pressing it again
// while stopped goes back to the start of the looped section, or of the song
els.projStopBtn.addEventListener("click", () => {
  const w = projWave;
  if (!w) return;
  if (w.ws.isPlaying()) w.ws.pause();
  else w.ws.setTime(w.loop ? w.loop.start : 0);
});
// ---------- player audio: the original mix or one or more stems together ----------
// A source is "" (original mix), a stem name ("guitar") or several joined by "+" ("bass+guitar").

const SRC = { open: false };
const srcEls = { picker: $("projSourcePicker"), btn: $("projSourceBtn"), menu: $("projSourceMenu") };

function stemNames() {
  return Object.keys((state.project && state.project.stems) || {});
}
function sourceStems(source) {
  return source ? source.split("+").filter(Boolean) : [];
}
function sourceLabel(source) {
  const stems = sourceStems(source);
  if (!stems.length) return "Mix original";
  const all = stemNames();
  const missing = all.filter((n) => !stems.includes(n));
  if (all.length > 2 && missing.length === 1) return `Sem ${(STEM_LABELS[missing[0]] || missing[0]).toLowerCase()}`;
  return stems.map((n) => STEM_LABELS[n] || n).join(" + ");
}

function setProjSource(source) {
  const stems = sourceStems(source).filter((n) => stemNames().includes(n)).sort();
  const value = stems.join("+");
  srcEls.btn.textContent = sourceLabel(value);
  srcEls.btn.title = `Tocando: ${sourceLabel(value)}. Clique para escolher a música original ou um ou mais stems juntos.`;
  renderSourceMenu(value);
  if (projWave && projWave.stem !== value) {
    switchSource(projWave, value);
    // loading another file resets the media element's rate
    projWave.ws.once("ready", () => projWave && projWave.ws.setPlaybackRate(Number(els.projRate.value) / 100, true));
  }
}

function renderSourceMenu(value) {
  const chosen = sourceStems(value);
  const names = stemNames();
  srcEls.menu.innerHTML = "";
  srcEls.btn.disabled = names.length === 0;
  const pick = (v) => setProjSource(v);
  srcEls.menu.append(el("label", { class: "src-opt" },
    el("input", { type: "radio", name: "srcMode", checked: !chosen.length, onchange: () => pick("") }),
    el("span", {}, el("strong", {}, "Mix original"), el("small", { class: "muted" }, "a música como foi enviada"))));
  srcEls.menu.append(el("div", { class: "src-sep" }, "Stems (marque um ou mais)"));
  for (const n of names) {
    srcEls.menu.append(el("label", { class: "src-opt" },
      el("input", {
        type: "checkbox", checked: chosen.includes(n),
        onchange: (e) => pick((e.target.checked ? [...chosen, n] : chosen.filter((x) => x !== n)).join("+")),
      }),
      el("span", {}, STEM_LABELS[n] || n)));
  }
  const without = (x) => names.filter((n) => n !== x).join("+");
  const quick = [];
  if (names.includes("vocals") && names.length > 1) quick.push(["Tudo menos a voz", without("vocals")]);
  if (names.includes("drums") && names.length > 1) quick.push(["Tudo menos a bateria", without("drums")]);
  if (names.includes("guitar") && names.length > 1) quick.push(["Tudo menos a guitarra", without("guitar")]);
  if (quick.length) {
    srcEls.menu.append(el("div", { class: "src-quick" },
      ...quick.map(([label, v]) => el("button", { type: "button", class: "chip", onclick: () => pick(v) }, label))));
  }
  srcEls.menu.append(el("p", { class: "muted small src-note" }, "Vários stems juntos: na primeira vez o servidor leva alguns segundos para mixar."));
}

function toggleSourceMenu(open = !SRC.open) {
  SRC.open = open;
  show(srcEls.menu, open);
  srcEls.btn.setAttribute("aria-expanded", open ? "true" : "false");
}
srcEls.btn.addEventListener("click", () => toggleSourceMenu());
document.addEventListener("click", (e) => {
  if (SRC.open && !srcEls.picker.contains(e.target)) toggleSourceMenu(false);
});
document.addEventListener("keydown", (e) => {
  if (SRC.open && e.key === "Escape") {
    toggleSourceMenu(false);
    srcEls.btn.focus();
  }
});

/** Slower playback keeping the pitch, so the chords still match what you play. */
function setProjRate(pct) {
  // two sliders (transport and chord sheet) drive the same playback rate
  for (const [slider, label] of [[els.projRate, els.projRateVal], [$("sheetRate"), $("sheetRateVal")]]) {
    slider.value = String(pct);
    label.textContent = `${pct}%`;
    label.classList.toggle("slowed", pct < 100);
    slider.style.setProperty("--fill", `${((pct - Number(slider.min)) / (Number(slider.max) - Number(slider.min))) * 100}%`);
  }
  if (projWave) projWave.ws.setPlaybackRate(pct / 100, true);
}
els.projRate.addEventListener("input", () => setProjRate(Number(els.projRate.value)));
$("sheetRate").addEventListener("input", () => setProjRate(Number($("sheetRate").value)));
els.snapCheck.addEventListener("change", () => {
  if (projWave && projWave.draft) {
    snapDraft();
    showDraft();
  }
});

els.projNameInput.addEventListener("change", async () => {
  const name = els.projNameInput.value.trim();
  if (!name) {
    els.projNameInput.value = state.project.name;
    return;
  }
  try {
    state.project = await patchJson(projectUrl(), { name });
    renderBreadcrumbs();
  } catch (err) {
    banner(els.projectStatus, `Erro ao renomear: ${err.message}`, "error");
  }
});
els.projBpmInput.addEventListener("change", async () => {
  const bpm = Number(els.projBpmInput.value);
  if (!(bpm > 20 && bpm < 320)) {
    els.projBpmInput.value = state.project.detected_bpm;
    return;
  }
  try {
    state.project = await patchJson(projectUrl(), { detected_bpm: bpm });
  } catch (err) {
    banner(els.projectStatus, `Erro ao salvar o BPM: ${err.message}`, "error");
  }
});

// ---------- project: analysis (Dettato full song / Demucs stems) ----------

function renderAnalysis() {
  const p = state.project;
  const tracks = p.multitrack || [];
  const stems = Object.keys(p.stems || {});
  const pitched = tracks.filter((t) => !t.is_drum).length;
  const guitars = tracks.filter((t) => t.is_guitar).length;
  els.mtSummary.textContent = tracks.length
    ? `${pitched} instrumento(s)${tracks.some((t) => t.is_drum) ? " + bateria" : ""}, ${guitars} faixa(s) de guitarra.`
    : "Ainda não transcrita.";
  els.multitrackBtn.textContent = tracks.length ? "Transcrever de novo" : "Transcrever música inteira";
  const midiName = (p.artifacts || {}).multitrack_midi;
  if (midiName && tracks.length) {
    els.dlFullMid.href = fileUrl(midiName);
    show(els.dlFullMid);
  } else {
    hide(els.dlFullMid);
  }
  els.stemSummary.textContent = stems.length
    ? `${stems.map((s) => STEM_LABELS[s] || s).join(", ")}${p.separation_device === "bypass" ? " (pista já isolada)" : ""}.`
    : "Não separadas.";
  els.separateBtn.textContent = stems.length ? "Separar de novo" : "Separar stems";
  els.separateBtn.disabled = p.is_isolated_stem;

  const parts = [];
  if (tracks.length) parts.push("MIDI completo pronto");
  if (stems.length) parts.push("stems prontas");
  els.analysisSummary.textContent = parts.length ? `· ${parts.join(" · ")}` : "· falta analisar";
  // open while there is nothing to transcribe from, so the next step is obvious
  show($("tabAnalysisDot"), !tracks.length && !stems.length);

  show(els.mixerBox, tracks.length > 0);
  renderMixer();
  if (harmonyIsCurrent()) renderHarmony();  // new stems / MIDI: suggest re-analysing
}

function trackLabel(t) {
  const name = el("span", { class: "track-name" }, t.name);
  const tag = ROLE_TAGS[t.role];
  if (tag) name.append(el("span", { class: `role-badge role-${t.role}` }, tag));
  return name;
}
function trackMeta(t) {
  return el("span", { class: "track-meta" }, t.is_drum
    ? `${t.note_count} batidas`
    : `${t.note_count} notas · ${t.lowest_note || "?"}–${t.highest_note || "?"}`);
}

function renderMixer() {
  els.multitrackTracks.innerHTML = "";
  for (const t of state.project.multitrack || []) {
    const ms = el("span", { class: "track-ms" });
    for (const [kind, label, title] of [["mute", "M", "Silenciar no player"], ["solo", "S", "Ouvir só esta faixa"]]) {
      ms.append(el("button", {
        type: "button",
        class: `ms-btn ms-${kind}`,
        title,
        dataset: { track: String(t.index), kind },
      }, label));
    }
    els.multitrackTracks.append(el("div", { class: "track-row mixer-row" + (t.is_drum ? " is-drum" : "") }, trackLabel(t), trackMeta(t), ms));
  }
  mpLoadSong();
}

async function runMultitrack() {
  if (state.busy || !state.project) return;
  setBusy(true);
  els.multitrackBtn.disabled = true;
  openTab("analysis");
  banner(els.analysisStatus, onDevice(
    "Transcrevendo a música inteira com Dettato na GPU... você já pode marcar as partes enquanto isso.",
    "Transcrevendo a música inteira com Dettato na CPU... pode levar vários minutos. Você já pode marcar as partes."), "busy");
  const projectId = state.project.job_id;
  try {
    const data = await postJson(`/api/projects/${projectId}/multitrack`, { split_guitar_roles: els.splitRolesCheck.checked });
    if (state.project && state.project.job_id === projectId) {
      state.project = { ...data.job, parts: state.project.parts };
      renderAnalysis();
    }
    const noGuitar = data.default_tab_tracks.length === 0 ? " Nenhuma faixa foi identificada como guitarra: escolha as faixas em cada parte." : "";
    banner(els.analysisStatus, `Música transcrita (${data.info.device}).${noGuitar}`);
  } catch (err) {
    banner(els.analysisStatus, `Erro: ${err.message}`, "error");
  } finally {
    els.multitrackBtn.disabled = false;
    setBusy(false);
  }
}

async function runSeparate(model) {
  if (state.busy || !state.project) return;
  setBusy(true);
  els.separateBtn.disabled = true;
  openTab("analysis");
  banner(els.analysisStatus, onDevice(
    "Separando as stems com Demucs na GPU... você já pode marcar as partes enquanto isso.",
    "Separando as stems com Demucs na CPU... pode levar vários minutos. Você já pode marcar as partes."), "busy");
  const projectId = state.project.job_id;
  try {
    const data = await postJson(`/api/projects/${projectId}/separate`, { model: model || els.separateModelSelect.value });
    if (state.project && state.project.job_id === projectId) {
      state.project = { ...data.job, parts: state.project.parts };
      renderAnalysis();
      if (state.view === "project") setProjSource(projWave ? projWave.stem : "");
    }
    banner(els.analysisStatus, `Stems separadas (${data.device}). Escolha a stem em cada parte, ou ouça-a em "Áudio".`);
  } catch (err) {
    banner(els.analysisStatus, `Erro: ${err.message}`, "error");
  } finally {
    els.separateBtn.disabled = state.project ? state.project.is_isolated_stem : false;
    setBusy(false);
  }
}

els.multitrackBtn.addEventListener("click", () => runMultitrack());
els.separateBtn.addEventListener("click", () => runSeparate());

// ---------- project: harmony (key, harmonic field, chords) ----------

const H = {
  data: null,        // harmony.json of the open project
  projectId: null,
  nowIdx: -1,        // chord under the playhead
  highlight: null,   // root (pc) highlighted from the harmonic field
};
const hEls = {
  btn: $("harmonyBtn"),
  meta: $("harmonyMeta"),
  status: $("harmonyStatus"),
  body: $("harmonyBody"),
  keyName: $("hKeyName"),
  keyConf: $("hKeyConf"),
  keyConfBar: $("hKeyConfBar"),
  scale: $("hScale"),
  keyCharacter: $("hKeyCharacter"),
  keyExtra: $("hKeyExtra"),
  live: $("liveChord"),
  liveHint: $("liveHint"),
  liveAnalyzeBtn: $("liveAnalyzeBtn"),
  liveKey: $("liveKey"),
  liveChordName: $("liveChordName"),
  liveNumeral: $("liveNumeral"),
  liveFn: $("liveFn"),
  liveProgress: $("liveProgress"),
  liveInfo: $("liveInfo"),
  liveNext: $("liveNext"),
  liveNextNum: $("liveNextNum"),
  field: $("hField"),
  loops: $("hLoops"),
  parts: $("hParts"),
  partsSection: $("hPartsSection"),
  outside: $("hOutside"),
  outsideSection: $("hOutsideSection"),
  lane: $("chordLane"),
  laneInner: $("chordLaneInner"),
};
const FN_NAMES = { T: "tônica", S: "subdominante", D: "dominante" };

function chordFunction(c) {
  if (!c.diatonic || !c.degree) return "X";
  return H.data.field[c.degree - 1].function;
}

async function loadHarmony() {
  const projectId = state.project.job_id;
  H.data = null;
  H.projectId = projectId;
  H.highlight = null;
  H.loaded = false;
  hide(hEls.status);
  renderHarmony();
  try {
    const data = await api(projectUrl("/harmony"));
    if (H.projectId !== projectId) return;
    H.data = data.harmony;
    H.loaded = true;
    renderHarmony();
  } catch (err) {
    banner(hEls.status, `Erro ao carregar a harmonia: ${err.message}`, "error");
  }
}

async function runHarmony() {
  if (state.busy || !state.project) return;
  setBusy(true);
  hEls.btn.disabled = true;
  banner(hEls.status, "Analisando a harmonia da música inteira... costuma levar de 10 a 40 segundos.", "busy");
  const projectId = state.project.job_id;
  try {
    const data = await postJson(`/api/projects/${projectId}/harmony`);
    if (H.projectId === projectId) {
      H.data = data.harmony;
          H.highlight = null;
      hide(hEls.status);
      renderHarmony();
      if (L.data && L.data.lyrics) loadLyrics();  // new chords for the sheet
    }
  } catch (err) {
    banner(hEls.status, `Erro: ${err.message}`, "error");
  } finally {
    hEls.btn.disabled = false;
    setBusy(false);
  }
}
hEls.btn.addEventListener("click", () => runHarmony());
hEls.liveAnalyzeBtn.addEventListener("click", () => runHarmony());

/** Stems / full MIDI that appeared after the analysis would make it more precise. */
function harmonyStaleReason(h) {
  const p = state.project;
  const harmonicStems = Object.keys(p.stems || {}).filter((s) => s !== "drums" && s !== "vocals");
  const reasons = [];
  if (!(h.stems || []).length && harmonicStems.length && !p.is_isolated_stem) reasons.push("as stems");
  if (!h.used_midi && hasMultitrack()) reasons.push("o MIDI completo");
  return reasons.length ? `Agora ${reasons.join(" e ")} já existe(m): analise de novo para um resultado mais preciso.` : "";
}

function renderHarmony() {
  const h = H.data;
  if (h && !harmonyIsCurrent()) return;
  show(hEls.body, !!h);
  // the hint waits for the GET, so it does not flash while the saved analysis loads
  show(hEls.liveHint, !h && H.loaded);
  show($("tabHarmonyDot"), (!h && H.loaded) || (!!h && !!harmonyStaleReason(h)));
  hEls.meta.classList.remove("warn-text");
  hEls.btn.textContent = h ? "Analisar de novo" : "Analisar harmonia";
  if (!h) {
    hEls.meta.textContent = hasStems() || hasMultitrack()
      ? "Descobre o tom, o campo harmônico e os acordes da música."
      : "Descobre o tom, o campo harmônico e os acordes. Fica mais preciso depois de separar as stems (na aba \"MIDI\").";
    renderChordUIs();
    return;
  }
  const src = h.used_midi ? `${h.source} + MIDI Dettato` : h.source;
  const stale = harmonyStaleReason(h);
  hEls.meta.textContent = stale || `Fonte: ${src} · ${h.stats.distinct_chords} acordes diferentes · ${Math.round(h.stats.diatonic_share * 100)}% do tempo dentro do campo.`;
  hEls.meta.classList.toggle("warn-text", !!stale);
  renderKey(h);
  renderField(h);
  renderLoops(h);
  renderOutside(h);
  renderHarmonyParts();
  renderChordUIs();
}

function renderKey(h) {
  const k = h.key;
  hEls.keyName.textContent = k.name;
  const pct = Math.round(k.confidence * 100);
  hEls.keyConfBar.style.width = `${pct}%`;
  hEls.keyConf.textContent = `Confiança ${pct}%${pct < 50 && k.alternatives.length ? ` · também pode ser ${k.alternatives.join(" ou ")}` : ""}`;
  hEls.scale.innerHTML = "";
  k.scale.forEach((n, i) => hEls.scale.append(el("span", { class: "scale-note" + (i === 0 ? " tonic" : "") }, n)));
  hEls.keyCharacter.textContent = `${k.character} ${k.characteristic}`;
  const extra = [];
  if (k.relative) extra.push(`Relativa: ${k.relative} (mesmas notas, outra "casa").`);
  if (Math.abs(h.tuning_cents) >= 10) {
    extra.push(`A gravação está ${h.tuning_cents > 0 ? "+" : ""}${h.tuning_cents} cents fora do Lá 440. Ajuste a afinação para tocar junto.`);
  }
  hEls.keyExtra.textContent = extra.join(" ");
}

function renderField(h) {
  hEls.field.innerHTML = "";
  const maxShare = Math.max(0.01, ...h.field.map((f) => f.share));
  for (const f of h.field) {
    const card = el("button", {
      type: "button",
      class: `field-card fn-${f.function}` + (f.share < 0.01 ? " unused" : "") + (H.highlight === f.root ? " active" : ""),
      title: `${f.function_pt}${f.characteristic ? ". Acorde característico do modo." : ""}`,
      onclick: () => {
        H.highlight = H.highlight === f.root ? null : f.root;
        renderField(h);
        renderChordLane(CU.project);
        renderChordLane(CU.part);
      },
    },
      el("span", { class: "field-numeral" }, f.numeral),
      el("strong", { class: "field-chord" }, f.triad),
      el("span", { class: "field-tetrad muted" }, f.tetrad),
      el("span", { class: "field-fn" }, FN_NAMES[f.function]),
      el("span", { class: "usage" }, el("span", { style: `width:${Math.round((f.share / maxShare) * 100)}%` })),
      el("span", { class: "muted small" }, f.share >= 0.01 ? `${Math.round(f.share * 100)}%` : "não aparece"));
    if (f.characteristic) card.append(el("span", { class: "badge char-badge" }, "característico"));
    hEls.field.append(card);
  }
}

function renderLoops(h) {
  hEls.loops.innerHTML = "";
  if (!h.loops.length) {
    hEls.loops.append(el("p", { class: "muted" }, "Nenhuma sequência clara se repete (ou a detecção variou demais)."));
    return;
  }
  for (const loop of h.loops) {
    const seq = el("span", { class: "loop-seq" });
    loop.chords.forEach((c, i) => {
      if (i) seq.append(el("span", { class: "loop-arrow" }, "→"));
      seq.append(el("span", { class: "loop-chord" }, el("strong", {}, c), el("small", {}, loop.numerals[i])));
    });
    hEls.loops.append(el("button", {
      type: "button",
      class: "loop-row",
      title: "Ouvir a primeira ocorrência em loop",
      onclick: () => playSelectionLoop({
        kind: "range", name: `Progressão ${loop.chords.join(" → ")}`,
        start: loop.starts[0], end: loop.ends[0], color: RANGE_COLOR,
      }),
    }, seq, el("span", { class: "badge" }, `${loop.count}×`), el("span", { class: "muted small" }, `1ª vez em ${fmtClock(loop.starts[0], 0)}`)));
  }
}

function renderOutside(h) {
  hEls.outside.innerHTML = "";
  show(hEls.outsideSection, h.outside.length > 0);
  for (const o of h.outside.slice(0, 8)) {
    hEls.outside.append(el("button", {
      type: "button",
      class: "outside-row",
      title: "Ir para a primeira vez que ele aparece",
      onclick: () => projWave && projWave.ws.setTime(o.first),
    },
      el("span", { class: "outside-chord" }, el("strong", {}, o.label), el("small", {}, o.numeral)),
      el("span", { class: "outside-text" }, o.explain),
      el("span", { class: "muted small" }, `${o.count}× · ${o.seconds.toFixed(1)} s`)));
  }
}

/** Chords inside [start, end): consecutive repeats merged. */
function chordsBetween(start, end) {
  const out = [];
  for (const c of H.data.chords) {
    if (c.end <= start + 0.05 || c.start >= end - 0.05) continue;
    const prev = out[out.length - 1];
    if (prev && prev.label === c.label) continue;
    out.push(c);
  }
  return out;
}

function harmonyIsCurrent() {
  return !!H.data && !!state.project && H.projectId === state.project.job_id;
}

function renderHarmonyParts() {
  if (!harmonyIsCurrent()) return;
  const parts = state.project.parts;
  hEls.parts.innerHTML = "";
  show(hEls.partsSection, parts.length > 0);
  for (const part of parts) {
    const chords = chordsBetween(part.start, part.end);
    const seq = el("span", { class: "hpart-seq" });
    chords.slice(0, 16).forEach((c) => {
      seq.append(el("span", { class: `mini-chord fn-${chordFunction(c)}`, title: c.numeral }, c.label));
    });
    if (chords.length > 16) seq.append(el("span", { class: "muted" }, `+${chords.length - 16}`));
    if (!chords.length) seq.append(el("span", { class: "muted" }, "sem acordes detectados"));
    hEls.parts.append(el("div", { class: "hpart-row" },
      el("span", { class: "part-dot", style: `background:${partColor(part)}` }),
      el("strong", { class: "part-name" }, part.name),
      seq));
  }
}

/** Chord lane + live chord strip of one view; the song page and the part page each have one. */
function chordUI(prefix, getWave) {
  const id = (n) => $(prefix ? prefix + n[0].toUpperCase() + n.slice(1) : n);
  return {
    getWave,
    nowIdx: -1,
    lane: id("chordLane"),
    laneInner: id("chordLaneInner"),
    live: id("liveChord"),
    liveKey: id("liveKey"),
    liveChordName: id("liveChordName"),
    liveNumeral: id("liveNumeral"),
    liveFn: id("liveFn"),
    liveProgress: id("liveProgress"),
    liveInfo: id("liveInfo"),
    liveNext: id("liveNext"),
    liveNextNum: id("liveNextNum"),
  };
}
const CU = {
  project: chordUI("", () => projWave),
  part: chordUI("part", () => partWave),
};

/** Lane, key and live strip of both views from the current harmony (or hide them). */
function renderChordUIs() {
  const h = harmonyIsCurrent() ? H.data : null;
  for (const ctx of Object.values(CU)) {
    show(ctx.lane, !!h);
    show(ctx.live, !!h);
    ctx.nowIdx = -1;
    if (h) ctx.liveKey.textContent = h.key.name;
    renderChordLane(ctx);
    const w = ctx.getWave();
    updateNowChord(w ? w.ws.getCurrentTime() : 0, true, ctx);
  }
  show($("partHarmonyHint"), !h && H.loaded);
}

function renderChordLane(ctx = CU.project) {
  const h = harmonyIsCurrent() ? H.data : null;
  ctx.laneInner.innerHTML = "";
  if (!h) return;
  const dur = (state.peaks && state.peaks.duration) || state.project.duration || 1;
  h.chords.forEach((c, i) => {
    const fn = chordFunction(c);
    const dim = H.highlight !== null && c.root !== H.highlight;
    const tip = [c.label, c.numeral, fn !== "X" ? FN_NAMES[fn] : "fora do campo", c.implied ? `representa ${c.implied}` : ""]
      .filter(Boolean).join(" · ") + (c.explain ? `\n${c.explain}` : "");
    ctx.laneInner.append(el("button", {
      type: "button",
      class: `lane-chord fn-${fn}` + (dim ? " dim" : "") + (i === ctx.nowIdx ? " now" : ""),
      style: `left:${(c.start / dur) * 100}%;width:${((c.end - c.start) / dur) * 100}%`,
      title: tip,
      dataset: { idx: String(i) },
      onclick: () => {
        const w = ctx.getWave();
        if (w) w.ws.setTime(c.start + 0.01);
      },
    }, c.label));
  });
  syncChordLane(ctx);
}

/** Follow the waveform's zoom and horizontal scroll. */
function syncChordLane(ctx = CU.project) {
  const w = ctx.getWave();
  if (!w || !H.data) return;
  const wrap = w.ws.getWrapper();
  const scroller = wrap && wrap.parentElement;
  if (!wrap) return;
  ctx.laneInner.style.width = `${wrap.scrollWidth}px`;
  ctx.laneInner.style.transform = `translateX(${-(scroller ? scroller.scrollLeft : 0)}px)`;
}

function chordIndexAt(t) {
  const chords = H.data.chords;
  let lo = 0;
  let hi = chords.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (t < chords[mid].start) hi = mid - 1;
    else if (t >= chords[mid].end) lo = mid + 1;
    else return mid;
  }
  return -1;
}

/** Live strip under the waveform: chord now (grau, função), time left on it, next chord. */
function updateNowChord(t, force = false, ctx = CU.project) {
  if (!harmonyIsCurrent()) return;
  const chords = H.data.chords;
  const idx = chordIndexAt(t);
  // in a silence between chords, show what is coming
  const nextIdx = idx >= 0 ? idx + 1 : chords.findIndex((c) => c.start > t);
  const cur = idx >= 0 ? chords[idx] : null;
  ctx.liveProgress.style.width = cur ? `${Math.min(100, ((t - cur.start) / (cur.end - cur.start)) * 100)}%` : "0%";
  if (idx === ctx.nowIdx && !force) return;

  const old = ctx.laneInner.querySelector(".lane-chord.now");
  if (old) old.classList.remove("now");
  ctx.nowIdx = idx;
  const next = nextIdx >= 0 ? chords[nextIdx] : null;
  ctx.liveNext.textContent = next ? next.label : "–";
  ctx.liveNext.className = `live-next-name${next ? ` fn-${chordFunction(next)}` : ""}`;
  ctx.liveNextNum.textContent = next ? next.numeral : "";
  if (!cur) {
    ctx.liveChordName.textContent = "–";
    ctx.liveChordName.className = "live-chord-name";
    ctx.liveNumeral.textContent = "";
    ctx.liveFn.textContent = "";
    ctx.liveInfo.textContent = force ? "Dê play para acompanhar os acordes." : "Sem acorde (pausa ou só bateria).";
    ctx.live.className = "live-chord";
    return;
  }
  const node = ctx.laneInner.querySelector(`[data-idx="${idx}"]`);
  if (node) node.classList.add("now");
  const fn = chordFunction(cur);
  ctx.live.className = `live-chord fn-${fn}`;
  ctx.liveChordName.textContent = cur.label;
  ctx.liveChordName.className = `live-chord-name fn-${fn}`;
  ctx.liveNumeral.textContent = cur.numeral;
  ctx.liveNumeral.className = `now-numeral fn-${fn}`;
  ctx.liveFn.textContent = fn !== "X" ? FN_NAMES[fn] : "fora do campo";
  const info = [];
  if (cur.implied) info.push(`Power chord (sem terça): aqui representa o ${cur.implied}.`);
  if (cur.explain) info.push(cur.explain);
  else if (fn !== "X") info.push(H.data.field[cur.degree - 1].function_pt + ".");
  ctx.liveInfo.textContent = info.join(" ");
}

// ---------- project: lyrics + chord sheet ----------

const L = {
  data: null,       // {lyrics, sheet, has_harmony}
  projectId: null,
  loaded: false,
};
const lEls = {
  status: $("lyricsStatus"),
  editor: $("lyricsEditor"),
  text: $("lyricsText"),
  lang: $("lyricsLang"),
  model: $("lyricsModel"),
  alignBtn: $("lyricsAlignBtn"),
  draftBtn: $("lyricsDraftBtn"),
  cancelBtn: $("lyricsCancelBtn"),
  hint: $("lyricsHint"),
  view: $("lyricsView"),
  meta: $("lyricsMeta"),
  noHarmony: $("sheetNoHarmony"),
  sheet: $("sheet"),
  filter: $("sheetFilter"),
  numerals: $("sheetNumerals"),
  follow: $("sheetFollow"),
  editBtn: $("lyricsEditBtn"),
  playBtn: $("sheetPlayBtn"),
  time: $("sheetTime"),
};

function lyricsIsCurrent() {
  return !!state.project && L.projectId === state.project.job_id;
}

async function loadLyrics() {
  const projectId = state.project.job_id;
  L.projectId = projectId;
  L.data = null;
  L.loaded = false;
  L.editing = false;  // opening a project always shows the synced sheet, never a leftover edit
  hide(lEls.status);
  lEls.text.value = "";
  resetGenius();
  renderLyrics();
  try {
    const data = await api(projectUrl("/lyrics"));
    if (L.projectId !== projectId) return;
    L.data = data;
    L.loaded = true;
    $("lyricsSaved").textContent = "";
    if (data.draft && !data.lyrics) {
      lEls.text.value = data.draft.text;
      setLang(data.draft.language);
    }
    applyLyricsPrefill(projectId);
    renderLyrics();
  } catch (err) {
    banner(lEls.status, `Erro ao carregar a letra: ${err.message}`, "error");
  }
}

/** Lyrics chosen in the creation pipeline: fill the box and open the tab, ready to align. */
function applyLyricsPrefill(projectId) {
  if (L.openLyrics !== projectId) return;
  L.openLyrics = null;
  if (L.data && L.data.lyrics) openTab("lyrics");
}

async function runLyrics(useText) {
  if (state.busy || !state.project) return;
  const text = useText ? lEls.text.value : "";
  if (useText && !text.trim()) {
    banner(lEls.status, "Cole a letra na caixa (ou use \"Sem letra? Transcrever a voz\").", "error");
    lEls.text.focus();
    return;
  }
  setBusy(true);
  lEls.alignBtn.disabled = lEls.draftBtn.disabled = true;
  banner(lEls.status, onDevice(
    "Ouvindo a voz com o Whisper e alinhando a letra... leva alguns minutos (na primeira vez o modelo é baixado).",
    "Ouvindo a voz com o Whisper na CPU... pode levar bem mais que 10 minutos."), "busy");
  const projectId = state.project.job_id;
  try {
    const data = await postJson(`/api/projects/${projectId}/lyrics`, {
      text, language: lEls.lang.value || null, model: lEls.model.value,
    });
    if (L.projectId === projectId) {
      L.data = data;
      L.loaded = true;
      L.editing = false;
      hide(lEls.status);
      renderLyrics();
    }
  } catch (err) {
    banner(lEls.status, `Erro: ${err.message}`, "error");
  } finally {
    lEls.alignBtn.disabled = lEls.draftBtn.disabled = false;
    setBusy(false);
  }
}
lEls.alignBtn.addEventListener("click", () => runLyrics(true));
lEls.draftBtn.addEventListener("click", () => runLyrics(false));
lEls.editBtn.addEventListener("click", () => {
  L.editing = true;
  lEls.text.value = (L.data.draft && L.data.draft.text) || L.data.lyrics.text;
  renderLyrics();
  lEls.text.focus();
});
lEls.cancelBtn.addEventListener("click", () => {
  L.editing = false;
  renderLyrics();
});

// ---------- lyrics draft: the text is saved in the project before it is aligned ----------

const LD = { timer: null, seq: 0 };

function setLang(code) {
  if (code && [...lEls.lang.options].some((o) => o.value === code)) lEls.lang.value = code;
}

/** Saves the lyrics box now (source: where it came from, e.g. "Genius: X · Y"). */
async function saveLyricsDraft(source) {
  clearTimeout(LD.timer);
  if (!state.project || !lyricsIsCurrent()) return;
  const projectId = state.project.job_id;
  const seq = ++LD.seq;
  const text = lEls.text.value;
  const prev = L.data && L.data.draft;
  const body = { text, language: lEls.lang.value || null, source: source !== undefined ? source : (prev && prev.source) || null };
  const saved = $("lyricsSaved");
  saved.textContent = "salvando...";
  try {
    const { draft } = await sendJson("PUT", `/api/projects/${projectId}/lyrics/draft`, body);
    if (L.data && L.projectId === projectId) L.data.draft = draft;
    if (seq === LD.seq) saved.textContent = draft ? "✓ rascunho salvo no projeto" : "";
  } catch (err) {
    if (seq === LD.seq) saved.textContent = `não foi possível salvar: ${err.message}`;
  }
}

/** Typing: save a moment after the last key. */
function scheduleLyricsDraft() {
  clearTimeout(LD.timer);
  $("lyricsSaved").textContent = "";
  LD.timer = setTimeout(() => saveLyricsDraft(), 800);
}
lEls.text.addEventListener("input", scheduleLyricsDraft);
lEls.lang.addEventListener("change", () => { if (lEls.text.value.trim()) saveLyricsDraft(); });

// ---------- lyrics: tag each stanza with its part (modal) ----------

const TAG_RE = /^\s*\[([^\]]{1,60})\]\s*$/;
const TAG_PRESETS = ["Intro", "Verso", "Pré-refrão", "Refrão", "Pós-refrão", "Ponte", "Solo", "Instrumental", "Interlúdio", "Outro"];
const T = {
  stanzas: [],   // [{tag: string|null, lines: [string]}]
  cur: -1,       // selected stanza
};
const tEls = {
  dialog: $("tagDialog"),
  palette: $("tagPalette"),
  list: $("tagStanzas"),
  count: $("tagCount"),
  summary: $("tagSummary"),
};

/** Text -> stanzas: blank lines split them, a "[Tag]" line names the stanza below it. */
function parseStanzas(text) {
  const out = [];
  let cur = null;
  const close = () => {
    if (cur && (cur.lines.length || cur.tag)) out.push(cur);
    cur = null;
  };
  for (const raw of text.replace(/\r\n/g, "\n").split("\n")) {
    const m = raw.match(TAG_RE);
    if (m) {
      if (cur && cur.lines.length) close();
      cur = { tag: m[1].trim(), lines: cur ? cur.lines : [] };
      continue;
    }
    if (!raw.trim()) {
      close();  // a tag alone ("[Instrumental]") is a stanza of its own
      continue;
    }
    if (!cur) cur = { tag: null, lines: [] };
    cur.lines.push(raw.trim());
  }
  close();
  return out;
}

function stanzasToText(stanzas) {
  return stanzas.map((s) => [s.tag ? `[${s.tag}]` : null, ...s.lines].filter((x) => x !== null).join("\n")).join("\n\n");
}

/** "Verso 3" when verses 1 and 2 are taken (by stanzas other than the selected one). */
function nextVerseLabel() {
  let max = 0;
  T.stanzas.forEach((s, i) => {
    if (i === T.cur || !s.tag) return;
    const m = s.tag.match(/^(?:verso|verse)\s*(\d+)?/i);
    if (m) max = Math.max(max, m[1] ? Number(m[1]) : 1);
  });
  return `Verso ${max + 1}`;
}

/** opts: {text, times: [line start s], onSave(text)}; default = the lyrics box. */
function openTagDialog(opts = {}) {
  T.onSave = opts.onSave || null;
  T.stanzas = parseStanzas(opts.text !== undefined ? opts.text : lEls.text.value);
  // the time each stanza starts (the n-th sung line of the text is the n-th timed line)
  if (opts.times) {
    let k = 0;
    for (const s of T.stanzas) {
      s.time = s.lines.length ? opts.times[k] : null;
      k += s.lines.length;
    }
  }
  if (!T.stanzas.length) {
    banner(lEls.status, "Cole ou pesquise a letra primeiro: as estrofes aparecem aqui para você marcar.", "error");
    return;
  }
  T.cur = Math.max(0, T.stanzas.findIndex((s) => !s.tag));
  renderTagDialog();
  tEls.dialog.showModal();
  scrollToStanza();
}

function renderTagDialog() {
  // palette: presets for the selected stanza ("Verso" gets the next free number)
  tEls.palette.innerHTML = "";
  const sel = T.stanzas[T.cur];
  const used = [...new Set(T.stanzas.map((s) => s.tag).filter(Boolean))];
  const labels = TAG_PRESETS.map((t) => (t === "Verso" ? nextVerseLabel() : t));
  // parts already used that are not presets (e.g. "Verse 1" from Genius, "Refrão 2") can be reused too
  for (const u of used) if (!labels.includes(u) && !/^(verso|verse)\b/i.test(u)) labels.push(u);
  for (const label of labels) {
    tEls.palette.append(el("button", {
      type: "button", class: "chip tag-chip" + (sel && sel.tag === label ? " is-active" : ""),
      onclick: () => setStanzaTag(label),
      disabled: !sel,
    }, label));
  }
  const custom = el("input", { type: "text", class: "tag-custom", maxlength: "40", placeholder: "Outra...", "aria-label": "Outro nome de parte" });
  custom.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && custom.value.trim()) {
      e.preventDefault();
      setStanzaTag(custom.value.trim());
    }
  });
  tEls.palette.append(custom,
    el("button", { type: "button", class: "chip tag-chip tag-none", onclick: () => setStanzaTag(null), disabled: !sel }, "Sem tag"));

  // stanzas
  tEls.list.innerHTML = "";
  T.stanzas.forEach((s, i) => {
    const item = el("li", {},
      el("button", {
        type: "button", class: "tag-stanza" + (i === T.cur ? " is-selected" : "") + (s.tag ? "" : " is-untagged"),
        "aria-pressed": i === T.cur ? "true" : "false",
        onclick: () => { T.cur = i; renderTagDialog(); },
      },
      el("span", { class: "tag-side" },
        el("span", { class: "tag-pill" + (s.tag ? ` ${tagTone(s.tag)}` : "") }, s.tag || "sem tag"),
        s.time !== undefined && s.time !== null ? el("span", { class: "tag-time muted small" }, fmtClock(s.time, 0)) : null),
      el("span", { class: "tag-lines" }, s.lines.length ? s.lines.join("\n") : "(sem letra: só a marcação)")));
    tEls.list.append(item);
  });
  const tagged = T.stanzas.filter((s) => s.tag).length;
  tEls.count.textContent = `${tagged} de ${T.stanzas.length} estrofes marcadas`;
}

/** A colour family per kind of part, so the structure is readable at a glance. */
function tagTone(tag) {
  const t = tag.toLowerCase();
  if (/^(refr|chorus|hook)/.test(t)) return "tone-chorus";
  if (/^(pr[eé]|pre)/.test(t)) return "tone-pre";
  if (/^(verso|verse)/.test(t)) return "tone-verse";
  if (/^(ponte|bridge)/.test(t)) return "tone-bridge";
  if (/^(intro|outro|final|end)/.test(t)) return "tone-edge";
  return "tone-other";
}

function setStanzaTag(tag) {
  const s = T.stanzas[T.cur];
  if (!s) return;
  s.tag = tag;
  // move on to the next stanza still without a tag (or simply the next one)
  const nextUntagged = T.stanzas.findIndex((x, i) => i > T.cur && !x.tag);
  if (nextUntagged >= 0) T.cur = nextUntagged;
  else if (T.cur < T.stanzas.length - 1) T.cur += 1;
  renderTagDialog();
  scrollToStanza();
}

function scrollToStanza() {
  const btn = tEls.list.querySelectorAll(".tag-stanza")[T.cur];
  if (btn) btn.scrollIntoView({ block: "nearest" });
}

function updateTagSummary() {
  const stanzas = parseStanzas(lEls.text.value);
  const tagged = stanzas.filter((s) => s.tag).length;
  tEls.summary.textContent = stanzas.length ? `${tagged} de ${stanzas.length} estrofes com parte marcada` : "";
}

$("tagOpenBtn").addEventListener("click", () => openTagDialog());
$("tagSaveBtn").addEventListener("click", () => {
  if (T.onSave) {
    const text = stanzasToText(T.stanzas);
    tEls.dialog.close();
    T.onSave(text);
    return;
  }
  lEls.text.value = stanzasToText(T.stanzas);
  tEls.dialog.close();
  updateTagSummary();
  saveLyricsDraft();
});
for (const id of ["tagCancelBtn", "tagCloseBtn"]) $(id).addEventListener("click", () => tEls.dialog.close());
$("tagClearBtn").addEventListener("click", () => {
  T.stanzas.forEach((s) => { s.tag = null; });
  T.cur = 0;
  renderTagDialog();
});
lEls.text.addEventListener("input", updateTagSummary);

// ---------- lyrics search on Genius (fills the lyrics box; the user still aligns it) ----------

const gEls = {
  form: $("geniusForm"),
  query: $("geniusQuery"),
  btn: $("geniusBtn"),
  status: $("geniusStatus"),
  results: $("geniusResults"),
};
let geniusSeq = 0;  // drops answers of an older search / another project

/** "01 - Il Sussurro dei Draghi v4" -> "Il Sussurro dei Draghi" */
/** "01 - Il Sussurro dei Draghi v4.wav" -> "Il Sussurro dei Draghi v4": no extension, no track number
 * ("01 - ", "1 - ", "01. ", "1) "...). A number without a separator stays: "7 Rings", "1984". */
function songNameFromFile(filename) {
  const base = filename.replace(/\.[^.]+$/, "").replace(/_+/g, " ").trim();
  return base.replace(/^\d{1,3}\s*[-–—.)]+\s*/, "").trim() || base;
}

function geniusQueryFromName(name) {
  return (name || "").replace(/^\s*\d+\s*[-._)]\s*/, "").replace(/\s+v\d+\s*$/i, "").trim();
}

function resetGenius() {
  geniusSeq++;
  gEls.query.value = state.project ? geniusQueryFromName(state.project.name) : "";
  gEls.results.innerHTML = "";
  hide(gEls.results);
  hide(gEls.status);
  gEls.btn.disabled = false;
}

async function searchGenius() {
  const q = gEls.query.value.trim();
  if (!q) {
    gEls.query.focus();
    return;
  }
  const seq = ++geniusSeq;
  gEls.btn.disabled = true;
  hide(gEls.results);
  banner(gEls.status, "Pesquisando no Genius...", "busy");
  try {
    const { results } = await api(`/api/lyrics/search?q=${encodeURIComponent(q)}`);
    if (seq !== geniusSeq) return;
    if (!results.length) {
      banner(gEls.status, "Nada encontrado. Tente outro nome ou inclua o artista.", "error");
      return;
    }
    hide(gEls.status);
    renderGeniusResults(results);
  } catch (err) {
    if (seq === geniusSeq) banner(gEls.status, `Erro: ${err.message}`, "error");
  } finally {
    if (seq === geniusSeq) gEls.btn.disabled = false;
  }
}

function renderGeniusResults(results) {
  gEls.results.innerHTML = "";
  for (const r of results) {
    const meta = [r.artist, r.year].filter(Boolean).join(" · ");
    const pending = r.lyrics_state && r.lyrics_state !== "complete";
    gEls.results.append(el("li", {},
      el("button", { type: "button", class: "genius-item", onclick: (e) => pickGenius(r, e.currentTarget) },
        r.thumbnail ? el("img", { src: r.thumbnail, alt: "", loading: "lazy", width: "44", height: "44" }) : el("span", { class: "genius-noart" }, "♪"),
        el("span", { class: "genius-text" },
          el("strong", {}, r.title),
          el("span", { class: "muted small" }, meta + (pending ? " · letra incompleta" : ""))),
        el("span", { class: "genius-use small" }, "Usar"))));
  }
  show(gEls.results);
}

async function pickGenius(r, btn) {
  const seq = ++geniusSeq;
  gEls.results.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  btn.classList.add("is-loading");
  banner(gEls.status, `Buscando a letra de "${r.title}"...`, "busy");
  try {
    const data = await api(`/api/lyrics/genius/${r.id}`);
    if (seq !== geniusSeq) return;
    lEls.text.value = data.text;
    updateTagSummary();
    setLang(data.language);
    saveLyricsDraft(`Genius: ${data.title} · ${data.artist}`);
    gEls.results.innerHTML = "";
    hide(gEls.results);
    banner(gEls.status, `Letra de "${data.title}" (${data.artist}) carregada do Genius. Confira e clique em "Alinhar com a voz".`);
    lEls.text.scrollTop = 0;
    lEls.text.focus({ preventScroll: true });
  } catch (err) {
    if (seq !== geniusSeq) return;
    banner(gEls.status, `Erro: ${err.message}`, "error");
    gEls.results.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    btn.classList.remove("is-loading");
  }
}

gEls.form.addEventListener("submit", (e) => {
  e.preventDefault();
  searchGenius();
});
lEls.playBtn.addEventListener("click", () => els.projPlayBtn.click());

/** Sheet options (speed, filter, numerals, follow) stay folded once the lyrics are synced. */
function setSheetOpts(open, remember = true) {
  show($("sheetOpts"), open);
  const btn = $("sheetOptsBtn");
  btn.setAttribute("aria-expanded", String(open));
  btn.classList.toggle("is-open", open);
  if (remember) {
    try {
      localStorage.setItem("escritab.sheetOpts", open ? "1" : "0");
    } catch {
      /* blocked storage */
    }
  }
}
$("sheetOptsBtn").addEventListener("click", () => setSheetOpts($("sheetOpts").classList.contains("hidden")));
try {
  setSheetOpts(localStorage.getItem("escritab.sheetOpts") === "1", false);
} catch {
  setSheetOpts(false, false);
}
lEls.numerals.addEventListener("change", () => renderSheet(SU.project));
lEls.filter.addEventListener("change", () => renderSheet(SU.project));

function lyricsHintText() {
  const p = state.project;
  const parts = [];
  if (!p.stems.vocals && !p.is_isolated_stem) {
    parts.push("Sem a stem de vocais o Whisper ouve a música inteira e erra mais: separe as stems na aba \"MIDI\" antes.");
  }
  parts.push("Mantenha uma linha por verso; tags como [Verse] e [Chorus] viram títulos de seção.");
  parts.push(`Leva uns ${Math.max(1, Math.round((p.duration / 60) * 0.5))}–${Math.max(2, Math.round((p.duration / 60) * 0.6))} min com GPU. Na primeira vez o modelo é baixado (~1,5 GB).`);
  return parts.join(" ");
}

function renderLyrics() {
  if (!lyricsIsCurrent()) return;
  renderLyricOverlay();
  const d = L.data;
  const has = !!(d && d.lyrics);
  const editing = !has || L.editing;
  show(lEls.editor, L.loaded && editing);
  show(lEls.view, has && !L.editing);
  show(lEls.cancelBtn, has && L.editing);
  // editing synced lyrics starts from them, not from an empty box
  if (has && L.editing && !lEls.text.value.trim()) lEls.text.value = d.lyrics.text;
  if (editing) updateTagSummary();
  show($("lyricsIntro"), editing);  // the "how it works" line only while setting it up
  lEls.alignBtn.textContent = has ? "Alinhar de novo" : "Alinhar com a voz";
  lEls.hint.textContent = lyricsHintText();
  if (!has) {
    SU.project.rows = [];
    lEls.sheet.innerHTML = "";
    renderPartSheet();
    return;
  }
  const ly = d.lyrics;
  const share = Math.round(ly.stats.matched_share * 100);
  lEls.meta.textContent = (ly.mode === "whisper"
    ? "Rascunho do Whisper (pode ter palavras erradas): corrija em \"Editar letra\" e alinhe de novo. "
    : `${share}% das palavras reconhecidas na voz. `)
    + `${ly.stats.lines} linhas · Whisper ${ly.model} (${ly.device === "cuda" ? "GPU" : "CPU"}) · ${ly.source}.`;
  show(lEls.noHarmony, !d.has_harmony);
  renderStructure();
  renderSheetFilter();
  renderSheet(SU.project);
  renderPartSheet();
}

const SECTION_SOURCE = {
  inferred: "inferido pela música",
  tag: "da tag na letra",
  repeat: "mesmo texto de outra seção",
  gap: "pausa longa na voz",
  guess: "bloco sem tag",
};

/** Song structure inferred from the lyrics (tags, repeated blocks, pauses): click = loop it. */
function renderStructure() {
  const box = $("songStructure");
  const list = $("songStructureList");
  const sections = (L.data && L.data.sections) || [];
  show(box, sections.length > 0);
  list.innerHTML = "";
  if (!sections.length) return;
  const tagged = sections.some((s) => s.source === "tag" && s.kind === "sung");
  $("songStructureHint").textContent = (tagged
    ? "pelas tags da letra"
    : "inferida (sem tags: blocos repetidos viram refrão)")
    + (L.data.has_harmony ? " · limites nas trocas de acorde" : "");
  const saved = state.project.parts;
  for (const s of sections) {
    const exists = saved.some((p) => p.name === s.name && Math.abs(p.start - s.start) < 1 && Math.abs(p.end - s.end) < 1);
    list.append(el("button", {
      type: "button",
      class: `chip section-chip${s.kind === "instrumental" ? " is-instrumental" : ""}${exists ? " is-saved" : ""}`,
      title: `${fmtClock(s.start)}–${fmtClock(s.end)} · ${SECTION_SOURCE[s.source] || ""}${s.why ? ` (${s.why})` : ""}${exists ? " · já é uma parte" : ""}\nClique para ouvir em loop`,
      onclick: () => playSelectionLoop({ kind: "range", name: s.name, start: s.start, end: s.end, color: RANGE_COLOR }),
    }, s.name, s.source === "inferred" ? el("span", { class: "section-guess", "aria-label": "inferido" }, "≈") : null,
      el("small", {}, fmtClock(s.start, 0))));
  }
  $("sectionsToPartsBtn").disabled = sections.every((s) =>
    saved.some((p) => p.name === s.name && Math.abs(p.start - s.start) < 1 && Math.abs(p.end - s.end) < 1));
}

$("sectionsToPartsBtn").addEventListener("click", async () => {
  if (!state.project) return;
  const btn = $("sectionsToPartsBtn");
  btn.disabled = true;
  try {
    const data = await postJson(projectUrl("/parts/from-lyrics"), {});
    state.project = data.job;
    drawPartRegions();
    renderPartList();
    renderStructure();
    renderSheetFilter();
    const msgs = [];
    if (data.created) msgs.push(`${data.created} parte(s) criada(s)`);
    if (data.updated) msgs.push(`${data.updated} parte(s) ajustada(s) à estrutura nova`);
    banner(lEls.status, msgs.length
      ? `${msgs.join(" e ")} a partir da letra. Ajuste os limites na waveform se precisar.`
      : "Todas as seções já eram partes.");
  } catch (err) {
    banner(lEls.status, `Erro ao criar as partes: ${err.message}`, "error");
    btn.disabled = false;
  }
});

function renderSheetFilter() {
  if (!state.project) return;
  const cur = lEls.filter.value;
  lEls.filter.innerHTML = "";
  lEls.filter.append(el("option", { value: "" }, "Música inteira"));
  for (const part of state.project.parts) lEls.filter.append(el("option", { value: part.part_id }, `Só: ${part.name}`));
  lEls.filter.value = [...lEls.filter.options].some((o) => o.value === cur) ? cur : "";
}

/** Chord columns for the current label mode (numerals are longer than names: re-space them). */
function sheetChordCols(chords, useNumerals) {
  let next = 0;
  return chords.map((c) => {
    const text = useNumerals ? c.numeral : c.label;
    const col = Math.max(c.pos, next);
    next = col + text.length + 1;
    return { ...c, text, col };
  });
}

/** A rendered chord sheet: the song page shows all of it (or one part), the part page its excerpt. */
const SU = {
  project: {
    sheet: lEls.sheet, numerals: lEls.numerals, follow: lEls.follow, time: lEls.time,
    getWave: () => projWave,
    getRange: () => {
      const part = lEls.filter.value && state.project.parts.find((x) => x.part_id === lEls.filter.value);
      return part ? { start: part.start, end: part.end } : null;
    },
    rows: [], curRow: -1,
  },
  part: {
    sheet: $("partSheet"), numerals: $("partSheetNumerals"), follow: $("partSheetFollow"), time: null,
    getWave: () => partWave,
    getRange: () => {
      const w = partWave;
      if (w && w.excerpt && Number.isFinite(w.excerpt.start)) return { start: w.excerpt.start, end: w.excerpt.end };
      const part = currentPart();
      return part ? { start: part.start, end: part.end } : null;
    },
    rows: [], curRow: -1,
  },
};
SU.part.numerals.addEventListener("change", () => renderSheet(SU.part));

/** The part page's sheet card: the excerpt, or a hint when there are no lyrics yet. */
function renderPartSheet() {
  const card = $("partLyricsCard");
  const hint = $("partSheetHint");
  if (state.view !== "part" || !lyricsIsCurrent() || !L.loaded) {
    hide(card);
    return;
  }
  show(card);
  const has = !!(L.data && L.data.lyrics);
  show(SU.part.sheet, has);
  show(hint, !has || !L.data.has_harmony);
  if (!has) {
    hint.textContent = "Ainda não há letra nesta música. Cole a letra no card \"Letra e cifra\" da página da música para ver a cifra deste trecho aqui.";
    SU.part.rows = [];
    SU.part.sheet.innerHTML = "";
    return;
  }
  hint.textContent = "Analise a harmonia na página da música para ver os acordes em cima da letra.";
  renderSheet(SU.part);
}

function renderSheet(ctx = SU.project) {
  if (!L.data || !L.data.sheet) return;
  const useNumerals = ctx.numerals.checked;
  const range = ctx.getRange();
  const inRange = (it) => !range || (it.end > range.start + 0.05 && it.start < range.end - 0.05);
  ctx.sheet.innerHTML = "";
  ctx.rows = [];
  ctx.curRow = -1;
  const seek = (t) => {
    const w = ctx.getWave();
    if (w) w.ws.setTime(Math.max(0, t));
  };
  const items = L.data.sheet;
  items.forEach((it, i) => {
    if (it.kind === "section") {
      const nextRow = items.slice(i + 1).find((x) => x.kind !== "section");
      if (range && !(nextRow && inRange(nextRow))) return;
      ctx.sheet.append(el("div", { class: "sheet-section" }, it.name));
      return;
    }
    if (!inRange(it)) return;
    if (it.kind === "chords") {
      const row = el("div", { class: "sheet-row sheet-instr", onclick: () => seek(it.start) },
        el("span", { class: "instr-mark", title: "Trecho instrumental" }, "♪"));
      for (const c of it.chords) {
        row.append(el("button", {
          type: "button",
          class: `instr-chord fn-${c.fn}`,
          title: `${c.label} · ${c.numeral} · ${fmtClock(c.start)}`,
          onclick: (e) => { e.stopPropagation(); seek(c.start); },
        }, useNumerals ? c.numeral : c.label));
      }
      ctx.sheet.append(row);
      ctx.rows.push({ start: it.start, end: it.end, node: row, words: [] });
      return;
    }
    // lyric line: chords absolutely placed by character column over the words
    const cols = sheetChordCols(it.chords, useNumerals);
    const width = Math.max(it.text.length, ...cols.map((c) => c.col + c.text.length));
    const chordLine = el("div", { class: "sheet-chords" + (cols.length ? "" : " empty") });
    for (const c of cols) {
      chordLine.append(el("button", {
        type: "button",
        class: `sheet-chord fn-${c.fn}`,
        style: `left:${c.col}ch`,
        title: `${c.label} · ${c.numeral} · ${fmtClock(c.start)}`,
        onclick: (e) => { e.stopPropagation(); seek(c.start); },
      }, c.text));
    }
    const textLine = el("div", { class: "sheet-text" });
    const words = [];
    it.words.forEach((w, k) => {
      if (k) textLine.append(" ");
      const node = el("span", { class: "sheet-word" + (w.matched ? "" : " guess") }, it.text.substr(w.offset, w.len));
      textLine.append(node);
      words.push({ start: w.start, node });
    });
    const row = el("div", {
      class: "sheet-row sheet-lyric" + (it.para ? " para" : ""),
      style: `min-width:${width + 1}ch`,
      title: `${fmtClock(it.start)} · clique para ir até aqui`,
      onclick: () => seek(it.start - 0.15),
    }, chordLine, textLine);
    ctx.sheet.append(row);
    ctx.rows.push({ start: it.start, end: it.end, node: row, words });
  });
  if (!ctx.rows.length) ctx.sheet.append(el("p", { class: "muted" }, "Nada cantado neste trecho."));
  const w = ctx.getWave();
  updateSheet(w ? w.ws.getCurrentTime() : 0, true, ctx);
}

// ---------- chord sheet: printable / PDF (black and white, chords in bold) ----------

function escHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/** The chord sheet as a standalone HTML page: two columns, chords bold over the syllables.
 * Each section stays in one piece; chords-only blocks are re-wrapped to fit a column. */
const PRINT_INSTR_COLS = 34;  // characters per line of a chords-only block

function sheetPrintHtml() {
  const ctx = SU.project;
  const useNumerals = ctx.numerals.checked;
  const range = ctx.getRange();
  const inRange = (it) => !range || (it.end > range.start + 0.05 && it.start < range.end - 0.05);
  const items = L.data.sheet;
  const groups = [];  // [{title, rows: [html], instr: [chord labels]}]
  const group = (title) => {
    const g = { title, rows: [], instr: null };
    groups.push(g);
    return g;
  };
  const flushInstr = (g) => {
    if (!g || !g.instr) return;
    // chords-only block: wrapped at PRINT_INSTR_COLS characters
    const lines = [];
    let cur = "";
    let len = 0;
    for (const c of g.instr) {
      const add = (len ? 3 : 0) + c.length;
      if (len && len + add > PRINT_INSTR_COLS) {
        lines.push(cur);
        cur = "";
        len = 0;
      }
      cur += `${len ? "   " : ""}<b>${escHtml(c)}</b>`;
      len += (len ? 3 : 0) + c.length;
    }
    if (cur) lines.push(cur);
    g.rows.push(`<pre class="instr">${lines.join("\n")}</pre>`);
    g.instr = null;
  };
  let g = null;
  let lastInstr = null;
  items.forEach((it, i) => {
    if (it.kind === "section") {
      const nextRow = items.slice(i + 1).find((x) => x.kind !== "section");
      if (range && !(nextRow && inRange(nextRow))) return;
      flushInstr(g);
      lastInstr = null;
      g = group(it.name);
      return;
    }
    if (!inRange(it)) return;
    if (it.kind === "chords") {
      // named after the instrumental section it falls in (Intro, Solo, Outro...)
      const sec = (L.data.sections || []).find((x) => x.kind === "instrumental" && it.start >= x.start - 0.5 && it.start < x.end);
      if (!g || (sec && sec !== lastInstr)) {
        flushInstr(g);
        g = group(sec ? sec.name : "");
      }
      lastInstr = sec || lastInstr;
      g.instr = (g.instr || []).concat(it.chords.map((c) => (useNumerals ? c.numeral : c.label)));
      return;
    }
    if (!g) g = group("");
    flushInstr(g);
    // chord line: each chord at its character column, padded with spaces (same layout as on screen)
    let line = "";
    let width = 0;
    for (const c of sheetChordCols(it.chords, useNumerals)) {
      line += " ".repeat(Math.max(0, c.col - width)) + `<b>${escHtml(c.text)}</b>`;
      width = Math.max(width, c.col) + c.text.length;
    }
    g.rows.push(`<pre class="line${it.para && g.rows.length ? " para" : ""}">${line ? `${line}\n` : ""}${escHtml(it.text)}</pre>`);
  });
  flushInstr(g);

  const p = state.project;
  const meta = [];
  if (H.data && H.data.key) meta.push(`Tom: <b>${escHtml(H.data.key.name)}</b>`);
  if (p.detected_bpm) meta.push(`${Math.round(p.detected_bpm)} BPM`);
  const part = range && lEls.filter.value && p.parts.find((x) => x.part_id === lEls.filter.value);
  if (part) meta.push(`Parte: ${escHtml(part.name)}`);
  if (useNumerals) meta.push("acordes em graus");
  const sections = groups.filter((x) => x.rows.length)
    .map((x) => `<section>${x.title ? `<h2>${escHtml(x.title)}</h2>` : ""}${x.rows.join("")}</section>`).join("\n");
  return `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>${escHtml(sheetPdfName())}</title>
<style>
  @page { size: A4; margin: 10mm 11mm; }
  :root { --fs: 9pt; }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; color: #000; background: #fff; }
  body { width: 188mm; font-family: "Segoe UI", Arial, sans-serif; }
  header { display: flex; align-items: baseline; gap: 4mm; flex-wrap: wrap;
           border-bottom: 0.4pt solid #000; padding-bottom: 1.5mm; margin-bottom: 3mm; }
  h1 { font-size: calc(var(--fs) * 1.7); margin: 0; }
  .meta { font-size: calc(var(--fs) * 1.05); }
  .cols { column-count: 2; column-gap: 7mm; column-rule: 0.3pt solid #bbb; }
  section { break-inside: avoid; page-break-inside: avoid; margin: 0 0 calc(var(--fs) * 0.9); }
  h2 { font-size: calc(var(--fs) * 0.95); margin: 0 0 calc(var(--fs) * 0.25); text-transform: uppercase;
       letter-spacing: .05em; }
  pre { margin: 0; font-family: "Cascadia Mono", Consolas, "Courier New", monospace; font-size: var(--fs);
        line-height: 1.22; white-space: pre-wrap; break-inside: avoid; }
  pre.line { margin-bottom: calc(var(--fs) * 0.2); }
  pre.para { margin-top: calc(var(--fs) * 0.55); }
  b { font-weight: 800; }
  .foot { margin-top: 2mm; font-size: calc(var(--fs) * 0.75); color: #555; column-span: all; }
</style></head><body>
<header><h1>${escHtml(p.name)}</h1>${meta.length ? `<span class="meta">${meta.join(" &nbsp;·&nbsp; ")}</span>` : ""}</header>
<div class="cols">
${sections || "<p>Nada cantado neste trecho.</p>"}
<p class="foot">Acordes detectados automaticamente: confira de ouvido.</p>
</div>
</body></html>`;
}

/** "Nome da música - cifra - escritab" (with the part in parentheses when the sheet is filtered):
 * the browser suggests the page title as the PDF file name. */
function sheetPdfName() {
  const p = state.project;
  const part = SU.project.getRange() && lEls.filter.value && p.parts.find((x) => x.part_id === lEls.filter.value);
  const name = `${p.name}${part ? ` (${part.name})` : ""} - cifra - escritab`;
  return name.replace(/[\\/:*?"<>|]+/g, " ").replace(/\s+/g, " ").trim();  // characters a file name cannot have
}

const PRINT_FONT_MAX = 9.5;   // pt
const PRINT_FONT_MIN = 6.5;   // below this it stops shrinking (and spills onto a second page)
const MM = 96 / 25.4;         // CSS px per mm

/** Largest font (between MIN and MAX) with which the page fits one A4 sheet. Returns the pt used. */
function fitPrintPage(doc) {
  const pageH = (297 - 20) * MM;  // A4 minus the @page margins
  const root = doc.documentElement;
  let fs = PRINT_FONT_MAX;
  for (; fs >= PRINT_FONT_MIN; fs -= 0.25) {
    root.style.setProperty("--fs", `${fs}pt`);
    if (doc.body.scrollHeight <= pageH) return fs;
  }
  root.style.setProperty("--fs", `${PRINT_FONT_MIN}pt`);
  return PRINT_FONT_MIN;
}

function exportSheetPdf() {
  if (!L.data || !L.data.sheet) return;
  const old = document.getElementById("printFrame");
  if (old) old.remove();
  // a hidden frame, A4-wide so the layout can be measured; its print dialog offers "Salvar como PDF"
  const frame = el("iframe", { id: "printFrame", title: "Cifra para impressão", "aria-hidden": "true" });
  frame.style.cssText = "position:fixed;left:-10000px;top:0;width:188mm;height:277mm;border:0;visibility:hidden";
  document.body.append(frame);
  const doc = frame.contentDocument;
  doc.open();
  doc.write(sheetPrintHtml());
  doc.close();
  const go = () => {
    const fs = fitPrintPage(doc);
    if (fs <= PRINT_FONT_MIN && doc.body.scrollHeight > (297 - 20) * MM) {
      banner(lEls.status, "A cifra é longa demais para uma página com letra legível: ela vai ocupar duas páginas.");
    }
    // the file name comes from the title: the frame's, or the page's in some browsers, so both
    const pageTitle = document.title;
    const restore = () => { document.title = pageTitle; };
    document.title = sheetPdfName();
    frame.contentWindow.addEventListener("afterprint", restore, { once: true });
    frame.contentWindow.focus();
    frame.contentWindow.print();
    setTimeout(restore, 1000);  // print() blocks until the dialog closes; fallback if afterprint never comes
  };
  if (doc.readyState === "complete") setTimeout(go, 50);
  else frame.addEventListener("load", go, { once: true });
}
$("sheetPdfBtn").addEventListener("click", exportSheetPdf);

/** Highlight the line (and the words already sung) under the playhead; keep it in view. */
function updateSheet(t, force = false, ctx = SU.project) {
  if (!lyricsIsCurrent() || !ctx.rows.length) return;
  if (ctx.time) ctx.time.textContent = fmtClock(t, 0);
  let idx = -1;
  for (let i = 0; i < ctx.rows.length; i++) {
    if (ctx.rows[i].start <= t + 0.05) idx = i;
    else break;
  }
  if (idx >= 0 && t > ctx.rows[idx].end + 1.0) idx = -1;  // between lines: nothing lit
  if (idx !== ctx.curRow || force) {
    if (ctx.curRow >= 0 && ctx.rows[ctx.curRow]) {
      const old = ctx.rows[ctx.curRow];
      old.node.classList.remove("current");
      old.words.forEach((w) => w.node.classList.remove("sung"));
    }
    ctx.curRow = idx;
    if (idx >= 0) {
      const row = ctx.rows[idx].node;
      row.classList.add("current");
      if (ctx.follow.checked) {
        const box = ctx.sheet;
        const top = row.offsetTop - box.clientHeight * 0.3;
        if (row.offsetTop < box.scrollTop || row.offsetTop + row.offsetHeight > box.scrollTop + box.clientHeight * 0.8) {
          box.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
        }
      }
    }
  }
  if (idx >= 0) for (const w of ctx.rows[idx].words) w.node.classList.toggle("sung", w.start <= t);
}

// ---------- view: part ----------

// ---------- part: scales of a solo (ear-training aid) ----------

const S = {
  partId: null,     // part the suggestions belong to
  data: null,       // {scales, profile, sources, has_harmony}
  selected: null,   // id ("11:minor_pentatonic") of the scale on display
  chordPcs: null,   // pitch classes of the chord sounding now (null = none)
  chordIdx: -2,
  loading: false,
  card: $("scaleCard"),
  status: $("scaleStatus"),
  body: $("scaleBody"),
  list: $("scaleList"),
  name: $("scaleName"),
  desc: $("scaleDesc"),
  notes: $("scaleNotes"),
  extra: $("scaleExtra"),
  fretboard: $("scaleFretboard"),
  sources: $("scaleSources"),
  useCheck: $("scaleUseCheck"),
  playBtn: $("scalePlayBtn"),
  engineHint: $("scaleEngineHint"),
};
const QUALITY_IVS = {
  maj: [0, 4, 7], min: [0, 3, 7], 5: [0, 7], dim: [0, 3, 6], sus4: [0, 5, 7],
  7: [0, 4, 7, 10], m7: [0, 3, 7, 10], maj7: [0, 4, 7, 11], m7b5: [0, 3, 6, 10],
};

function isSoloPart(part) {
  const tp = part.transcription_params;
  return /solo|riff|lead|fill|melodia/i.test(part.name) || (tp && tp.engine === "lead");
}

function selectedScale() {
  if (!S.data || S.partId !== (currentPart() || {}).part_id) return null;
  return S.data.scales.find((s) => s.id === S.selected) || null;
}

/** Scale id the transcription should use (lead engine only), or null. */
function transcriptionScale() {
  const s = selectedScale();
  return s && S.useCheck.checked ? s.id : null;
}

function resetScales(part) {
  S.partId = part.part_id;
  S.data = null;
  S.chordIdx = -2;
  S.chordPcs = null;
  const tp = part.transcription_params;
  S.selected = (tp && tp.scale) || null;
  S.useCheck.checked = !tp || tp.engine !== "lead" || !!tp.scale;
  hide(S.body);
  hide(S.status);
  S.sources.textContent = "";
  S.card.open = isSoloPart(part);
  renderScaleEngineHint();
  if (S.card.open) loadScales();
}

async function loadScales() {
  const part = currentPart();
  if (!part || S.loading) return;
  const partId = part.part_id;
  S.loading = true;
  banner(S.status, "Ouvindo as notas do trecho...", "busy");
  try {
    const stem = els.selectedStemSelect.value || "";
    const data = await api(projectUrl(`/parts/${partId}/scales?stem=${encodeURIComponent(stem)}`));
    if ((currentPart() || {}).part_id !== partId) return;
    S.partId = partId;
    S.data = data;
    if (!data.scales.some((s) => s.id === S.selected)) S.selected = data.scales.length ? data.scales[0].id : null;
    hide(S.status);
    renderScales();
  } catch (err) {
    if ((currentPart() || {}).part_id === partId) banner(S.status, `Erro: ${err.message}`, "error");
  } finally {
    S.loading = false;
  }
}

function renderScales() {
  const d = S.data;
  show(S.body, !!(d && d.scales.length));
  if (!d) return;
  S.list.innerHTML = "";
  d.scales.forEach((s, i) => {
    const pct = Math.round(s.probability * 100);
    const btn = el("button", {
      type: "button",
      role: "radio",
      class: "scale-option" + (s.id === S.selected ? " selected" : ""),
      "aria-checked": String(s.id === S.selected),
      title: s.same_notes_as.length ? `Mesmas notas: ${s.same_notes_as.join(", ")}` : "",
      onclick: () => {
        S.selected = s.id;
        renderScales();
      },
    },
    el("span", { class: "scale-rank" }, String(i + 1)),
    el("span", { class: "scale-option-main" },
      el("strong", {}, s.name),
      el("span", { class: "scale-option-notes muted small" }, s.notes.map((n) => n.name).join(" "))),
    el("span", { class: "scale-prob", title: "Quanto esta escala explica o trecho, comparada às outras" },
      el("span", { class: "scale-prob-bar" }, el("span", { style: `width:${Math.max(3, pct)}%` })),
      el("span", { class: "small" }, pct < 1 ? "<1%" : `${pct}%`)));
    S.list.append(btn);
  });
  S.sources.textContent = `Baseado em: ${d.sources.join(" · ")}.` +
    (d.has_harmony ? "" : " Analise a harmonia na página da música para acertar melhor a tônica.") +
    (currentPart().artifacts.midi ? "" : " Depois de gerar a tablatura, as notas transcritas também entram na conta.");
  renderScaleDetail();
}

function renderScaleDetail() {
  const s = selectedScale();
  renderScaleEngineHint();
  if (!s) return;
  S.name.textContent = s.name;
  S.desc.textContent = s.description;
  S.notes.innerHTML = "";
  const chord = S.chordPcs;
  for (const n of s.notes) {
    const heard = Math.min(1, n.share * 5);   // 20% of the line or more = full bar
    S.notes.append(el("button", {
      type: "button",
      class: "scale-note" + (n.degree === "1" ? " root" : "") + (chord && chord.has(n.pc) ? " in-chord" : ""),
      title: `${n.name} (${n.degree}) · ${Math.round(n.share * 100)}% do que o trecho toca. Clique para ouvir.`,
      onclick: () => playPitches([scaleBaseMidi(s) + ((n.pc - s.root + 12) % 12)]),
    },
    el("strong", {}, n.name),
    el("span", { class: "scale-degree" }, n.degree),
    el("span", { class: "scale-heard" }, el("span", { style: `width:${Math.round(heard * 100)}%` }))));
  }
  const extra = [];
  extra.push(`Cobre ${Math.round(s.coverage * 100)}% das notas ouvidas.`);
  if (s.chord_fit !== null) extra.push(`Combina com ${Math.round(s.chord_fit * 100)}% dos acordes do trecho.`);
  if (s.outside.length) extra.push(`Notas fora dela que aparecem: ${s.outside.map((o) => o.name).join(", ")} (passagem, bend ou outra escala).`);
  if (s.same_notes_as.length) extra.push(`Mesmas notas de: ${s.same_notes_as.join(", ")}.`);
  S.extra.textContent = extra.join(" ");
  drawFretboard(s);
}

function renderScaleEngineHint() {
  const s = selectedScale();
  S.engineHint.textContent = s && S.useCheck.checked
    ? `Escala usada para resolver notas ambíguas: ${s.name} (troque no card "Escalas").`
    : "";
}

/** Fretboard with the scale's notes; high string on top, as in a tab. */
function drawFretboard(s) {
  const tuning = state.tunings.find((t) => t.id === els.tuningSelect.value) || { pitches: [40, 45, 50, 55, 59, 64] };
  const strings = [...tuning.pitches].reverse();
  const frets = Math.min(24, Math.max(12, Number(els.maxFretsInput.value) || 22));
  const fw = 46, sh = 22, left = 34, top = 18;
  const width = left + fw * (frets + 0.5);
  const height = top + sh * (strings.length - 1) + 26;
  const pcs = new Map(s.notes.map((n) => [n.pc, n]));
  const chord = S.chordPcs;
  const svg = S.fretboard;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.style.minWidth = `${Math.round(width * 0.82)}px`;
  const ns = "http://www.w3.org/2000/svg";
  const add = (tag, attrs, text) => {
    const e = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    svg.append(e);
    return e;
  };
  svg.innerHTML = "";
  const x = (f) => (f === 0 ? left - fw * 0.45 : left + fw * (f - 0.5));
  const bottom = top + sh * (strings.length - 1);
  for (const f of [3, 5, 7, 9, 12, 15, 17, 19, 21, 24]) {
    if (f > frets) continue;
    add("text", { x: x(f), y: bottom + 20, class: "fb-fretnum" }, String(f));
    if (f % 12 === 0) {
      add("circle", { cx: x(f), cy: top + sh * 0.5 * (strings.length - 1) - sh, r: 4, class: "fb-inlay" });
      add("circle", { cx: x(f), cy: top + sh * 0.5 * (strings.length - 1) + sh, r: 4, class: "fb-inlay" });
    } else {
      add("circle", { cx: x(f), cy: top + sh * 0.5 * (strings.length - 1), r: 4, class: "fb-inlay" });
    }
  }
  for (let f = 0; f <= frets; f++) {
    add("line", { x1: left + fw * f, x2: left + fw * f, y1: top, y2: bottom, class: f === 0 ? "fb-nut" : "fb-fret" });
  }
  strings.forEach((p, i) => {
    const y = top + sh * i;
    add("line", { x1: left, x2: left + fw * frets, y1: y, y2: y, class: "fb-string", "stroke-width": 0.8 + i * 0.25 });
    for (let f = 0; f <= frets; f++) {
      const pc = (p + f) % 12;
      const n = pcs.get(pc);
      if (!n) continue;
      const cls = "fb-note" + (n.degree === "1" ? " root" : "") + (chord && chord.has(pc) ? " in-chord" : "");
      const g = add("g", { class: cls, transform: `translate(${x(f)},${y})` });
      const c = document.createElementNS(ns, "circle");
      c.setAttribute("r", 9.5);
      const t = document.createElementNS(ns, "text");
      t.setAttribute("dy", "0.35em");
      t.textContent = n.name;
      const tip = document.createElementNS(ns, "title");
      tip.textContent = `${n.name} (${n.degree}) · casa ${f}`;
      g.append(c, t, tip);
      g.addEventListener("click", () => playPitches([p + f]));
    }
  });
}

/** Mark the notes of the chord under the playhead on the chips and the fretboard. */
function scaleSyncChord(t) {
  if (!S.card.open || !selectedScale() || !harmonyIsCurrent()) return;
  const idx = chordIndexAt(t);
  if (idx === S.chordIdx) return;
  S.chordIdx = idx;
  const c = idx >= 0 ? H.data.chords[idx] : null;
  S.chordPcs = c ? new Set((QUALITY_IVS[c.quality] || [0, 4, 7]).map((iv) => (c.root + iv) % 12)) : null;
  renderScaleDetail();
}

// a tiny plucked synth, enough to hear the scale (the tab player's SoundFont is not loaded here)
let scaleAudio = null;
function scaleBaseMidi(s) {
  return 52 + ((s.root - 4 + 12) % 12);   // the root between E3 and D#4
}
function playPitches(pitches, step = 0.26) {
  scaleAudio = scaleAudio || new AudioContext();
  const ctx = scaleAudio;
  if (ctx.state === "suspended") ctx.resume();
  const t0 = ctx.currentTime + 0.03;
  pitches.forEach((p, i) => {
    const t = t0 + i * step;
    const osc = ctx.createOscillator();
    const filt = ctx.createBiquadFilter();
    const gain = ctx.createGain();
    osc.type = "sawtooth";
    osc.frequency.value = 440 * 2 ** ((p - 69) / 12);
    filt.type = "lowpass";
    filt.frequency.setValueAtTime(3200, t);
    filt.frequency.exponentialRampToValueAtTime(700, t + 0.4);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.22, t + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.7);
    osc.connect(filt).connect(gain).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 0.75);
  });
}

S.playBtn.addEventListener("click", () => {
  const s = selectedScale();
  if (!s) return;
  const base = scaleBaseMidi(s);
  const up = s.notes.map((n) => base + ((n.pc - s.root + 12) % 12));
  up.push(base + 12);
  playPitches([...up, ...up.slice(0, -1).reverse()]);
});
S.card.addEventListener("toggle", () => {
  if (S.card.open && !S.data && !S.loading) loadScales();
});
S.useCheck.addEventListener("change", renderScaleEngineHint);
els.tuningSelect.addEventListener("change", () => selectedScale() && drawFretboard(selectedScale()));
els.maxFretsInput.addEventListener("change", () => selectedScale() && drawFretboard(selectedScale()));

async function renderPartView() {
  const part = currentPart();
  els.partNameInput.value = part.name;
  renderPartMeta();
  hide(els.partStatus);
  setPartRateUi(100);
  fillSourceSelect(els.partSource, preferredStem(part));
  renderSourcePicker(part);
  restorePartSettings(part);
  renderPartResult(part, null);
  resetScales(part);
  restorePartTab(part);

  partWave = await createWave(els.partWave, els.partSource.value);
  const w = partWave;
  w.excerpt = { start: part.start, end: part.end };   // replaced by the region once the wave is ready
  w.onTime = (t) => {
    const r = w.excerpt;
    els.partTime.textContent = fmtClock(Math.max(0, t - r.start));
    $("partTimeTotal").textContent = fmtClock(r.end - r.start);
    updateNowChord(t, false, CU.part);
    syncChordLane(CU.part);
    updateSheet(t, false, SU.part);
    scaleSyncChord(t);
  };
  w.onTime(part.start);
  w.onInteraction = () => {};  // a click outside the excerpt does not switch the loop off: the toggle does
  els.partLoopCheck.checked = true;
  syncPartLoop();
  for (const ev of ["scroll", "zoom", "redraw", "redrawcomplete"]) w.ws.on(ev, () => syncChordLane(CU.part));
  w.ws.on("seeking", (t) => {
    updateNowChord(t, false, CU.part);
    updateSheet(t, false, SU.part);
    scaleSyncChord(t);
  });
  hide($("partLyricsCard"));
  loadHarmony();
  loadLyrics();
  setPartPlaying(false);
  w.ws.on("play", () => setPartPlaying(true));
  w.ws.on("pause", () => setPartPlaying(false));
  w.regions.on("region-clicked", (region, e) => {
    e.stopPropagation();
    w.ws.setTime(clickTime(w, e));  // exact spot inside the excerpt, not its start
  });
  w.regions.on("region-updated", (r) => {
    els.partStartInput.value = r.start.toFixed(2);
    els.partEndInput.value = r.end.toFixed(2);
    syncPartLoop();
    els.partRangeSaveBtn.classList.add("btn-attention");
    renderPartSheet();
  });
  whenReady(w, () => {
    if (partWave !== w) return;
    const { start, end } = w.excerpt;
    w.excerpt = w.regions.addRegion({
      id: "excerpt",
      start,
      end,
      color: withAlpha(partColor(part), 0.25),
      drag: false,
      resize: true,
    });
    // zoom so the excerpt fills most of the width, with some context on both sides
    const width = els.partWave.clientWidth || 800;
    const span = end - start;
    w.ws.zoom(Math.max(1, (width * 0.7) / span));
    w.ws.setScrollTime(Math.max(0, start - span * 0.2));
    w.ws.setTime(start);
  });
}

function renderPartMeta() {
  const part = currentPart();
  els.partMeta.textContent = `${fmtRange(part.start, part.end)} · ${state.project.detected_bpm} BPM`;
  $("partLcdRange").textContent = fmtRange(part.start, part.end);
  $("partBpmLcd").textContent = state.project.detected_bpm ? Math.round(state.project.detected_bpm) : "–";
  els.partStartInput.value = part.start.toFixed(2);
  els.partEndInput.value = part.end.toFixed(2);
  els.partRangeSaveBtn.classList.remove("btn-attention");
}

function preferredStem(part) {
  const tp = part.transcription_params;
  if (tp && tp.source === "stem" && state.project.stems[tp.stem.split("+")[0]]) return tp.stem.split("+")[0];
  return "";
}

function partSourceValue() {
  const r = els.sourcePicker.querySelector("input[name=partSource]:checked");
  return r ? r.value : null;
}

function renderSourcePicker(part) {
  const mt = hasMultitrack();
  const st = hasStems();
  const radios = els.sourcePicker.querySelectorAll("input[name=partSource]");
  radios.forEach((r) => {
    r.disabled = r.value === "multitrack" ? !mt : !st;
    r.closest("label").classList.toggle("is-disabled", r.disabled);
  });
  const tp = part.transcription_params;
  let source = tp && tp.source;
  if (source === "multitrack" && !mt) source = null;
  if (source === "stem" && !st) source = null;
  if (!source) source = mt && state.project.mode !== "classic" ? "multitrack" : st ? "stem" : mt ? "multitrack" : null;
  radios.forEach((r) => (r.checked = r.value === source));
  show(els.sourcePicker, mt || st);
  show(els.srcNone, !mt && !st);
  els.generateBtn.disabled = !mt && !st;
  renderPartTracks(part);
  renderStemSelect(part);
  syncSourcePanels();
}

function syncSourcePanels() {
  const source = partSourceValue();
  show(els.srcMultitrack, source === "multitrack");
  show(els.srcStem, source === "stem");
  syncEngineControls();
}
els.sourcePicker.addEventListener("change", syncSourcePanels);

function renderPartTracks(part) {
  els.partTracks.innerHTML = "";
  const tracks = state.project.multitrack || [];
  const tp = part.transcription_params;
  const previous = tp && tp.source === "multitrack" && tp.track_indices.length ? new Set(tp.track_indices) : null;
  show(els.trackPicks, tracks.some((t) => t.is_guitar));
  for (const t of tracks) {
    if (t.is_drum) continue;
    const cb = el("input", { type: "checkbox", value: String(t.index) });
    cb.checked = previous ? previous.has(t.index) : t.is_guitar && t.note_count > 0;
    els.partTracks.append(el("label", { class: "track-row" }, cb, trackLabel(t), trackMeta(t)));
  }
}

function selectedTrackIndices() {
  return Array.from(els.partTracks.querySelectorAll("input:checked")).map((cb) => Number(cb.value));
}

els.trackPicks.querySelectorAll("[data-pick]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const pick = btn.dataset.pick;
    const tracks = state.project.multitrack || [];
    els.partTracks.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      const t = tracks.find((x) => x.index === Number(cb.value));
      if (!t) return;
      // a guitar track that was not split (no role) counts as both base and solo
      cb.checked = pick === "guitar" ? t.is_guitar : t.is_guitar && (t.role === pick || !t.role);
    });
  });
});

function renderStemSelect(part) {
  const stems = state.project.stems || {};
  els.selectedStemSelect.innerHTML = "";
  const options = Object.keys(stems).map((k) => [k, STEM_LABELS[k] || k]);
  if (stems.guitar && stems.other) options.splice(1, 0, ["guitar+other", "Guitarra + Outros (mixadas)"]);
  for (const [value, text] of options) els.selectedStemSelect.append(el("option", { value }, text));
  const tp = part.transcription_params;
  const previous = tp && tp.source === "stem" ? tp.stem : null;
  els.selectedStemSelect.value = previous && options.some(([v]) => v === previous)
    ? previous
    : stems.guitar ? "guitar" : (options[0] || [""])[0];
  if (tp && tp.source === "stem") els.engineSelect.value = tp.engine;
}

function syncEngineControls() {
  const engine = els.engineSelect.value;
  document.querySelectorAll(".basic-pitch-only").forEach((x) => show(x, engine === "basic_pitch"));
  document.querySelectorAll(".lead-only").forEach((x) => show(x, engine === "lead"));
  document.querySelectorAll(".engine-tuning").forEach((x) => show(x, engine !== "dettato"));
}
els.engineSelect.addEventListener("change", syncEngineControls);

/** The part's last settings, else the project's tuning guess / defaults. */
function restorePartSettings(part) {
  const tp = part.transcription_params;
  const tab = part.tab_params;
  const other = state.project.parts.find((p) => p.tab_params);
  els.tuningSelect.value = (tab || (other && other.tab_params) || {}).tuning || "standard_e";
  els.fretPrefSelect.value = (tab && tab.fret_preference) || (/solo/i.test(part.name) ? "high" : "auto");
  els.maxFretsInput.value = (tab && tab.max_frets) || 24;
  if (tp) {
    els.minLenSlider.value = tp.minimum_note_length_ms;
    els.quantizeSelect.value = tp.quantize_grid || "off";
    els.suppressHarmonicsCheck.checked = tp.suppress_harmonics;
    els.onsetSlider.value = tp.onset_threshold;
    els.frameSlider.value = tp.frame_threshold;
    if (tp.voicing_threshold) els.voicingSlider.value = tp.voicing_threshold;
    els.monophonicCheck.checked = !!tp.monophonic;
    els.gateSlider.value = tp.noise_gate_db;
    els.bandpassCheck.checked = tp.bandpass_enabled;
  }
  if (!tp) {
    // a part named "Solo" / "Riff" starts on the monophonic engine
    const single = /solo|riff|lead/i.test(part.name);
    els.engineSelect.value = single ? "lead" : "dettato";
    els.monophonicCheck.checked = single;
  }
  syncEngineControls();
  [els.onsetSlider, els.frameSlider, els.voicingSlider, els.gateSlider, els.minLenSlider].forEach((s) => s.dispatchEvent(new Event("input")));
}

function renderPartResult(part, asciiText) {
  const st = part.stats || {};
  els.partSummary.textContent = st.total_notes
    ? `${st.total_notes} notas${st.lowest_note ? ` (${st.lowest_note} a ${st.highest_note})` : ""} · ${st.engine || ""}`
    : "";
  els.generateBtn.textContent = part.artifacts.musicxml ? "Gerar de novo" : "Gerar tablatura do trecho";
  const hasTab = !!part.artifacts.musicxml;
  show($("tabEmpty"), !hasTab);
  show($("ptabTabDot"), !hasTab);
  if (!hasTab) {
    hide(els.playerSection);
    return;
  }
  els.dlGp5.href = partFileUrl(part, part.artifacts.gp5);
  els.dlXml.href = partFileUrl(part, part.artifacts.musicxml);
  els.dlMid.href = part.artifacts.midi ? partFileUrl(part, part.artifacts.midi) : "#";
  els.dlTxt.href = partFileUrl(part, part.artifacts.txt);
  const base = `${state.project.name} - ${part.name}`.replace(/[\\/:*?"<>|]+/g, "_");
  els.dlGp5.download = `${base}.gp5`;
  els.dlXml.download = `${base}.musicxml`;
  els.dlMid.download = `${base}.mid`;
  els.dlTxt.download = `${base}.txt`;
  show(els.playerSection);
  if (asciiText !== null) {
    els.asciiPreviewBox.textContent = asciiText;
  } else {
    els.asciiPreviewBox.textContent = "";
    fetch(partFileUrl(part, part.artifacts.txt)).then((r) => (r.ok ? r.text() : "")).then((t) => {
      if (currentPart() === part) els.asciiPreviewBox.textContent = t;
    });
  }
  initAlphaTab(partFileUrl(part, part.artifacts.musicxml));
}

els.generateBtn.addEventListener("click", async () => {
  const part = currentPart();
  const source = partSourceValue();
  if (!part || !source || state.busy) return;
  if (source === "multitrack" && !selectedTrackIndices().length) {
    banner(els.partStatus, "Marque pelo menos uma faixa para a tablatura.", "error");
    return;
  }
  const bpm = state.project.detected_bpm;
  const tuning = els.tuningSelect.value;
  const maxFrets = Number(els.maxFretsInput.value);
  const transcription = {
    source,
    engine: source === "multitrack" ? "dettato" : els.engineSelect.value,
    track_indices: source === "multitrack" ? selectedTrackIndices() : [],
    stem: source === "stem" ? els.selectedStemSelect.value : "guitar",
    onset_threshold: Number(els.onsetSlider.value),
    frame_threshold: Number(els.frameSlider.value),
    voicing_threshold: Number(els.voicingSlider.value),
    scale: source === "stem" && els.engineSelect.value === "lead" ? transcriptionScale() : null,
    monophonic: els.monophonicCheck.checked,
    noise_gate_db: Number(els.gateSlider.value),
    bandpass_enabled: els.bandpassCheck.checked,
    minimum_note_length_ms: Number(els.minLenSlider.value),
    quantize_grid: els.quantizeSelect.value,
    suppress_harmonics: els.suppressHarmonicsCheck.checked,
    tuning,
    max_frets: maxFrets,
    bpm,
  };
  const tab = { tuning, max_frets: maxFrets, fret_preference: els.fretPrefSelect.value, bpm };
  const firstStemRun = source === "stem" && transcription.engine === "dettato";
  setBusy(true);
  els.generateBtn.disabled = true;
  banner(els.partStatus, firstStemRun
    ? onDevice("Transcrevendo (na primeira vez a stem inteira passa pelo Dettato na GPU)...",
      "Transcrevendo (na primeira vez a stem inteira passa pelo Dettato na CPU, pode demorar)...")
    : "Recortando as notas do trecho e montando a tablatura...", "busy");
  try {
    const data = await postJson(projectUrl(`/parts/${part.part_id}/generate`), { transcription, tab });
    state.project = { ...state.project, parts: data.job.parts };
    hide(els.partStatus);
    renderPartResult(currentPart(), data.ascii_preview);
    openPartTab("tab");
    revealPartTabs();
    if (S.card.open) loadScales();  // the transcribed notes are better evidence than the raw audio
  } catch (err) {
    banner(els.partStatus, `Erro: ${err.message}`, "error");
  } finally {
    els.generateBtn.disabled = false;
    setBusy(false);
  }
});

function setExcerpt(w, start, end) {
  if (w.excerpt.setOptions) w.excerpt.setOptions({ start, end });
  else w.excerpt = { start, end };
  syncPartLoop();
}

/** The part player loops the excerpt while "Repetir trecho" is on; off, the song plays on. */
function syncPartLoop() {
  const w = partWave;
  const on = els.partLoopCheck.checked;
  els.partLoopToggle.classList.toggle("active", on);
  if (w && w.excerpt) w.loop = on ? { start: w.excerpt.start, end: w.excerpt.end } : null;
}

els.partPlayBtn.addEventListener("click", () => {
  const w = partWave;
  if (!w) return;
  if (w.ws.isPlaying()) {
    w.ws.pause();
    return;
  }
  const { start, end } = w.excerpt;
  const t = w.ws.getCurrentTime();
  syncPartLoop();
  // looping: start from the excerpt when the cursor is outside it; otherwise play from the cursor
  if (w.loop && (t < start || t >= end - 0.05)) w.ws.setTime(start);
  w.ws.play();
});
els.partStopBtn.addEventListener("click", () => {
  const w = partWave;
  if (!w) return;
  if (w.ws.isPlaying()) w.ws.pause();  // same DAW-style stop as the song player
  else w.ws.setTime(w.excerpt.start);
});

// space = play / pause (unless typing or on a control that handles it itself)
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest("input, textarea, select, button, a, [contenteditable]")) return;
  const btn = state.view === "project" ? els.projPlayBtn : state.view === "part" ? els.partPlayBtn : null;
  if (!btn) return;
  e.preventDefault();
  btn.click();
});
els.partLoopCheck.addEventListener("change", syncPartLoop);

/** Jump to the start of the excerpt and play (keeps "Repetir trecho" as it is). */
function playPartFromStart() {
  const w = partWave;
  if (!w || !w.excerpt) return;
  syncPartLoop();
  w.ws.setTime(w.excerpt.start);
  w.ws.play();
}
$("partFromStartBtn").addEventListener("click", playPartFromStart);
/** Song player: from the start of the selected section, or of the song when nothing is selected. */
function playProjectFromStart() {
  const w = projWave;
  if (!w) return;
  w.ws.setTime(sel.cur ? sel.cur.start : 0);
  w.ws.play();
}
$("projFromStartBtn").addEventListener("click", playProjectFromStart);

document.addEventListener("keydown", (e) => {
  if (e.key !== "Home" || e.ctrlKey || e.metaKey || e.altKey) return;
  if (state.view !== "part" && state.view !== "project") return;
  if (e.target.closest("input, textarea, select, [contenteditable]")) return;
  e.preventDefault();
  if (state.view === "part") playPartFromStart();
  else playProjectFromStart();
});
function setPartRateUi(pct) {
  const s = els.partRate;
  s.value = pct;
  s.style.setProperty("--fill", `${((pct - Number(s.min)) / (Number(s.max) - Number(s.min))) * 100}%`);
  els.partRateVal.textContent = `${pct}%`;
  els.partRateVal.classList.toggle("slowed", pct !== 100);
}
function setPartPlaying(playing) {
  els.partPlayBtn.classList.toggle("is-playing", playing);
  els.partPlayBtn.setAttribute("aria-label", playing ? "Pausar" : "Play");
}
els.partRate.addEventListener("input", () => {
  const pct = Number(els.partRate.value);
  setPartRateUi(pct);
  if (partWave) partWave.ws.setPlaybackRate(pct / 100, true);
});
els.partRate.addEventListener("dblclick", () => {
  setPartRateUi(100);
  if (partWave) partWave.ws.setPlaybackRate(1, true);
});

// ---------- part page: tabs (Tablatura / Transcrição / Escalas / Cifra) ----------

const PART_TABS = ["tab", "transcribe", "scales", "sheet"];
const partTabsEl = $("partTabs");
const partPlayerCard = $("partPlayerCard");
const PART_TAB_KEY = "escritab.partTab";
let tabRenderPending = false;  // AlphaTab was (re)built while its panel was hidden

function openPartTab(name, remember = true) {
  if (!PART_TABS.includes(name)) name = "tab";
  for (const b of partTabsEl.querySelectorAll(".tab")) {
    const on = b.dataset.ptab === name;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;
  }
  for (const panel of document.querySelectorAll("[data-part-panel]")) show(panel, panel.dataset.partPanel === name);
  if (name === "scales" && !S.card.open) S.card.open = true;  // its "toggle" listener loads the scales
  if (name === "tab" && tabRenderPending && alphaTabApi) {
    tabRenderPending = false;
    alphaTabApi.render();
  }
  if (remember) {
    try {
      localStorage.setItem(PART_TAB_KEY, name);
    } catch {
      /* private mode / blocked storage */
    }
  }
}

/** Last tab used, but a part without tablatura opens on "Transcrição" instead of an empty tab. */
function restorePartTab(part) {
  let name = null;
  try {
    name = localStorage.getItem(PART_TAB_KEY);
  } catch {
    /* ignore */
  }
  if (!PART_TABS.includes(name)) name = "tab";
  if (name === "tab" && !part.artifacts.musicxml) name = "transcribe";
  openPartTab(name, false);
}

function revealPartTabs() {
  if (partTabsEl.getBoundingClientRect().top < 0) partTabsEl.scrollIntoView({ block: "start" });
}

partTabsEl.addEventListener("click", (e) => {
  const b = e.target.closest(".tab");
  if (!b) return;
  openPartTab(b.dataset.ptab);
  revealPartTabs();
});
partTabsEl.addEventListener("keydown", (e) => {
  const order = [...partTabsEl.querySelectorAll(".tab")];
  const i = order.indexOf(document.activeElement);
  if (i < 0) return;
  let j = null;
  if (e.key === "ArrowRight") j = (i + 1) % order.length;
  else if (e.key === "ArrowLeft") j = (i - 1 + order.length) % order.length;
  else if (e.key === "Home") j = 0;
  else if (e.key === "End") j = order.length - 1;
  if (j === null) return;
  e.preventDefault();
  e.stopPropagation();  // Home here moves between tabs, not "play from start"
  order[j].focus();
  openPartTab(order[j].dataset.ptab);
});
$("tabEmptyBtn").addEventListener("click", () => {
  openPartTab("transcribe");
  revealPartTabs();
});
// the scales card is a <details>: inside its tab it stays open
S.card.addEventListener("toggle", () => {
  if (!S.card.open && !S.card.parentElement.classList.contains("hidden")) S.card.open = true;
});
new ResizeObserver(() => {
  document.documentElement.style.setProperty("--part-player-h", `${partPlayerCard.offsetHeight}px`);
}).observe(partPlayerCard);
new IntersectionObserver(([entry]) => {
  partPlayerCard.classList.toggle("is-stuck", !entry.isIntersecting && entry.boundingClientRect.top < 0);
}).observe($("partPlayerSentinel"));
els.partSource.addEventListener("change", () => {
  if (!partWave) return;
  switchSource(partWave, els.partSource.value);
  partWave.ws.once("ready", () => partWave && partWave.ws.setPlaybackRate(Number(els.partRate.value) / 100, true));
});

els.partNameInput.addEventListener("change", async () => {
  const part = currentPart();
  const name = els.partNameInput.value.trim();
  if (!part || !name) {
    if (part) els.partNameInput.value = part.name;
    return;
  }
  try {
    const data = await patchJson(projectUrl(`/parts/${part.part_id}`), { name });
    state.project = { ...state.project, parts: data.job.parts };
    renderBreadcrumbs();
  } catch (err) {
    banner(els.partStatus, `Erro ao renomear: ${err.message}`, "error");
  }
});

els.partRangeSaveBtn.addEventListener("click", async () => {
  const part = currentPart();
  if (!part) return;
  const start = Number(els.partStartInput.value);
  const end = Number(els.partEndInput.value);
  if (part.artifacts.musicxml && !confirm("Mudar os limites apaga a tablatura atual desta parte. Continuar?")) return;
  try {
    const data = await patchJson(projectUrl(`/parts/${part.part_id}`), { start, end });
    state.project = { ...state.project, parts: data.job.parts };
    if (data.lyrics_changed) loadLyrics();  // the stanza moved into the new bounds
    const updated = currentPart();
    renderPartMeta();
    renderPartResult(updated, null);
    if (partWave) setExcerpt(partWave, updated.start, updated.end);
  } catch (err) {
    banner(els.partStatus, `Erro: ${err.message}`, "error");
  }
});
[els.partStartInput, els.partEndInput].forEach((input) =>
  input.addEventListener("input", () => {
    els.partRangeSaveBtn.classList.add("btn-attention");
    const start = Number(els.partStartInput.value);
    const end = Number(els.partEndInput.value);
    if (partWave && end > start) setExcerpt(partWave, start, end);
  }));

els.partDeleteBtn.addEventListener("click", async () => {
  const part = currentPart();
  if (!part || !confirm(`Excluir a parte "${part.name}" e a tablatura dela?`)) return;
  try {
    const data = await sendJson("DELETE", projectUrl(`/parts/${part.part_id}`));
    state.project = { ...state.project, parts: data.job.parts };
    navigate(`#/p/${state.project.job_id}`);
  } catch (err) {
    banner(els.partStatus, `Erro ao excluir: ${err.message}`, "error");
  }
});

// ---------- tab player (AlphaTab) ----------

// Keep in sync with the <script> tag in index.html (never @latest: a new release can break playback).
const ALPHATAB_CDN = "https://cdn.jsdelivr.net/npm/@coderline/alphatab@1.9.0/dist";
let tabFontLoaded = null; // SoundFont URL the tab player currently has

/** Load the SoundFont chosen for the tab player if it is not the one already loaded. */
function syncTabFont() {
  const url = tabFontUrl();
  if (!alphaTabApi || url === tabFontLoaded) return;
  tabFontLoaded = url;
  // The player only exists once the score has loaded and then loads settings.player.soundFont;
  // before that, loadSoundFont() is silently ignored.
  alphaTabApi.settings.player.soundFont = url;
  if (alphaTabApi.player) alphaTabApi.loadSoundFont(url, false);
}

function initAlphaTab(musicXmlUrl) {
  if (typeof alphaTab === "undefined") {
    banner(els.tabStatus, "O AlphaTab não carregou (sem internet para baixar do CDN?).", "error");
    return;
  }
  if (alphaTabApi) {
    alphaTabApi.destroy();
    els.alphaTabContainer.innerHTML = "";
  }
  hide(els.tabStatus);
  alphaTabApi = new alphaTab.AlphaTabApi(els.alphaTabContainer, {
    core: {
      file: musicXmlUrl,
      fontDirectory: `${ALPHATAB_CDN}/font/`,
    },
    player: {
      enablePlayer: true,
      enableCursor: true,
      enableUserInteraction: true,
      soundFont: tabFontUrl(),
      scrollElement: els.alphaTabViewport,
    },
  });
  tabFontLoaded = tabFontUrl();
  tabRenderPending = els.playerSection.offsetParent === null;  // rendered at width 0: redo when shown
  // Without these the tab player fails silently (Play does nothing, no sound).
  alphaTabApi.soundFontLoad.on((e) => {
    if (e.total > 0 && e.loaded < e.total) banner(els.tabStatus, `Carregando o som da tablatura... ${Math.round((e.loaded / e.total) * 100)}%`, "busy");
  });
  alphaTabApi.soundFontLoaded.on(() => hide(els.tabStatus));
  alphaTabApi.playerReady.on(() => hide(els.tabStatus));
  alphaTabApi.error.on((err) => {
    console.error("[EscriTab] AlphaTab", err);
    banner(els.tabStatus, `Erro no player da tablatura: ${(err && err.message) || err}`, "error");
  });
  // transport LCD + play button state (same behaviour as the main DAW transport)
  resetTabLcd();
  alphaTabApi.scoreLoaded.on((score) => {
    $("tabBpmLcd").textContent = score && score.tempo ? Math.round(score.tempo) : "–";
  });
  alphaTabApi.playerStateChanged.on((e) => setTabPlaying(e.state === 1));
  alphaTabApi.playerPositionChanged.on((e) => {
    $("tabTime").textContent = fmtClock(e.currentTime / 1000);
    $("tabTimeTotal").textContent = fmtClock(e.endTime / 1000);
  });
  alphaTabApi.playedBeatChanged.on((beat) => {
    try {
      const bar = beat.voice.bar;
      const mb = bar.masterBar;
      const beatTicks = (960 * 4) / (mb.timeSignatureDenominator || 4);
      $("tabBarBeat").textContent = `${bar.index + 1}.${Math.floor(beat.playbackStart / beatTicks) + 1}`;
      const tempo = mb.tempoAutomations && mb.tempoAutomations.length ? mb.tempoAutomations[0].value : null;
      if (tempo) $("tabBpmLcd").textContent = Math.round(tempo);
    } catch { /* model shape differs: leave the LCD as is */ }
  });
  alphaTabApi.isLooping = els.tabLoopCheck.checked;
  alphaTabApi.playbackSpeed = Number(els.tabRate.value) / 100;
  alphaTabApi.metronomeVolume = els.metronomeCheck.checked ? 1 : 0;
}

function setTabPlaying(playing) {
  els.playPauseBtn.classList.toggle("is-playing", playing);
  els.playPauseBtn.setAttribute("aria-label", playing ? "Pausar" : "Play");
}
function resetTabLcd() {
  setTabPlaying(false);
  $("tabTime").textContent = fmtClock(0);
  $("tabTimeTotal").textContent = fmtClock(0);
  $("tabBarBeat").textContent = "–";
  $("tabBpmLcd").textContent = "–";
}
function setTabRate(pct) {
  const s = els.tabRate;
  s.value = pct;
  s.style.setProperty("--fill", `${((pct - Number(s.min)) / (Number(s.max) - Number(s.min))) * 100}%`);
  els.tabRateVal.textContent = `${pct}%`;
  els.tabRateVal.classList.toggle("slowed", pct !== 100);
  if (alphaTabApi) alphaTabApi.playbackSpeed = pct / 100;
}

els.playPauseBtn.addEventListener("click", () => alphaTabApi && alphaTabApi.playPause());
els.stopBtn.addEventListener("click", () => alphaTabApi && alphaTabApi.stop());
els.tabLoopCheck.addEventListener("change", () => {
  if (alphaTabApi) alphaTabApi.isLooping = els.tabLoopCheck.checked;
});
els.metronomeCheck.addEventListener("change", () => {
  if (alphaTabApi) alphaTabApi.metronomeVolume = els.metronomeCheck.checked ? 1 : 0;
});
els.tabRate.addEventListener("input", () => setTabRate(Number(els.tabRate.value)));
els.tabRate.addEventListener("dblclick", () => setTabRate(100));
setTabRate(100);

// ---------- sliders ----------

function bindSlider(slider, out, fmt) {
  slider.addEventListener("input", () => {
    out.textContent = fmt(slider.value);
  });
}
bindSlider(els.onsetSlider, els.onsetVal, (v) => Number(v).toFixed(2));
bindSlider(els.frameSlider, els.frameVal, (v) => Number(v).toFixed(2));
bindSlider(els.voicingSlider, els.voicingVal, (v) => Number(v).toFixed(2));
bindSlider(els.gateSlider, els.gateVal, (v) => v);
bindSlider(els.minLenSlider, els.minLenVal, (v) => v);

// ---------- SoundFonts + full-song MIDI player ----------

const SONIVOX_URL = `${ALPHATAB_CDN}/soundfont/sonivox.sf2`;
const PREFS_KEY = "escritab.sound";
// pretty_midi's layout: one channel per instrument in file order, skipping 9 (drums always on 9)
const MELODIC_CHANNELS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15];
const sound = { fonts: [], dirs: [], player: null, dragging: false };
const mpEls = {
  box: $("midiPlayer"),
  hint: $("mpFontsHint"),
  base: $("mpBaseFont"),
  rhythm: $("mpRhythmPreset"),
  lead: $("mpLeadPreset"),
  drums: $("mpDrumKit"),
  play: $("mpPlayBtn"),
  stop: $("mpStopBtn"),
  seek: $("mpSeek"),
  time: $("mpTime"),
  status: $("mpStatus"),
  tabFont: $("tabFontSelect"),
  summary: $("mpSoundSummary"),
  dialog: $("settingsDialog"),
  reload: $("reloadFontsBtn"),
};

function loadPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY)) || {};
  } catch {
    return {};
  }
}

function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      base: mpEls.base.value,
      rhythm: mpEls.rhythm.value,
      lead: mpEls.lead.value,
      drums: mpEls.drums.value,
      tab: mpEls.tabFont.value,
    }));
  } catch {
    /* private window: preferences are just not remembered */
  }
}

function fontById(id) {
  return sound.fonts.find((f) => f.id === id);
}

function addOption(parent, value, label) {
  const opt = document.createElement("option");
  opt.value = value;
  opt.textContent = label;
  parent.appendChild(opt);
}

function restoreValue(select, value) {
  if (value !== undefined && Array.from(select.options).some((o) => o.value === value)) select.value = value;
}

/** kind "guitar": every preset of the instrument SoundFonts + the guitars (24-31) of the GM banks; "drums": kits (bank 128). */
function fillPresetSelect(select, kind) {
  select.innerHTML = "";
  addOption(select, "", kind === "drums" ? "Kit padrão da SoundFont base" : "Original da faixa (SoundFont base)");
  for (const f of sound.fonts) {
    const presets = f.presets.filter((p) => kind === "drums"
      ? p.bank === 128
      : p.bank !== 128 && (!f.general_midi || (p.bank === 0 && p.program >= 24 && p.program <= 31)));
    if (!presets.length) continue;
    const group = document.createElement("optgroup");
    group.label = f.name;
    for (const p of presets) addOption(group, `${f.id}:${p.bank}:${p.program}`, `${p.name} (${p.bank}:${p.program})`);
    select.appendChild(group);
  }
}

/** Fetch the SoundFont library and (re)fill the settings selects, keeping the saved choices. */
async function refreshSoundfonts() {
  try {
    const data = await api("/api/soundfonts");
    sound.fonts = data.soundfonts;
    sound.dirs = data.dirs;
  } catch {
    sound.fonts = [];
  }
  const prefs = loadPrefs();
  const gm = sound.fonts.filter((f) => f.general_midi);

  mpEls.base.innerHTML = "";
  gm.forEach((f) => addOption(mpEls.base, f.id, `${f.name} (${f.size_mb} MB)`));
  fillPresetSelect(mpEls.rhythm, "guitar");
  fillPresetSelect(mpEls.lead, "guitar");
  fillPresetSelect(mpEls.drums, "drums");
  restoreValue(mpEls.base, prefs.base);
  restoreValue(mpEls.rhythm, prefs.rhythm);
  restoreValue(mpEls.lead, prefs.lead);
  restoreValue(mpEls.drums, prefs.drums);

  mpEls.tabFont.innerHTML = "";
  addOption(mpEls.tabFont, "", "Sonivox (padrão, leve)");
  gm.forEach((f) => addOption(mpEls.tabFont, f.id, `${f.name} (${f.size_mb} MB)`));
  restoreValue(mpEls.tabFont, prefs.tab);
  syncTabFont(); // the tab may have been created before the saved choice was restored

  mpEls.hint.textContent = sound.fonts.length
    ? `${sound.fonts.length} SoundFont(s) em ${sound.dirs.join(", ")}`
    : `Nenhuma SoundFont em ${sound.dirs.join(", ") || "./soundfonts"}`;
  renderSoundSummary();
}

function selectedLabel(select) {
  const opt = select.selectedOptions[0];
  return opt ? opt.textContent.replace(/\s*\([^)]*\)$/, "") : "—";
}

/** One line under the player telling which sounds are in use (they are changed in Configurações). */
function renderSoundSummary() {
  if (!mpEls.base.value) {
    mpEls.summary.textContent = "Nenhuma SoundFont General MIDI selecionada.";
    return;
  }
  const parts = [`Base: ${selectedLabel(mpEls.base)}`];
  if (mpEls.rhythm.value) parts.push(`Guitarra base: ${selectedLabel(mpEls.rhythm)}`);
  if (mpEls.lead.value) parts.push(`Solo: ${selectedLabel(mpEls.lead)}`);
  if (mpEls.drums.value) parts.push(`Bateria: ${selectedLabel(mpEls.drums)}`);
  mpEls.summary.textContent = parts.join(" · ");
}

async function loadSoundfonts() {
  sound.player = typeof MidiPlayer !== "undefined" && MidiPlayer.available() ? new MidiPlayer() : null;
  await refreshSoundfonts();
  if (sound.player) sound.player.onState = mpRender;
  mpLoadSong();
}

function openSettings() {
  if (typeof mpEls.dialog.showModal === "function") mpEls.dialog.showModal();
  else mpEls.dialog.setAttribute("open", "");
}

$("settingsBtn").addEventListener("click", openSettings);
document.querySelectorAll("[data-open-settings]").forEach((b) => b.addEventListener("click", openSettings));
// click on the backdrop closes the dialog
mpEls.dialog.addEventListener("click", (e) => {
  if (e.target === mpEls.dialog) mpEls.dialog.close();
});

mpEls.reload.addEventListener("click", async () => {
  mpEls.reload.disabled = true;
  try {
    await refreshSoundfonts();
    if (!sound.player || !sound.player.playing) mpLoadSong();
  } finally {
    mpEls.reload.disabled = false;
  }
});

function tabFontUrl() {
  const id = mpEls.tabFont.value;
  return id ? `/api/soundfonts/${id}` : SONIVOX_URL;
}

function mpReset() {
  if (sound.player) sound.player.stop();
  hide(mpEls.status);
  mpEls.box.classList.add("hidden");
}

async function mpLoadSong() {
  const midiName = state.project && (state.project.artifacts || {}).multitrack_midi;
  const tracks = (state.project && state.project.multitrack) || [];
  if (!midiName || !tracks.length) {
    mpReset();
    return;
  }
  mpEls.box.classList.remove("hidden");
  const problem = !sound.player
    ? "O player não carregou (sem internet para baixar o FluidSynth?)."
    : !sound.fonts.some((f) => f.general_midi)
      ? `Nenhuma SoundFont General MIDI encontrada em ${sound.dirs.join(", ") || "./soundfonts"}. Coloque um .sf2 (ex.: GeneralUser GS) lá ou defina ESCRITAB_SOUNDFONTS.`
      : null;
  mpEls.play.disabled = mpEls.stop.disabled = Boolean(problem);
  if (problem) {
    banner(mpEls.status, problem, "error");
    return;
  }
  hide(mpEls.status);
  try {
    let melodicIdx = 0;
    await sound.player.setSong(
      fileUrl(midiName),
      tracks.map((t) => ({
        ...t,
        channel: t.is_drum ? 9 : MELODIC_CHANNELS[melodicIdx++ % MELODIC_CHANNELS.length],
      })),
    );
  } catch (err) {
    banner(mpEls.status, `Erro ao carregar o MIDI: ${err.message}`, "error");
  }
}

function parseRoute(value) {
  const [sf, bank, program] = value.split(":");
  return { sf, bank: Number(bank), program: Number(program) };
}

/** Which SoundFont / bank / preset each channel plays; loads the SoundFonts that are still missing. */
async function mpApplyRoutes() {
  const p = sound.player;
  const base = mpEls.base.value;
  const routes = new Map();
  for (const t of p.channels.values()) {
    const original = { sf: base, bank: 0, program: t.program ?? 0 };
    let r = original;
    if (t.is_drum) r = mpEls.drums.value ? parseRoute(mpEls.drums.value) : { sf: base, bank: 128, program: 0 };
    else if (t.role === "lead" || t.role === "lead_candidate") r = mpEls.lead.value ? parseRoute(mpEls.lead.value) : original;
    else if (t.is_guitar) r = mpEls.rhythm.value ? parseRoute(mpEls.rhythm.value) : original;
    routes.set(t.channel, r);
  }
  const missing = [...new Set([...routes.values()].map((r) => r.sf))].filter((id) => !p.fonts.has(id));
  for (const id of missing) {
    const f = fontById(id);
    banner(mpEls.status, `Carregando a SoundFont ${f ? f.name : id} (${f ? f.size_mb : "?"} MB)...`, "busy");
    await p.loadFont(id);
  }
  hide(mpEls.status);
  p.setRoutes(routes);
}

function fmtTime(sec) {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function mpRender() {
  const p = sound.player;
  if (!p) return;
  mpEls.play.innerHTML = p.playing ? "&#10074;&#10074; Pause" : "&#9654; Play";
  mpEls.seek.disabled = !p.total;
  if (p.total && !sound.dragging) mpEls.seek.value = String(Math.round((p.position / p.total) * 1000));
  mpEls.time.textContent = p.total ? `${fmtTime(p.seconds(p.position))} / ${fmtTime(p.seconds(p.total))}` : "0:00";
  els.multitrackTracks.querySelectorAll(".ms-btn").forEach((b) => {
    const i = Number(b.dataset.track);
    b.classList.toggle("active", b.dataset.kind === "mute" ? p.muted.has(i) : p.soloed.has(i));
  });
}

mpEls.play.addEventListener("click", async () => {
  const p = sound.player;
  if (!p) return;
  if (p.playing) {
    p.pause();
    return;
  }
  mpEls.play.disabled = true;
  try {
    await mpApplyRoutes();
    await p.play();
  } catch (err) {
    banner(mpEls.status, `Erro no player: ${err.message}`, "error");
  } finally {
    mpEls.play.disabled = false;
  }
});

mpEls.stop.addEventListener("click", () => sound.player && sound.player.stop());

mpEls.seek.addEventListener("input", () => {
  sound.dragging = true;
});
mpEls.seek.addEventListener("change", () => {
  sound.dragging = false;
  const p = sound.player;
  if (p && p.total) p.seek(Math.round((Number(mpEls.seek.value) / 1000) * p.total));
});

[mpEls.base, mpEls.rhythm, mpEls.lead, mpEls.drums].forEach((sel) => {
  sel.addEventListener("change", async () => {
    savePrefs();
    renderSoundSummary();
    const p = sound.player;
    if (!p || !p.playing) return; // applied on the next Play
    try {
      await mpApplyRoutes();
    } catch (err) {
      banner(mpEls.status, `Erro ao trocar o instrumento: ${err.message}`, "error");
    }
  });
});

els.multitrackTracks.addEventListener("click", (e) => {
  const b = e.target.closest(".ms-btn");
  if (!b || !sound.player) return;
  e.preventDefault(); // the row is a <label>: do not toggle the tab checkbox
  const i = Number(b.dataset.track);
  if (b.dataset.kind === "mute") sound.player.toggleMute(i);
  else sound.player.toggleSolo(i);
});

mpEls.tabFont.addEventListener("change", () => {
  savePrefs();
  syncTabFont();
});

// ---------- init ----------

syncEngineControls();
loadSystemInfo();
loadSoundfonts();
window.addEventListener("hashchange", route);
// the part view restores the tuning, so the list must exist first
loadTunings().catch(() => {}).finally(route);
