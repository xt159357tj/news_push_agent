let userTimezone = "Asia/Shanghai";
let currentSubId = null;
let sending = false;

const SUGGESTIONS = [
  "立即生成简报",
  "查看历史简报",
  "我现在的订阅设置是什么？",
  "列出 workspace 里的文件",
  "搜索最近的 Claude 新闻",
];

function updateTimezone(value) {
  try {
    new Intl.DateTimeFormat("zh-CN", { timeZone: value }).format();
    userTimezone = value;
  } catch {
    userTimezone = "Asia/Shanghai";
  }
  const tz = document.getElementById("delivery_timezone");
  if (tz) tz.textContent = userTimezone;
}

function formatDigestTime(value, timeZone = userTimezone) {
  if (!value) return "时间未知";
  // Older API versions returned UTC datetimes without an explicit offset.
  const timestamp = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).format(date);
}

function updateDeliveryTime() {
  const hour = document.getElementById("delivery_hour").value.padStart(2, "0");
  const minute = document.getElementById("delivery_minute").value.padStart(2, "0");
  const value = `${hour}:${minute}`;
  document.getElementById("delivery_time").value = value;
  document.getElementById("delivery_time_display").textContent = value;
  document.getElementById("delivery_hour_value").textContent = hour;
  document.getElementById("delivery_minute_value").textContent = minute;
  document.getElementById("delivery_hour").setAttribute("aria-valuetext", `${hour} 时`);
  document.getElementById("delivery_minute").setAttribute("aria-valuetext", `${minute} 分`);
}

function setDeliveryTime(value) {
  const valid = /^([01]\d|2[0-3]):([0-5]\d)$/.test(value);
  const [hour, minute] = (valid ? value : "08:00").split(":");
  document.getElementById("delivery_hour").value = String(Number(hour));
  document.getElementById("delivery_minute").value = String(Number(minute));
  updateDeliveryTime();
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text);
  }
  return res.json();
}

function split(v) {
  return (v || "").split(/[,，]/).map((s) => s.trim()).filter(Boolean);
}

function setPanelMessage(id, text, isError = false) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("error", isError);
}

function updateStatusLine(profile, sub) {
  const el = document.getElementById("status-line");
  if (!el) return;
  const who = profile && profile.name ? profile.name : "访客";
  const tz = profile && profile.timezone ? profile.timezone : userTimezone;
  const time = sub && sub.delivery_time ? `每天 ${sub.delivery_time} 推送` : "尚未设置推送时间";
  const topics = sub && sub.topics && sub.topics.length ? sub.topics.join("、") : "尚未设置订阅话题";
  el.textContent = `${who} · ${tz} · ${time} · ${topics}`;
}

let cachedProfile = null;
let cachedSub = null;

async function loadSettings() {
  try {
    const profile = await api("/api/profile");
    cachedProfile = profile;
    document.getElementById("name").value = profile.name || "";
    document.getElementById("email").value = profile.email || "";
    document.getElementById("timezone").value = profile.timezone || "Asia/Shanghai";
    updateTimezone(profile.timezone || "Asia/Shanghai");
  } catch (e) { console.error(e); }

  try {
    const subs = await api("/api/subscriptions");
    if (subs.length) {
      const s = subs[subs.length - 1];
      cachedSub = s;
      document.getElementById("topics").value = (s.topics || []).join(",");
      document.getElementById("keywords").value = (s.keywords || []).join(",");
      document.getElementById("language").value = s.language || "zh";
      document.getElementById("max_items").value = s.max_items || 10;
      setDeliveryTime(s.delivery_time || "08:00");
      currentSubId = s.id;
    }
  } catch (e) { console.error(e); }
  updateStatusLine(cachedProfile, cachedSub);
}

async function saveProfile() {
  try {
    const profile = await api("/api/profile", {
      method: "PUT",
      body: JSON.stringify({
        name: document.getElementById("name").value,
        email: document.getElementById("email").value,
        timezone: document.getElementById("timezone").value,
      }),
    });
    cachedProfile = profile;
    updateTimezone(profile.timezone || "Asia/Shanghai");
    updateStatusLine(cachedProfile, cachedSub);
    setPanelMessage("profile-message", "已保存");
    appendChatMessage("assistant", `登录信息已更新：${profile.name}（${profile.email}），时区 ${profile.timezone}。`);
    closeDialog("dlg-profile");
  } catch (e) {
    setPanelMessage("profile-message", `保存失败：${e.message}`, true);
  }
}

// Persist subscription fields. `fields` limits which inputs are read so the
// time panel and the preference panel can save independently.
async function persistSubscription() {
  const payload = {
    topics: split(document.getElementById("topics").value),
    keywords: split(document.getElementById("keywords").value),
    language: document.getElementById("language").value,
    max_items: Number(document.getElementById("max_items").value) || 10,
    delivery_time: document.getElementById("delivery_time").value || "08:00",
    notify_email: true,
  };
  let out;
  if (currentSubId) {
    out = await api(`/api/subscriptions/${currentSubId}`, { method: "PUT", body: JSON.stringify(payload) });
  } else {
    out = await api("/api/subscriptions", { method: "POST", body: JSON.stringify(payload) });
    currentSubId = out.id;
  }
  cachedSub = out;
  updateStatusLine(cachedProfile, cachedSub);
  return out;
}

async function saveSubscription() {
  try {
    const out = await persistSubscription();
    setPanelMessage("sub-message", "已保存");
    const topics = out.topics.length ? out.topics.join("、") : "（未设置）";
    appendChatMessage("assistant", `订阅偏好已更新：话题 ${topics}，${out.max_items} 条，语言 ${out.language === "zh" ? "中文" : "English"}。想马上看效果，可以对我说「立即生成简报」。`);
    closeDialog("dlg-sub");
  } catch (e) {
    setPanelMessage("sub-message", `保存失败：${e.message}`, true);
  }
}

async function saveDeliveryTime() {
  try {
    const out = await persistSubscription();
    setPanelMessage("time-message", "已保存");
    appendChatMessage("assistant", `推送时间已设为每天 ${out.delivery_time}（${userTimezone}）。`);
    closeDialog("dlg-time");
  } catch (e) {
    setPanelMessage("time-message", `保存失败：${e.message}`, true);
  }
}

function openDialog(id) {
  const dlg = document.getElementById(id);
  if (dlg && typeof dlg.showModal === "function") dlg.showModal();
}

function closeDialog(id) {
  const dlg = document.getElementById(id);
  if (dlg && typeof dlg.close === "function") dlg.close();
}

// ---------- chat ----------
const LINK_RE = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>"')，。）]+)/g;

// Build bubble content with DOM nodes only (no innerHTML) so model output
// can never inject markup; URLs and [text](url) become safe links.
function renderRichText(container, text) {
  container.textContent = "";
  let last = 0;
  const source = text || "";
  for (const m of source.matchAll(LINK_RE)) {
    if (m.index > last) container.appendChild(document.createTextNode(source.slice(last, m.index)));
    const a = document.createElement("a");
    a.href = m[2] || m[3];
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = m[1] || m[3];
    container.appendChild(a);
    last = m.index + m[0].length;
  }
  if (last < source.length) container.appendChild(document.createTextNode(source.slice(last)));
}

function appendChatMessage(role, content, toolCalls) {
  const log = document.getElementById("chat-log");
  if (!log) return null;
  const wrap = document.createElement("div");
  wrap.className = `chat-msg ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  renderRichText(bubble, content);
  wrap.appendChild(bubble);

  if (toolCalls && toolCalls.length) {
    const trace = document.createElement("div");
    trace.className = "chat-trace";
    for (const t of toolCalls) {
      const chip = document.createElement("span");
      chip.className = `trace-chip${t.ok ? "" : " fail"}`;
      chip.textContent = `${t.ok ? "✓" : "✗"} ${t.name}`;
      chip.title = JSON.stringify(t.arguments || {});
      trace.appendChild(chip);
    }
    wrap.appendChild(trace);
  }

  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return wrap;
}

function showPending() {
  const wrap = appendChatMessage("assistant", "");
  const bubble = wrap.querySelector(".chat-bubble");
  bubble.classList.add("pending");
  bubble.textContent = "思考中";
  const dots = document.createElement("span");
  dots.className = "dots";
  bubble.appendChild(dots);
  return wrap;
}

function renderGreeting() {
  const who = cachedProfile && cachedProfile.name ? cachedProfile.name : "你好";
  const hasSub = Boolean(cachedSub);
  const lines = [
    `${who}，我是每日 AI 新闻助手。`,
    "登录信息、推送时间、订阅偏好请用右上角的按钮设置；其余都直接对我说就行：",
    "· 「立即生成简报」按你的订阅偏好现场整理一份",
    "· 「查看历史简报」「打开第 2 份」回看以前的简报",
    "· 「搜索最近的 Claude 新闻」「列出 workspace 里的文件」",
  ];
  if (!hasSub) lines.push("你还没有订阅偏好，先点「订阅偏好」设置关注的话题吧。");
  appendChatMessage("assistant", lines.join("\n"));
}

function renderSuggestions() {
  const box = document.getElementById("suggestions");
  if (!box) return;
  box.textContent = "";
  for (const text of SUGGESTIONS) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "suggestion";
    b.textContent = text;
    b.addEventListener("click", () => sendChat(text));
    box.appendChild(b);
  }
}

async function loadChatHistory() {
  try {
    const msgs = await api("/api/chat/history?session_id=default");
    const log = document.getElementById("chat-log");
    if (!log) return;
    log.textContent = "";
    if (!msgs.length) {
      renderGreeting();
      return;
    }
    for (const m of msgs) appendChatMessage(m.role, m.content, m.tool_calls);
  } catch (e) {
    console.error(e);
    renderGreeting();
  }
}

function autoGrow(el) {
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
}

async function sendChat(preset) {
  if (sending) return;
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  if (!input) return;

  const text = (typeof preset === "string" ? preset : input.value).trim();
  if (!text) return;

  sending = true;
  if (sendBtn) sendBtn.disabled = true;
  appendChatMessage("user", text, []);
  input.value = "";
  autoGrow(input);
  const pending = showPending();

  try {
    const res = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message: text, session_id: "default" }),
    });
    pending.remove();
    appendChatMessage("assistant", res.reply, res.tool_calls);
    // A tool call may have changed what the header summarizes.
    if ((res.tool_calls || []).some((t) => t.name === "generate_digest" && t.ok)) {
      updateStatusLine(cachedProfile, cachedSub);
    }
  } catch (e) {
    pending.remove();
    appendChatMessage("assistant", `出错了：${e.message}`);
  } finally {
    sending = false;
    if (sendBtn) sendBtn.disabled = false;
    input.focus();
  }
}

function bindUi() {
  for (const id of ["delivery_hour", "delivery_minute"]) {
    document.getElementById(id).addEventListener("input", updateDeliveryTime);
  }
  for (const btn of document.querySelectorAll("[data-open]")) {
    btn.addEventListener("click", () => openDialog(btn.dataset.open));
  }
  for (const btn of document.querySelectorAll("[data-close]")) {
    btn.addEventListener("click", () => closeDialog(btn.dataset.close));
  }
  document.getElementById("save-profile").addEventListener("click", saveProfile);
  document.getElementById("save-time").addEventListener("click", saveDeliveryTime);
  document.getElementById("save-sub").addEventListener("click", saveSubscription);
  document.getElementById("chat-send").addEventListener("click", () => sendChat());
  const chatInput = document.getElementById("chat-input");
  chatInput.addEventListener("input", () => autoGrow(chatInput));
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendChat();
    }
  });
}

async function init() {
  bindUi();
  setDeliveryTime("08:00");
  renderSuggestions();
  await loadSettings();
  await loadChatHistory();
}

init();
