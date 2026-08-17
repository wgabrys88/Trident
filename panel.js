"use strict";
const $ = id => document.getElementById(id);
let schema, state, events, recording = null, playbackContext = null, playbackNode = null, ttsSocket = null;
let lastEvent = 0, ttsSession = "", ttsLanguage = "", ttsStyle = "", ttsReferenceGeneration = -1;
let playback = {received: 0, played: 0, expected: 0, text: "", done: false, timer: 0};
let playbackWait = null, clientStage = "", installingAll = false;
let diagnostic = {input: null, output: [], outputRate: 24000, transcript: "", answer: "", source: "turn"};
async function api(path, body, raw = false) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: raw ? {"Content-Type": "audio/wav"} : {"Content-Type": "application/json"},
    body: raw ? body : JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw Error(result.error || `${response.status} ${response.statusText}`);
  return result;
}
const command = (op, values = {}) => api("/api", {op, ...values});
function fail(error) {
  const fault = $("fault");
  fault.textContent = error && error.message ? error.message : String(error);
  fault.hidden = false;
  window.setTimeout(() => { if (fault.textContent) fault.hidden = true; }, 9000);
}
function fillLanguages(select, languages) { select.replaceChildren(...Object.entries(languages).map(([code, name]) => { const option = document.createElement("option"); option.value = code; option.textContent = `${name} (${code})`; return option; })); }
async function save(path, value) { await command("set", {values: {[path]: value}}); }
function coerceField(path, raw) {
  const spec = schema.fields[path] || {};
  if (spec.type === "bool") return Boolean(raw);
  if (spec.type === "int") { const value = Number.parseInt(raw, 10); if (!Number.isFinite(value)) throw Error(`${path} must be an integer`); return value; }
  if (spec.type === "float") { const value = Number.parseFloat(raw); if (!Number.isFinite(value)) throw Error(`${path} must be a number`); return value; }
  return raw;
}
function bindField(element) {
  const path = element.dataset.path;
  if (!path || element.dataset.bound) return;
  element.dataset.bound = "1";
  if (element.type === "checkbox") element.checked = Boolean(state.config[path]);
  else if (state.config[path] !== undefined) element.value = state.config[path];
  element.addEventListener("change", () => {
    try {
      const value = element.type === "checkbox" ? element.checked : coerceField(path, element.value);
      save(path, value).catch(fail);
    } catch (error) { fail(error); }
  });
}
function bindConfig() { fillLanguages($("conversation-language"), schema.languages.conversation); fillLanguages($("speech-language"), schema.languages.speech); for (const element of document.querySelectorAll("[data-path]")) bindField(element); }
function buildParamGroups() {
  const root = $("param-groups");
  if (!root || !schema.param_groups) return;
  root.replaceChildren();
  for (const group of schema.param_groups) {
    const block = document.createElement("details"), summary = document.createElement("summary"), body = document.createElement("div"), note = document.createElement("p"), grid = document.createElement("div");
    block.className = "param-group"; summary.innerHTML = `<strong>${group.title}</strong>`; body.className = "param-body"; note.className = "microcopy"; note.textContent = group.apply || ""; grid.className = "param-grid";
    for (const path of group.fields || []) {
      const spec = schema.fields[path];
      if (!spec) continue;
      const label = document.createElement("label"), input = document.createElement("input");
      label.textContent = spec.label; input.type = "number"; input.dataset.path = path;
      if (spec.min !== undefined) input.min = spec.min;
      if (spec.max !== undefined) input.max = spec.max;
      input.step = spec.type === "int" ? "1" : "any";
      if (state.config[path] !== undefined) input.value = state.config[path];
      label.append(input); grid.append(label);
    }
    body.append(note, grid); block.append(summary, body); root.append(block);
  }
  for (const element of root.querySelectorAll("[data-path]")) bindField(element);
}
function syncParamFields() {
  for (const element of document.querySelectorAll("#param-groups [data-path]")) {
    if (document.activeElement === element) continue;
    const value = state.config[element.dataset.path];
    if (value !== undefined && String(element.value) !== String(value)) element.value = value;
  }
}
function jobFor(kind, name) { return state.jobs[`${kind}:${name}`] || null; }
function isReady(value) { return Boolean(value) && value.status === "ready"; }
function activeBrainReady() { const brain = state.brain || {}; return brain.active === "custom" ? Boolean(brain.custom && brain.custom.status === "ready") : isReady(state.models[brain.model || "gemma"]); }
function engineCanStart(name) {
  if (name === "brain") return isReady(state.components.gemma) && activeBrainReady();
  const component = name === "asr" ? "parakeet" : "tts";
  const models = name === "asr" ? ["parakeet"] : ["chatterbox-t3", "chatterbox-codec", "chatterbox-s3t", "reference"];
  return isReady(state.components[component]) && models.every(model => isReady(state.models[model]));
}
function conversationReady() { return ["asr", "brain", "tts"].every(engineCanStart); }
function enginesRunning() { return ["asr", "brain", "tts"].every(name => state.engines[name].status === "running"); }
function installEntries() {
  const out = [];
  for (const [name, spec] of Object.entries(schema.prerequisites)) out.push({kind: "prerequisite", name, label: spec.label, value: state.prerequisites[name], op: "install_prerequisite"});
  for (const [name, spec] of Object.entries(schema.components)) out.push({kind: "component", name, label: spec.label, value: state.components[name], op: "install_component"});
  for (const name of ["chatterbox-t3", "chatterbox-codec", "chatterbox-s3t", "parakeet", "gemma", "reference"]) out.push({kind: "model", name, label: schema.models[name].label, value: state.models[name], op: "download_model"});
  return out;
}
async function installEntry(entry) { if (entry.kind === "prerequisite" && entry.name === "python") throw Error("Python 3.11 is a host prerequisite"); await command(entry.op, {name: entry.name}); await waitForJob(entry.kind, entry.name); }
function renderInstallStrip() {
  const root = $("install-strip");
  const entries = installEntries();
  root.replaceChildren();
  for (const entry of entries) {
    const job = jobFor(entry.kind, entry.name);
    const status = job && job.status === "running" ? "running" : entry.value.status;
    const label = document.createElement("label");
    label.className = `install-chip ${status === "ready" ? "ready" : status === "running" ? "busy" : "bad"}`;
    label.title = job && job.status === "running" ? `${job.message} ${job.progress}%` : `${entry.label}: ${status}`;
    const input = document.createElement("input");
    input.type = "checkbox"; input.checked = status === "ready"; input.indeterminate = status === "running";
    input.disabled = status === "running" || (entry.kind === "prerequisite" && entry.name === "python");
    input.onchange = () => { if (!input.checked) input.checked = true; else installEntry(entry).catch(fail); };
    label.append(input, document.createTextNode(entry.label.replace(" VULKAN", "").replace("CHATTERBOX V3 ", "")));
    root.append(label);
  }
  const engines = $("engine-strip"); engines.replaceChildren();
  for (const name of ["asr", "brain", "tts"]) {
    const value = state.engines[name];
    const span = document.createElement("span");
    span.className = `engine-pill ${value.status}`;
    span.textContent = `${name.toUpperCase()}: ${value.status}`;
    engines.append(span);
  }
  const readyCount = entries.filter(entry => isReady(entry.value)).length;
  const activeEntry = entries.find(entry => {
    const job = jobFor(entry.kind, entry.name);
    return job && job.status === "running";
  });
  $("setup-summary").textContent = `${readyCount}/${entries.length} required ready`;
  if (!installingAll) {
    const statusLine = $("install-all-state");
    if (activeEntry) {
      const job = jobFor(activeEntry.kind, activeEntry.name);
      statusLine.textContent = `${job.message} - ${job.progress}%`;
    } else if (readyCount === entries.length) {
      statusLine.textContent = "All required items are verified";
    } else {
      statusLine.textContent = `${entries.length - readyCount} required item${entries.length - readyCount === 1 ? " is" : "s are"} missing`;
    }
  }
  renderBrain();
}
function renderBrain() {
  const select = $("brain-select"), familyWrap = $("brain-family-wrap"), urlWrap = $("brain-url-wrap"), apply = $("brain-apply");
  if (!select || !state.brain) return;
  const catalog = state.brain.catalog || schema.brains || {};
  if (!select.dataset.bound) {
    select.replaceChildren(...Object.entries(catalog).map(([id, spec]) => {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = spec.label || id;
      return option;
    }));
    select.dataset.bound = "1";
  }
  select.value = state.brain.active || "gemma";
  const custom = select.value === "custom";
  familyWrap.hidden = !custom; urlWrap.hidden = !custom; apply.hidden = !custom;
  const download = $("brain-download");
  const selected = catalog[select.value] && catalog[select.value].model;
  download.hidden = custom || !selected || isReady(state.models[selected]);
  download.onclick = () => installEntry({kind: "model", name: selected, label: schema.models[selected].label, value: state.models[selected], op: "download_model"}).catch(fail);
  if (custom) {
    $("brain-family").value = (state.brain.custom && state.brain.custom.family) || "generic";
    if (!$("brain-url").value) $("brain-url").value = (state.brain.custom && state.brain.custom.url) || "";
  }
  const job = jobFor("brain", "custom");
  $("brain-state").textContent = job && job.status === "running"
    ? `${job.message} - ${job.progress}%`
    : `${state.brain.label} - ${state.brain.ready ? "ready" : "use Download selected"}`;
}
function renderReference() { const ref = state.reference, source = ref.custom ? "Custom voice reference" : "Official Chatterbox demo voice"; $("reference-state").textContent = ref.status === "ready" ? `${source} - ${ref.duration.toFixed(1)} seconds. Identity only -- clip language does not have to match the spoken language. Use "Less reference accent" if you want less of the speaker's accent.` : `Voice reference: ${ref.status}.`; }
function renderFlow() {
  const flow = state.flow || {stage: "idle", transcript: "", answer: "", error: ""};
  const localStage = recording ? "listening" : clientStage || flow.stage;
  const current = localStage === "ready_to_speak" ? "speaking" : localStage;
  const order = ["listening", "transcribing", "thinking", "speaking"];
  const currentIndex = order.indexOf(current);
  document.querySelectorAll(".pipeline li").forEach(item => {
    const index = order.indexOf(item.dataset.stage);
    item.classList.toggle("active", index === currentIndex && !["complete", "error"].includes(localStage));
    item.classList.toggle("done", localStage === "complete" || (currentIndex >= 0 && index < currentIndex));
  });
  const badge = $("flow-badge");
  const labels = {idle: "Idle", listening: "Listening", transcribing: "Transcribing", thinking: "Thinking", ready_to_speak: "Preparing voice", speaking: "Speaking", complete: "Complete", error: "Error"};
  badge.textContent = labels[localStage] || localStage;
  badge.className = `badge ${localStage === "error" ? "bad" : ["listening", "transcribing", "thinking", "ready_to_speak", "speaking"].includes(localStage) ? "busy" : localStage === "complete" ? "good" : ""}`;
  if (flow.transcript) {
    $("transcript").textContent = flow.transcript;
    $("transcript").classList.remove("muted");
    $("asr-state").textContent = "Complete";
  } else if (localStage === "transcribing") {
    $("transcript").textContent = "Recognizing speech...";
    $("asr-state").textContent = "Working";
  }
  if (flow.answer && playback.text !== flow.answer && !["speaking", "complete"].includes(localStage)) renderAnswerWords(flow.answer);
  if (localStage === "thinking") $("speech-state").textContent = "Writing reply";
  if (localStage === "ready_to_speak") $("speech-state").textContent = "Voice ready";
  if (localStage === "error") fail(Error(flow.error || "Pipeline failed"));
}
function renderAnswerWords(text) {
  playback.text = text;
  const answer = $("answer");
  answer.classList.remove("muted");
  answer.replaceChildren();
  const parts = text.match(/\S+\s*/g) || [];
  for (const part of parts) {
    const span = document.createElement("span");
    span.className = "word";
    span.textContent = part;
    answer.append(span);
  }
}
function highlightSpoken() {
  const words = [...$("answer").querySelectorAll(".word")];
  if (!words.length) return;
  const estimated = Math.max(24000, words.length * 0.42 * 24000);
  const denominator = playback.done ? Math.max(playback.expected, 1) : Math.max(playback.expected, estimated, playback.received);
  const fraction = Math.max(0, Math.min(1, playback.played / denominator));
  const completed = playback.done && playback.played >= playback.expected ? words.length : Math.floor(fraction * words.length);
  words.forEach((word, index) => {
    word.classList.toggle("spoken", index < completed);
    word.classList.toggle("current", index === completed && completed < words.length);
  });
  if (playback.done && playback.played >= playback.expected) {
    clientStage = "complete";
    $("speech-state").textContent = "Playback complete";
    renderFlow();
    window.clearInterval(playback.timer);
    playback.timer = 0;
    $("cancel-speech").disabled = true;
    finishPlayback();
  }
}
function render(next) {
  state = next; renderInstallStrip(); renderReference(); renderFlow();
  const running = Object.values(state.engines).filter(engine => engine.status === "running").length;
  const ready = conversationReady(), live = enginesRunning();
  $("engines-toggle").textContent = running === 3 ? "Stop engines" : "Start engines";
  $("engines-toggle").disabled = running === 0 && !ready;
  $("install-all").disabled = installingAll;
  $("record").disabled = !recording && !live; $("pick-audio").disabled = !live || Boolean(recording);
  $("speak-text").disabled = state.engines.tts.status !== "running"; syncParamFields();
  if (recording) paintMic();
  else if (!live) $("recording-time").textContent = ready ? "Start engines, then open the microphone." : "Install the missing checked items first.";
}
async function waitForJob(kind, name, timeout = 3600000) {
  const key = `${kind}:${name}`;
  await waitFor(next => {
    const job = next.jobs[key];
    return Boolean(job) && ["running", "done", "error"].includes(job.status);
  }, timeout);
  await waitFor(next => {
    const job = next.jobs[key];
    if (job && job.status === "error") throw Error(job.error || `${key} failed`);
    return Boolean(job) && job.status === "done";
  }, timeout);
}
function requiredInstallSteps() {
  const steps = [];
  for (const name of Object.keys(schema.prerequisites)) {
    if (name === "python") continue;
    if (!isReady(state.prerequisites[name])) steps.push({kind: "prerequisite", name, op: "install_prerequisite", label: schema.prerequisites[name].label});
  }
  for (const name of Object.keys(schema.components)) {
    if (!isReady(state.components[name])) steps.push({kind: "component", name, op: "install_component", label: schema.components[name].label});
  }
  const models = ["chatterbox-t3", "chatterbox-codec", "chatterbox-s3t", "parakeet", "gemma", "reference"];
  const extra = state.brain && state.brain.model && state.brain.model !== "custom" ? state.brain.model : "";
  if (extra && !models.includes(extra)) models.push(extra);
  for (const name of models) {
    if (state.models[name] && !isReady(state.models[name])) steps.push({kind: "model", name, op: "download_model", label: schema.models[name].label});
  }
  return steps;
}
async function installAll() {
  if (installingAll) return;
  if (state.prerequisites.python && !isReady(state.prerequisites.python)) {
    throw Error("Python is a host prerequisite and cannot be installed from this panel");
  }
  const steps = requiredInstallSteps();
  const status = $("install-all-state");
  if (!steps.length) {
    status.textContent = "Nothing is missing. Start engines when you want the servers running.";
    return;
  }
  installingAll = true;
  $("install-all").disabled = true;
  try {
    for (let index = 0; index < steps.length; index += 1) {
      const step = steps[index];
      status.textContent = `Installing ${index + 1}/${steps.length}: ${step.label}`;
      await command(step.op, {name: step.name});
      await waitForJob(step.kind, step.name);
    }
    status.textContent = "Install complete. Required items are verified.";
  } finally {
    installingAll = false;
    render(state);
  }
}
async function waitFor(test, timeout = 180000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const next = await command("state");
    render(next);
    if (test(next)) return next;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw Error("Operation timed out");
}
async function ensureEngine(name) {
  if (state.engines[name].status === "running") return;
  await command("load_engine", {name});
  await waitFor(next => next.engines[name].status === "running" || next.engines[name].status === "error");
  if (state.engines[name].status !== "running") throw Error(state.engines[name].error || `${name} failed to start`);
}
async function startAll() {
  for (const name of ["asr", "brain", "tts"]) await ensureEngine(name);
}
async function stopAll() {
  await closeTts();
  for (const name of ["tts", "brain", "asr"]) {
    if (state.engines[name].status !== "stopped") {
      await command("unload_engine", {name});
      await waitFor(next => next.engines[name].status === "stopped");
    }
  }
}
async function ensurePlayback() {
  if (!playbackContext) {
    playbackContext = new AudioContext({sampleRate: 24000});
    await playbackContext.audioWorklet.addModule("/audio-processor.js");
    playbackNode = new AudioWorkletNode(playbackContext, "pcm-ring");
    playbackNode.connect(playbackContext.destination);
    playbackNode.port.onmessage = event => {
      if (event.data && ["played", "drained"].includes(event.data.type)) {
        playback.played = event.data.samples;
        highlightSpoken();
      }
    };
  }
  if (playbackContext.state !== "running") await playbackContext.resume();
}
function reportTts(event, data = {}) {
  command("tts_event", {lane: "a", event, ...data}).catch(fail);
}
function finishPlayback() { const done = playbackWait; playbackWait = null; if (done) done(); }
async function closeTts() {
  const socket = ttsSocket;
  ttsSocket = null;
  ttsSession = "";
  ttsLanguage = "";
  ttsStyle = "";
  ttsReferenceGeneration = -1;
  if (!socket) return;
  await new Promise(resolve => {
    let settled = false;
    const done = () => { if (!settled) { settled = true; resolve(); } };
    socket.addEventListener("close", done, {once: true});
    try { if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: "close"})); } catch (_) {}
    try { socket.close(); } catch (_) {}
    window.setTimeout(done, 750);
  });
}
async function openTts(language, style) {
  const generation = Number(state.reference_generation || 0);
  if (ttsSocket && ttsSocket.readyState === WebSocket.OPEN && ttsSession && ttsLanguage === language && ttsStyle === style && ttsReferenceGeneration === generation) return ttsSocket;
  await closeTts();
  await ensureEngine("tts");
  await ensurePlayback();
  const setup = await command("tts_session", {lane: "a", language, style});
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(setup.url);
    ttsSocket = socket;
    socket.binaryType = "arraybuffer";
    const timeout = window.setTimeout(() => reject(Error("Voice connection timed out")), 30000);
    socket.onopen = () => socket.send(JSON.stringify(setup.message));
    socket.onerror = () => reject(Error("Voice WebSocket failed"));
    socket.onclose = () => {
      if (ttsSocket === socket) {
        ttsSocket = null;
        ttsSession = "";
        reportTts("closed");
        finishPlayback();
      }
    };
    socket.onmessage = event => {
      if (event.data instanceof ArrayBuffer) {
        const view = new DataView(event.data);
        const pcm = new Float32Array(event.data.byteLength / 2);
        for (let i = 0; i < pcm.length; i++) pcm[i] = view.getInt16(i * 2, true) / 32768;
        playback.received += pcm.length;
        diagnostic.output.push(pcm);
        playbackNode.port.postMessage(pcm);
        return;
      }
      const message = JSON.parse(event.data);
      if (message.type === "ready") {
        window.clearTimeout(timeout);
        ttsSession = message.session_id || "";
        ttsLanguage = language;
        ttsStyle = style;
        ttsReferenceGeneration = Number(state.reference_generation || 0);
        command("tts_event", {lane: "a", event: "ready", session_id: ttsSession}).then(() => resolve(socket)).catch(reject);
      } else if (message.type === "synthesize_started") {
        clientStage = "speaking";
        $("speech-state").textContent = "Streaming audio";
        reportTts("synthesize_started", {session_id: ttsSession, request_id: message.request_id});
        renderFlow();
      } else if (message.type === "chunk_done") {
        playback.done = true;
        playback.expected = playback.received;
        reportTts("chunk_done", {session_id: ttsSession, request_id: message.request_id, samples: playback.received});
        renderDiagnostic(true);
        highlightSpoken();
      } else if (message.type === "cancelled") {
        playback.done = true;
        playback.expected = playback.played;
        reportTts("cancelled", {session_id: ttsSession, request_id: message.request_id, samples: playback.received});
        $("speech-state").textContent = "Stopped";
        finishPlayback();
      } else if (message.type === "error") {
        playback.done = true;
        reportTts("error", {message: message.message || "Voice error", request_id: message.request_id});
        fail(Error(message.message || "Voice error"));
        finishPlayback();
      }
    };
  });
}
async function speak(text, language, style = "natural", source = "lab") {
  text = String(text || "").trim();
  if (!text) throw Error("There is no text to speak");
  const held = Boolean(recording && recording.busy);
  if (recording) recording.busy = true;
  finishPlayback();
  renderAnswerWords(text);
  clientStage = "speaking";
  playback = {received: 0, played: 0, expected: 0, text, done: false, timer: 0};
  diagnostic.output = []; diagnostic.answer = text; diagnostic.source = source;
  if (source !== "turn") { diagnostic.input = null; diagnostic.transcript = ""; }
  renderFlow(); renderDiagnostic(false);
  try {
    await ensurePlayback();
    playbackNode.port.postMessage({type: "clear"});
    const socket = await openTts(language, style);
    const request = await command("tts_request", {lane: "a", text});
    $("cancel-speech").disabled = false;
    playback.timer = window.setInterval(highlightSpoken, 100);
    socket.send(JSON.stringify(request.message));
    await new Promise(resolve => { playbackWait = resolve; });
  } finally {
    if (recording && !held) recording.busy = false;
  }
}
async function cancelSpeech() {
  if (ttsSession) await command("tts_cancel", {session_id: ttsSession});
  clientStage = "idle";
  if (playbackNode) playbackNode.port.postMessage({type: "clear"});
  $("cancel-speech").disabled = true;
  finishPlayback();
}
function makeWav(parts, rate) {
  const length = parts.reduce((sum, part) => sum + part.length, 0);
  const data = new ArrayBuffer(44 + length * 2);
  const view = new DataView(data);
  const text = (offset, value) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + length * 2, true); text(8, "WAVEfmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const part of parts) for (let i = 0; i < part.length; i++, offset += 2) view.setInt16(offset, Math.max(-1, Math.min(1, part[i])) * 32767, true);
  return data;
}
function rms(frame) {
  let sum = 0;
  for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
  return Math.sqrt(sum / frame.length);
}
function trimPreRoll(parts, rate) {
  const limit = Math.floor(rate * 0.3);
  let total = parts.reduce((sum, part) => sum + part.length, 0);
  while (parts.length && total - parts[0].length >= limit) total -= parts.shift().length;
}
function paintMic() { const on = Boolean(state.config["conversation.vad"]); $("record").textContent = recording ? (on ? "Stop listening" : "Stop and ask") : "Start listening"; $("record").classList.toggle("danger", Boolean(recording)); }
function paintMicLevel() {
  if (!recording) return;
  const seconds = ((performance.now() - recording.started) / 1000).toFixed(1);
  const level = recording.lastRms.toFixed(3);
  $("recording-time").textContent = recording.busy
    ? `Microphone open - waiting for the reply - RMS ${level}`
    : `Listening - ${seconds} s - RMS ${level}${recording.speaking ? " - speech" : ""}`;
}
function onCapture(frame) {
  if (!recording || !frame || !frame.length || recording.busy) return;
  const rec = recording;
  const level = rms(frame);
  rec.lastRms = level;
  if (!state.config["conversation.vad"]) {
    rec.parts.push(frame);
    return;
  }
  const rate = rec.context.sampleRate;
  const ms = frame.length / rate * 1000;
  if (level >= Number(state.config["asr.vad.threshold"])) {
    if (!rec.speaking) {
      rec.speaking = true;
      rec.speechMs = 0;
      rec.silenceMs = 0;
    }
    rec.speechMs += ms;
    rec.silenceMs = 0;
    rec.parts.push(frame);
    return;
  }
  if (!rec.speaking) {
    rec.parts.push(frame);
    trimPreRoll(rec.parts, rate);
    return;
  }
  rec.parts.push(frame);
  rec.silenceMs += ms;
  if (rec.silenceMs < Number(state.config["asr.vad.silence_ms"])) return;
  const ready = rec.speechMs >= Number(state.config["asr.vad.min_speech_ms"]);
  const parts = rec.parts;
  rec.parts = [];
  rec.speaking = false;
  rec.speechMs = 0;
  rec.silenceMs = 0;
  if (ready) submitUtterance(makeWav(parts, rate)).catch(fail);
}
async function submitUtterance(wav) {
  if (!recording) return;
  recording.busy = true;
  try {
    await runTurn(wav);
  } finally {
    if (recording) recording.busy = false;
  }
}
async function startRecording() {
  const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false}, video: false});
  const context = new AudioContext({sampleRate: 16000});
  await context.audioWorklet.addModule("/audio-processor.js");
  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, "pcm-capture", {numberOfInputs: 1, numberOfOutputs: 0});
  node.port.onmessage = event => onCapture(event.data);
  source.connect(node);
  recording = {stream, context, source, node, parts: [], started: performance.now(), timer: 0, speaking: false, speechMs: 0, silenceMs: 0, lastRms: 0, busy: false};
  paintMic();
  state.flow.stage = "listening";
  renderFlow();
  recording.timer = window.setInterval(paintMicLevel, 100);
}
async function stopRecording() {
  const current = recording;
  recording = null;
  window.clearInterval(current.timer);
  current.source.disconnect();
  current.node.disconnect();
  current.stream.getTracks().forEach(track => track.stop());
  const rate = current.context.sampleRate;
  await current.context.close();
  paintMic();
  if (state.config["conversation.vad"]) {
    $("recording-time").textContent = "Microphone closed.";
    return;
  }
  $("recording-time").textContent = "Processing the recording...";
  if (!current.parts.length) throw Error("No microphone audio was captured");
  await runTurn(makeWav(current.parts, rate));
}
async function runTurn(wav) {
  diagnostic.input = decodeWav(wav); diagnostic.output = []; diagnostic.transcript = ""; diagnostic.answer = ""; diagnostic.source = "turn"; renderDiagnostic(false);
  clientStage = "transcribing";
  $("transcript").textContent = "Recognizing speech...";
  $("transcript").classList.remove("muted");
  $("answer").textContent = "Waiting for the assistant...";
  $("answer").classList.add("muted");
  $("asr-state").textContent = "Working";
  $("speech-state").textContent = "Waiting";
  const language = $("conversation-language").value;
  const result = await api(`/api?op=turn&language=${encodeURIComponent(language)}`, wav, true);
  if (!result.text) throw Error("The assistant returned no reply");
  diagnostic.transcript = (result.results && result.results.asr && result.results.asr.text) || (state.flow && state.flow.transcript) || "";
  await speak(result.text, language, "natural", "turn");
  $("recording-time").textContent = recording && state.config["conversation.vad"]
    ? "Listening for the next utterance."
    : "Ready for another question.";
}
function decodeWav(buffer) {
  const view = new DataView(buffer), ascii = (o, n) => String.fromCharCode(...new Uint8Array(buffer, o, n));
  if (view.byteLength < 44 || ascii(0, 4) !== "RIFF" || ascii(8, 4) !== "WAVE") throw Error("Audio diagnostic requires a PCM WAV file");
  let offset = 12, format = null, dataOffset = 0, dataSize = 0;
  while (offset + 8 <= view.byteLength) {
    const id = ascii(offset, 4), size = view.getUint32(offset + 4, true), body = offset + 8;
    if (id === "fmt " && size >= 16) format = {codec: view.getUint16(body, true), channels: view.getUint16(body + 2, true), rate: view.getUint32(body + 4, true), bits: view.getUint16(body + 14, true)};
    if (id === "data") { dataOffset = body; dataSize = Math.min(size, view.byteLength - body); break; }
    offset = body + size + (size & 1);
  }
  if (!format || format.codec !== 1 || format.bits !== 16 || !dataOffset) throw Error("Audio diagnostic supports PCM16 WAV only");
  const frames = Math.floor(dataSize / 2 / format.channels), samples = new Float32Array(frames);
  for (let i = 0; i < frames; i++) {
    let value = 0;
    for (let c = 0; c < format.channels; c++) value += view.getInt16(dataOffset + (i * format.channels + c) * 2, true) / 32768;
    samples[i] = value / format.channels;
  }
  return {samples, rate: format.rate};
}
function joinOutput() {
  const length = diagnostic.output.reduce((sum, chunk) => sum + chunk.length, 0), samples = new Float32Array(length);
  let offset = 0; for (const chunk of diagnostic.output) { samples.set(chunk, offset); offset += chunk.length; }
  return {samples, rate: diagnostic.outputRate};
}
function signalMetrics(signal, seams = []) {
  const {samples, rate} = signal || {}; if (!samples || !samples.length) return null;
  let sum = 0, sumsq = 0, peak = 0, clipped = 0, crossings = 0;
  for (let i = 0; i < samples.length; i++) { const x = samples[i]; sum += x; sumsq += x * x; peak = Math.max(peak, Math.abs(x)); clipped += Math.abs(x) >= .999; if (i && (x >= 0) !== (samples[i - 1] >= 0)) crossings++; }
  const size = Math.min(1024, samples.length), start = Math.max(0, Math.floor((samples.length - size) / 2));
  let weighted = 0, energy = 0;
  for (let k = 1; k < Math.floor(size / 2); k += 2) { let re = 0, im = 0; for (let n = 0; n < size; n++) { const a = 2 * Math.PI * k * n / size, x = samples[start + n]; re += x * Math.cos(a); im -= x * Math.sin(a); } const m = re * re + im * im; energy += m; weighted += m * k * rate / size; }
  const jumps = seams.map(i => i > 0 && i < samples.length ? Math.abs(samples[i] - samples[i - 1]) : 0);
  return {seconds: samples.length / rate, rms_dbfs: 20 * Math.log10(Math.max(1e-9, Math.sqrt(sumsq / samples.length))), peak_dbfs: 20 * Math.log10(Math.max(1e-9, peak)), clip_pct: 100 * clipped / samples.length, dc: sum / samples.length, zcr_hz: crossings * rate / (2 * samples.length), centroid_hz: energy ? weighted / energy : 0, seam_peak: jumps.length ? Math.max(...jumps) : 0};
}
function outputSeams() { let total = 0; const seams = []; for (let i = 0; i < diagnostic.output.length - 1; i++) { total += diagnostic.output[i].length; seams.push(total); } return seams; }
function drawSpectrogram(ctx, signal, y, height, label, metrics) {
  const {samples, rate} = signal || {}; ctx.fillStyle = "#070a0c"; ctx.fillRect(8, y, 704, height);
  ctx.fillStyle = "#dbe5ea"; ctx.font = "12px ui-monospace,Consolas,monospace";
  if (!samples || !samples.length) { ctx.fillText(`${label}: no audio`, 18, y + 22); return; }
  const n = 256, frames = Math.min(88, Math.max(1, Math.floor(samples.length / n))), maxBin = Math.max(2, Math.min(n / 2, Math.floor(8000 * n / rate))), top = y + 28, h = height - 38, w = 684 / frames;
  for (let frame = 0; frame < frames; frame++) {
    const start = Math.floor(frame * Math.max(0, samples.length - n) / Math.max(1, frames - 1));
    for (let k = 0; k < maxBin; k++) { let re = 0, im = 0; for (let i = 0; i < n; i++) { const x = (samples[start + i] || 0) * (.5 - .5 * Math.cos(2 * Math.PI * i / (n - 1))), a = 2 * Math.PI * k * i / n; re += x * Math.cos(a); im -= x * Math.sin(a); } const db = 20 * Math.log10(Math.max(1e-7, Math.hypot(re, im) / n)); const v = Math.round(255 * Math.max(0, Math.min(1, (db + 80) / 70))); ctx.fillStyle = `rgb(${v},${v},${v})`; ctx.fillRect(18 + frame * w, top + h - (k + 1) * h / maxBin, Math.ceil(w), Math.ceil(h / maxBin)); }
  }
  ctx.fillStyle = "#dbe5ea"; ctx.fillText(`${label} ${rate} Hz | ${metrics.seconds.toFixed(2)} s | RMS ${metrics.rms_dbfs.toFixed(1)} dBFS | peak ${metrics.peak_dbfs.toFixed(1)} | clip ${metrics.clip_pct.toFixed(3)}% | centroid ${metrics.centroid_hz.toFixed(0)} Hz`, 18, y + 17);
}
function renderDiagnostic(logMetric = false) {
  const canvas = $("audio-diagnostic"), ctx = canvas.getContext("2d"), input = diagnostic.input, output = joinOutput();
  const im = signalMetrics(input), om = signalMetrics(output, outputSeams());
  ctx.fillStyle = "#050708"; ctx.fillRect(0, 0, canvas.width, canvas.height); ctx.fillStyle = "#f1f5f7"; ctx.font = "bold 15px system-ui"; ctx.fillText("TRIDENT AUDIO SIGNAL REPORT", 12, 20);
  drawSpectrogram(ctx, input, 32, 190, "INPUT", im || {}); drawSpectrogram(ctx, output, 228, 190, "OUTPUT", om || {});
  ctx.font = "11px ui-monospace,Consolas,monospace"; ctx.fillStyle = "#9caab4";
  const clean = value => String(value || "").replace(/\s+/g, " ").slice(0, 100);
  ctx.fillText(`ASR: ${clean(diagnostic.transcript)}`, 12, 442); ctx.fillText(`TTS: ${clean(diagnostic.answer)}`, 12, 460);
  if (om) ctx.fillText(`OUTPUT dc=${om.dc.toFixed(5)} zcr=${om.zcr_hz.toFixed(0)}Hz seam_peak=${om.seam_peak.toFixed(5)} chunks=${diagnostic.output.length}`, 12, 478);
  $("audio-metrics").textContent = om ? `Output: ${om.seconds.toFixed(2)} s, RMS ${om.rms_dbfs.toFixed(1)} dBFS, peak ${om.peak_dbfs.toFixed(1)} dBFS, clipped ${om.clip_pct.toFixed(3)}%, seam jump ${om.seam_peak.toFixed(5)}.` : im ? `Input: ${im.seconds.toFixed(2)} s, RMS ${im.rms_dbfs.toFixed(1)} dBFS, peak ${im.peak_dbfs.toFixed(1)} dBFS.` : "Run one conversation turn to populate signal metrics.";
  $("save-diagnostic").disabled = !im && !om;
  if (logMetric && om) command("note", {component: "audio", msg: "output", data: om}).catch(() => {});
}
function saveDiagnostic() { const canvas = $("audio-diagnostic"); canvas.toBlob(blob => { if (!blob) return; const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "trident-audio-diagnostic.png"; a.click(); window.setTimeout(() => URL.revokeObjectURL(a.href), 1000); }, "image/png"); }
function formatLogTime(ts) {
  return typeof ts === "number" ? new Date(ts * 1000).toLocaleTimeString() : "--:--:--";
}
function paintLogs(lines) {
  const box = $("log-output");
  box.replaceChildren();
  for (const line of lines) {
    const div = document.createElement("div");
    const level = String(line.level || "").toLowerCase();
    div.className = `log-entry ${level === "error" ? "log-error" : level === "warn" ? "log-warn" : ""}`;
    const data = line.data && Object.keys(line.data).length ? ` ${JSON.stringify(line.data)}` : "";
    div.textContent = `[${formatLogTime(line.ts)}] ${line.component || "-"} - ${line.msg || ""}${data}`;
    box.append(div);
  }
  box.scrollTop = box.scrollHeight;
}
async function refreshLogs() {
  const result = await command("log", {limit: 120});
  paintLogs(result.lines || []);
}
function openEvents() {
  events = new EventSource("/api?op=events");
  const connection = $("connection");
  const touch = () => {
    lastEvent = Date.now();
    connection.textContent = "Local service online";
    connection.className = "badge good";
  };
  events.addEventListener("state", event => { touch(); render(JSON.parse(event.data)); });
  events.addEventListener("job", event => {
    touch();
    const value = JSON.parse(event.data);
    state.jobs[value.key] = value;
    render(state);
  });
  events.addEventListener("ping", touch);
  events.onerror = () => {
    connection.textContent = "Reconnecting";
    connection.className = "badge bad";
  };
  window.setInterval(() => {
    if (lastEvent && Date.now() - lastEvent > 22000) events.onerror();
  }, 3000);
}
$("record").onclick = () => (recording ? stopRecording() : startRecording()).catch(fail);
$("pick-audio").onclick = () => $("audio-file").click();
$("audio-file").onchange = async () => {
  try {
    const file = $("audio-file").files[0];
    if (file) await runTurn(await file.arrayBuffer());
  } catch (error) { fail(error); }
  $("audio-file").value = "";
};
$("cancel-speech").onclick = () => cancelSpeech().catch(fail);
$("speak-text").onclick = () => speak($("speech-text").value, $("speech-language").value, $("speech-style").value, "lab").catch(fail);
$("upload-reference").onclick = () => $("reference-file").click();
$("reference-file").onchange = async () => {
  try {
    const file = $("reference-file").files[0];
    if (file) await api("/api?op=upload_reference", await file.arrayBuffer(), true);
  } catch (error) { fail(error); }
  $("reference-file").value = "";
};
$("install-all").onclick = () => installAll().catch(error => { installingAll = false; $("install-all").disabled = false; fail(error); });
$("engines-toggle").onclick = () => (Object.values(state.engines).every(engine => engine.status === "running") ? stopAll() : startAll()).catch(fail);
$("save-diagnostic").onclick = saveDiagnostic;
$("refresh-log").onclick = () => refreshLogs().catch(fail);
$("clear-log").onclick = () => command("clear_log").then(result => paintLogs(result.lines || [])).catch(fail);
$("brain-select").onchange = () => {
  const name = $("brain-select").value;
  if (name === "custom") {
    renderBrain();
    return;
  }
  command("set_brain", {name}).catch(fail);
};
$("brain-apply").onclick = async () => {
  try {
    const result = await command("set_brain", {name: "custom", url: $("brain-url").value, family: $("brain-family").value});
    if (result.accepted) await waitFor(next => next.brain && next.brain.active === "custom" && next.brain.ready);
  } catch (error) { fail(error); }
};
api("/api").then(boot => {
  schema = boot.schema;
  state = boot.state;
  bindConfig();
  buildParamGroups();
  render(state);
  refreshLogs().catch(() => {});
  openEvents();
}).catch(fail);
