let currentJobId = null;
let alphaTabApi = null;

const els = {
  recentJobsSelect: document.getElementById("recentJobsSelect"),
  uploadForm: document.getElementById("uploadForm"),
  audioFileInput: document.getElementById("audioFileInput"),
  isolatedStemCheck: document.getElementById("isolatedStemCheck"),
  demucsModelSelect: document.getElementById("demucsModelSelect"),
  uploadBtn: document.getElementById("uploadBtn"),
  step1Status: document.getElementById("step1Status"),
  stemsContainer: document.getElementById("stemsContainer"),
  selectedStemSelect: document.getElementById("selectedStemSelect"),
  onsetSlider: document.getElementById("onsetSlider"),
  onsetVal: document.getElementById("onsetVal"),
  frameSlider: document.getElementById("frameSlider"),
  frameVal: document.getElementById("frameVal"),
  minLenSlider: document.getElementById("minLenSlider"),
  minLenVal: document.getElementById("minLenVal"),
  gateSlider: document.getElementById("gateSlider"),
  gateVal: document.getElementById("gateVal"),
  quantizeSelect: document.getElementById("quantizeSelect"),
  bandpassCheck: document.getElementById("bandpassCheck"),
  suppressHarmonicsCheck: document.getElementById("suppressHarmonicsCheck"),
  transcribeBtn: document.getElementById("transcribeBtn"),
  midiStatsBadge: document.getElementById("midiStatsBadge"),
  tuningSelect: document.getElementById("tuningSelect"),
  fretPrefSelect: document.getElementById("fretPrefSelect"),
  bpmInput: document.getElementById("bpmInput"),
  maxFretsInput: document.getElementById("maxFretsInput"),
  generateTabBtn: document.getElementById("generateTabBtn"),
  exportLinks: document.getElementById("exportLinks"),
  dlGp5: document.getElementById("dlGp5"),
  dlXml: document.getElementById("dlXml"),
  dlMid: document.getElementById("dlMid"),
  dlTxt: document.getElementById("dlTxt"),
  playPauseBtn: document.getElementById("playPauseBtn"),
  stopBtn: document.getElementById("stopBtn"),
  loopCheck: document.getElementById("loopCheck"),
  metronomeCheck: document.getElementById("metronomeCheck"),
  speedSelect: document.getElementById("speedSelect"),
  alphaTabContainer: document.getElementById("alphaTabContainer"),
  alphaTabViewport: document.getElementById("alphaTabViewport"),
  asciiPreviewBox: document.getElementById("asciiPreviewBox"),
};

function setStatus(msg, isError = false) {
  els.step1Status.textContent = msg;
  els.step1Status.classList.remove("hidden");
  els.step1Status.style.borderColor = isError ? "#ef4444" : "#3b82f6";
}

function bindSliders() {
  els.onsetSlider.addEventListener("input", () => {
    els.onsetVal.textContent = Number(els.onsetSlider.value).toFixed(2);
  });
  els.frameSlider.addEventListener("input", () => {
    els.frameVal.textContent = Number(els.frameSlider.value).toFixed(2);
  });
  els.minLenSlider.addEventListener("input", () => {
    els.minLenVal.textContent = els.minLenSlider.value;
  });
  els.gateSlider.addEventListener("input", () => {
    els.gateVal.textContent = els.gateSlider.value;
  });
}

async function loadTunings() {
  const res = await fetch("/api/tunings");
  const tunings = await res.json();
  els.tuningSelect.innerHTML = "";
  for (const t of tunings) {
    const opt = document.createElement("option");
    opt.value = t.id;
    opt.textContent = t.name;
    els.tuningSelect.appendChild(opt);
  }
}

async function loadRecentJobs() {
  const res = await fetch("/api/jobs");
  const jobs = await res.json();
  els.recentJobsSelect.innerHTML = '<option value="">-- Nova Sessão --</option>';
  for (const j of jobs) {
    const opt = document.createElement("option");
    opt.value = j.job_id;
    opt.textContent = `${j.original_filename} (${j.job_id.slice(0, 6)})`;
    els.recentJobsSelect.appendChild(opt);
  }
}

function renderStems(job) {
  els.stemsContainer.innerHTML = "";
  const stemEntries = Object.entries(job.stems || {});
  if (stemEntries.length === 0) {
    els.stemsContainer.innerHTML = '<p class="muted">Nenhuma stem extraída ainda.</p>';
    return;
  }
  for (const [stemName, filename] of stemEntries) {
    const row = document.createElement("div");
    row.className = "stem-row";
    const label = document.createElement("strong");
    label.textContent = stemName;
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.src = `/api/jobs/${job.job_id}/files/${filename}`;
    row.appendChild(label);
    row.appendChild(audio);
    els.stemsContainer.appendChild(row);
  }
  els.transcribeBtn.disabled = false;
}

function initAlphaTab(musicXmlUrl) {
  if ( typeof alphaTab === "undefined" ) {
    return;
  }
  if (alphaTabApi) {
    alphaTabApi.destroy();
    els.alphaTabContainer.innerHTML = "";
  }
  alphaTabApi = new alphaTab.AlphaTabApi(els.alphaTabContainer, {
    core: {
      file: musicXmlUrl,
      fontDirectory: "https://cdn.jsdelivr.net/npm/@coderline/alphatab@latest/dist/font/",
    },
    player: {
      enablePlayer: true,
      enableCursor: true,
      enableUserInteraction: true,
      soundFont: "https://cdn.jsdelivr.net/npm/@coderline/alphatab@latest/dist/soundfont/sonivox.sf2",
      scrollElement: els.alphaTabViewport,
    },
  });

  els.playPauseBtn.disabled = false;
  els.stopBtn.disabled = false;
}

els.uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = els.audioFileInput.files[0];
  if (!file) return;

  els.uploadBtn.disabled = true;
  setStatus("Enviando áudio e detectando BPM...");

  try {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("is_isolated_stem", els.isolatedStemCheck.checked ? "true" : "false");

    const upRes = await fetch("/api/jobs/upload", { method: "POST", body: formData });
    if (!upRes.ok) throw new Error(await upRes.text());
    let job = await upRes.json();
    currentJobId = job.job_id;
    els.bpmInput.value = job.detected_bpm || 120;

    if (!job.is_isolated_stem) {
      setStatus("Separando stems com Demucs (GPU/CPU)... Isso pode levar alguns instantes.");
      const sepRes = await fetch(`/api/jobs/${currentJobId}/separate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: els.demucsModelSelect.value }),
      });
      if (!sepRes.ok) throw new Error(await sepRes.text());
      const sepData = await sepRes.json();
      job = sepData.job;
      setStatus(`Stems separadas com sucesso (Dispositivo: ${sepData.device}).`);
    } else {
      setStatus("Stem isolada carregada! Pronta para transcrição MIDI.");
    }

    renderStems(job);
    await loadRecentJobs();
    els.recentJobsSelect.value = currentJobId;
  } catch (err) {
    setStatus(`Erro: ${err.message}`, true);
  } finally {
    els.uploadBtn.disabled = false;
  }
});

els.transcribeBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  els.transcribeBtn.disabled = true;
  els.midiStatsBadge.classList.remove("hidden");
  els.midiStatsBadge.textContent = "Transcrevendo áudio para MIDI...";

  try {
    const payload = {
      stem: els.selectedStemSelect.value,
      bandpass_enabled: els.bandpassCheck.checked,
      noise_gate_db: Number(els.gateSlider.value),
      onset_threshold: Number(els.onsetSlider.value),
      frame_threshold: Number(els.frameSlider.value),
      minimum_note_length_ms: Number(els.minLenSlider.value),
      suppress_harmonics: els.suppressHarmonicsCheck.checked,
      quantize_grid: els.quantizeSelect.value,
      tuning: els.tuningSelect.value,
      max_frets: Number(els.maxFretsInput.value),
      bpm: Number(els.bpmInput.value),
    };

    const res = await fetch(`/api/jobs/${currentJobId}/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Erro na transcrição");
    }

    const st = data.stats;
    els.midiStatsBadge.textContent = `${st.total_notes} notas (${st.lowest_note || ""} a ${st.highest_note || ""}) | Harmônicos removidos: ${st.harmonics_removed}`;
    els.generateTabBtn.disabled = false;
  } catch (err) {
    els.midiStatsBadge.textContent = `Aviso: ${err.message}`;
  } finally {
    els.transcribeBtn.disabled = false;
  }
});

els.generateTabBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  els.generateTabBtn.disabled = true;

  try {
    const payload = {
      tuning: els.tuningSelect.value,
      max_frets: Number(els.maxFretsInput.value),
      fret_preference: els.fretPrefSelect.value,
      bpm: Number(els.bpmInput.value),
    };

    const res = await fetch(`/api/jobs/${currentJobId}/tab`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Erro ao gerar tablatura");

    els.dlGp5.href = data.artifacts.gp5;
    els.dlXml.href = data.artifacts.musicxml;
    els.dlMid.href = data.artifacts.midi;
    els.dlTxt.href = data.artifacts.txt;
    els.exportLinks.classList.remove("hidden");

    els.asciiPreviewBox.textContent = data.ascii_preview;
    initAlphaTab(data.artifacts.musicxml);
  } catch (err) {
    alert(`Erro ao gerar tablatura: ${err.message}`);
  } finally {
    els.generateTabBtn.disabled = false;
  }
});

els.recentJobsSelect.addEventListener("change", async () => {
  const jobId = els.recentJobsSelect.value;
  if (!jobId) return;
  const res = await fetch(`/api/jobs/${jobId}`);
  if (!res.ok) return;
  const job = await res.json();
  currentJobId = job.job_id;
  els.bpmInput.value = job.detected_bpm || 120;
  renderStems(job);
  if (job.artifacts && job.artifacts.midi) {
    els.generateTabBtn.disabled = false;
  }
  if (job.artifacts && job.artifacts.musicxml) {
    const xmlUrl = `/api/jobs/${job.job_id}/files/${job.artifacts.musicxml}`;
    initAlphaTab(xmlUrl);
  }
});

els.playPauseBtn.addEventListener("click", () => {
  if (alphaTabApi) alphaTabApi.playPause();
});
els.stopBtn.addEventListener("click", () => {
  if (alphaTabApi) alphaTabApi.stop();
});
els.loopCheck.addEventListener("change", () => {
  if (alphaTabApi) alphaTabApi.isLooping = els.loopCheck.checked;
});
els.metronomeCheck.addEventListener("change", () => {
  if (alphaTabApi) alphaTabApi.metronomeVolume = els.metronomeCheck.checked ? 1 : 0;
});
els.speedSelect.addEventListener("change", () => {
  if (alphaTabApi) alphaTabApi.playbackSpeed = Number(els.speedSelect.value);
});

bindSliders();
loadTunings();
loadRecentJobs();
