const $ = id => document.getElementById(id);
let schema = null, state = null, recording = null, playContext = null, playing = null;
let playNext = 0, playAt = 0, lastHeard = "", lastAnswer = "", rmsText = "", primed = false, padTarget = "answer";
const voices = [];
let playTail = Promise.resolve();
const pending = new Map();

const TIMIT_SA1 = "She had your dark suit in greasy wash water all year.";
const TIMIT_SA2 = "Don't ask me to carry an oily rag like that.";
const HARVARD_1 = "The birch canoe slid on the smooth planks. Glue the sheet to the dark blue background. It's easy to tell the depth of a well. These days a chicken leg is a rare dish. Rice is often served in round bowls. The juice of lemons makes fine punch. The box was thrown beside the parked truck. The hogs were fed chopped corn and garbage. Four hours of steady work faced us. A large size in stockings is hard to sell.";
const RAINBOW = "When the sunlight strikes raindrops in the air, they act as a prism and form a rainbow. The rainbow is a division of white light into many beautiful colors. These take the shape of a long round arch, with its path high above, and its two ends apparently beyond the horizon. There is, according to legend, a boiling pot of gold at one end. People look, but no one ever finds it. When a man looks for something beyond his reach, his friends say he is looking for the pot of gold at the end of the rainbow. Throughout the centuries people have explained the rainbow in various ways. Some have accepted it as a miracle without physical explanation. To the Hebrews it was a token that there would be no more universal floods. The Greeks used to imagine that it was a sign from the gods to foretell war or heavy rain. The Norsemen considered the rainbow as a bridge over which the gods passed from earth to their home in the sky. Others have tried to explain the phenomenon physically. Aristotle thought that the rainbow was caused by reflection of the sun's rays by the rain. Since then physicists have found that it is not reflection, but refraction by the raindrops which causes the rainbows. Many complicated ideas about the rainbow have been formed. The difference in the rainbow depends considerably upon the size of the drops, and the width of the colored band increases as the size of the drops increases. The actual primary rainbow observed is said to be the effect of super-imposition of a number of bows. If the red of the second bow falls upon the green of the first, the result is to give a bow with an abnormally wide yellow band, since red and green light when mixed form yellow. This is a very common type of bow, one showing mainly red and yellow, with little or no green or blue.";
const GRANDFATHER = "You wished to know all about my grandfather. Well, he is nearly ninety-three years old. He dresses himself in an ancient black frock coat, usually minus several buttons; yet he still thinks as swiftly as ever. A long, flowing beard clings to his chin, giving those who observe him a pronounced feeling of the utmost respect. When he speaks his voice is just a bit cracked and quivers a trifle. Twice each day he plays skillfully and with zest upon our small organ. Except in the winter when the ooze or snow or ice prevents, he slowly takes a short walk in the open air each day. We have often urged him to walk more and smoke less, but he always answers, Banana Oil! Grandfather likes to be modern in his language.";
const WIND_EN = "The North Wind and the Sun were disputing which was the stronger, when a traveler came along wrapped in a warm cloak. They agreed that the one who first succeeded in making the traveler take his cloak off should be considered stronger than the other. Then the North Wind blew as hard as he could, but the more he blew the more closely did the traveler fold his cloak around him; and at last the North Wind gave up the attempt. Then the Sun shone out warmly, and immediately the traveler took off his cloak. And so the North Wind was obliged to confess that the Sun was the stronger of the two.";
const WIND_DE = "Einst stritten sich Nordwind und Sonne, wer von ihnen beiden wohl der Stärkere wäre, als ein Wanderer, der in einen warmen Mantel gehüllt war, des Weges daherkam. Sie wurden einig, daß derjenige für den Stärkeren gelten sollte, der den Wanderer zwingen würde, seinen Mantel abzunehmen. Der Nordwind blies mit aller Macht, aber je mehr er blies, desto fester hüllte sich der Wanderer in seinen Mantel ein. Endlich gab der Nordwind den Kampf auf. Nun erwärmte die Sonne die Luft mit ihren freundlichen Strahlen, und schon nach wenigen Augenblicken zog der Wanderer seinen Mantel aus. Da mußte der Nordwind zugeben, daß die Sonne von ihnen beiden der Stärkere war.";
const WIND_PL = "Wiatr i Słońce sprzeczali się, który z nich jest silniejszy. Wtem ujrzeli podróżnego, który zbliżał się owinięty w ciepły płaszcz. Umówili się, że ten będzie uznany za silniejszego, kto pierwszy zmusi podróżnego, by zdjął płaszcz. Wiatr zaczął dąć z całej siły, ale im bardziej wiał, tym ciaśniej podróżny owinął się w płaszcz. Wreszcie Wiatr zrezygnował. Wtedy Słońce zaczęło przygrzewać i podróżny natychmiast zdjął płaszcz. Wiatr musiał uznać, że Słońce jest silniejsze.";
const PAD = {
  short: TIMIT_SA1, sa2: TIMIT_SA2, harvard: HARVARD_1, mid: RAINBOW, grandfather: GRANDFATHER,
  "wind-en": WIND_EN, "wind-de": WIND_DE, "wind-pl": WIND_PL,
  long: [RAINBOW, GRANDFATHER, HARVARD_1, WIND_EN, WIND_DE, WIND_PL].join(" "),
};

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
function coreReady() {
  return state && schema
    && ["python", "git", "cmake", "msvc", "vulkan"].every(n => state.prerequisites[n]?.status === "ready")
    && ["tts", "parakeet", "gemma"].every(n => state.components[n]?.status === "ready")
    && schema.required_models.every(n => state.models[n]?.status === "ready");
}
function enginesRunning() {
  return state && ["asr", "brain", "tts"].every(n => state.engines[n]?.status === "running");
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
  $("connection").className = "pill good";
  const pre = status(state.prerequisites);
  const comp = status({tts: state.components.tts, parakeet: state.components.parakeet, gemma: state.components.gemma});
  const models = status(Object.fromEntries(schema.required_models.map(n => [n, state.models[n]])));
  const on = enginesRunning();
  $("dots").innerHTML = [["pre", pre], ["runtimes", comp], ["models", models], ["engines", on ? "running" : status(state.engines)]]
    .map(([n, v]) => `<span class="dot ${v}" title="${n}: ${v}"></span>`).join("");
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
  const dirty = Object.entries(state.dirty || {}).filter(([, x]) => x).map(([n]) => n);
  $("live").textContent = [
    dirty.length ? `restart ${dirty.join(", ")}` : "",
    jobs.length ? jobs.map(j => `${j.stage}: ${j.message}${j.progress ? ` (${j.progress}%)` : ""}`).join(" · ") : "",
    v.stage && v.stage !== "idle" ? v.stage : "",
    rmsText,
  ].filter(Boolean).join(" · ") || "Start engines, then speak, upload, or type.";
  $("live").classList.toggle("dirty", dirty.length > 0);
  $("stop-voice").disabled = !(playing || v.tts?.status === "running");
  renderKnobs();
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
    for (const name of ["git", "cmake", "msvc", "vulkan"]) {
      if (state.prerequisites[name]?.status !== "ready") await runJob("install_prerequisite", name, "prerequisite");
    }
    for (const name of ["tts", "parakeet", "gemma"]) {
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
    rec.parts.push(frame);
    const limit = Math.floor(rec.rate * schema.mic.pre_roll_ms / 1000);
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
  const context = new AudioContext({sampleRate: schema.mic.sample_rate});
  await context.audioWorklet.addModule("/audio-processor.js");
  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, "pcm-capture", {numberOfInputs: 1, numberOfOutputs: 0});
  recording = {stream, context, source, node, rate: context.sampleRate, parts: [], speaking: false, speechMs: 0, silenceMs: 0, busy: false};
  node.port.onmessage = e => capture(e.data);
  source.connect(node);
  $("record").textContent = "Stop mic";
  $("record").classList.add("live-mic");
}
async function stopMic(send = true) {
  const rec = recording; if (!rec) return;
  recording = null;
  rmsText = "";
  rec.node.disconnect(); rec.source.disconnect();
  rec.stream.getTracks().forEach(t => t.stop());
  await rec.context.close();
  $("record").textContent = "Mic";
  $("record").classList.remove("live-mic");
  const duration = sampleCount(rec.parts) / rec.rate;
  if (send && duration >= schema.mic.vad_min_speech_ms / 1000) await processUtterance(makeWav(rec.parts, rec.rate), duration, null);
  else render();
}
async function processUtterance(buffer, seconds, rec) {
  if (rec) rec.busy = true;
  try {
    if ($("clone").checked && seconds >= schema.mic.clone_reference_seconds) await wav("upload_reference", buffer);
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
  const ctx = new AudioContext({sampleRate: schema.mic.sample_rate});
  try {
    const decoded = await ctx.decodeAudioData((await file.arrayBuffer()).slice(0));
    const n = decoded.length, chs = decoded.numberOfChannels, mono = new Float32Array(n);
    for (let c = 0; c < chs; c++) {
      const ch = decoded.getChannelData(c);
      for (let i = 0; i < n; i++) mono[i] += ch[i] / chs;
    }
    let pcm = mono;
    if (decoded.sampleRate !== schema.mic.sample_rate) {
      const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(mono.length * schema.mic.sample_rate / decoded.sampleRate)), schema.mic.sample_rate);
      const buf = offline.createBuffer(1, mono.length, decoded.sampleRate);
      buf.getChannelData(0).set(mono);
      const src = offline.createBufferSource();
      src.buffer = buf; src.connect(offline.destination); src.start();
      pcm = (await offline.startRendering()).getChannelData(0);
    }
    return {wav: makeWav([pcm], schema.mic.sample_rate), seconds: pcm.length / schema.mic.sample_rate};
  } finally { await ctx.close(); }
}

const KNOB_LABEL = {
  mic: "Mic", asr_runtime: "Parakeet", asr_chunk: "Parakeet chunk",
  tts_runtime: "Chatterbox load", tts_sample: "Chatterbox", tts_voice: "Clone / emotion",
  tts_chunk: "Chatterbox pack", brain_runtime: "Gemma load", brain_generation: "Gemma",
  brain_thinking: "Gemma thinking", brain_system: "Gemma prompt",
};
function knobControl(group, name, spec) {
  const id = `knob-${group}-${name || "value"}`;
  if (spec.type === "text") return `<textarea id="${id}" data-group="${group}">${spec.value}</textarea>`;
  if (spec.type === "bool") return `<input id="${id}" data-group="${group}" data-key="${name || ""}" type="checkbox"${spec.value ? " checked" : ""}>`;
  if (spec.type === "choice") {
    return `<select id="${id}" data-group="${group}" data-key="${name}">${spec.choices.map(c => `<option${c === String(spec.value) ? " selected" : ""}>${c}</option>`).join("")}</select>`;
  }
  return `<input id="${id}" data-group="${group}" data-key="${name}" type="number" value="${spec.value}" min="${spec.min}" max="${spec.max}" step="${spec.step}">`;
}
function renderKnobs() {
  const root = $("knobs");
  if (!root || !schema?.settings) return;
  if (root.dataset.ready === "1") {
    for (const [group, spec] of Object.entries(schema.settings)) {
      if (spec && spec.type) {
        const el = $(`knob-${group}-value`);
        if (!el || document.activeElement === el) continue;
        if (spec.type === "bool") el.checked = !!spec.value; else el.value = spec.value;
        continue;
      }
      for (const [name, field] of Object.entries(spec)) {
        const el = $(`knob-${group}-${name}`);
        if (!el || document.activeElement === el) continue;
        if (field.type === "bool") el.checked = !!field.value; else el.value = field.value;
      }
    }
    return;
  }
  const bits = [];
  for (const [group, spec] of Object.entries(schema.settings)) {
    bits.push(`<div class="knob-group">${KNOB_LABEL[group] || group}</div>`);
    if (spec && spec.type) { bits.push(`<label>${KNOB_LABEL[group] || group}${knobControl(group, "", spec)}</label>`); continue; }
    for (const [name, field] of Object.entries(spec)) bits.push(`<label>${name}${knobControl(group, name, field)}</label>`);
  }
  root.innerHTML = `<div class="knob-grid">${bits.join("")}</div>`;
  root.querySelectorAll("input,select,textarea").forEach(el => el.addEventListener("change", () => applyKnob(el).catch(showError)));
  root.dataset.ready = "1";
}
async function applyKnob(el) {
  const group = el.dataset.group;
  const value = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
  await post("configure", {patch: group === "brain_system" || group === "brain_thinking" ? {[group]: value} : {[group]: {[el.dataset.key]: value}}});
}

const events = new EventSource("/events");
events.addEventListener("update", e => settle(JSON.parse(e.data)));
events.onerror = () => { $("connection").textContent = "offline"; $("connection").className = "pill bad"; };
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
$("transcript").addEventListener("focus", () => { padTarget = "transcript"; });
$("answer").addEventListener("focus", () => { padTarget = "answer"; });
$("pad").addEventListener("click", ev => {
  const btn = ev.target.closest("[data-pad]");
  if (!btn || !PAD[btn.dataset.pad]) return;
  const field = $(padTarget);
  field.value = PAD[btn.dataset.pad];
  field.focus();
});
$("audio-file").onchange = ev => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (file) audioFileToWav(file).then(({wav, seconds}) => processUtterance(wav, seconds, null)).catch(showError);
};
window.addEventListener("pagehide", () => {
  navigator.sendBeacon("/api", new Blob([JSON.stringify({op: "goodbye"})], {type: "application/json"}));
});
