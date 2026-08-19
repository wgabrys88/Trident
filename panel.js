const $ = id => document.getElementById(id);
let schema = null, state = null, recording = null, playContext = null, playing = null;
let playNext = 0, playAt = 0, lastHeard = "", lastAnswer = "", rmsText = "", primed = false;
const voices = [];
let playTail = Promise.resolve();
const pending = new Map();

function fault(message = "") {
  $("fault").hidden = !message;
  $("fault").textContent = message;
}
function showError(e) { fault(e?.message || String(e)); }
async function json(url, options = {}) {
  const response = await fetch(url, {cache: "no-store", ...options});
  const body = await response.json();
  if (!response.ok) throw Error(body.error || `HTTP ${response.status}`);
  return body;
}
async function post(op, data = {}) {
  return json("/api", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({op, ...data})});
}
async function wav(op, buffer) {
  return json(`/api?op=${encodeURIComponent(op)}`, {method: "POST", headers: {"Content-Type": "audio/wav"}, body: buffer});
}
function status(values) {
  const list = Object.values(values || {});
  if (!list.length) return "missing";
  if (list.some(x => x.status === "error")) return "error";
  if (list.every(x => x.status === "ready" || x.status === "running")) return "ready";
  return "missing";
}
function names(obj) { return Object.keys(obj || {}); }
function mic() { return schema.mic; }
function coreReady() {
  return state && schema
    && names(schema.prerequisites).every(n => state.prerequisites[n]?.status === "ready")
    && names(schema.components).every(n => state.components[n]?.status === "ready")
    && schema.required_models.every(n => state.models[n]?.status === "ready");
}
function enginesRunning() {
  return state && names(state.engines).every(n => state.engines[n]?.status === "running");
}
function lang() { return $("reply-language").value; }
function live() { return state?.live || {}; }
function spoken() {
  const v = live(), tts = v.tts || {}, brain = v.brain || {};
  if (tts.status === "running") return (tts.text || "").trim();
  return (brain.text || tts.text || "").trim();
}
function paintField(el, auto, text) {
  if (document.activeElement === el || !auto.checked) return;
  el.value = text;
}

function render() {
  if (!state || !schema) return;
  $("connection").textContent = "online";
  const pre = status(state.prerequisites);
  const comp = status(state.components);
  const models = status(Object.fromEntries(schema.required_models.map(n => [n, state.models[n]])));
  const on = enginesRunning();
  $("dots").textContent = `pre ${pre} · runtimes ${comp} · models ${models} · engines ${on ? "running" : status(state.engines)}`;
  $("install").disabled = coreReady();
  $("engines").disabled = !coreReady();
  $("engines").textContent = on ? "Stop" : "Start";
  $("record").disabled = $("audio-file").disabled = $("heard-send").disabled = $("answer-send").disabled = !on;
  const v = live();
  $("asr-state").textContent = v.asr?.status || "idle";
  $("brain-state").textContent = v.tts?.status === "running" ? "speaking" : (v.brain?.status || "idle");
  paintField($("transcript"), $("heard-auto"), (v.asr?.text || "").trim());
  paintField($("answer"), $("answer-auto"), spoken());
  fault(v.error || "");
  const jobs = Object.values(state.jobs || {}).filter(j => j.status === "running");
  $("live").textContent = [
    jobs.length ? jobs.map(j => `${j.stage}: ${j.message}${j.progress ? ` (${j.progress}%)` : ""}`).join(" · ") : "",
    v.stage && v.stage !== "idle" ? v.stage : "",
    rmsText,
  ].filter(Boolean).join(" · ") || "Start engines, then speak, upload, or type.";
  $("stop-voice").disabled = !(playing || v.tts?.status === "running");
}

async function send(kind) {
  const heard = kind === "heard";
  const text = $(heard ? "transcript" : "answer").value.trim();
  if (!text) return showError(Error(heard ? "Heard is empty" : "Answer is empty"));
  if (heard) { lastHeard = text; await post("brain", {prompt: text, language: lang()}); }
  else { lastAnswer = text; await post("tts", {text, language: lang()}); }
}
function advance() {
  const v = live(), asr = v.asr || {}, brain = v.brain || {};
  const heard = (asr.text || "").trim(), answer = (brain.text || "").trim();
  if ($("heard-auto").checked && asr.status === "done" && heard && heard !== lastHeard && brain.status !== "running") {
    lastHeard = heard;
    post("brain", {prompt: heard, language: lang()}).catch(showError);
  }
  if ($("answer-auto").checked && brain.status === "done" && answer && answer !== lastAnswer && v.tts?.status !== "running") {
    lastAnswer = answer;
    post("tts", {text: answer, language: lang()}).catch(showError);
  }
}
function settle(inspect) {
  schema = inspect.schema;
  state = inspect.state;
  if (schema && !$("reply-language").options.length) {
    const sel = $("reply-language");
    sel.innerHTML = "";
    for (const [code, name] of Object.entries(schema.languages.reply)) sel.add(new Option(`${name} (${code})`, code));
    sel.value = schema.languages.default_reply;
  }
  const tts = live().tts || {};
  if (tts.status === "done" && tts.text) lastAnswer = tts.text.trim();
  if (!primed) {
    lastHeard = (live().asr?.text || "").trim();
    primed = true;
    render();
    return;
  }
  render();
  for (const [key, wait] of [...pending]) {
    const job = state.jobs?.[key];
    if (job?.status === "done") { pending.delete(key); wait.resolve(); }
    else if (job?.status === "error") { pending.delete(key); wait.reject(Error(job.error || job.message || key)); }
  }
  advance();
  playLive();
}

async function runJob(op, name, kind) {
  const key = `${kind}:${name}`;
  const p = new Promise((resolve, reject) => pending.set(key, {resolve, reject}));
  try {
    await post(op, {name});
    const job = state?.jobs?.[key];
    if (job?.status === "done") return;
    if (job?.status === "error") throw Error(job.error || job.message || key);
    await p;
  } finally { pending.delete(key); }
}
async function installMissing() {
  fault(); $("install").disabled = true;
  try {
    for (const name of names(schema.prerequisites)) {
      if (state.prerequisites[name]?.status !== "ready") await runJob("install_prerequisite", name, "prerequisite");
    }
    for (const name of names(schema.components)) {
      if (state.components[name]?.status !== "ready") await runJob("install_component", name, "component");
    }
    for (const name of schema.required_models) {
      if (state.models[name]?.status !== "ready") await runJob("download_model", name, "model");
    }
  } catch (e) { showError(e); }
}
async function toggleEngines() {
  fault(); $("engines").disabled = true;
  try {
    if (enginesRunning()) {
      for (const name of ["tts", "brain", "asr"]) await runJob("unload_engine", name, "engine");
    } else {
      for (const name of ["asr", "brain", "tts"]) {
        if (state.engines[name]?.status !== "running") await runJob("load_engine", name, "engine");
      }
    }
  } catch (e) { showError(e); }
}

function makeWav(parts, rate) {
  const n = parts.reduce((a, p) => a + p.length, 0), out = new ArrayBuffer(44 + n * 2), v = new DataView(out);
  const tag = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  tag(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); tag(8, "WAVEfmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  tag(36, "data"); v.setUint32(40, n * 2, true);
  let o = 44;
  for (const p of parts) for (let i = 0; i < p.length; i++, o += 2) v.setInt16(o, Math.round(Math.max(-1, Math.min(1, p[i])) * 32767), true);
  return out;
}
function rms(frame) { let n = 0; for (const x of frame) n += x * x; return Math.sqrt(n / Math.max(1, frame.length)); }
function sampleCount(parts) { return parts.reduce((n, p) => n + p.length, 0); }
function capture(frame) {
  const rec = recording;
  if (!rec || rec.busy || !frame?.length) return;
  const m = mic(), level = rms(frame), ms = frame.length * 1000 / rec.rate;
  if (level >= m.vad_threshold) {
    rec.speaking = true; rec.speechMs += ms; rec.silenceMs = 0; rec.parts.push(frame);
  } else if (rec.speaking) {
    rec.parts.push(frame); rec.silenceMs += ms;
    if (m.auto_send && rec.silenceMs >= m.vad_silence_ms && rec.speechMs >= m.vad_min_speech_ms) {
      const parts = rec.parts; rec.parts = []; rec.speaking = false; rec.speechMs = rec.silenceMs = 0;
      processUtterance(makeWav(parts, rec.rate), sampleCount(parts) / rec.rate, rec).catch(showError);
    }
  } else {
    rec.parts.push(frame);
    const limit = Math.floor(rec.rate * m.pre_roll_ms / 1000);
    while (rec.parts.length > 1 && sampleCount(rec.parts) - rec.parts[0].length > limit) rec.parts.shift();
  }
  rmsText = rec.busy ? "" : `RMS ${level.toFixed(3)}${rec.speaking ? " · speech" : ""}`;
  render();
}
async function ensurePlaybackContext() {
  if (!playContext || playContext.state === "closed") playContext = new AudioContext();
  if (playContext.state === "suspended") await playContext.resume();
  return playContext;
}
async function startMic() {
  fault();
  await ensurePlaybackContext();
  const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}, video: false});
  const context = new AudioContext({sampleRate: mic().sample_rate});
  await context.audioWorklet.addModule("/audio-processor.js");
  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, "pcm-capture", {numberOfInputs: 1, numberOfOutputs: 0});
  recording = {stream, context, source, node, rate: context.sampleRate, parts: [], speaking: false, speechMs: 0, silenceMs: 0, busy: false};
  node.port.onmessage = e => capture(e.data);
  source.connect(node);
  $("record").textContent = "Stop mic";
}
async function stopMic(send = true) {
  const rec = recording; if (!rec) return;
  recording = null;
  rmsText = "";
  rec.node.disconnect(); rec.source.disconnect();
  rec.stream.getTracks().forEach(t => t.stop());
  await rec.context.close();
  $("record").textContent = "Mic";
  const duration = sampleCount(rec.parts) / rec.rate;
  if (send && duration >= mic().vad_min_speech_ms / 1000) await processUtterance(makeWav(rec.parts, rec.rate), duration, null);
  else render();
}
async function processUtterance(buffer, seconds, rec) {
  if (rec) rec.busy = true;
  try {
    if ($("clone").checked && seconds >= mic().clone_reference_seconds) await wav("upload_reference", buffer);
    const asr = await wav("asr", buffer);
    if (!String(asr.result?.text || "").trim()) throw Error("Parakeet returned no transcript");
  } finally {
    if (rec) { rec.busy = false; rec.parts = []; rec.speaking = false; rec.speechMs = rec.silenceMs = 0; }
  }
}
function hush() {
  for (const src of voices) { try { src.stop(); } catch {} }
  voices.length = 0;
  playing = null;
  playAt = 0;
  playNext = 0;
}
function stopVoice() {
  hush();
  post("tts_cancel");
  $("stop-voice").disabled = true;
}
function playLive() {
  playTail = playTail.then(drainPacks).catch(showError);
}
async function enqueuePack(index) {
  const ctx = await ensurePlaybackContext();
  const response = await fetch(`/last-chunk.wav?c=${index}`, {cache: "no-store"});
  if (!response.ok) return;
  const audio = await ctx.decodeAudioData((await response.arrayBuffer()).slice(0));
  const src = ctx.createBufferSource();
  src.buffer = audio;
  src.connect(ctx.destination);
  const when = Math.max(playAt, ctx.currentTime);
  src.onended = () => {
    const at = voices.indexOf(src);
    if (at >= 0) voices.splice(at, 1);
    if (playing === src) playing = null;
    if (!voices.length) $("stop-voice").disabled = true;
  };
  src.start(when);
  playAt = when + audio.duration;
  playing = src;
  voices.push(src);
  $("stop-voice").disabled = false;
}
async function drainPacks() {
  const tts = live().tts;
  if (!tts || (tts.status !== "running" && tts.status !== "done")) return;
  const text = (tts.text || "").trim();
  if (!$("answer-auto").checked && text !== $("answer").value.trim()) return;
  const ready = tts.status === "done" ? Number(tts.chunks || 0) : Number(tts.chunk) + 1;
  if (!Number.isFinite(ready) || ready <= 0) return;
  if (tts.status === "running" && Number(tts.chunk) === 0 && playNext > 0) hush();
  while (playNext < ready) {
    const index = playNext;
    playNext += 1;
    await enqueuePack(index);
  }
}
async function audioFileToWav(file) {
  const rate = mic().sample_rate;
  const ctx = new AudioContext({sampleRate: rate});
  try {
    const decoded = await ctx.decodeAudioData((await file.arrayBuffer()).slice(0));
    const n = decoded.length, chs = decoded.numberOfChannels, mono = new Float32Array(n);
    for (let c = 0; c < chs; c++) {
      const ch = decoded.getChannelData(c);
      for (let i = 0; i < n; i++) mono[i] += ch[i] / chs;
    }
    let pcm = mono;
    if (decoded.sampleRate !== rate) {
      const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(mono.length * rate / decoded.sampleRate)), rate);
      const buf = offline.createBuffer(1, mono.length, decoded.sampleRate);
      buf.getChannelData(0).set(mono);
      const src = offline.createBufferSource();
      src.buffer = buf; src.connect(offline.destination); src.start();
      pcm = (await offline.startRendering()).getChannelData(0);
    }
    return {wav: makeWav([pcm], rate), seconds: pcm.length / rate};
  } finally { await ctx.close(); }
}

const events = new EventSource("/events");
events.addEventListener("update", e => settle(JSON.parse(e.data)));
events.onerror = () => { $("connection").textContent = "offline"; };
document.addEventListener("pointerdown", () => ensurePlaybackContext(), {once: true});
$("install").onclick = installMissing;
$("engines").onclick = toggleEngines;
$("record").onclick = () => (recording ? stopMic(true) : startMic()).catch(showError);
$("stop-voice").onclick = stopVoice;
$("heard-send").onclick = () => send("heard").catch(showError);
$("answer-send").onclick = () => send("answer").catch(showError);
$("heard-auto").onchange = () => {
  const text = $("transcript").value.trim();
  if ($("heard-auto").checked && text && text !== lastHeard) send("heard").catch(showError);
};
$("answer-auto").onchange = () => {
  const text = $("answer").value.trim();
  if ($("answer-auto").checked && text && text !== lastAnswer) send("answer").catch(showError);
};
$("audio-file").onchange = ev => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (file) audioFileToWav(file).then(({wav, seconds}) => processUtterance(wav, seconds, null)).catch(showError);
};
window.addEventListener("pagehide", () => {
  navigator.sendBeacon("/api", new Blob([JSON.stringify({op: "goodbye"})], {type: "application/json"}));
});
