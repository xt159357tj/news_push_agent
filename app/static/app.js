let userTimezone = "Asia/Shanghai";
let currentDigest = null;

function updateTimezone(value) {
  try {
    new Intl.DateTimeFormat("zh-CN", { timeZone: value }).format();
    userTimezone = value;
  } catch {
    userTimezone = "Asia/Shanghai";
  }
  document.getElementById("delivery_timezone").textContent = userTimezone;
  document.getElementById("history_timezone").textContent = userTimezone;
  refreshDigestTimestamp();
}

function refreshDigestTimestamp() {
  if (currentDigest) {
    document.getElementById("digest-generated-at").textContent = `生成于 ${formatDigestTime(currentDigest.generated_at)}（${userTimezone}）`;
  }
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

async function init() {
  for (const id of ["delivery_hour", "delivery_minute"]) {
    document.getElementById(id).addEventListener("input", updateDeliveryTime);
  }
  setDeliveryTime("08:00");
  try {
    const profile = await api("/api/profile");
    document.getElementById("name").value = profile.name || "";
    document.getElementById("email").value = profile.email || "";
    document.getElementById("timezone").value = profile.timezone || "Asia/Shanghai";
    updateTimezone(profile.timezone || "Asia/Shanghai");
  } catch (e) { console.error(e); }

  try {
    const subs = await api("/api/subscriptions");
    if (subs.length) {
      const s = subs[subs.length - 1];
      document.getElementById("topics").value = (s.topics || []).join(",");
      document.getElementById("keywords").value = (s.keywords || []).join(",");
      document.getElementById("language").value = s.language || "zh";
      document.getElementById("max_items").value = s.max_items || 10;
      setDeliveryTime(s.delivery_time || "08:00");
      window.currentSubId = s.id;
    }
  } catch (e) { console.error(e); }

  loadDigests();
}

async function saveProfile() {
  const profile = await api("/api/profile", {
    method: "PUT",
    body: JSON.stringify({
      name: document.getElementById("name").value,
      email: document.getElementById("email").value,
      timezone: document.getElementById("timezone").value,
    }),
  });
  updateTimezone(profile.timezone || "Asia/Shanghai");
  await loadDigests();
  document.getElementById("sub-message").textContent = "身份已保存";
}

async function saveSubscription() {
  const payload = {
    topics: split(document.getElementById("topics").value),
    keywords: split(document.getElementById("keywords").value),
    language: document.getElementById("language").value,
    max_items: Number(document.getElementById("max_items").value) || 10,
    delivery_time: document.getElementById("delivery_time").value || "08:00",
    notify_email: true,
  };
  let out;
  if (window.currentSubId) {
    out = await api(`/api/subscriptions/${window.currentSubId}`, { method: "PUT", body: JSON.stringify(payload) });
  } else {
    out = await api("/api/subscriptions", { method: "POST", body: JSON.stringify(payload) });
    window.currentSubId = out.id;
  }
  document.getElementById("sub-message").textContent = "订阅已保存";
}

function split(v) {
  return (v || "").split(/[,，]/).map((s) => s.trim()).filter(Boolean);
}

async function generate() {
  if (!window.currentSubId) {
    alert("请先保存订阅偏好");
    return;
  }
  const statusEl = document.getElementById("job-status");
  statusEl.textContent = "任务已提交，正在生成（可能耗时数十秒）…";
  const job = await api("/api/generate", {
    method: "POST",
    body: JSON.stringify({ subscription_id: window.currentSubId }),
  });
  // Simple polling
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    const cur = await api(`/api/jobs/${job.id}`);
    statusEl.textContent = `状态：${cur.status}` + (cur.error ? ` — ${cur.error}` : "");
    if (cur.status === "succeeded" || cur.status === "failed") {
      break;
    }
  }
  loadDigests();
}

async function loadDigests() {
  const list = await api("/api/digests");
  const el = document.getElementById("digest-list");
  el.innerHTML = "";
  for (const d of list) {
    const a = document.createElement("a");
    a.href = "#";
    a.textContent = `${formatDigestTime(d.generated_at)} — ${d.title}`;
    a.onclick = (e) => { e.preventDefault(); showDigest(d.id); };
    el.appendChild(a);
    el.appendChild(document.createElement("br"));
  }
}

async function showDigest(id) {
  const d = await api(`/api/digests/${id}`);
  currentDigest = d;
  document.getElementById("digest-detail").hidden = false;
  document.getElementById("digest-title").textContent = d.title;
  refreshDigestTimestamp();
  document.getElementById("digest-overview").textContent = d.overview || "";
  const itemsEl = document.getElementById("digest-items");
  itemsEl.innerHTML = "";
  for (const item of d.items) {
    const div = document.createElement("div");
    div.className = "item";
    const h = document.createElement("h4");
    const link = document.createElement("a");
    link.href = item.url;
    link.target = "_blank";
    link.textContent = item.title;
    h.appendChild(link);
    div.appendChild(h);
    if (item.summary) {
      const p = document.createElement("p");
      p.textContent = item.summary;
      div.appendChild(p);
    }
    if (item.reason) {
      const r = document.createElement("p");
      r.className = "reason";
      r.textContent = `入选理由：${item.reason}`;
      div.appendChild(r);
    }
    itemsEl.appendChild(div);
  }
}

init();
