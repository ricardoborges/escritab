// Virtualized Canvas 2D Piano Roll engine + pure note-reading helpers.
// Exposes window.PianoRoll (and CommonJS module.exports in Node.js for unit tests).
(function (root) {
  "use strict";

  const KEY_W = 56, RULER_H = 28, MIN_PX_PER_SEC = 4, MAX_PX_PER_SEC = 800;
  const SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
  const PT_LETTERS = { C: "Dó", D: "Ré", E: "Mi", F: "Fá", G: "Sol", A: "Lá", B: "Si" };
  const BLACK_PCS = new Set([1, 3, 6, 8, 10]);
  const NOTE_TO_PC = {
    C: 0, "B#": 0, "C#": 1, DB: 1, D: 2, "D#": 3, EB: 3, E: 4, FB: 4, "E#": 5, F: 5,
    "F#": 6, GB: 6, G: 7, "G#": 8, AB: 8, A: 9, "A#": 10, BB: 10, B: 11, CB: 11,
  };
  const GM_DRUMS = {
    35: ["Kick", "Bumbo"], 36: ["Kick", "Bumbo"], 37: ["Rim", "Aro"], 38: ["Snare", "Caixa"],
    39: ["Clap", "Palma"], 40: ["Snare", "Caixa"], 41: ["Low Tom", "Surdo grave"],
    42: ["HH", "Chimbal fechado"], 43: ["Low Tom", "Surdo"], 44: ["Pedal HH", "Chimbal pedal"],
    45: ["Mid Tom", "Tom médio"], 46: ["OHH", "Chimbal aberto"], 47: ["Mid Tom", "Tom médio"],
    48: ["Hi Tom", "Tom agudo"], 49: ["Crash", "Prato de ataque"], 50: ["Hi Tom", "Tom agudo"],
    51: ["Ride", "Condução"], 52: ["China", "Prato China"], 53: ["Ride Bell", "Cúpula"],
    54: ["Tamb", "Pandeiro"], 55: ["Splash", "Splash"], 56: ["Cowbell", "Caneca"],
    57: ["Crash", "Prato de ataque"], 59: ["Ride", "Condução"],
  };
  const TRACK_COLORS = [
    "#3b82f6", "#f59e0b", "#10b981", "#a855f7", "#f43f5e",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#eab308",
  ];

  function bisect(arr, target, inclusive, getter) {
    let lo = 0, hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      const v = getter(arr, mid);
      if (inclusive ? v <= target : v < target) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  function hexRgb(hex) {
    const clean = String(hex || "#3b82f6").replace("#", "");
    const n = parseInt(clean.length === 3 ? clean.replace(/(.)/g, "$1$1") : clean, 16);
    return Number.isFinite(n) ? [(n >> 16) & 255, (n >> 8) & 255, n & 255] : [59, 130, 246];
  }

  function isLightRgb(r, g, b) {
    return 0.299 * r + 0.587 * g + 0.114 * b > 155;
  }

  function fmtClock(sec) {
    const s = Math.max(0, Math.floor(sec));
    return `${Math.floor(s / 60)}:${s % 60 < 10 ? "0" : ""}${s % 60}`;
  }

  function withClip(ctx, x, y, w, h, fn) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();
    fn();
    ctx.restore();
  }

  function roundRectPath(ctx, x, y, w, h, r) {
    const rad = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.beginPath();
    if (typeof ctx.roundRect === "function") { ctx.roundRect(x, y, w, h, rad); return; }
    ctx.moveTo(x + rad, y);
    ctx.arcTo(x + w, y, x + w, y + h, rad);
    ctx.arcTo(x + w, y + h, x, y + h, rad);
    ctx.arcTo(x, y + h, x, y, rad);
    ctx.arcTo(x, y, x + w, y, rad);
    ctx.closePath();
  }

  function parseNotePc(token) {
    if (typeof token === "number" && Number.isFinite(token)) return ((Math.round(token) % 12) + 12) % 12;
    if (typeof token !== "string") return null;
    const m = token.trim().match(/^([A-Ga-g][#b]?)/);
    const pc = m ? NOTE_TO_PC[m[1].toUpperCase()] : undefined;
    return pc === undefined ? null : pc;
  }

  class PianoRoll {
    static TRACK_COLORS = TRACK_COLORS;

    static trackColor(index) {
      return TRACK_COLORS[Math.abs(Number(index) || 0) % TRACK_COLORS.length];
    }

    static prepareTracks(rawTracks) {
      if (!Array.isArray(rawTracks)) return [];
      return rawTracks.map((t, idx) => {
        const trackIndex = t && t.index !== undefined ? Number(t.index) : idx;
        const notes = (Array.isArray(t?.notes) ? t.notes : [])
          .map((n) => [Number(n[0]), Number(n[1]), Number(n[2]), Number(n[3] ?? 96)]);
        for (let i = 1; i < notes.length; i++) {
          if (notes[i][0] < notes[i - 1][0] || (notes[i][0] === notes[i - 1][0] && notes[i][2] < notes[i - 1][2])) {
            notes.sort((a, b) => a[0] - b[0] || a[2] - b[2]);
            break;
          }
        }
        const maxEndPrefix = new Float64Array(notes.length);
        let maxEnd = -Infinity;
        for (let i = 0; i < notes.length; i++) {
          if (notes[i][1] > maxEnd) maxEnd = notes[i][1];
          maxEndPrefix[i] = maxEnd;
        }
        return {
          ...t,
          index: trackIndex,
          name: t?.name || `Track ${trackIndex + 1}`,
          is_drum: Boolean(t?.is_drum),
          is_guitar: Boolean(t?.is_guitar),
          role: t?.role ?? null,
          color: PianoRoll.trackColor(trackIndex),
          notes,
          maxEndPrefix,
        };
      });
    }

    static visibleNoteRange(notes, maxEndPrefix, tStart, tEnd) {
      if (!notes || !notes.length || tEnd < tStart) return [0, 0];
      const startIdx = bisect(maxEndPrefix, tStart, false, (a, i) => a[i]);
      const endIdx = bisect(notes, tEnd, true, (a, i) => a[i][0]);
      return startIdx >= endIdx ? [endIdx, endIdx] : [startIdx, endIdx];
    }

    static pitchRange(tracks, selectedSet) {
      let lo = Infinity, hi = -Infinity;
      for (const t of tracks || []) {
        if (selectedSet && !selectedSet.has(t.index)) continue;
        for (const n of t.notes || []) {
          if (n[2] < lo) lo = n[2];
          if (n[2] > hi) hi = n[2];
        }
      }
      if (lo > hi) return { minPitch: 40, maxPitch: 76 };
      let minPitch = Math.max(12, lo - 2), maxPitch = Math.min(108, hi + 2);
      if (maxPitch - minPitch < 14) {
        const need = 14 - (maxPitch - minPitch), padLo = Math.floor(need / 2);
        minPitch = Math.max(12, minPitch - padLo);
        maxPitch = Math.min(108, maxPitch + (need - padLo));
        if (maxPitch - minPitch < 14) {
          if (minPitch === 12) maxPitch = 26;
          else if (maxPitch === 108) minPitch = 94;
        }
      }
      return { minPitch, maxPitch };
    }

    static activeNotesAt(tracks, selectedSet, t) {
      const out = [];
      for (const tr of tracks || []) {
        if (selectedSet && !selectedSet.has(tr.index)) continue;
        const [sIdx, eIdx] = PianoRoll.visibleNoteRange(tr.notes, tr.maxEndPrefix, t, t);
        for (let i = sIdx; i < eIdx; i++) {
          const n = tr.notes[i];
          if (n[0] <= t && t < n[1]) {
            out.push({
              trackIndex: tr.index, trackName: tr.name, color: tr.color || PianoRoll.trackColor(tr.index),
              isDrum: Boolean(tr.is_drum), isGuitar: Boolean(tr.is_guitar), role: tr.role ?? null,
              start: n[0], end: n[1], pitch: n[2], velocity: n[3],
            });
          }
        }
      }
      return out.sort((a, b) => b.pitch - a.pitch || a.trackIndex - b.trackIndex);
    }

    static noteLabel(pitch, useFlats = false, isDrum = false) {
      const p = Math.round(Number(pitch) || 0);
      if (isDrum) {
        const entry = GM_DRUMS[p];
        return entry
          ? { name: `${entry[0]} (${p})`, pt: `${entry[1]} (${p})`, short: entry[0] }
          : { name: `Drum (${p})`, pt: `Bateria (${p})`, short: `D${p}` };
      }
      const pc = ((p % 12) + 12) % 12, oct = Math.floor(p / 12) - 1;
      const base = (useFlats ? FLAT_NAMES : SHARP_NAMES)[pc];
      return { name: `${base}${oct}`, pt: `${PT_LETTERS[base[0]] || base[0]}${base.slice(1)}${oct}`, short: `${base}${oct}` };
    }

    static guitarPositions(pitch, tuningPitches = [40, 45, 50, 55, 59, 64], maxFrets = 24) {
      const p = Math.round(Number(pitch) || 0);
      const tuning = Array.isArray(tuningPitches) && tuningPitches.length ? tuningPitches : [40, 45, 50, 55, 59, 64];
      const limit = Math.max(0, Number(maxFrets) || 24), n = tuning.length, out = [];
      for (let i = 0; i < n; i++) {
        const fret = p - Number(tuning[i]);
        if (fret >= 0 && fret <= limit) out.push({ string: n - i, fret });
      }
      const score = (pos) => (pos.fret > 15 ? (pos.fret - 15) * 3.5 : 0)
        + (pos.fret > 12 ? (pos.string - 1) * 1.2 : 0) + pos.fret * 0.7 + (pos.string - 1) * 0.45;
      return out.sort((a, b) => score(a) - score(b) || a.string - b.string);
    }

    constructor(canvas, callbacks = {}) {
      Object.assign(this, {
        canvas: canvas || null,
        ctx: canvas && typeof canvas.getContext === "function" ? canvas.getContext("2d") : null,
        callbacks: callbacks || {},
        duration: 0, tracks: [], selectedTracks: new Set(), beats: [], parts: [], selectedPartId: null,
        harmony: null, tonic: null, scalePcs: null, useFlats: false,
        tuningPitches: [40, 45, 50, 55, 59, 64], maxFrets: 24, follow: true, highlightScale: true,
        playheadSec: 0, isPlaying: false, minPitch: 40, maxPitch: 76, tStart: 0, pxPerSec: 80,
        width: 0, height: 0, dpr: 1, _hasSized: false, _holdFollowUntil: 0, _destroyed: false,
        hoverNote: null, hoverPitch: null, pinnedNote: null, _drawnNotes: [], _lastActiveKey: "",
        _drag: null, _rafId: null,
      });

      this._canvasListeners = [
        ["mousedown", (e) => this._handleMouseDown(e)],
        ["mousemove", (e) => this._handleMouseMove(e)],
        ["mouseup", () => this._handleMouseUp()],
        ["mouseleave", () => this._handleMouseLeave()],
        ["wheel", (e) => this._handleWheel(e), { passive: false }],
      ];
      this._onDragMove = (e) => this._handleDragMove(e);
      this._onDragUp = () => this._handleDragUp();
      if (this.canvas && typeof this.canvas.addEventListener === "function") {
        for (const [ev, fn, opts] of this._canvasListeners) this.canvas.addEventListener(ev, fn, opts);
      }
      this._ro = typeof ResizeObserver !== "undefined" && this.canvas ? new ResizeObserver(() => this.resize()) : null;
      if (this._ro) this._ro.observe(this.canvas);
      this.resize();
    }

    setData({ duration, tracks, beats, parts, harmony, tuningPitches, maxFrets } = {}) {
      this.tracks = PianoRoll.prepareTracks(tracks || []);
      let maxEnd = Number(duration) || 0;
      for (const t of this.tracks) {
        if (t.maxEndPrefix.length) maxEnd = Math.max(maxEnd, t.maxEndPrefix[t.maxEndPrefix.length - 1]);
      }
      this.duration = maxEnd;
      if (beats !== undefined) this.beats = Array.isArray(beats) ? beats.slice().sort((a, b) => a - b) : [];
      if (parts !== undefined) this.parts = Array.isArray(parts) ? parts.slice() : [];
      if (harmony !== undefined) this._applyHarmony(harmony);
      if (tuningPitches !== undefined) this.setTuning(tuningPitches, maxFrets ?? this.maxFrets);
      this.pinnedNote = null;
      this.hoverNote = null;
      if (this.selectedTracks.size === 0 && this.tracks.length > 0) {
        const melodic = this.tracks.filter((t) => !t.is_drum && t.notes.length > 0).map((t) => t.index);
        this.selectedTracks = new Set(melodic.length ? melodic : this.tracks.map((t) => t.index));
      }
      this._updatePitchRange();
      this.fitAll();
      this._emitActiveChange(true);
    }

    setSelectedTracks(indicesIterable) {
      this.selectedTracks = new Set(indicesIterable || []);
      if (this.pinnedNote && !this.selectedTracks.has(this.pinnedNote.trackIndex)) this.pinnedNote = null;
      this._updatePitchRange();
      this._emitActiveChange(true);
      this._scheduleRender();
    }

    _applyHarmony(harmony) {
      this.harmony = harmony || null;
      const k = harmony?.key || null;
      if (!k) { this.tonic = null; this.scalePcs = null; this.useFlats = false; return; }
      this.tonic = typeof k.tonic === "number" && Number.isFinite(k.tonic)
        ? ((Math.round(k.tonic) % 12) + 12) % 12 : parseNotePc(k.tonic_name);
      const pcs = new Set();
      let hasFlat = false;
      for (const item of Array.isArray(k.scale) ? k.scale : []) {
        if (typeof item === "string" && /^[A-G]b/.test(item.trim())) hasFlat = true;
        const pc = parseNotePc(item);
        if (pc !== null) pcs.add(pc);
      }
      this.scalePcs = pcs.size ? pcs : null;
      if (typeof k.flats === "boolean") this.useFlats = k.flats;
      else if (hasFlat) this.useFlats = true;
      else if (typeof k.tonic_name === "string") {
        const tn = k.tonic_name.trim();
        this.useFlats = /^[A-G]b/.test(tn) || (tn === "F" && k.family !== "minor" && k.mode !== "lydian");
      } else this.useFlats = false;
    }

    setHarmony(harmony) { this._applyHarmony(harmony); this._scheduleRender(); }
    setBeats(beats) { this.beats = Array.isArray(beats) ? beats.slice().sort((a, b) => a - b) : []; this._scheduleRender(); }
    setParts(parts) { this.parts = Array.isArray(parts) ? parts.slice() : []; this._scheduleRender(); }
    setSelectedPart(partId) { this.selectedPartId = partId ?? null; this._scheduleRender(); }
    setHighlightScale(enabled) { this.highlightScale = Boolean(enabled); this._scheduleRender(); }

    setTuning(pitches, maxFrets = 24) {
      if (Array.isArray(pitches) && pitches.length) this.tuningPitches = pitches.slice();
      this.maxFrets = Math.max(12, Math.min(24, Number(maxFrets) || 24));
      this._scheduleRender();
    }

    setFollow(enabled) {
      this.follow = Boolean(enabled);
      this._holdFollowUntil = 0;
      if (this.follow) this._keepPlayheadVisible();
      this._scheduleRender();
    }

    setTime(sec, isPlaying = false) {
      this.playheadSec = Math.max(0, Number(sec) || 0);
      this.isPlaying = Boolean(isPlaying);
      if (this.follow && !this._drag && this._nowMs() >= this._holdFollowUntil) this._keepPlayheadVisible();
      this._emitActiveChange(false);
      this._scheduleRender();
    }

    zoomBy(factor, anchorSec) {
      const anchor = anchorSec == null ? this.playheadSec : Number(anchorSec) || 0;
      const offsetPx = (anchor - this.tStart) * this.pxPerSec;
      this.pxPerSec = Math.max(this._minPxPerSec(), Math.min(MAX_PX_PER_SEC, this.pxPerSec * (Number(factor) || 1)));
      this.tStart = anchor - offsetPx / this.pxPerSec;
      this._clampViewport();
      this._scheduleRender();
    }

    fitRange(t0, t1) {
      const start = Math.max(0, Number(t0) || 0), end = Math.max(start + 0.5, Number(t1) || start + 1);
      this.pxPerSec = Math.max(this._minPxPerSec(), Math.min(MAX_PX_PER_SEC, this._gridW() / (end - start)));
      this.tStart = start;
      this._clampViewport();
      this._scheduleRender();
    }

    fitAll() { this.fitRange(0, this._effectiveDuration()); }

    resize() {
      if (!this.canvas) return;
      const rect = typeof this.canvas.getBoundingClientRect === "function" ? this.canvas.getBoundingClientRect() : null;
      const w = Math.round((rect && rect.width) || this.canvas.clientWidth || this.canvas.width || 0);
      const h = Math.round((rect && rect.height) || this.canvas.clientHeight || this.canvas.height || 0);
      if (w <= 0 || h <= 0) return;
      const dpr = typeof window !== "undefined" ? Math.max(1, window.devicePixelRatio || 1) : 1;
      const pw = Math.round(w * dpr), ph = Math.round(h * dpr);
      if (this.canvas.width !== pw) this.canvas.width = pw;
      if (this.canvas.height !== ph) this.canvas.height = ph;
      this.width = w; this.height = h; this.dpr = dpr;
      if (!this._hasSized) { this._hasSized = true; if (this.duration > 0) this.fitAll(); }
      else this._clampViewport();
      this._draw();
    }

    destroy() {
      this._destroyed = true;
      if (this._rafId != null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(this._rafId);
      this._rafId = null;
      if (this._ro) { this._ro.disconnect(); this._ro = null; }
      if (this.canvas && typeof this.canvas.removeEventListener === "function") {
        for (const [ev, fn, opts] of this._canvasListeners) this.canvas.removeEventListener(ev, fn, opts);
      }
      this._toggleWindowDrag(false);
    }

    _nowMs() { return typeof performance !== "undefined" ? performance.now() : Date.now(); }
    _effectiveDuration() { return Math.max(1, this.duration || 0); }
    _gridW() { return Math.max(80, (this.width || 800) - KEY_W); }
    _minPxPerSec() { return Math.min(MIN_PX_PER_SEC, this._gridW() / this._effectiveDuration()); }
    _sameNote(a, b) { return Boolean(a && b && a.trackIndex === b.trackIndex && a.pitch === b.pitch && a.start === b.start); }
    _updatePitchRange() { const r = PianoRoll.pitchRange(this.tracks, this.selectedTracks); this.minPitch = r.minPitch; this.maxPitch = r.maxPitch; }

    _clampViewport() {
      const maxStart = Math.max(0, this._effectiveDuration() - this._gridW() / this.pxPerSec);
      this.tStart = Math.max(0, Math.min(maxStart, this.tStart));
    }

    _keepPlayheadVisible() {
      const viewDur = this._gridW() / this.pxPerSec;
      const rightEdge = this.isPlaying ? 0.8 : 1.0;
      if (this.playheadSec < this.tStart || this.playheadSec > this.tStart + viewDur * rightEdge) {
        this.tStart = this.playheadSec - viewDur * 0.2;
        this._clampViewport();
      }
    }

    _emitActiveChange(force) {
      const active = PianoRoll.activeNotesAt(this.tracks, this.selectedTracks, this.playheadSec);
      const pin = this.pinnedNote ? `${this.pinnedNote.trackIndex}:${this.pinnedNote.pitch}:${this.pinnedNote.start}` : "";
      const key = active.map((a) => `${a.trackIndex}:${a.pitch}:${a.start}`).join("|") + "#" + pin;
      if (force || key !== this._lastActiveKey) {
        this._lastActiveKey = key;
        if (typeof this.callbacks.onActiveChange === "function") this.callbacks.onActiveChange(active, this.pinnedNote);
      }
    }

    _scheduleRender() {
      if (this._destroyed || !this.ctx) return;
      if (typeof requestAnimationFrame !== "function") { this._draw(); return; }
      if (this._rafId != null) return;
      this._rafId = requestAnimationFrame(() => { this._rafId = null; if (!this._destroyed) this._draw(); });
    }

    _draw() {
      const ctx = this.ctx;
      if (!ctx || this.width <= 0 || this.height <= 0) return;
      const w = this.width, h = this.height, gridW = w - KEY_W, gridH = Math.max(1, h - RULER_H);
      const rowH = gridH / (this.maxPitch - this.minPitch + 1), tEnd = this.tStart + gridW / this.pxPerSec;
      ctx.save();
      ctx.scale(this.dpr, this.dpr);
      ctx.fillStyle = "#0e1117";
      ctx.fillRect(0, 0, w, h);
      withClip(ctx, KEY_W, RULER_H, gridW, gridH, () => {
        this._drawGrid(ctx, gridW, gridH, rowH, tEnd);
        this._drawNotes(ctx, w, gridW, gridH, rowH);
      });
      this._drawRuler(ctx, w, gridW, tEnd);
      this._drawKeyboard(ctx, h, rowH);
      this._drawPlayhead(ctx, w, h);
      ctx.restore();
    }

    _drawGrid(ctx, gridW, gridH, rowH, tEnd) {
      const useScale = this.highlightScale && this.scalePcs;
      for (let p = this.minPitch; p <= this.maxPitch; p++) {
        const y = RULER_H + (this.maxPitch - p) * rowH, pc = ((p % 12) + 12) % 12;
        ctx.fillStyle = BLACK_PCS.has(pc) ? "#0a0d12" : "#11161f";
        ctx.fillRect(KEY_W, y, gridW, rowH + 0.5);
        if (useScale && this.scalePcs.has(pc)) {
          ctx.fillStyle = pc === this.tonic ? "rgba(245, 158, 11, 0.13)" : "rgba(59, 130, 246, 0.08)";
          ctx.fillRect(KEY_W, y, gridW, rowH + 0.5);
        }
        ctx.fillStyle = pc === 0 ? "rgba(255, 255, 255, 0.15)" : "rgba(255, 255, 255, 0.04)";
        ctx.fillRect(KEY_W, y + rowH - 1, gridW, 1);
      }
      if (!this.beats.length) return;
      const bStart = bisect(this.beats, this.tStart, false, (a, i) => a[i]);
      const bEnd = bisect(this.beats, tEnd, true, (a, i) => a[i]);
      let step = 1;
      if (bEnd - bStart > 1 && gridW / (bEnd - bStart) < 14) {
        step = 4;
        while (gridW / ((bEnd - bStart) / step) < 14) step *= 2;
      }
      for (let i = bStart; i < bEnd; i++) {
        if (i % step !== 0 && i % 4 !== 0) continue;
        const x = Math.round(KEY_W + (this.beats[i] - this.tStart) * this.pxPerSec);
        ctx.fillStyle = i % 4 === 0 ? "rgba(255, 255, 255, 0.12)" : "rgba(255, 255, 255, 0.045)";
        ctx.fillRect(x, RULER_H, 1, gridH);
      }
    }

    _drawNotes(ctx, w, gridW, gridH, rowH) {
      const rank = (t) => (t.role === "lead" ? 2 : t.role === "lead_candidate" ? 1 : 0);
      const selected = this.tracks.filter((t) => this.selectedTracks.has(t.index)).sort((a, b) => rank(a) - rank(b) || a.index - b.index);
      const tEnd = this.tStart + gridW / this.pxPerSec;
      this._drawnNotes = [];
      if (!selected.length) {
        this._drawHint(ctx, KEY_W + gridW / 2, RULER_H + gridH / 2, "Selecione um ou mais instrumentos acima para ler as notas");
        return;
      }
      const buckets = new Map();
      let drawnCount = 0;
      for (const tr of selected) {
        const [sIdx, eIdx] = PianoRoll.visibleNoteRange(tr.notes, tr.maxEndPrefix, this.tStart, tEnd);
        const [cr, cg, cb] = hexRgb(tr.color);
        const textColor = isLightRgb(cr, cg, cb) ? "#0e1117" : "#ffffff";
        for (let i = sIdx; i < eIdx; i++) {
          const [start, end, pitch, vel] = tr.notes[i];
          if (end < this.tStart || pitch < this.minPitch || pitch > this.maxPitch) continue;
          drawnCount++;
          const x = KEY_W + (start - this.tStart) * this.pxPerSec, rawW = Math.max(3, (end - start) * this.pxPerSec);
          const baseY = RULER_H + (this.maxPitch - pitch) * rowH;
          let overlap = 0, list = buckets.get(pitch);
          if (!list) buckets.set(pitch, (list = []));
          else for (const prev of list) if (prev[2] !== tr.index && prev[0] < end && prev[1] > start) overlap++;
          list.push([start, end, tr.index]);

          const padY = rowH >= 8 ? 1 : 0, shift = overlap > 0 && rowH >= 10;
          const barH = Math.max(3, rowH - padY * 2 - (shift ? 2 : 0));
          const barY = !shift ? baseY + padY : (overlap % 2 === 1 ? baseY + padY + 2 : baseY);
          const isActive = start <= this.playheadSec && this.playheadSec < end;
          const noteObj = {
            trackIndex: tr.index, trackName: tr.name, color: tr.color, isDrum: tr.is_drum,
            isGuitar: tr.is_guitar, role: tr.role, start, end, pitch, velocity: vel,
          };
          const highlight = isActive || this._sameNote(noteObj, this.pinnedNote);
          const hover = this._sameNote(noteObj, this.hoverNote);

          roundRectPath(ctx, x, barY, rawW, barH, 3);
          ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${isActive ? 0.98 : 0.52 + 0.42 * (Math.max(1, Math.min(127, vel)) / 127)})`;
          ctx.fill();
          ctx.lineWidth = highlight ? 2 : hover ? 1.5 : 1;
          ctx.strokeStyle = highlight ? "#fef08a" : hover ? "#ffffff" : "rgba(255, 255, 255, 0.35)";
          ctx.stroke();

          if (rawW >= 24 && rowH >= 11) {
            const clipX = Math.max(KEY_W, x), clipW = Math.min(x + rawW, w) - clipX;
            if (clipW >= 18) {
              withClip(ctx, clipX + 2, barY, clipW - 4, barH, () => {
                ctx.fillStyle = textColor;
                ctx.font = "600 10px system-ui, -apple-system, sans-serif";
                ctx.textBaseline = "middle";
                ctx.fillText(PianoRoll.noteLabel(pitch, this.useFlats, tr.is_drum).short, clipX + 4, barY + barH / 2 + 0.5);
              });
            }
          }
          this._drawnNotes.push({ x, y: barY, w: rawW, h: barH, note: noteObj });
        }
      }
      if (drawnCount === 0 && !selected.some((t) => t.notes.length > 0)) {
        this._drawHint(ctx, KEY_W + gridW / 2, RULER_H + gridH / 2, "As faixas selecionadas não possuem notas");
      }
    }

    _drawHint(ctx, cx, cy, text) {
      ctx.fillStyle = "#8b949e"; ctx.font = "500 13px system-ui, -apple-system, sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(text, cx, cy); ctx.textAlign = "left";
    }

    _drawRuler(ctx, w, gridW, tEnd) {
      ctx.fillStyle = "#161b22"; ctx.fillRect(0, 0, w, RULER_H);
      ctx.fillStyle = "#30363d"; ctx.fillRect(0, RULER_H - 1, w, 1);
      withClip(ctx, KEY_W, 0, gridW, RULER_H - 1, () => {
        for (const part of this.parts) {
          if (part.end < this.tStart || part.start > tEnd) continue;
          const x1 = Math.max(KEY_W, KEY_W + (part.start - this.tStart) * this.pxPerSec);
          const pw = Math.min(w, KEY_W + (part.end - this.tStart) * this.pxPerSec) - x1;
          if (pw < 2) continue;
          const isSel = part.part_id === this.selectedPartId;
          ctx.fillStyle = isSel ? "rgba(245, 158, 11, 0.28)" : "rgba(59, 130, 246, 0.16)";
          ctx.fillRect(x1, 1, pw, 13);
          ctx.fillStyle = isSel ? "#f59e0b" : "rgba(96, 165, 250, 0.65)";
          ctx.fillRect(x1, 1, pw, 2); ctx.fillRect(x1, 1, 1, 13);
          if (pw >= 26) {
            withClip(ctx, x1 + 3, 1, pw - 6, 13, () => {
              ctx.fillStyle = isSel ? "#fde68a" : "#c9d1d9";
              ctx.font = "600 9.5px system-ui, -apple-system, sans-serif"; ctx.textBaseline = "middle";
              ctx.fillText(part.name || "", x1 + 4, 8);
            });
          }
        }
        const tickStep = [1, 2, 5, 10, 15, 30, 60, 120].find((s) => s >= 78 / this.pxPerSec) || 120;
        ctx.font = "500 10px system-ui, -apple-system, sans-serif"; ctx.textBaseline = "middle";
        for (let t = Math.ceil(this.tStart / tickStep) * tickStep; t <= tEnd + 0.001; t += tickStep) {
          const x = Math.round(KEY_W + (t - this.tStart) * this.pxPerSec);
          ctx.fillStyle = "rgba(255, 255, 255, 0.16)"; ctx.fillRect(x, 16, 1, 11);
          ctx.fillStyle = "#8b949e"; ctx.fillText(fmtClock(t), x + 4, 21);
        }
      });
    }

    _drawKeyboard(ctx, h, rowH) {
      const activeMap = new Map();
      for (const a of PianoRoll.activeNotesAt(this.tracks, this.selectedTracks, this.playheadSec)) {
        if (!activeMap.has(a.pitch)) activeMap.set(a.pitch, a.color);
      }
      withClip(ctx, 0, RULER_H, KEY_W, Math.max(0, h - RULER_H), () => {
        const useScale = this.highlightScale && this.scalePcs;
        for (let p = this.minPitch; p <= this.maxPitch; p++) {
          const y = RULER_H + (this.maxPitch - p) * rowH, pc = ((p % 12) + 12) % 12;
          const isBlack = BLACK_PCS.has(pc), activeColor = activeMap.get(p);
          ctx.fillStyle = activeColor || (this.hoverPitch === p ? "#3b4656" : isBlack ? "#151a23" : "#28303d");
          ctx.fillRect(0, y, KEY_W - 1, rowH);
          if (useScale && pc === this.tonic && !activeColor) { ctx.fillStyle = "#f59e0b"; ctx.fillRect(0, y, 3, rowH); }
          ctx.fillStyle = "#0e1117"; ctx.fillRect(0, y + rowH - 1, KEY_W, 1);
          if (rowH >= 5 && (rowH >= 13 || (rowH >= 8 && !isBlack) || pc === 0 || pc === 7)) {
            const light = activeColor && isLightRgb(...hexRgb(activeColor));
            ctx.fillStyle = light ? "#0e1117" : (activeColor || pc === 0 ? "#ffffff" : "#b1bac4");
            ctx.font = `${pc === 0 || activeColor ? "700" : "500"} ${Math.min(11, Math.max(8, Math.floor(rowH - 2)))}px system-ui, sans-serif`;
            ctx.textBaseline = "middle";
            ctx.fillText(PianoRoll.noteLabel(p, this.useFlats, false).short, 6, y + rowH / 2 + 0.5);
          }
        }
        ctx.fillStyle = "#30363d"; ctx.fillRect(KEY_W - 1, RULER_H, 1, h - RULER_H);
      });
    }

    _drawPlayhead(ctx, w, h) {
      const x = KEY_W + (this.playheadSec - this.tStart) * this.pxPerSec;
      if (x < KEY_W || x > w) return;
      ctx.fillStyle = "#fbbf24";
      ctx.fillRect(Math.round(x) - 1, 0, 2, h);
      ctx.beginPath(); ctx.moveTo(x - 5, 0); ctx.lineTo(x + 5, 0); ctx.lineTo(x, 7); ctx.closePath(); ctx.fill();
    }

    _canvasCoords(e) {
      if (!this.canvas || typeof this.canvas.getBoundingClientRect !== "function") return { x: 0, y: 0 };
      const r = this.canvas.getBoundingClientRect();
      return { x: (e.clientX ?? 0) - r.left, y: (e.clientY ?? 0) - r.top };
    }

    _hitTest(x, y) {
      const rowH = Math.max(1, this.height - RULER_H) / (this.maxPitch - this.minPitch + 1);
      const pitch = Math.max(this.minPitch, Math.min(this.maxPitch, this.maxPitch - Math.floor((y - RULER_H) / rowH)));
      const time = Math.max(0, Math.min(this._effectiveDuration(), this.tStart + (x - KEY_W) / this.pxPerSec));
      if (x < KEY_W && y >= RULER_H) return { zone: "key", pitch, time };
      if (y < RULER_H && x >= KEY_W) {
        return { zone: "ruler", time, part: y <= 15 ? this.parts.find((p) => time >= p.start && time <= p.end) || null : null };
      }
      if (x >= KEY_W && y >= RULER_H) {
        for (let i = this._drawnNotes.length - 1; i >= 0; i--) {
          const item = this._drawnNotes[i], hitW = Math.max(7, item.w), padY = item.h < 8 ? (8 - item.h) / 2 : 0;
          if (x >= item.x - 1 && x <= item.x + hitW + 1 && y >= item.y - padY && y <= item.y + item.h + padY) {
            return { zone: "note", note: item.note, time, pitch };
          }
        }
        return { zone: "grid", time, pitch };
      }
      return { zone: "corner", time, pitch };
    }

    _toggleWindowDrag(bind) {
      const fn = bind ? "addEventListener" : "removeEventListener";
      if (typeof window !== "undefined" && typeof window[fn] === "function") {
        window[fn]("mousemove", this._onDragMove);
        window[fn]("mouseup", this._onDragUp);
      }
    }

    _handleMouseDown(e) {
      if (e.button !== 0 || this._destroyed) return;
      const { x, y } = this._canvasCoords(e), hit = this._hitTest(x, y);
      if (hit.zone === "key") {
        this.hoverPitch = hit.pitch;
        this._audition({
          trackIndex: null, trackName: "Teclado", color: TRACK_COLORS[0], isDrum: false,
          pitch: hit.pitch, velocity: 96, duration: 0.45, start: this.playheadSec, end: this.playheadSec + 0.45,
        });
        this._scheduleRender();
        return;
      }
      this._drag = { startX: e.clientX ?? x, startTStart: this.tStart, moved: false, hit };
      this._toggleWindowDrag(true);
    }

    _handleDragMove(e) {
      if (!this._drag || this._destroyed) return;
      const dx = (e.clientX ?? 0) - this._drag.startX;
      if (!this._drag.moved && Math.abs(dx) > 4) {
        this._drag.moved = true;
        if (this.canvas?.style) this.canvas.style.cursor = "grabbing";
      }
      if (this._drag.moved) {
        this.tStart = this._drag.startTStart - dx / this.pxPerSec;
        this._clampViewport(); this._holdFollowUntil = this._nowMs() + 1800; this._scheduleRender();
      }
    }

    _handleDragUp() {
      if (!this._drag || this._destroyed) return;
      const drag = this._drag; this._drag = null; this._toggleWindowDrag(false);
      if (this.canvas?.style) this.canvas.style.cursor = "default";
      if (drag.moved) return;
      const hit = drag.hit;
      if (hit.zone === "note") {
        this.pinnedNote = hit.note;
        this._audition({ ...hit.note, duration: Math.min(2.0, Math.max(0.15, hit.note.end - hit.note.start)) });
        this._emitActiveChange(true); this._scheduleRender();
      } else if (hit.zone === "ruler") {
        if (hit.part && typeof this.callbacks.onSelectPart === "function") this.callbacks.onSelectPart(hit.part);
        if (typeof this.callbacks.onSeek === "function") this.callbacks.onSeek(hit.part ? hit.part.start : hit.time);
      } else if (hit.zone === "grid") {
        if (this.pinnedNote) { this.pinnedNote = null; this._emitActiveChange(true); this._scheduleRender(); }
        if (typeof this.callbacks.onSeek === "function") this.callbacks.onSeek(hit.time);
      }
    }

    _handleMouseMove(e) {
      if (this._destroyed) return;
      if (this._drag) {
        if (typeof window === "undefined" || typeof window.addEventListener !== "function") this._handleDragMove(e);
        return;
      }
      const { x, y } = this._canvasCoords(e);
      if (x < 0 || y < 0 || x > this.width || y > this.height) return;
      const hit = this._hitTest(x, y);
      const nextNote = hit.zone === "note" ? hit.note : null, nextPitch = hit.zone === "key" ? hit.pitch : null;
      const noteChanged = !this._sameNote(nextNote, this.hoverNote), pitchChanged = nextPitch !== this.hoverPitch;
      this.hoverNote = nextNote; this.hoverPitch = nextPitch;
      if (this.canvas?.style) {
        this.canvas.style.cursor = (hit.zone === "note" || hit.zone === "key" || (hit.zone === "ruler" && hit.part)) ? "pointer" : "default";
      }
      if (typeof this.callbacks.onHoverNote === "function" && (nextNote || noteChanged)) {
        this.callbacks.onHoverNote(nextNote, e.clientX ?? 0, e.clientY ?? 0);
      }
      if (noteChanged || pitchChanged) this._scheduleRender();
    }

    _handleMouseUp() {
      if (this._drag && (typeof window === "undefined" || typeof window.addEventListener !== "function")) this._handleDragUp();
    }

    _handleMouseLeave() {
      if (this._destroyed) return;
      const hadHover = Boolean(this.hoverNote || this.hoverPitch !== null);
      this.hoverNote = null; this.hoverPitch = null;
      if (typeof this.callbacks.onHoverNote === "function") this.callbacks.onHoverNote(null, 0, 0);
      if (hadHover) this._scheduleRender();
    }

    _handleWheel(e) {
      if (this._destroyed) return;
      const { x } = this._canvasCoords(e);
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        this.zoomBy(Math.exp(-(e.deltaY || 0) * 0.0025), this.tStart + Math.max(0, x - KEY_W) / this.pxPerSec);
      } else if (e.shiftKey || Math.abs(e.deltaX || 0) > Math.abs(e.deltaY || 0)) {
        e.preventDefault();
        const delta = Math.abs(e.deltaX || 0) > Math.abs(e.deltaY || 0) ? e.deltaX : e.deltaY;
        this.tStart += delta / this.pxPerSec;
        this._clampViewport(); this._holdFollowUntil = this._nowMs() + 1800; this._scheduleRender();
      }
    }

    _audition(info) {
      if (typeof this.callbacks.onAuditionNote === "function") this.callbacks.onAuditionNote(info);
    }
  }

  if (root && typeof root === "object") root.PianoRoll = PianoRoll;
  if (typeof module !== "undefined" && module.exports) module.exports = PianoRoll;
})(typeof window !== "undefined" ? window : globalThis);
