const $ = id => document.getElementById(id);
const sleep = ms => new Promise(r => setTimeout(r, ms));
let schema = null, state = null, recording = null, playContext = null, playing = null, ttsAbort = null;

function fault(message = "") {
  const box = $("fault");
  box.hidden = !message;
  box.textContent = message;
}
function showError(e) { fault(e?.message || String(e)); $("mic-status").textContent = "Error."; }

async function json(url, options = {}) {
  const response = await fetch(url, {cache: "no-store", ...options});
  const body = await response.json();
  if (!response.ok) throw Error(body.error || `HTTP ${response.status}`);
  return body;
}
async function post(op, data = {}, signal) {
  return json("/api", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({op, ...data}), signal});
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
  return ["python", "git", "cmake", "msvc", "vulkan"].every(n => state.prerequisites[n]?.status === "ready")
    && ["tts", "parakeet", "gemma"].every(n => state.components[n]?.status === "ready")
    && schema.required_models.every(n => state.models[n]?.status === "ready");
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
  $("system-grid").innerHTML = [
    card("Prerequisites", pre, pre === "ready" ? "ready" : "installation required"),
    card("Runtimes", comp, comp === "ready" ? "ready" : "installation required"),
    card("Models", modelStatus, modelStatus === "ready" ? "ready" : "download/conversion required"),
    card("Engines", status(state.engines), enginesRunning() ? "running" : "stopped"),
  ].join("");
  const ready = coreReady();
  $("system-status").textContent = ready ? "Installation complete." : "Install missing prerequisites, runtimes, and pinned models.";
  $("install").disabled = ready;
  $("engines").disabled = !ready;
  $("engines").textContent = enginesRunning() ? "Stop engines" : "Start engines";
  const live = enginesRunning();
  $("record").disabled = !live;
  $("speak-run").disabled = !live;
  $("audio-file").disabled = !live;
  const jobs = Object.values(state.jobs || {}).filter(j => j.status === "running");
  $("job-status").textContent = jobs.length ? jobs.map(j => `${j.stage}: ${j.message}${j.progress ? ` (${j.progress}%)` : ""}`).join(" · ") : "";
}
async function waitJob(key) {
  for (;;) {
    state = await json("/api?op=state");
    render();
    const job = state.jobs?.[key];
    if (job && job.status === "error") throw Error(job.error || job.message || `${key} failed`);
    if (job && job.status === "done") return;
    await sleep(650);
  }
}
async function runJob(op, name, kind) {
  await post(op, {name});
  await waitJob(`${kind}:${name}`);
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
function sampleCount(parts) { return parts.reduce((n, p) => n + p.length, 0); }
function trimPreRoll(rec) {
  const limit = Math.floor(rec.rate * schema.mic.pre_roll_ms / 1000);
  while (rec.parts.length > 1 && sampleCount(rec.parts) - rec.parts[0].length > limit) rec.parts.shift();
}
function capture(frame) {
  const rec = recording;
  if (!rec || rec.busy || !frame?.length) return;
  const level = rms(frame), ms = frame.length * 1000 / rec.rate;
  if (level >= schema.mic.vad_threshold) {
    rec.speaking = true; rec.speechMs += ms; rec.silenceMs = 0; rec.parts.push(frame);
  } else if (rec.speaking) {
    rec.parts.push(frame); rec.silenceMs += ms;
    if (schema.mic.auto_send && rec.silenceMs >= schema.mic.vad_silence_ms && rec.speechMs >= schema.mic.vad_min_speech_ms) {
      const parts = rec.parts; rec.parts = []; rec.speaking = false; rec.speechMs = rec.silenceMs = 0;
      processUtterance(makeWav(parts, rec.rate), sampleCount(parts) / rec.rate, rec).catch(showError);
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
  const context = new AudioContext({sampleRate: schema.mic.sample_rate});
  await context.audioWorklet.addModule("/audio-processor.js");
  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, "pcm-capture", {numberOfInputs: 1, numberOfOutputs: 0});
  recording = {stream, context, source, node, rate: context.sampleRate, parts: [], speaking: false, speechMs: 0, silenceMs: 0, busy: false};
  node.port.onmessage = e => capture(e.data);
  source.connect(node);
  $("record").textContent = "Stop microphone";
  $("record").classList.add("live-mic");
  $("mic-status").textContent = "Listening…";
}
async function stopMic(send = true) {
  const rec = recording; if (!rec) return;
  recording = null;
  rec.node.disconnect(); rec.source.disconnect();
  rec.stream.getTracks().forEach(t => t.stop());
  await rec.context.close();
  $("record").textContent = "Start microphone";
  $("record").classList.remove("live-mic");
  const duration = sampleCount(rec.parts) / rec.rate;
  if (send && duration >= schema.mic.vad_min_speech_ms / 1000) await processUtterance(makeWav(rec.parts, rec.rate), duration, null);
  else $("mic-status").textContent = "Microphone stopped.";
}
async function processUtterance(buffer, seconds, rec) {
  if (rec) rec.busy = true;
  try {
    $("asr-state").textContent = "final"; $("brain-state").textContent = "waiting";
    $("mic-status").textContent = "Transcribing…";
    if ($("clone").checked && seconds >= schema.mic.clone_reference_seconds) {
      await wav("upload_reference", buffer);
      $("mic-status").textContent = "Voice reference updated. Transcribing…";
    }
    const asr = await wav("asr", buffer);
    const transcript = String(asr.result?.text || "").trim();
    if (!transcript) throw Error("Parakeet returned no transcript");
    $("transcript").textContent = transcript;
    $("transcript").classList.remove("muted");
    $("asr-state").textContent = "done";
    $("brain-state").textContent = "thinking";
    $("answer").textContent = "Thinking locally…";
    $("answer").classList.add("muted");
    const lang = $("reply-language").value;
    const brain = await post("brain", {prompt: `Respond naturally to this speech transcript:\n\n${transcript}`, language: lang});
    const answer = String(brain.text || "").trim();
    if (!answer) throw Error("Brain returned no answer");
    $("answer").textContent = answer;
    $("answer").classList.remove("muted");
    $("brain-state").textContent = "speaking";
    await speak(answer, lang);
    $("brain-state").textContent = "done";
    $("mic-status").textContent = recording ? "Listening for the next utterance…" : "Ready.";
  } finally {
    if (rec) { rec.busy = false; rec.parts = []; rec.speaking = false; rec.speechMs = rec.silenceMs = 0; }
  }
}
function stopVoice() {
  if (ttsAbort) ttsAbort.abort();
  ttsAbort = null;
  if (playing) { playing.stop(); playing = null; }
  post("tts_cancel");
  $("stop-voice").disabled = true;
}
async function speak(text, language) {
  stopVoice();
  const ctx = await ensurePlaybackContext();
  ttsAbort = new AbortController();
  $("stop-voice").disabled = false;
  try {
    await post("tts", {text, language}, ttsAbort.signal);
    const response = await fetch("/last-output.wav", {cache: "no-store", signal: ttsAbort.signal});
    if (!response.ok) throw Error("TTS produced no audio");
    const audio = await ctx.decodeAudioData((await response.arrayBuffer()).slice(0));
    await new Promise(resolve => {
      const src = ctx.createBufferSource();
      playing = src;
      src.buffer = audio;
      src.connect(ctx.destination);
      src.onended = () => { if (playing === src) playing = null; resolve(); };
      src.start();
    });
  } finally {
    $("stop-voice").disabled = true;
    ttsAbort = null;
  }
}
async function resampleMono(samples, from, to) {
  if (from === to) return samples;
  const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(samples.length * to / from)), to);
  const buf = offline.createBuffer(1, samples.length, from);
  buf.getChannelData(0).set(samples);
  const src = offline.createBufferSource();
  src.buffer = buf;
  src.connect(offline.destination);
  src.start();
  return (await offline.startRendering()).getChannelData(0);
}
async function audioFileToWav(file) {
  const ctx = new AudioContext({sampleRate: schema.mic.sample_rate});
  try {
    const decoded = await ctx.decodeAudioData((await file.arrayBuffer()).slice(0));
    const n = decoded.length, chs = decoded.numberOfChannels, mono = new Float32Array(n);
    for (let c = 0; c < chs; c++) {
      const ch = decoded.getChannelData(c);
      for (let i = 0; i < n; i++) mono[i] += ch[i] / chs;
    }
    const pcm = await resampleMono(mono, decoded.sampleRate, schema.mic.sample_rate);
    return {wav: makeWav([pcm], schema.mic.sample_rate), seconds: pcm.length / schema.mic.sample_rate};
  } finally { await ctx.close(); }
}
function fillLanguages(select) {
  select.innerHTML = "";
  for (const [code, name] of Object.entries(schema.languages.reply)) select.add(new Option(`${name} (${code})`, code));
  select.value = schema.languages.default_reply;
}
$("install").onclick = installMissing;
$("engines").onclick = toggleEngines;
$("record").onclick = () => (recording ? stopMic(true) : startMic()).catch(showError);
$("stop-voice").onclick = stopVoice;
$("speak-run").onclick = () => {
  const text = $("speak-text").value.trim();
  if (!text) return showError(Error("Type text to speak"));
  speak(text, $("input-language").value).catch(showError);
};
$("audio-file").onchange = ev => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  audioFileToWav(file).then(({wav, seconds}) => {
    $("mic-status").textContent = `${file.name} · ${seconds.toFixed(2)}s`;
    return processUtterance(wav, seconds, null);
  }).catch(showError);
};
refresh().then(() => {
  fillLanguages($("reply-language"));
  fillLanguages($("input-language"));
}).catch(showError);
