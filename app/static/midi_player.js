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
    this.channels = new Map();   // channel -> track (one track per channel, pretty_midi layout)
    this.routes = new Map();     // channel -> {sf, bank, program}
    this.muted = new Set();      // track indices
    this.soloed = new Set();
    this.playing = false;
    this._starting = false;
    this._session = 0;
    this.position = 0;           // ticks, kept while paused
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
      this.total = 0;
    }
    this.channels = new Map(tracks.map((t) => [t.channel, t]));
    this.muted.clear();
    this.soloed.clear();
    this.position = 0;
    this.onState();
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
    this.position = await this.synth.retrievePlayerCurrentTick();
    this.synth.stopPlayer();
    this.onState();
  }

  async stop() {
    await this.pause();
    this.position = 0;
    this.onState();
  }

  seek(ticks) {
    this.position = Math.max(0, Math.min(ticks, this.total || ticks));
    if (this.playing) this.synth.seekPlayer(this.position);
    this.onState();
  }

  seconds(ticks) {
    return (ticks * 60) / (this.bpm * this.division);
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
        this.position = 0;
        this._stopClock();
        this.synth.stopPlayer();
      } else {
        this.position = tick;
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
