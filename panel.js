"use strict";

const $ = id => document.getElementById(id);
let schema;
let state;
let events;
let lastEvent = 0;
let recording = null;
let playbackContext = null;
let playbackNode = null;
let ttsSocket = null;
let ttsSession = "";
let ttsLanguage = "";
let ttsStyle = "";
let ttsReferenceGeneration = -1;
let playback = {received: 0, played: 0, expected: 0, text: "", done: false, timer: 0};
let clientStage = "";

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

function fillLanguages(select, languages) {
  select.replaceChildren(...Object.entries(languages).map(([code, name]) => {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = `${name} (${code})`;
    return option;
  }));
}

async function save(path, value) {
  await command("set", {values: {[path]: value}});
}

function bindConfig() {
  fillLanguages($("conversation-language"), schema.languages.conversation);
  fillLanguages($("speech-language"), schema.languages.speech);
  for (const element of document.querySelectorAll("[data-path]")) {
    const path = element.dataset.path;
    if (element.type === "checkbox") element.checked = Boolean(state.config[path]);
    else element.value = state.config[path];
    element.addEventListener("change", () => {
      const value = element.type === "checkbox" ? element.checked : element.value;
      save(path, value).catch(fail);
    });
  }
}

function stateClass(status) {
  return ["ready", "running", "done"].includes(status) ? "state-ready" : ["missing", "unverified", "invalid", "error"].includes(status) ? "state-error" : "";
}

function jobFor(kind, name) {
  return state.jobs[`${kind}:${name}`] || null;
}

function isReady(value) {
  return Boolean(value) && value.status === "ready";
}

function activeBrainReady() {
  const brain = state.brain || {};
  if (brain.active === "custom") return Boolean(brain.custom && brain.custom.status === "ready");
  return isReady(state.models[brain.model || "gemma"]);
}

function engineCanStart(name) {
  const components = {asr: "parakeet", brain: "gemma", tts: "tts"};
  if (name === "brain") return isReady(state.components.gemma) && activeBrainReady();
  const models = {asr: ["parakeet"], tts: ["chatterbox-t3", "chatterbox-codec", "chatterbox-s3t", "reference"]};
  return isReady(state.components[components[name]]) && models[name].every(model => isReady(state.models[model]));
}

function conversationReady() {
  return ["asr", "brain", "tts"].every(engineCanStart);
}

function setupItem(label, status, action, actionLabel, detail = "", blocked = false, allowWhileRunning = false) {
  const item = document.createElement("div");
  item.className = "setup-item";
  const copy = document.createElement("div");
  const strong = document.createElement("strong");
  const small = document.createElement("small");
  strong.textContent = label;
  small.textContent = detail || status;
  small.className = stateClass(status);
  copy.append(strong, small);
  item.append(copy);
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = actionLabel;
    button.disabled = blocked || status === "ready" || (status === "running" && !allowWhileRunning);
    button.onclick = () => action().catch(fail);
    item.append(button);
  }
  return item;
}

function renderSetup() {
  const prereqs = $("prerequisites");
  prereqs.replaceChildren();
  for (const [name, spec] of Object.entries(schema.prerequisites)) {
    const value = state.prerequisites[name];
    const job = jobFor("prerequisite", name);
    const status = job && job.status === "running" ? "running" : value.status;
    const detail = job && job.status === "running" ? `${job.message} · ${job.progress}%` : value.path || value.status;
    prereqs.append(setupItem(spec.label, status, () => command("install_prerequisite", {name}), "Install", detail));
  }
  const components = $("components");
  components.replaceChildren();
  for (const [name, spec] of Object.entries(schema.components)) {
    const value = state.components[name];
    const job = jobFor("component", name);
    const status = job && job.status === "running" ? "running" : value.status;
    const detail = job && job.status === "running" ? `${job.message} · ${job.progress}%` : value.revision;
    components.append(setupItem(spec.label, status, () => command("install_component", {name}), name === "tts" ? "Build" : "Download", detail));
  }
  const models = $("models");
  models.replaceChildren();
  for (const [name, spec] of Object.entries(schema.models)) {
    const value = state.models[name];
    const job = jobFor("model", name);
    const status = job && job.status === "running" ? "running" : value.status;
    const detail = job && job.status === "running" ? `${job.message} · ${job.progress}%` : `${status} · ${(spec.size / 1048576).toFixed(0)} MiB`;
    models.append(setupItem(spec.label, status, () => command("download_model", {name}), "Download", detail));
  }
  const engines = $("engines");
  engines.replaceChildren();
  for (const name of ["asr", "brain", "tts"]) {
    const value = state.engines[name];
    const job = jobFor("engine", name);
    const status = job && job.status === "running" ? "running" : value.status;
    const label = {asr: "Parakeet ear", brain: `Brain · ${(state.brain && state.brain.label) || "llama.cpp"}`, tts: "Chatterbox voice"}[name];
    const stopping = value.status === "running";
    const action = status === "loading" ? null : () => command(stopping ? "unload_engine" : "load_engine", {name});
    engines.append(setupItem(label, status, action, stopping ? "Stop" : "Start", value.error || status, !stopping && !engineCanStart(name), stopping));
  }
  const readyComponents = Object.values(state.components).filter(value => value.status === "ready").length;
  const readyModels = Object.values(state.models).filter(value => value.status === "ready").length;
  $("setup-summary").textContent = `${readyComponents}/${Object.keys(state.components).length} components · ${readyModels}/${Object.keys(state.models).length} assets`;
  renderBrain();
}

function renderBrain() {
  const select = $("brain-select");
  const familyWrap = $("brain-family-wrap");
  const urlWrap = $("brain-url-wrap");
  const apply = $("brain-apply");
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
  familyWrap.hidden = !custom;
  urlWrap.hidden = !custom;
  apply.hidden = !custom;
  if (custom) {
    $("brain-family").value = (state.brain.custom && state.brain.custom.family) || "generic";
    if (!$("brain-url").value) $("brain-url").value = (state.brain.custom && state.brain.custom.url) || "";
  }
  const job = jobFor("brain", "custom");
  $("brain-state").textContent = job && job.status === "running"
    ? `${job.message} · ${job.progress}%`
    : `${state.brain.label} · ${state.brain.ready ? "ready" : "download this GGUF in Models first"}`;
}

function renderReference() {
  const ref = state.reference;
  const source = ref.custom ? "Custom voice reference" : "Official Chatterbox demo voice";
  $("reference-state").textContent = ref.status === "ready"
    ? `${source} · ${ref.duration.toFixed(1)} seconds. Identity only — clip language does not have to match the spoken language. Use “Less reference accent” if you want less of the speaker’s accent.`
    : `Voice reference: ${ref.status}.`;
}

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
    $("transcript").textContent = "Recognizing speech…";
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
  }
}

function render(next) {
  state = next;
  renderSetup();
  renderReference();
  renderFlow();
  const running = Object.values(state.engines).filter(engine => engine.status === "running").length;
  const ready = conversationReady();
  $("engines-toggle").textContent = running === 3 ? "Stop engines" : "Start engines";
  $("engines-toggle").disabled = running === 0 && !ready;
  $("start-all").disabled = !ready;
  $("record").disabled = !recording && !ready;
  $("pick-audio").disabled = !ready;
  $("speak-text").disabled = !engineCanStart("tts");
  if (!recording && !ready) $("recording-time").textContent = "Complete Setup before starting a conversation. Missing components or assets are shown below.";
}

async function waitFor(test, timeout = 180000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const next = await api("/api/state");
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
  return command("tts_event", {lane: "a", event, ...data}).catch(() => {});
}

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
      }
    };
    socket.onmessage = event => {
      if (event.data instanceof ArrayBuffer) {
        const view = new DataView(event.data);
        const pcm = new Float32Array(event.data.byteLength / 2);
        for (let i = 0; i < pcm.length; i++) pcm[i] = view.getInt16(i * 2, true) / 32768;
        playback.received += pcm.length;
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
        reportTts("ready", {session_id: ttsSession});
        resolve(socket);
      } else if (message.type === "synthesize_started") {
        clientStage = "speaking";
        $("speech-state").textContent = "Streaming audio";
        reportTts("synthesize_started", {session_id: ttsSession, request_id: message.request_id});
        renderFlow();
      } else if (message.type === "chunk_done") {
        playback.done = true;
        playback.expected = playback.received;
        reportTts("chunk_done", {session_id: ttsSession, request_id: message.request_id, samples: playback.received});
        highlightSpoken();
      } else if (message.type === "cancelled") {
        playback.done = true;
        playback.expected = playback.played;
        reportTts("cancelled", {session_id: ttsSession, request_id: message.request_id, samples: playback.received});
        $("speech-state").textContent = "Stopped";
      } else if (message.type === "error") {
        reportTts("error", {message: message.message || "Voice error", request_id: message.request_id});
        fail(Error(message.message || "Voice error"));
      }
    };
  });
}

async function speak(text, language, style = "natural") {
  text = String(text || "").trim();
  if (!text) throw Error("There is no text to speak");
  renderAnswerWords(text);
  clientStage = "speaking";
  playback = {received: 0, played: 0, expected: 0, text, done: false, timer: 0};
  renderFlow();
  await ensurePlayback();
  playbackNode.port.postMessage({type: "clear"});
  const socket = await openTts(language, style);
  const request = await command("tts_request", {lane: "a", text});
  $("cancel-speech").disabled = false;
  playback.timer = window.setInterval(highlightSpoken, 100);
  socket.send(JSON.stringify(request.message));
}

async function cancelSpeech() {
  if (ttsSession) await command("tts_cancel", {session_id: ttsSession});
  clientStage = "idle";
  if (playbackNode) playbackNode.port.postMessage({type: "clear"});
  $("cancel-speech").disabled = true;
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

async function startRecording() {
  const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false}, video: false});
  const context = new AudioContext({sampleRate: 16000});
  await context.audioWorklet.addModule("/audio-processor.js");
  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, "pcm-capture", {numberOfInputs: 1, numberOfOutputs: 0});
  const parts = [];
  node.port.onmessage = event => { if (event.data && event.data.length) parts.push(event.data); };
  source.connect(node);
  recording = {stream, context, source, node, parts, started: performance.now(), timer: 0};
  $("record").textContent = "Stop and ask";
  $("record").classList.add("danger");
  state.flow.stage = "listening";
  renderFlow();
  recording.timer = window.setInterval(() => {
    const seconds = (performance.now() - recording.started) / 1000;
    $("recording-time").textContent = `Listening through the Windows default microphone · ${seconds.toFixed(1)} seconds`;
  }, 100);
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
  $("record").textContent = "Start listening";
  $("record").classList.remove("danger");
  $("recording-time").textContent = "Processing the recording…";
  if (!current.parts.length) throw Error("No microphone audio was captured");
  await runTurn(makeWav(current.parts, rate));
}

async function runTurn(wav) {
  clientStage = "";
  $("transcript").textContent = "Recognizing speech…";
  $("transcript").classList.remove("muted");
  $("answer").textContent = "Waiting for the assistant…";
  $("answer").classList.add("muted");
  $("asr-state").textContent = "Working";
  $("speech-state").textContent = "Waiting";
  const language = $("conversation-language").value;
  const clone = $("clone-voice").checked;
  const result = await api(`/api?op=turn&source=upload&language=${encodeURIComponent(language)}&clone=${clone ? "true" : "false"}`, wav, true);
  if (!result.text) throw Error("The assistant returned no reply");
  await speak(result.text, language, "natural");
  $("recording-time").textContent = "Ready for another question.";
}

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
    div.textContent = `[${formatLogTime(line.ts)}] ${line.component || "-"} · ${line.msg || ""}${data}`;
    box.append(div);
  }
  box.scrollTop = box.scrollHeight;
}

async function refreshLogs() {
  const result = await command("log", {limit: 120});
  paintLogs(result.lines || []);
}

function openEvents() {
  events = new EventSource("/api/events");
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
$("speak-text").onclick = () => speak($("speech-text").value, $("speech-language").value, $("speech-style").value).catch(fail);
$("upload-reference").onclick = () => $("reference-file").click();
$("reference-file").onchange = async () => {
  try {
    const file = $("reference-file").files[0];
    if (file) await api("/api/reference", await file.arrayBuffer(), true);
  } catch (error) { fail(error); }
  $("reference-file").value = "";
};
$("start-all").onclick = () => startAll().catch(fail);
$("stop-all").onclick = () => stopAll().catch(fail);
$("engines-toggle").onclick = () => (Object.values(state.engines).every(engine => engine.status === "running") ? stopAll() : startAll()).catch(fail);
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
  render(state);
  refreshLogs().catch(() => {});
  openEvents();
}).catch(fail);
