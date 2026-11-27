// Full-song MIDI player: FluidSynth (WebAssembly, js-synthesizer) + the local SoundFont library.
//
// Several SoundFonts can be loaded at once. Every program change coming from the MIDI file is
// intercepted and routed explicitly (soundfont + bank + preset) per channel, so a guitar-only
// SoundFont whose presets sit on programs 0-8 never replaces the piano of the base GM bank.

/* global JSSynth */

class MidiPlayer {
  constructor() {
    this.ctx = null;
    this.synth = null;
    this.fonts = new Map();      // server soundfont id -> fluid sfont id
    this.loading = new Map();    // server soundfont id -> Promise
    this.midi = null;            // ArrayBuffer of the current file
    this.midiUrl = null;
    this.division = 480;
    this.tempoMap = null;
    this.channels = new Map();   // channel -> track (one track per channel, pretty_midi layout)
    this.routes = new Map();     // channel -> {sf, bank, program}
    this.muted = new Set();      // track indices
    this.soloed = new Set();
    this.playing = false;
    this._starting = false;
    this._session = 0;
    this.position = 0;           // ticks, kept while paused
    this._anchorTick = 0;
    this._anchorPerf = 0;
    this._auditionTimers = new Map();
    this.total = 0;
    this.bpm = 120;
    this.onState = () => {};
  }

  static available() {
    return typeof JSSynth !== "undefined";
  }

  async _ensureEngine() {
    if (!this.synth) {
      await JSSynth.waitForReady();
      this.ctx = new AudioContext();
      this.synth = new JSSynth.Synthesizer();
      this.synth.init(this.ctx.sampleRate, { initialGain: 0.5, midiChannelCount: 16 });
      const node = this.synth.createAudioNode(this.ctx, 8192);
      node.connect(this.ctx.destination);
    }
    if (this.ctx.state === "suspended") await this.ctx.resume();
  }

  /** Load a SoundFont from the server library once; resolves to its fluid id. */
  async loadFont(sfId) {
    await this._ensureEngine();
    if (this.fonts.has(sfId)) return this.fonts.get(sfId);
    if (!this.loading.has(sfId)) {
      this.loading.set(sfId, (async () => {
        const res = await fetch(`/api/soundfonts/${sfId}`);
        if (!res.ok) throw new Error(`SoundFont ${sfId}: HTTP ${res.status}`);
        const id = await this.synth.loadSFont(await res.arrayBuffer());
        this.fonts.set(sfId, id);
        return id;
      })().finally(() => this.loading.delete(sfId)));
    }
    return this.loading.get(sfId);
  }

  /** tracks: [{index, channel, is_drum, ...}]. Fetches the MIDI file (playback starts with play()). */
  async setSong(midiUrl, tracks) {
    await this.stop();
    if (midiUrl !== this.midiUrl) {
      const res = await fetch(midiUrl);
      if (!res.ok) throw new Error(`MIDI: HTTP ${res.status}`);
      this.midi = await res.arrayBuffer();
      this.midiUrl = midiUrl;
      const dv = new DataView(this.midi);
      const div = dv.getUint16(12);
      this.division = div & 0x8000 ? 480 : div;   // SMPTE timing never comes out of pretty_midi
      this.tempoMap = MidiPlayer.parseTempoMap(this.midi, this.division);
      this.total = 0;
    }
    this.channels = new Map(tracks.map((t) => [t.channel, t]));
    this.muted.clear();
    this.soloed.clear();
    this._setTick(0);
    this.onState();
  }

  _setTick(tick) {
    this.position = tick;
    this._anchorTick = tick;
    this._anchorPerf = typeof performance !== "undefined" ? performance.now() : 0;
  }

  /** routes: Map channel -> {sf (server id), bank, program}. Fonts must already be loaded. */
  setRoutes(routes) {
    this.routes = routes;
    if (this.synth) for (const ch of routes.keys()) this._applyRoute(ch);
  }

  _applyRoute(ch, fallbackProgram) {
    const r = this.routes.get(ch);
    if (!r) return false;
    const fid = this.fonts.get(r.sf);
    if (fid === undefined) return false;
    const program = r.program === null ? (fallbackProgram ?? 0) : r.program;
    this.synth.midiProgramSelect(ch, fid, r.bank, program);
    return true;
  }

  _audible(ch) {
    const t = this.channels.get(ch);
    if (!t) return true;
    if (this.soloed.size) return this.soloed.has(t.index);
    return !this.muted.has(t.index);
  }

  _hook(synth, type, ev) {
    const kind = type & 0xf0;
    const ch = ev.getChannel();
    if (kind === 0xc0) return this._applyRoute(ch, ev.getProgram());     // routed: swallow the raw change
    if (kind === 0x90 && ev.getVelocity() > 0) return !this._audible(ch);  // muted: drop note-ons only
    return false;
  }

  toggleMute(index) {
    this.muted.has(index) ? this.muted.delete(index) : this.muted.add(index);
    this._silenceInaudible();
  }

  toggleSolo(index) {
    this.soloed.has(index) ? this.soloed.delete(index) : this.soloed.add(index);
    this._silenceInaudible();
  }

  _silenceInaudible() {
    if (this.synth) for (const ch of this.channels.keys()) if (!this._audible(ch)) this.synth.midiAllNotesOff(ch);
    this.onState();
  }

  async play() {
    if (!this.midi || this.playing || this._starting) return;
    this._starting = true;
    const session = ++this._session;
    const resumeAt = this.position;
    try {
      await this._ensureEngine();
      // a fresh player each time: resuming after a pause or the end of the song behaves the same
      await this.synth.resetPlayer();
      this.synth.hookPlayerMIDIEvents((s, t, e) => this._hook(s, t, e));
      await this.synth.addSMFDataToPlayer(this.midi.slice(0));
    } finally {
      this._starting = false;
    }
    const gain = this.synth.getGain();
    if (resumeAt > 0) this.synth.setGain(0);   // don't let the first instants of the song leak out
    await this.synth.playPlayer();   // resolves at once; the end of the song is detected by the clock
    this.playing = true;
    // FluidSynth only parses the file on its first audio callback: wait for it before seeking
    this.total = await this._poll(() => this.synth.retrievePlayerTotalTicks(), (v) => v > 0);
    this.bpm = (await this.synth.retrievePlayerBpm()) || 120;
    if (resumeAt > 0) {
      this.synth.seekPlayer(resumeAt);
      await this._poll(() => this.synth.retrievePlayerCurrentTick(), (v) => v >= resumeAt);
      this.synth.setGain(gain);
    }
    this._setTick(resumeAt);
    this._startClock();
    this.onState();
  }

  async _poll(get, ok, timeoutMs = 3000) {
    const t0 = performance.now();
    let v = await get();
    while (!ok(v) && performance.now() - t0 < timeoutMs) {
      await new Promise((r) => setTimeout(r, 15));
      v = await get();
    }
    return v;
  }

  async pause() {
    if (!this.playing) return;
    this.playing = false;
    this._session++;
    this._stopClock();
    this._setTick(await this.synth.retrievePlayerCurrentTick());
    this.synth.stopPlayer();
    this.onState();
  }

  async stop() {
    await this.pause();
    this._setTick(0);
    this.onState();
  }

  seek(ticks) {
    this._setTick(Math.max(0, Math.min(ticks, this.total || ticks)));
    if (this.playing) this.synth.seekPlayer(this.position);
    this.onState();
  }

  static parseTempoMap(buf, division = 480) {
    const u8 = new Uint8Array(buf);
    const dv = new DataView(buf);
    if (u8.length < 22 || dv.getUint32(0) !== 0x4d546864) return null; // "MThd"
    let pos = 8 + dv.getUint32(4);
    if (pos + 8 > u8.length || dv.getUint32(pos) !== 0x4d54726b) return null; // "MTrk"
    const end = Math.min(u8.length, pos + 8 + dv.getUint32(pos + 4));
    pos += 8;

    const readVlq = () => {
      let v = 0;
      while (pos < end) {
        const b = u8[pos++];
        v = (v << 7) | (b & 0x7f);
        if (!(b & 0x80)) break;
      }
      return v;
    };

    const segs = [{ tick: 0, usPerBeat: 500000, sec: 0 }];
    let tick = 0;
    let running = 0;
    while (pos < end) {
      tick += readVlq();
      if (pos >= end) break;
      let status = u8[pos];
      if (status < 0x80) {
        status = running;
      } else {
        pos++;
        if (status < 0xf0) running = status;
      }
      if (status === 0xff) {
        const metaType = u8[pos++];
        const len = readVlq();
        if (metaType === 0x51 && len === 3 && pos + 3 <= end) {
          const us = (u8[pos] << 16) | (u8[pos + 1] << 8) | u8[pos + 2];
          const prev = segs[segs.length - 1];
          const sec = prev.sec + ((tick - prev.tick) * prev.usPerBeat) / (1e6 * division);
          if (tick === prev.tick) prev.usPerBeat = us;
          else segs.push({ tick, usPerBeat: us, sec });
        }
        pos += len;
      } else if (status === 0xf0 || status === 0xf7) {
        pos += readVlq();
      } else {
        const hi = status & 0xf0;
        pos += hi === 0xc0 || hi === 0xd0 ? 1 : 2;
      }
    }
    return segs;
  }

  seconds(ticks) {
    if (this.tempoMap && this.tempoMap.length) {
      let lo = 0, hi = this.tempoMap.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (this.tempoMap[mid].tick <= ticks) lo = mid;
        else hi = mid - 1;
      }
      const s = this.tempoMap[lo];
      return s.sec + ((ticks - s.tick) * s.usPerBeat) / (1e6 * this.division);
    }
    return (ticks * 60) / (this.bpm * this.division);
  }

  ticksAtSeconds(sec) {
    const t = Math.max(0, Number(sec) || 0);
    if (this.tempoMap && this.tempoMap.length) {
      let lo = 0, hi = this.tempoMap.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (this.tempoMap[mid].sec <= t) lo = mid;
        else hi = mid - 1;
      }
      const s = this.tempoMap[lo];
      return Math.round(s.tick + ((t - s.sec) * 1e6 * this.division) / s.usPerBeat);
    }
    return Math.round((t * this.bpm * this.division) / 60);
  }

  currentSeconds() {
    const base = this.seconds(this.playing ? this._anchorTick : this.position);
    if (!this.playing || typeof performance === "undefined") return base;
    const dt = Math.max(0, (performance.now() - this._anchorPerf) / 1000);
    const cap = this.total ? this.seconds(this.total) : Infinity;
    return Math.min(cap, base + dt);
  }

  auditionNote(channel, pitch, velocity = 96, durationMs = 420) {
    if (!this.synth) return false;
    const ch = Math.max(0, Math.min(15, Number(channel) || 0));
    if (!this._applyRoute(ch, 0) && ch !== 9) return false;
    if (this.ctx && this.ctx.state === "suspended") this.ctx.resume();
    const key = `${ch}:${pitch}`;
    const prev = this._auditionTimers.get(key);
    if (prev) {
      clearTimeout(prev);
      this.synth.midiNoteOff(ch, pitch);
    }
    this.synth.midiNoteOn(ch, pitch, Math.max(1, Math.min(127, Math.round(velocity))));
    const timer = setTimeout(() => {
      this._auditionTimers.delete(key);
      if (this.synth) this.synth.midiNoteOff(ch, pitch);
    }, Math.max(60, durationMs));
    this._auditionTimers.set(key, timer);
    return true;
  }

  _startClock() {
    this._stopClock();
    const session = this._session;
    this._timer = setInterval(async () => {
      if (!this.playing || session !== this._session) return;
      const tick = await this.synth.retrievePlayerCurrentTick();
      // FluidSynth may keep reporting "playing" a while after the last event: the tick count decides
      if (!this.synth.isPlayerPlaying() || (this.total && tick >= this.total)) {
        this.playing = false;
        this._setTick(0);
        this._stopClock();
        this.synth.stopPlayer();
      } else {
        this._setTick(tick);
      }
      this.onState();
    }, 250);
  }

  _stopClock() {
    clearInterval(this._timer);
    this._timer = null;
  }
}

window.MidiPlayer = MidiPlayer;
