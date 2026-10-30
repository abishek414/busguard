// Small helpers shared by the control room and officer screens.

// Make text safe to put inside HTML
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function csrfToken() {
  const row = document.cookie.split("; ").find(c => c.startsWith("csrftoken="));
  return row ? decodeURIComponent(row.split("=")[1]) : "";
}

async function getJSON(url) {
  const r = await fetch(url, { headers: { "Accept": "application/json" } });
  if (r.redirected || r.status === 403) { window.location.reload(); throw new Error("logged out"); }
  if (!r.ok) throw new Error("Server error " + r.status);
  return r.json();
}

// POST form data to Django (with the CSRF token)
async function postForm(url, data = {}) {
  const body = new FormData();
  for (const [k, v] of Object.entries(data)) body.append(k, v);
  const r = await fetch(url, { method: "POST", headers: { "X-CSRFToken": csrfToken() }, body });
  const json = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(json.error || "Server error " + r.status);
  return json;
}

// --- Sound alerts ---
// Browsers only allow sound after the user clicks something, so each page
// has a "Start" button that calls enableSound().
let audioCtx = null;

function enableSound() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === "suspended") audioCtx.resume();
}

function beep(times = 3, freq = 880) {
  if (!audioCtx) return;
  for (let i = 0; i < times; i++) {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.frequency.value = freq;
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    const t = audioCtx.currentTime + i * 0.4;
    gain.gain.setValueAtTime(0.3, t);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.3);
    osc.start(t);
    osc.stop(t + 0.3);
  }
}

function badge(incident) {
  return `<span class="badge st-${esc(incident.status)}">${esc(incident.status_display)}</span>`;
}
