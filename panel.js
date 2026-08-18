const $ = id => document.getElementById(id);
const sleep = ms => new Promise(r => setTimeout(r, ms));
let schema = null, state = null, recording = null, partialPromise = null;
let playContext = null, ttsSocket = null, playAt = 0, sources = new Set();

function fault(message = "") {
  const box = $("fault");
  box.hidden = !message;
  box.textContent = message;
}
async function json(url, options = {}) {
  const response = await fetch(url, {cache: "no-store", ...options});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(body.error || `HTTP ${response.status}`);
  return body;
}
async function post(op, data = {}) {
  return json("/api", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({op, ...data})});
}
async function wav(op, buffer) {
  return json(`/api?op=${encodeURIComponent(op)}`, {method: "POST", headers: {"Content-Type": "audio/wav"}, body: buffer});
}
async function refresh() {
  const result = await json("/api?op=inspect");
  schema = result.schema;
  state = result.state;
  render();
  return state;
}
function status(values) {
  const list = Object.values(values || {});
  if (!list.length) return "missing";
  if (list.some(x => x.status === "error")) return "error";
  if (list.every(x => x.status === "ready" || x.status === "running")) return "ready";
  return "missing";
}
function card(label, value, detail) {
  return `<div class="status-item ${value}"><b>${label}</b><span>${detail}</span></div>`;
}
function coreReady() {
  if (!state || !schema) return false;
  const prereq = ["python", "git", "cmake", "msvc", "vulkan"].every(n => state.prerequisites[n]?.status === "ready");
  const components = ["tts", "parakeet", "gemma"].every(n => state.components[n]?.status === "ready");
  const models = schema.required_models.every(n => state.models[n]?.status === "ready");
  return prereq && components && models;
}
function enginesRunning() {
  return state && ["asr", "brain", "tts"].every(n => state.engines[n]?.status === "running");
}
function render() {
  if (!state || !schema) return;
  $("connection").textContent = "Controller online";
  $("connection").className = "pill good";
  const pre = status(state.prerequisites);
  const comp = status({tts: state.components.tts, parakeet: state.components.parakeet, gemma: state.components.gemma});
  const modelStatus = status(Object.fromEntries(schema.required_models.map(n => [n, state.models[n]])));
  const engineStatus = status(state.engines);
  $("system-grid").innerHTML = [
    card("Prerequisites", pre, pre === "ready" ? "ready" : "installation required"),
    card("Runtimes", comp, comp === "ready" ? "ready" : "installation required"),
    card("Models", modelStatus, modelStatus === "ready" ? "verified" : "download/conversion required"),
    card("Engines", engineStatus, enginesRunning() ? "running" : "stopped"),
  ].join("");
  const ready = coreReady();
  $("system-status").textContent = ready ? "Installation complete." : "Install missing prerequisites, runtimes, and pinned models.";
  $("install").disabled = ready;
  $("engines").disabled = !ready;
  $("engines").textContent = enginesRunning() ? "Stop engines" : "Start engines";
  $("record").disabled = !enginesRunning();
  const jobs = Object.values(state.jobs || {}).filter(j => j.status === "running");
  if (jobs.length) $("job-status").textContent = jobs.map(j => `${j.stage}: ${j.message}${j.progress ? ` (${j.progress}%)` : ""}`).join(" · ");
  else if (!$("job-status").textContent.includes("failed")) $("job-status").textContent = "";
}
async function waitJob(key) {
  for (;;) {
    const s = await json("/api?op=state");
    state = s; render();
    const job = state.jobs?.[key];
    if (job && job.status === "error") throw Error(job.error || job.message || `${key} failed`);
    if (job && job.status === "done") return;
    await sleep(650);
  }
}
async function runJob(op, name, kind) {
  const result = await post(op, {name});
  await waitJob(`${kind}:${name}`);
  return result;
}
async function installMissing() {
  fault(); $("install").disabled = true;
  try {
    await refresh();
    for (const name of ["git", "cmake", "msvc", "vulkan"]) {
      if (state.prerequisites[name]?.status !== "ready") await runJob("install_prerequisite", name, "prerequisite");
    }
    for (const name of ["tts", "parakeet", "gemma"]) {
      await refresh();
      if (state.components[name]?.status !== "ready") await runJob("install_component", name, "component");
    }
    for (const name of schema.required_models) {
      await refresh();
      if (state.models[name]?.status !== "ready") await runJob("download_model", name, "model");
    }
    await refresh();
  } catch (e) {
    $("job-status").textContent = `Installation failed: ${e.message}`;
    fault(e.message);
  }
}
async function toggleEngines() {
  fault(); $("engines").disabled = true;
  try {
    await refresh();
    if (enginesRunning()) {
      for (const name of ["tts", "brain", "asr"]) await runJob("unload_engine", name, "engine");
    } else {
      for (const name of ["asr", "brain", "tts"]) {
        await refresh();
        if (state.engines[name]?.status !== "running") await runJob("load_engine", name, "engine");
      }
    }
    await refresh();
  } catch (e) { fault(e.message); }
  finally { $("engines").disabled = false; }
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
function rms(frame) {
  let n = 0; for (const x of frame) n += x * x;
  return Math.sqrt(n / Math.max(1, frame.length));
}
function samples(parts) { return parts.reduce((n, p) => n + p.length, 0); }
function trimPreRoll(rec) {
  const limit = Math.floor(rec.rate * schema.mic.pre_roll_ms / 1000);
  while (rec.parts.length > 1 && samples(rec.parts) - rec.parts[0].length > limit) rec.parts.shift();
}
async function liveAsr(rec) {
  if (!recording || rec !== recording || rec.busy || rec.partialBusy) return;
  const ms = samples(rec.parts) * 1000 / rec.rate;
  if (ms < schema.mic.partial_min_ms) return;
  rec.partialBusy = true;
  const buffer = makeWav(rec.parts.slice(), rec.rate);
  partialPromise = wav("asr", buffer).then(r => {
    const text = String(r.result?.text || "").trim();
    if (text && recording === rec && !rec.busy) {
      $("transcript").textContent = text;
      $("transcript").classList.remove("muted");
      $("asr-state").textContent = "live";
    }
  }).catch(() => {}).finally(() => { rec.partialBusy = false; partialPromise = null; });
}
function capture(frame) {
  const rec = recording;
  if (!rec || rec.busy || !frame?.length) return;
  const level = rms(frame), ms = frame.length * 1000 / rec.rate;
  rec.level = level;
  if (level >= schema.mic.vad_threshold) {
    rec.speaking = true; rec.speechMs += ms; rec.silenceMs = 0; rec.parts.push(frame);
    if (performance.now() - rec.lastPartial >= schema.mic.partial_asr_ms) { rec.lastPartial = performance.now(); liveAsr(rec); }
  } else if (rec.speaking) {
    rec.parts.push(frame); rec.silenceMs += ms;
    if (schema.mic.auto_send && rec.silenceMs >= schema.mic.vad_silence_ms && rec.speechMs >= schema.mic.vad_min_speech_ms) {
      const parts = rec.parts; rec.parts = []; rec.speaking = false; rec.speechMs = rec.silenceMs = 0;
      processUtterance(makeWav(parts, rec.rate), samples(parts) / rec.rate, rec).catch(showError);
    }
  } else {
    rec.parts.push(frame); trimPreRoll(rec);
  }
  $("mic-status").textContent = rec.busy ? "Processing…" : `Listening · RMS ${level.toFixed(3)}${rec.speaking ? " · speech" : ""}`;
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
  let context;
  try { context = new AudioContext({sampleRate: schema.mic.sample_rate}); }
  catch { context = new AudioContext(); }
  await context.audioWorklet.addModule("/audio-processor.js");
  const source = context.createMediaStreamSource(stream), node = new AudioWorkletNode(context, "pcm-capture", {numberOfInputs: 1, numberOfOutputs: 0});
  const rec = {stream, context, source, node, rate: context.sampleRate, parts: [], speaking: false, speechMs: 0, silenceMs: 0, busy: false, partialBusy: false, lastPartial: 0, level: 0};
  node.port.onmessage = e => capture(e.data); source.connect(node); recording = rec;
  $("record").textContent = "Stop microphone"; $("record").classList.add("live-mic");
  $("mic-status").textContent = "Listening…";
}
async function stopMic(send = true) {
  const rec = recording; if (!rec) return;
  recording = null; rec.node.disconnect(); rec.source.disconnect(); rec.stream.getTracks().forEach(t => t.stop()); await rec.context.close();
  $("record").textContent = "Start microphone"; $("record").classList.remove("live-mic");
  const duration = samples(rec.parts) / rec.rate;
  if (send && duration >= schema.mic.vad_min_speech_ms / 1000) await processUtterance(makeWav(rec.parts, rec.rate), duration, null);
  else $("mic-status").textContent = "Microphone stopped.";
}
async function processUtterance(buffer, seconds, rec) {
  if (rec) rec.busy = true;
  try {
    if (partialPromise) await partialPromise;
    $("asr-state").textContent = "final"; $("brain-state").textContent = "waiting";
    $("mic-status").textContent = "Transcribing final utterance…";
    if ($("clone").checked && seconds >= schema.mic.clone_reference_seconds) { await wav("upload_reference", buffer); $("mic-status").textContent = "Voice reference updated. Transcribing…"; }
    const asr = await wav("asr", buffer), transcript = String(asr.result?.text || "").trim();
    if (!transcript) throw Error("Parakeet returned no transcript");
    $("transcript").textContent = transcript; $("transcript").classList.remove("muted"); $("asr-state").textContent = "done";
    $("brain-state").textContent = "thinking"; $("answer").textContent = "Thinking locally…"; $("answer").classList.add("muted");
    const lang = $("reply-language").value;
    const brain = await post("brain", {prompt: `Respond naturally to this speech transcript:\n\n${transcript}`, language: lang});
    const answer = String(brain.text || "").trim(); if (!answer) throw Error("Brain returned no answer");
    $("answer").textContent = answer; $("answer").classList.remove("muted"); $("brain-state").textContent = "speaking";
    await speak(answer, lang); $("brain-state").textContent = "done";
    $("mic-status").textContent = recording ? "Listening for the next utterance…" : "Ready.";
  } finally { if (rec) { rec.busy = false; rec.parts = []; rec.speaking = false; rec.speechMs = rec.silenceMs = 0; } }
}

function id() { return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
async function stopVoice() {
  if (ttsSocket && ttsSocket.readyState === WebSocket.OPEN) { try { ttsSocket.send(JSON.stringify({type: "cancel"})); ttsSocket.send(JSON.stringify({type: "close"})); } catch {} }
  ttsSocket = null; for (const source of sources) { try { source.stop(); } catch {} } sources.clear(); playAt = 0; $("stop-voice").disabled = true;
}
function queuePcm(buffer) {
  const ctx = playContext, view = new DataView(buffer), n = Math.floor(buffer.byteLength / 2), audio = ctx.createBuffer(1, n, schema.tts.sample_rate), out = audio.getChannelData(0);
  for (let i = 0; i < n; i++) out[i] = view.getInt16(i * 2, true) / 32768;
  const source = ctx.createBufferSource(); source.buffer = audio; source.connect(ctx.destination);
  const start = Math.max(ctx.currentTime + 0.05, playAt || 0); source.start(start); playAt = start + audio.duration;
  sources.add(source); source.onended = () => sources.delete(source);
}
async function speak(text, language) {
  await stopVoice(); const ctx = await ensurePlaybackContext(), session = await post("tts_session", {language});
  playAt = ctx.currentTime + 0.08; $("stop-voice").disabled = false;
  await new Promise((resolve, reject) => {
    const ws = new WebSocket(session.url); ttsSocket = ws; ws.binaryType = "arraybuffer"; let done = false;
    const finish = err => { if (done) return; done = true; $("stop-voice").disabled = true; if (ttsSocket === ws) ttsSocket = null; err ? reject(err) : resolve(); };
    ws.onopen = () => ws.send(JSON.stringify(session.message));
    ws.onerror = () => finish(Error("TTS WebSocket failed"));
    ws.onmessage = event => {
      if (event.data instanceof ArrayBuffer) { queuePcm(event.data); return; }
      let msg; try { msg = JSON.parse(event.data); } catch { return; }
      if (msg.type === "ready") ws.send(JSON.stringify({type: "synthesize", text, request_id: `req-${id()}`}));
      else if (msg.type === "error") finish(Error(msg.message || "TTS error"));
      else if (msg.type === "chunk_done") {
        try { ws.send(JSON.stringify({type: "close"})); } catch {}
        const wait = Math.max(0, (playAt - ctx.currentTime) * 1000 + 70); setTimeout(() => finish(), wait);
      }
    };
    ws.onclose = () => { if (!done && playAt <= ctx.currentTime + .05) finish(); };
  });
}
function showError(e) { fault(e?.message || String(e)); $("mic-status").textContent = "Error."; }
function populateLanguages() {
  const select = $("reply-language"); select.innerHTML = "";
  for (const [code, name] of Object.entries(schema.languages.reply)) select.add(new Option(`${name} (${code})`, code));
  select.value = schema.languages.default_reply;
}
function connectEvents() {
  const es = new EventSource("/api?op=events");
  es.addEventListener("state", e => { state = JSON.parse(e.data); render(); });
  es.onerror = () => { $("connection").textContent = "Reconnecting"; $("connection").className = "pill bad"; };
}
$("install").onclick = installMissing;
$("engines").onclick = toggleEngines;
$("record").onclick = () => (recording ? stopMic(true) : startMic()).catch(showError);
$("stop-voice").onclick = () => stopVoice().catch(showError);
refresh().then(() => { populateLanguages(); connectEvents(); }).catch(showError);
