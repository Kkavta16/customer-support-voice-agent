// Customer Support client: connects to the Pipecat bot over WebRTC and renders the conversation.
import { PipecatClient } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";

const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"), statusText: $("statusText"), avatar: $("avatar"), agentState: $("agentState"),
  activity: $("activity"), activityText: $("activityText"), callBtn: $("callBtn"), callLabel: $("callLabel"),
  muteBtn: $("muteBtn"), messages: $("messages"), composer: $("composer"), textInput: $("textInput"),
  sendBtn: $("sendBtn"), audio: $("botAudio"), toast: $("toast"),
};

// Friendly, customer-facing labels. The server only sends tool names, never queries or results.
const TOOL_LABELS = {
  search_products: "Searching our catalog…",
  get_product_details: "Looking up product details…",
  query_products_sql: "Checking our catalog…",
  get_order_status: "Checking your order…",
  get_policy: "Checking our policies…",
  create_support_ticket: "Creating your support ticket…",
};

const AGENT_TEXT = {
  idle: "Start a call to talk with our AI support agent",
  connecting: "Connecting you to an agent…",
  ready: "I'm listening. Go ahead and speak",
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking… (you can interrupt anytime)",
};

let client = null;
let pendingPrompt = null;
let bot = null;      // { el, segments: Map } for the agent message being spoken
let userLive = null; // element showing the in-progress user transcript

// ---------- connection ----------
async function startCall() {
  setCallState("connecting");
  client = new PipecatClient({
    transport: new SmallWebRTCTransport(),
    enableMic: true,
    enableCam: false,
    callbacks: {
      onBotReady: () => {
        setCallState("live");
        if (pendingPrompt) sendText(pendingPrompt);
        pendingPrompt = null;
      },
      onDisconnected: () => setCallState("idle"),
      onTrackStarted: (track, participant) => {
        if (track.kind === "audio" && !participant?.local) els.audio.srcObject = new MediaStream([track]);
      },
      onUserStartedSpeaking: () => { setAgent("listening"); bot = null; },
      onUserStoppedSpeaking: () => setAgent("thinking"),
      onUserTranscript: ({ text, final }) => showUserTranscript(text, final),
      onBotLlmStarted: () => { bot = null; },
      onBotOutput: (data) => showBotOutput(data),
      onBotStartedSpeaking: () => { setAgent("speaking"); hideActivity(); },
      onBotStoppedSpeaking: () => setAgent("ready"),
      onLLMFunctionCallStarted: ({ function_name }) => showActivity(TOOL_LABELS[function_name] || "One moment…"),
      onLLMFunctionCallStopped: () => hideActivity(),
      onLocalAudioLevel: (level) => setLevel(level, "user"),
      onRemoteAudioLevel: (level) => setLevel(level, "bot"),
      onError: (msg) => toast(msg?.data?.message || "Something went wrong"),
    },
  });
  try {
    await client.connect({ webrtcRequestParams: { endpoint: "/api/offer" } });
  } catch (e) {
    console.error(e);
    toast("Couldn't connect. Is the server running and is the microphone allowed?");
    setCallState("idle");
  }
}

async function endCall() {
  await client?.disconnect();
  client = null;
  setCallState("idle");
}

function sendText(text) {
  if (client?.connected) {
    bot = null;
    addMessage("user", text);
    client.sendText(text);
    setAgent("thinking");
  } else {
    pendingPrompt = text;
    if (!client) startCall();
  }
}

// ---------- conversation rendering ----------
function addMessage(role, text) {
  els.messages.querySelector(".empty")?.remove();
  const row = document.createElement("div");
  row.className = `msg ${role}`;
  row.innerHTML = `<div class="bubble"></div><span class="meta">${role === "user" ? "You" : "Support agent"} · ${time()}</span>`;
  row.querySelector(".bubble").textContent = text;
  els.messages.appendChild(row);
  scrollDown();
  return row.querySelector(".bubble");
}

function showUserTranscript(text, final) {
  if (!text?.trim()) return;
  if (!userLive) {
    userLive = addMessage("user", text);
    userLive.classList.add("live");
  }
  userLive.textContent = text;
  if (final) {
    userLive.classList.remove("live");
    userLive = null;
  }
  scrollDown();
}

function showBotOutput(data) {
  if (!data?.text || data.spoken_status === "in-progress") return;
  if (!bot) bot = { el: addMessage("agent", ""), segments: new Map() };
  bot.segments.set(data.segment_id ?? bot.segments.size, data.text.trim());
  bot.el.textContent = [...bot.segments.values()].join(" ");
  scrollDown();
}

// ---------- UI state ----------
function setCallState(state) {
  const live = state === "live";
  els.status.dataset.state = state;
  els.statusText.textContent = { idle: "Offline", connecting: "Connecting", live: "Live" }[state];
  els.callBtn.classList.toggle("danger", state !== "idle");
  els.callBtn.disabled = state === "connecting";
  els.callLabel.textContent = state === "idle" ? "Start call" : state === "connecting" ? "Connecting…" : "End call";
  els.muteBtn.disabled = !live;
  els.textInput.disabled = els.sendBtn.disabled = !live;
  els.avatar.dataset.state = state === "live" ? "ready" : state;
  setAgent(state === "live" ? "ready" : state);
  if (state === "idle") {
    hideActivity();
    setMuted(false);
    bot = userLive = null;
  }
}

function setAgent(state) {
  els.agentState.textContent = AGENT_TEXT[state];
  els.avatar.dataset.state = state;
}

function showActivity(text) {
  els.activityText.textContent = text;
  els.activity.hidden = false;
  setAgent("thinking");
}

function hideActivity() {
  els.activity.hidden = true;
}

function setLevel(level, who) {
  const speaking = els.avatar.dataset.state;
  if ((who === "bot" && speaking === "speaking") || (who === "user" && speaking === "listening")) {
    els.avatar.style.setProperty("--level", Math.min(1, level * 3).toFixed(2));
  }
}

function setMuted(muted) {
  els.muteBtn.setAttribute("aria-pressed", String(muted));
  els.muteBtn.title = muted ? "Unmute microphone" : "Mute microphone";
}

function toast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (els.toast.hidden = true), 5000);
}

const time = () => new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const scrollDown = () => (els.messages.scrollTop = els.messages.scrollHeight);

// ---------- wiring ----------
els.callBtn.onclick = () => (client ? endCall() : startCall());

// Hang up when the tab is closed or refreshed, so no agent is left running in the background.
window.addEventListener("pagehide", () => client?.disconnect());

els.muteBtn.onclick = () => {
  const muted = client.isMicEnabled;
  client.enableMic(!muted);
  setMuted(muted);
};

els.composer.onsubmit = (e) => {
  e.preventDefault();
  const text = els.textInput.value.trim();
  if (!text) return;
  sendText(text);
  els.textInput.value = "";
};

document.querySelectorAll("[data-prompt]").forEach((chip) => {
  chip.onclick = () => sendText(chip.dataset.prompt);
});

$("clearBtn").onclick = () => {
  els.messages.innerHTML = `<div class="empty">The conversation will appear here.</div>`;
  bot = userLive = null;
};

fetch("/api/info").then((r) => r.json()).then((info) => {
  $("storeName").textContent = info.store;
  $("stack").textContent = `Runs locally · Whisper ${info.stt} · ${info.llm} · Kokoro · Pipecat`;
}).catch(() => {});

setCallState("idle");
