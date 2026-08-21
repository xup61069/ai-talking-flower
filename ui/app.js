/* AI Talking Flower - app.js (拆自 index.html) */
"use strict";
const $ = (id) => document.getElementById(id);
let ws = null;
let state = "等待說話";
let currentFlowerMsgBubble = null;
let currentThinkingRow = null;

function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function toast(text, kind = "info", ms = 4500) {
  const container = $("toasts");
  const el = document.createElement("div");
  el.className = "toast " + kind;
  const icons = { info: "🌸", warn: "⏰", error: "⚠️" };
  el.innerHTML = `<span>${icons[kind] || "✨"}</span> <span>${esc(text)}</span>`;
  container.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transform = "translateY(10px)";
    el.style.transition = "all 0.3s ease";
    setTimeout(() => el.remove(), 300);
  }, ms);
}

function setState(s) {
  state = s;
  $("state-text").textContent = s;
  const pill = $("status-pill");
  const stage = $("avatar-stage");
  pill.className = "status-pill " + (s.includes("說話") ? "speaking" : s.includes("正在聽") ? "listening" : s.includes("思考") || s.includes("辨識") ? "thinking" : "");
  stage.className = "avatar-stage panel " + (s.includes("說話") ? "speaking" : s.includes("正在聽") ? "listening" : s.includes("思考") || s.includes("辨識") ? "thinking" : "");
  
  if (s.includes("說話")) {
    $("flower-mouth").setAttribute("d", "M60,76 Q70,92 80,76 Q70,82 60,76");
    removeThinkingIndicator();
  } else if (s.includes("思考") || s.includes("辨識")) {
    $("flower-mouth").setAttribute("d", "M66,78 Q70,74 74,78");
    showThinkingIndicator();
  } else {
    $("flower-mouth").setAttribute("d", "M64,76 Q70,83 76,76");
    removeThinkingIndicator();
  }
}

function showThinkingIndicator() {
  if (currentThinkingRow) return;
  const chat = $("chat");
  const row = document.createElement("div");
  row.className = "msg-row flower";
  row.id = "thinking-row";
  row.innerHTML = `
    <div class="msg-avatar">🌸</div>
    <div class="msg-bubble">
      <div class="typing-indicator">
        <span class="typing-dot"></span>
        <span class="typing-dot"></span>
        <span class="typing-dot"></span>
      </div>
    </div>
  `;
  chat.appendChild(row);
  chat.scrollTop = chat.scrollHeight;
  currentThinkingRow = row;
}

function removeThinkingIndicator() {
  if (currentThinkingRow) {
    currentThinkingRow.remove();
    currentThinkingRow = null;
  }
}

function appendMsg(role, text) {
  removeThinkingIndicator();
  const chat = $("chat");
  const row = document.createElement("div");
  row.className = "msg-row " + role;
  row.innerHTML = `
    <div class="msg-avatar">${role === "user" ? "👤" : "🌸"}</div>
    <div class="msg-bubble">${esc(text)}</div>
  `;
  chat.appendChild(row);
  chat.scrollTop = chat.scrollHeight;
  if (role === "flower") {
    const preview = text.slice(0, 32) + (text.length > 32 ? "…" : "");
    $("speech-bubble").textContent = "「" + preview + "」";
  }
  return row.querySelector(".msg-bubble");
}

function appendLog(message, isError) {
  const logs = $("logs");
  if (logs.firstChild && logs.firstChild.nodeType === 3) logs.innerHTML = "";
  const line = document.createElement("div");
  line.className = isError ? "err" : "";
  line.textContent = message;
  logs.appendChild(line);
  while (logs.children.length > 250) logs.removeChild(logs.firstChild);
  logs.scrollTop = logs.scrollHeight;
}

function spawnParticles(x, y) {
  const emojis = ["🌸", "💖", "✨", "⭐", "🌺"];
  const stage = $("avatar-stage");
  const rect = stage.getBoundingClientRect();
  for (let i = 0; i < 7; i++) {
    const p = document.createElement("span");
    p.className = "particle";
    p.textContent = emojis[Math.floor(Math.random() * emojis.length)];
    p.style.left = (x - rect.left - 10) + "px";
    p.style.top = (y - rect.top - 10) + "px";
    const angle = (Math.PI * 2 * i) / 7 + (Math.random() - 0.5);
    const dist = 50 + Math.random() * 40;
    p.style.setProperty("--dx", (Math.cos(angle) * dist) + "px");
    p.style.setProperty("--dy", (Math.sin(angle) * dist - 25) + "px");
    p.style.setProperty("--rot", ((Math.random() - 0.5) * 60) + "deg");
    stage.appendChild(p);
    setTimeout(() => p.remove(), 800);
  }
}

let audioCtx = null;
function getAudioCtx() {
  if (!audioCtx) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (AudioCtx) audioCtx = new AudioCtx();
  }
  if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function playChime() {
  try {
    const ctx = getAudioCtx();
    if (!ctx) return;
    const notes = [523.25, 659.25, 783.99]; // C5, E5, G5
    notes.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, ctx.currentTime + i * 0.07);
      gain.gain.setValueAtTime(0.06, ctx.currentTime + i * 0.07);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.07 + 0.3);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(ctx.currentTime + i * 0.07);
      osc.stop(ctx.currentTime + i * 0.07 + 0.32);
    });
  } catch (e) {}
}

function playReminderBell() {
  try {
    const ctx = getAudioCtx();
    if (!ctx) return;
    const notes = [659.25, 880, 1046.5, 1318.5]; // E5, A5, C6, E6
    notes.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(freq, ctx.currentTime + i * 0.1);
      gain.gain.setValueAtTime(0.08, ctx.currentTime + i * 0.1);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.1 + 0.5);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(ctx.currentTime + i * 0.1);
      osc.stop(ctx.currentTime + i * 0.1 + 0.55);
    });
  } catch (e) {}
}

function triggerPokeAnimation(e) {
  playChime();
  const svg = $("flower-svg");
  svg.classList.remove("flower-poked");
  void svg.offsetWidth;
  svg.classList.add("flower-poked");
  if (e && e.clientX) {
    spawnParticles(e.clientX, e.clientY);
  } else {
    const rect = $("avatar-stage").getBoundingClientRect();
    spawnParticles(rect.left + rect.width / 2, rect.top + rect.height / 2);
  }
}

function handleEvent(evt) {
  switch (evt.type) {
    case "state":
      setState(evt.state);
      break;
    case "audio":
      const pct = Math.min(100, evt.rms / (evt.threshold || 0.001) * 40);
      $("rms-bar").style.width = pct + "%";
      $("rms-text").textContent = `rms ${evt.rms.toFixed(4)} / 門檻 ${(evt.threshold || 0.008).toFixed(4)}`;
      break;
    case "user_text":
      appendMsg("user", evt.text);
      currentFlowerMsgBubble = null;
      break;
    case "flower_delta":
      removeThinkingIndicator();
      if (!currentFlowerMsgBubble) currentFlowerMsgBubble = appendMsg("flower", "");
      currentFlowerMsgBubble.textContent += evt.text;
      const preview = currentFlowerMsgBubble.textContent.slice(0, 32) + "…";
      $("speech-bubble").textContent = "「" + preview + "」";
      $("chat").scrollTop = $("chat").scrollHeight;
      break;
    case "metrics":
      $("hud-asr").textContent = (evt.asr_ms || 0) + "ms";
      $("hud-ttft").textContent = (evt.ttft_ms || 0) + "ms";
      $("hud-ttfa").textContent = (evt.ttfa_ms || 0) + "ms";
      $("hud-total").textContent = (evt.total_turn_ms || 0) + "ms";
      break;
    case "poke":
      triggerPokeAnimation();
      $("speech-bubble").textContent = "「" + evt.text + "」";
      break;
    case "reminder":
      playReminderBell();
      toast("⏰ 提醒時間到：" + evt.text, "warn", 12000);
      renderReminders();
      break;
    case "reminder_added":
      playChime();
      renderReminders();
      break;
    case "persona_changed":
      toast(`已切換性格至「${evt.name}」🌸`, "info");
      renderPersonas();
      break;
    case "log":
      appendLog(evt.message);
      break;
    case "error":
      appendLog("錯誤：" + (evt.message || ""), true);
      toast("系統發生錯誤，請看日誌", "error");
      break;
    case "paused":
      $("btn-listen").textContent = "恢復聆聽";
      toast("已暫停聆聽", "info");
      break;
    case "resumed":
      $("btn-listen").textContent = "暫停聆聽";
      toast("已恢復聆聽", "info");
      break;
    case "calibration":
      $("calibrate-result").textContent = evt.message || "";
      toast(evt.message || "AEC 校準完成", "info", 8000);
      break;
  }
}

function connect() {
  ws = new WebSocket("ws://" + location.host + "/api/ws");
  ws.onmessage = (e) => { try { handleEvent(JSON.parse(e.data)); } catch (err) { } };
  ws.onclose = () => {
    $("state-text").textContent = "重新連線中…";
    setTimeout(connect, 1500);
  };
}

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}
async function get(path) { return (await fetch(path)).json(); }
async function del(path) { return (await fetch(path, { method: "DELETE" })).json(); }

$("avatar-stage").onclick = async (e) => {
  triggerPokeAnimation(e);
  await post("/api/action", { action: "poke" });
};
$("btn-poke").onclick = (e) => {
  e.stopPropagation();
  triggerPokeAnimation();
  post("/api/action", { action: "poke" });
};

$("btn-listen").onclick = async () => {
  const paused = $("btn-listen").textContent.includes("恢復");
  const res = await post("/api/action", { action: paused ? "resume" : "pause" });
  if (!res.ok) toast("操作失敗：" + (res.reason || ""), "warn");
};

$("btn-restart").onclick = async () => {
  toast("正在要求重啟管線…", "info");
  await post("/api/action", { action: "restart" });
};

$("btn-clear-chat").onclick = () => {
  $("chat").innerHTML = '<div class="mini" style="text-align:center; padding:10px">已清空即時視窗</div>';
};

function sendQuickPrompt(txt) {
  $("test-llm-text").value = txt;
  $("btn-test-llm").click();
}

$("btn-test-tts").onclick = async () => {
  const res = await post("/api/action", { action: "test_tts", text: $("test-tts-text").value });
  if (!res.ok) toast(res.reason || "測試失敗", "warn");
};

$("btn-test-llm").onclick = async () => {
  const text = $("test-llm-text").value.trim();
  if (!text) return;
  const res = await post("/api/action", { action: "test_llm", text, speak: true });
  if (!res.ok) toast(res.reason || "測試失敗", "warn");
};

// 鍵盤快捷鍵（空白鍵戳戳、Enter 快速發送）
window.addEventListener("keydown", (e) => {
  const tag = document.activeElement ? document.activeElement.tagName.toLowerCase() : "";
  if (e.code === "Space" && tag !== "input" && tag !== "textarea" && tag !== "select") {
    e.preventDefault();
    $("btn-poke").click();
  }
});
$("test-llm-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    $("btn-test-llm").click();
  }
});
$("test-tts-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    $("btn-test-tts").click();
  }
});

/* ---------- 🎭 性格卡片 ---------- */
async function renderPersonas() {
  const data = await get("/api/personas");
  const wrap = $("persona-cards");
  wrap.innerHTML = "";
  data.personas.forEach((p) => {
    const card = document.createElement("div");
    card.className = "persona-card" + (data.active === p.id ? " active" : "");
    card.innerHTML = `
      <div class="persona-header">
        <span class="persona-tag">${esc(p.tag)}</span>
      </div>
      <div class="persona-desc">${esc(p.description)}</div>
    `;
    card.onclick = async () => {
      await post("/api/personas/select", { id: p.id });
    };
    wrap.appendChild(card);
  });
}

/* ---------- ⏰ 提醒事項 ---------- */
async function renderReminders() {
  const data = await get("/api/reminders");
  const wrap = $("reminder-list");
  wrap.innerHTML = "";
  if (!data.reminders || !data.reminders.length) {
    wrap.innerHTML = '<div class="mini" style="padding:4px 0; color:var(--text-muted)">尚無排程中的提醒</div>';
    return;
  }
  data.reminders.forEach((r) => {
    const el = document.createElement("div");
    el.className = "reminder-item";
    const minutes = Math.max(1, Math.round(r.due_in_s / 60));
    el.innerHTML = `
      <span class="reminder-text">📌 ${esc(r.text)}</span>
      <span class="reminder-timer">約 ${minutes} 分鐘後</span>
      <button class="danger small" style="padding:1px 6px">✕</button>
    `;
    el.querySelector("button").onclick = async (e) => {
      e.stopPropagation();
      await del(`/api/reminders/${r.id}`);
      renderReminders();
    };
    wrap.appendChild(el);
  });
}

$("btn-add-reminder").onclick = async () => {
  const text = $("reminder-text").value.trim();
  const in_seconds = Number($("reminder-time").value);
  if (!text) { toast("請輸入提醒內容", "warn"); return; }
  const res = await post("/api/reminders", { text, in_seconds });
  if (res.ok) {
    $("reminder-text").value = "";
    toast("已成功排程提醒事項！", "info");
    renderReminders();
  } else {
    toast(res.reason || "設定失敗", "warn");
  }
};

/* ---------- 設定表單 ---------- */
const SECTION_NAMES = {
  app: "應用程式 (App)", audio: "音訊裝置 (Audio)", aec: "回音消除 (AEC)", interaction: "互動與插話 (Interaction)",
  idle_chat: "主動碎碎念 (Idle Chat)", vad: "語音偵測 (VAD)", asr: "語音辨識 (ASR)", llm: "大腦思維 (LLM)", tts: "聲音合成 (TTS)",
};

function fieldInput(spec) {
  const name = "f-" + spec.path;
  if (spec.kind === "bool") {
    return `<label><input type="checkbox" id="${name}" data-path="${spec.path}" ${spec.value ? "checked" : ""}> ${esc(spec.label)}</label>`;
  }
  let input;
  if (spec.kind === "choice") {
    input = `<select id="${name}" data-path="${spec.path}">` +
      spec.options.map((o) => `<option value="${esc(o)}" ${String(o) === String(spec.value) ? "selected" : ""}>${esc(o)}</option>`).join("") +
      "</select>";
  } else if (spec.kind === "text") {
    input = `<textarea id="${name}" data-path="${spec.path}" rows="3">${esc(spec.value)}</textarea>`;
  } else if (spec.kind === "str") {
    input = `<input type="text" id="${name}" data-path="${spec.path}" value="${esc(spec.value)}">`;
  } else {
    const attrs = `min="${spec.minimum}" max="${spec.maximum}" step="${spec.step}"`;
    input = `<input type="number" id="${name}" data-path="${spec.path}" value="${esc(spec.value)}" ${attrs}>`;
  }
  return `<div class="field"><label>${esc(spec.label)} <span class="tag ${spec.apply}">${spec.apply === "live" ? "即時生效" : "需重啟"}</span></label>${input}</div>`;
}

async function renderSettings() {
  const data = await get("/api/settings");
  const filter = ($("settings-filter").value || "").toLowerCase();
  const groups = {};
  for (const spec of data.settings) {
    if (filter && !spec.path.toLowerCase().includes(filter) && !spec.label.toLowerCase().includes(filter)) continue;
    const section = spec.path.split(".")[0];
    (groups[section] = groups[section] || []).push(spec);
  }
  const wrap = $("settings");
  wrap.innerHTML = "";
  for (const [section, specs] of Object.entries(groups)) {
    const details = document.createElement("details");
    details.open = Boolean(filter);
    const summary = document.createElement("summary");
    summary.innerHTML = `<span>${SECTION_NAMES[section] || section}</span> <span class="mini">${specs.length} 項</span>`;
    details.appendChild(summary);
    const body = document.createElement("div");
    body.className = "body";
    body.innerHTML = specs.map(fieldInput).join("");
    details.appendChild(body);
    wrap.appendChild(details);
  }
}

$("settings-filter").oninput = renderSettings;

$("btn-settings-save").onclick = async () => {
  const paths = {};
  document.querySelectorAll("[data-path]").forEach((el) => {
    if (el.type === "checkbox") paths[el.dataset.path] = el.checked;
    else if (el.type === "number") {
      const value = Number(el.value);
      if (Number.isFinite(value)) paths[el.dataset.path] = value;
    }
    else paths[el.dataset.path] = el.value;
  });
  const res = await (await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  })).json();
  $("settings-status").textContent = res.errors
    ? "錯誤：" + res.errors.join("、")
    : `已套用 ${res.applied_live.length} 項即時設定${res.needs_restart.length ? "，需重啟項目將自動重建" : ""}`;
  if (res.needs_restart.length) toast("有設定需要重啟管線，花花將自動重載", "warn", 7000);
};

/* ---------- 記憶管理 ---------- */
let memVisible = false;
async function refreshMemory(keyword = "") {
  const url = keyword ? `/api/memory/search?q=${encodeURIComponent(keyword)}` : "/api/memory";
  const data = await get(url);
  $("mem-count").textContent = `${data.messages.length} 則`;
  const wrap = $("memlist");
  if (!memVisible) { wrap.style.display = ""; memVisible = true; }
  wrap.innerHTML = "";
  if (!data.messages.length) {
    wrap.innerHTML = '<div class="mini" style="color:var(--text-muted)">（無相符對話紀錄）</div>';
    return;
  }
  data.messages.forEach((m) => {
    const item = document.createElement("div");
    item.className = "mem-item";
    item.innerHTML = `
      <div class="content"><b>${m.role === "user" ? "你" : "花花"}</b> ${esc(m.content)}</div>
      ${m.id ? `<button class="danger small" style="padding:1px 5px">✕</button>` : ""}
    `;
    if (m.id) {
      item.querySelector("button").onclick = async () => {
        await del(`/api/memory/${m.id}`);
        refreshMemory($("mem-search").value);
      };
    }
    wrap.appendChild(item);
  });
}

$("btn-mem-refresh").onclick = () => refreshMemory($("mem-search").value);
$("btn-mem-search").onclick = () => refreshMemory($("mem-search").value);
$("mem-search").onkeyup = (e) => { if (e.key === "Enter") refreshMemory($("mem-search").value); };

$("btn-mem-export").onclick = async () => {
  const res = await post("/api/action", { action: "export_memory" });
  if (!res.ok) return;
  const blob = new Blob([JSON.stringify(res.messages, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "flower-memory.json";
  a.click();
  URL.revokeObjectURL(a.href);
};

$("btn-mem-clear").onclick = async () => {
  if (!confirm("確定清除所有對話記憶？")) return;
  const res = await post("/api/action", { action: "clear_memory" });
  if (res.ok) { $("mem-count").textContent = "0 則"; $("memlist").innerHTML = ""; toast("已清空歷史記憶", "info"); }
};

$("btn-calibrate").onclick = async () => {
  $("calibrate-result").textContent = "校準中（約 6 秒）…";
  $("btn-calibrate").disabled = true;
  const res = await post("/api/action", { action: "calibrate_aec" });
  if (!res.ok) { $("calibrate-result").textContent = res.reason || "校準失敗"; }
  setTimeout(() => { $("btn-calibrate").disabled = false; }, 9000);
};

async function renderVoices() {
  const status = await get("/api/status");
  $("flower-name").textContent = status.name || "花花";
  const isCosy = String(status.tts_backend || "").includes("cosyvoice");
  $("voice-panel").style.display = isCosy ? "" : "none";
  $("voice-muted").style.display = isCosy ? "none" : "";
  if (!isCosy) return;
  const data = await get("/api/voices");
  const wrap = $("voices");
  wrap.innerHTML = "";
  const mk = (index, label, active, extra) => {
    const el = document.createElement("label");
    el.style.cssText = "display:flex; align-items:center; gap:8px; padding:6px 10px; background:var(--panel-sub); border-radius:8px; font-size:12.5px; cursor:pointer";
    el.innerHTML = `<input type="radio" name="voice" value="${index}" ${active ? "checked" : ""}> <span>${label}</span> <span class="mini" style="color:var(--text-muted)">${extra || ""}</span>`;
    el.querySelector("input").onchange = async () => {
      toast(`正在切換至「${label}」並重載…`, "warn", 35000);
      await post("/api/voices", { index });
    };
    return el;
  };
  wrap.appendChild(mk(0, "官方示範音色", data.active === null || !data.active));
  data.voices.forEach((v) => wrap.appendChild(mk(v.index, v.voice, data.active === v.voice, v.transcript)));
  const styleRes = await get("/api/style");
  $("style-text").value = styleRes.style || "";
}

$("btn-style-save").onclick = async () => {
  const res = await (await fetch("/api/style", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ style: $("style-text").value }),
  })).json();
  toast(res.ok ? "風格已更新" : "風格儲存失敗", res.ok ? "info" : "warn");
};

/* ---------- 🎨 主題切換與即時時鐘 ---------- */
function initThemes() {
  const select = $("theme-select");
  if (!select) return;
  const saved = localStorage.getItem("flower_theme") || "theme-sakura";
  document.body.className = saved;
  select.value = saved;
  select.onchange = () => {
    document.body.className = select.value;
    localStorage.setItem("flower_theme", select.value);
  };
}

function updateLiveClock() {
  const el = $("live-clock");
  if (!el) return;
  const now = new Date();
  const days = ["週日", "週一", "週二", "週三", "週四", "週五", "週六"];
  const pad = (n) => String(n).padStart(2, "0");
  const mo = pad(now.getMonth() + 1);
  const da = pad(now.getDate());
  const hr = pad(now.getHours());
  const mi = pad(now.getMinutes());
  const se = pad(now.getSeconds());
  const day = days[now.getDay()];
  el.innerHTML = `<span>🕒 <b>${mo}/${da}</b> (${day}) <b>${hr}:${mi}:${se}</b></span>`;
}

function initWaveform() {
  const canvas = $("waveform-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let wavePhase = 0;

  function renderWave() {
    requestAnimationFrame(renderWave);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const isSpeaking = state === "說話中";
    const isListening = state === "正在聽";
    const isThinking = state === "思考中";

    const amp = isSpeaking ? 11 : (isListening ? 6.5 : (isThinking ? 4 : 1.5));
    const freq = isSpeaking ? 0.08 : (isListening ? 0.055 : 0.035);
    const color = isSpeaking ? "#ff6ea3" : (isListening ? "#4ee49d" : (isThinking ? "#ffb84d" : "rgba(255,255,255,0.18)"));

    ctx.beginPath();
    ctx.lineWidth = isSpeaking || isListening ? 2.2 : 1.2;
    ctx.strokeStyle = color;
    ctx.shadowBlur = isSpeaking || isListening ? 6 : 0;
    ctx.shadowColor = color;

    const midY = canvas.height / 2;
    for (let x = 0; x < canvas.width; x++) {
      const envelope = Math.sin(Math.PI * (x / canvas.width));
      const y = midY + Math.sin(x * freq + wavePhase) * amp * envelope;
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.shadowBlur = 0;
    wavePhase += isSpeaking ? 0.14 : (isListening ? 0.08 : 0.03);
  }
  renderWave();
}

async function init() {
  initThemes();
  updateLiveClock();
  setInterval(updateLiveClock, 1000);
  initWaveform();
  connect();
  await renderSettings();
  await renderVoices();
  await renderPersonas();
  await renderReminders();
  refreshMemory();
  setInterval(() => { refreshMemory($("mem-search").value); renderReminders(); }, 30000);
}
init();