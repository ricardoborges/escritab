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
  isolatedStemCheck: $("isolatedStemCheck"),
  demucsModelSelect: $("demucsModelSelect"),
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
  projZoom: $("projZoom"),
  projSource: $("projSource"),
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
  loopCheck: $("loopCheck"),
  metronomeCheck: $("metronomeCheck"),
  speedSelect: $("speedSelect"),
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
    if (r.view === "project") await renderProjectView();
    else await renderPartView();
  } catch (err) {
    showView("projects");
    await loadProjectList();
    banner(els.uploadStatus, `Não foi possível abrir: ${err.message}`, "error");
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

function selectedAnalysis() {
  return els.newProjectForm.querySelector("input[name=analysis]:checked").value;
}
els.newProjectForm.querySelectorAll("input[name=analysis]").forEach((r) =>
  r.addEventListener("change", () => {
    document.querySelectorAll(".classic-only").forEach((x) => show(x, selectedAnalysis() === "classic"));
  }));

els.audioFileInput.addEventListener("change", () => {
  const f = els.audioFileInput.files[0];
  els.fileNameDisplay.textContent = f ? f.name : "Arraste um MP3/WAV/FLAC ou clique para selecionar";
  if (f && !els.projectNameInput.value) els.projectNameInput.placeholder = f.name.replace(/\.[^.]+$/, "").replace(/_+/g, " ");
});

els.newProjectForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = els.audioFileInput.files[0];
  if (!file || state.busy) return;
  const mode = selectedAnalysis();
  const isolated = mode === "classic" && els.isolatedStemCheck.checked;
  setBusy(true);
  els.createProjectBtn.disabled = true;
  banner(els.uploadStatus, "Enviando o áudio e detectando o andamento...", "busy");
  try {
    const form = new FormData();
    form.append("file", file);
    form.append("name", els.projectNameInput.value);
    form.append("mode", mode);
    form.append("is_isolated_stem", isolated ? "true" : "false");
    const project = await api("/api/projects", { method: "POST", body: form });
    els.newProjectForm.reset();
    els.fileNameDisplay.textContent = "Arraste um MP3/WAV/FLAC ou clique para selecionar";
    hide(els.uploadStatus);
    state.project = project;
    state.peaks = null;
    navigate(`#/p/${project.job_id}`);
    // the analysis runs in the project view, so the waveform is usable meanwhile
    setTimeout(() => {
      if (mode === "dettato") runMultitrack();
      else if (!isolated) runSeparate(els.demucsModelSelect.value);
    }, 0);
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
  return fileUrl(stem ? state.project.stems[stem] : state.project.input_path);
}
function fillSourceSelect(select, value) {
  select.innerHTML = "";
  for (const [v, label] of audioSources()) select.append(el("option", { value: v }, label));
  select.value = audioSources().some(([v]) => v === value) ? value : "";
  select.disabled = audioSources().length < 2;
}

async function createWave(container, stem) {
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
  });
  const regions = ws.registerPlugin(WaveSurfer.Regions.create());
  const w = { ws, regions, loop: null, draft: null, stem };
  ws.on("timeupdate", (t) => {
    if (w.loop && t >= w.loop.end) ws.setTime(w.loop.start);
    if (w.onTime) w.onTime(t);
  });
  // a click outside the looped excerpt ends the loop
  ws.on("interaction", (t) => {
    if (w.loop && (t < w.loop.start || t > w.loop.end)) w.loop = null;
  });
  return w;
}

/** Run fn once the waveform knows the duration (regions created before it collapse to 0). */
function whenReady(w, fn) {
  if (w.ws.getDecodedData() && w.ws.getDuration()) fn();
  else w.ws.once("ready", () => fn());
}

function destroyWave(w) {
  if (!w) return;
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
  hide(els.projectStatus);
  els.projNameInput.value = p.name;
  els.projBpmInput.value = p.detected_bpm;
  els.projMeta.textContent = `${fmtClock(p.duration, 0)} · ${p.original_filename}`;
  renderAnalysis();
  renderPartList();
  fillSourceSelect(els.projSource, "");
  hide(els.draftPanel);
  els.projZoom.value = "0";

  projWave = await createWave(els.projWave, "");
  const w = projWave;
  w.onTime = (t) => {
    els.projTime.textContent = `${fmtClock(t)} / ${fmtClock(p.duration)}`;
  };
  w.onTime(0);
  w.ws.on("play", () => (els.projPlayBtn.innerHTML = "&#10074;&#10074; Pause"));
  w.ws.on("pause", () => (els.projPlayBtn.innerHTML = "&#9654; Play"));
  whenReady(w, () => {
    if (projWave !== w) return;
    drawPartRegions();
    w.regions.enableDragSelection({ color: DRAFT_COLOR });
  });
  w.regions.on("region-created", (region) => {
    if (region.id.startsWith("part-")) return;
    if (w.draft && w.draft !== region) w.draft.remove();
    w.draft = region;
    snapDraft();
    showDraft();
  });
  w.regions.on("region-updated", (region) => {
    if (region === w.draft) {
      snapDraft();
      showDraft();
      if (w.loop) w.loop = { start: region.start, end: region.end };
    }
  });
  w.regions.on("region-clicked", (region, e) => {
    e.stopPropagation();
    playLoop(w, region.start, region.end);
  });
  w.regions.on("region-double-clicked", (region) => {
    if (region.id.startsWith("part-")) navigate(`#/p/${p.job_id}/part/${region.id.slice(5)}`);
  });
}

function drawPartRegions() {
  const w = projWave;
  if (!w) return;
  w.regions.getRegions().filter((r) => r.id.startsWith("part-")).forEach((r) => r.remove());
  for (const part of state.project.parts) {
    w.regions.addRegion({
      id: `part-${part.part_id}`,
      start: part.start,
      end: part.end,
      color: withAlpha(partColor(part), 0.22),
      content: part.name,
      drag: false,
      resize: false,
    });
  }
}

function snapDraft() {
  const r = projWave.draft;
  const start = snapTime(r.start);
  let end = snapTime(r.end);
  if (end - start < 0.5) end = r.end;
  r.setOptions({ start, end });
}

function showDraft() {
  const r = projWave.draft;
  els.draftRange.textContent = fmtRange(r.start, r.end);
  show(els.draftPanel);
  if (!els.draftName.value) els.draftName.focus();
}

function discardDraft() {
  if (projWave && projWave.draft) {
    if (projWave.loop) {
      projWave.loop = null;
      projWave.ws.pause();
    }
    projWave.draft.remove();
    projWave.draft = null;
  }
  els.draftName.value = "";
  hide(els.draftPanel);
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
});

els.draftPlayBtn.addEventListener("click", () => {
  const r = projWave && projWave.draft;
  if (r) playLoop(projWave, r.start, r.end);
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
  if (!name) {
    els.draftName.focus();
    banner(els.projectStatus, "Dê um nome para a parte (ex.: Intro, Riff, Solo).", "error");
    return;
  }
  els.draftSaveBtn.disabled = true;
  try {
    const data = await postJson(projectUrl("/parts"), { name, start: r.start, end: r.end });
    state.project = data.job;
    discardDraft();
    hide(els.projectStatus);
    drawPartRegions();
    renderPartList();
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
  for (const part of parts) {
    const ready = !!part.artifacts.musicxml;
    const status = ready
      ? el("span", { class: "badge ok" }, `tab pronta · ${part.stats.total_notes || 0} notas`)
      : el("span", { class: "badge" }, "sem tab");
    const row = el("div", { class: "part-row" },
      el("span", { class: "part-dot", style: `background:${partColor(part)}` }),
      el("strong", { class: "part-name" }, part.name),
      el("span", { class: "muted" }, fmtRange(part.start, part.end)),
      status,
      el("span", { class: "part-actions" },
        el("button", {
          type: "button",
          class: "btn-outline btn-small",
          title: "Ouvir em loop",
          onclick: () => projWave && playLoop(projWave, part.start, part.end),
        }, "⟳ Ouvir"),
        el("a", { class: "btn-primary btn-small", href: `#/p/${state.project.job_id}/part/${part.part_id}` },
          ready ? "Estudar →" : "Transcrever →")));
    els.partList.append(row);
  }
}

els.projPlayBtn.addEventListener("click", () => {
  if (!projWave) return;
  projWave.ws.playPause();
});
els.projStopBtn.addEventListener("click", () => {
  if (!projWave) return;
  projWave.loop = null;
  projWave.ws.stop();
});
els.projZoom.addEventListener("input", () => {
  if (projWave && projWave.ws.getDecodedData()) projWave.ws.zoom(Number(els.projZoom.value));
});
els.projSource.addEventListener("change", () => {
  if (projWave) switchSource(projWave, els.projSource.value);
});
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
  if (!tracks.length && !stems.length) els.analysisBox.open = true;

  show(els.mixerBox, tracks.length > 0);
  renderMixer();
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
  els.analysisBox.open = true;
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
  els.analysisBox.open = true;
  banner(els.analysisStatus, onDevice(
    "Separando as stems com Demucs na GPU... você já pode marcar as partes enquanto isso.",
    "Separando as stems com Demucs na CPU... pode levar vários minutos. Você já pode marcar as partes."), "busy");
  const projectId = state.project.job_id;
  try {
    const data = await postJson(`/api/projects/${projectId}/separate`, { model: model || els.separateModelSelect.value });
    if (state.project && state.project.job_id === projectId) {
      state.project = { ...data.job, parts: state.project.parts };
      renderAnalysis();
      if (state.view === "project") fillSourceSelect(els.projSource, els.projSource.value);
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

// ---------- view: part ----------

async function renderPartView() {
  const part = currentPart();
  els.partNameInput.value = part.name;
  renderPartMeta();
  hide(els.partStatus);
  els.partRate.value = "100";
  els.partRateVal.textContent = "100%";
  fillSourceSelect(els.partSource, preferredStem(part));
  renderSourcePicker(part);
  restorePartSettings(part);
  renderPartResult(part, null);

  partWave = await createWave(els.partWave, els.partSource.value);
  const w = partWave;
  w.excerpt = { start: part.start, end: part.end };   // replaced by the region once the wave is ready
  w.loop = { start: part.start, end: part.end };
  w.onTime = (t) => {
    const r = w.excerpt;
    els.partTime.textContent = `${fmtClock(Math.max(0, t - r.start))} / ${fmtClock(r.end - r.start)}`;
  };
  w.onTime(part.start);
  w.ws.on("play", () => (els.partPlayBtn.innerHTML = "&#10074;&#10074; Pausar"));
  w.ws.on("pause", () => (els.partPlayBtn.innerHTML = "&#10227; Tocar trecho"));
  w.regions.on("region-updated", (r) => {
    els.partStartInput.value = r.start.toFixed(2);
    els.partEndInput.value = r.end.toFixed(2);
    w.loop = { start: r.start, end: r.end };
    els.partRangeSaveBtn.classList.add("btn-attention");
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
  document.querySelectorAll(".basic-pitch-only").forEach((x) => show(x, els.engineSelect.value === "basic_pitch"));
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
    els.gateSlider.value = tp.noise_gate_db;
    els.bandpassCheck.checked = tp.bandpass_enabled;
  }
  [els.onsetSlider, els.frameSlider, els.gateSlider, els.minLenSlider].forEach((s) => s.dispatchEvent(new Event("input")));
}

function renderPartResult(part, asciiText) {
  const st = part.stats || {};
  els.partSummary.textContent = st.total_notes
    ? `${st.total_notes} notas${st.lowest_note ? ` (${st.lowest_note} a ${st.highest_note})` : ""} · ${st.engine || ""}`
    : "";
  els.generateBtn.textContent = part.artifacts.musicxml ? "Gerar de novo" : "Gerar tablatura do trecho";
  if (!part.artifacts.musicxml) {
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
  w.loop = { start, end };
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
  w.loop = { start, end };
  if (t < start || t >= end - 0.05) w.ws.setTime(start);
  w.ws.play();
});
els.partStopBtn.addEventListener("click", () => {
  const w = partWave;
  if (!w) return;
  w.ws.pause();
  w.ws.setTime(w.excerpt.start);
});
els.partRate.addEventListener("input", () => {
  const rate = Number(els.partRate.value) / 100;
  els.partRateVal.textContent = `${els.partRate.value}%`;
  if (partWave) partWave.ws.setPlaybackRate(rate, true);
});
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
  alphaTabApi.isLooping = els.loopCheck.checked;
  alphaTabApi.playbackSpeed = Number(els.speedSelect.value);
  alphaTabApi.metronomeVolume = els.metronomeCheck.checked ? 1 : 0;
}

els.playPauseBtn.addEventListener("click", () => alphaTabApi && alphaTabApi.playPause());
els.stopBtn.addEventListener("click", () => alphaTabApi && alphaTabApi.stop());
els.loopCheck.addEventListener("change", () => {
  if (alphaTabApi) alphaTabApi.isLooping = els.loopCheck.checked;
});
els.metronomeCheck.addEventListener("change", () => {
  if (alphaTabApi) alphaTabApi.metronomeVolume = els.metronomeCheck.checked ? 1 : 0;
});
els.speedSelect.addEventListener("change", () => {
  if (alphaTabApi) alphaTabApi.playbackSpeed = Number(els.speedSelect.value);
});

// ---------- sliders ----------

function bindSlider(slider, out, fmt) {
  slider.addEventListener("input", () => {
    out.textContent = fmt(slider.value);
  });
}
bindSlider(els.onsetSlider, els.onsetVal, (v) => Number(v).toFixed(2));
bindSlider(els.frameSlider, els.frameVal, (v) => Number(v).toFixed(2));
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
    await sound.player.setSong(
      fileUrl(midiName),
      tracks.map((t) => ({ ...t, channel: t.is_drum ? 9 : MELODIC_CHANNELS[t.index % MELODIC_CHANNELS.length] })),
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
