// Control room screen.
// Every 2 seconds it asks the server for news (polling), then updates:
//   stats bar, live map, live bus list, incident list and the selected incident.
// An incident stays in "Active incidents" until the officer taps Resolved
// (or the checker dismisses it as a false alarm).

const POLL_MS = 2000;
const state = {
  incidents: {},          // id -> incident (active + recent)
  activeIds: [],
  buses: [],
  officers: [],
  selectedId: null,
  renderedKey: null,      // stops the video restarting on every poll
  seenNew: new Set(),     // new alerts we've already announced
  seenNoReply: new Set(), // "officer didn't reply" warnings already announced
  firstLoad: true,
};

const $ = id => document.getElementById(id);

// ---------- Start ----------
const map = makeMap("map");
const busDots = new DotLayer(map);
const officerDots = new DotLayer(map);
const incidentDots = new DotLayer(map);
let helpLine = null;   // line from the bus to the officer helping (or the nearest free officer)

$("start-btn").addEventListener("click", () => {
  enableSound();
  beep(1, 660);
  if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
  $("start-box").classList.add("d-none");
});

refresh();
setInterval(refresh, POLL_MS);

// ---------- Polling ----------
async function refresh() {
  try {
    const data = await getJSON("/api/control/feed/");
    state.incidents = {};
    for (const i of [...data.incidents, ...data.recent]) state.incidents[i.id] = i;
    state.activeIds = data.incidents.map(i => i.id);
    state.buses = data.buses;
    state.officers = data.officers;

    setLive(true);
    renderStats(data);
    renderBuses(data.buses, data.incidents);
    updateMap(data);

    // 1) Brand new alerts -> beep, banner, pop-up, open it
    const fresh = data.incidents.filter(i => isActionable(i) && !state.seenNew.has(i.id));
    fresh.forEach(i => state.seenNew.add(i.id));
    if (fresh.length && !state.firstLoad) {
      const i = fresh[0];
      announce(`New alert: ${i.bus_id} at ${i.time}${i.score != null ? ` (AI ${i.score}%)` : ""}`, 3, 880);
      if (!state.selectedId || !isActionable(state.incidents[state.selectedId])) select(i.id);
    }
    // 2) Officer hasn't replied in time -> warn again until someone acts
    const noReply = data.incidents.filter(i => i.no_response && !state.seenNoReply.has(i.id + ":" + i.officer_id));
    noReply.forEach(i => state.seenNoReply.add(i.id + ":" + i.officer_id));
    if (noReply.length && !state.firstLoad) {
      const i = noReply[0];
      announce(`No reply from ${i.officer_name} for ${i.bus_id}. Send it to another officer.`, 2, 520);
      select(i.id);
    }
    if (state.firstLoad && !state.selectedId && data.incidents.length) select(data.incidents[0].id);
    state.firstLoad = false;

    renderLists(data.incidents, data.recent, fresh.map(i => i.id));
    renderDetail();
  } catch (e) {
    setLive(false);
    console.error(e);
  }
}

function announce(text, beeps, freq) {
  beep(beeps, freq);
  showBanner(text);
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification("BusGuard", { body: text }); } catch (e) { /* some browsers block this */ }
  }
}

function setLive(ok) {
  const el = $("live-status");
  el.className = "badge " + (ok ? "text-bg-success" : "text-bg-warning");
  el.textContent = ok ? "Live" : "Connection lost, retrying...";
}

function showBanner(text) {
  const b = $("alert-banner");
  b.textContent = text;
  b.classList.remove("d-none");
  clearTimeout(showBanner.timer);
  showBanner.timer = setTimeout(() => b.classList.add("d-none"), 15000);
}

function isActionable(i) {
  return i && (i.status === "new" || i.status === "error");
}

// ---------- Stats bar ----------
function renderStats(data) {
  const s = data.stats;
  $("stat-open").textContent = s.open;
  $("stat-today").textContent = s.today;
  $("stat-resolved").textContent = s.resolved;
  $("stat-response").textContent = s.avg_response_seconds == null ? "-"
    : s.avg_response_seconds < 60 ? `${s.avg_response_seconds}s`
    : `${(s.avg_response_seconds / 60).toFixed(1)} min`;
  $("stat-officers").textContent = data.officers_on_duty;
  $("model-status").textContent = data.model_ready
    ? "AI: trained fight model"
    : "AI: placeholder rule (no trained model)";
}

// ---------- Live buses ----------
function renderBuses(buses, active) {
  if (!buses.length) return;
  $("bus-list").innerHTML = buses.map(b => {
    const ai = b.score == null ? "waiting for photos"
      : b.fight ? `<b class="text-danger">possible fight ${b.score}%</b>`
      : `<span class="text-success">normal ${b.score}%</span>`;
    const ago = b.seconds_ago == null ? "" : b.seconds_ago < 60 ? `${b.seconds_ago}s ago` : "a while ago";
    const photo = b.frame_url ? `<img src="${esc(b.frame_url)}" alt="Latest photo from ${esc(b.name)}">`
                              : `<div class="no-photo"></div>`;
    return `
      <div class="card bus-card mb-2 shadow-sm ${b.fight && b.online ? "fight" : ""}" data-bus="${esc(b.name)}">
        <div class="card-body p-2 d-flex gap-2 align-items-center">
          ${photo}
          <div class="small flex-grow-1">
            <div class="fw-semibold">${esc(b.name)}
              <span class="badge ${b.online ? "text-bg-success" : "text-bg-secondary"}">${b.online ? "Online" : "Offline"}</span></div>
            <div>AI: ${ai}</div>
            <div class="text-secondary">Updated ${ago}</div>
          </div>
        </div>
      </div>`;
  }).join("");
  document.querySelectorAll("[data-bus]").forEach(el => el.addEventListener("click", () => {
    const b = state.buses.find(x => x.name === el.dataset.bus);
    if (b && b.latitude != null) map.setView([b.latitude, b.longitude], 14);
  }));
}

// ---------- Map ----------
function busColour(b, incidents) {
  if (!b.online) return "grey";
  const open = incidents.filter(i => i.bus_id === b.name);
  if (b.fight || open.some(i => isActionable(i) || i.no_response)) return "red";
  if (open.some(i => i.status === "assigned" || i.status === "on_way")) return "orange";
  return "green";
}

function updateMap(data) {
  busDots.update(data.buses.map(b => ({
    key: b.name, lat: b.latitude, lon: b.longitude, radius: 10, permanent: true,
    colour: busColour(b, data.incidents),
    label: `${esc(b.name)}${b.score != null ? ` &middot; AI ${b.score}%` : ""}`,
    onClick: () => {
      const i = data.incidents.find(x => x.bus_id === b.name);
      if (i) select(i.id);
    },
  })));
  officerDots.update(data.officers.filter(o => o.online || o.busy_with).map(o => ({
    key: "o" + o.id, lat: o.latitude, lon: o.longitude, radius: 7,
    colour: o.busy_with ? "orange" : "blue",
    label: `Officer ${esc(o.name)}${o.busy_with ? " (on a job)" : ""}`,
  })));
  incidentDots.update(data.incidents.filter(i => i.latitude != null).map(i => ({
    key: "i" + i.id, lat: i.latitude, lon: i.longitude, radius: 5, colour: "red",
    label: `Incident #${i.id} reported here at ${esc(i.time)}`,
    onClick: () => select(i.id),
  })));
  drawHelpLine();
}

function nearestFreeOfficer(lat, lon) {
  const free = state.officers.filter(o => o.online && !o.busy_with && o.latitude != null);
  free.sort((a, b) => distanceKm(lat, lon, a.latitude, a.longitude) - distanceKm(lat, lon, b.latitude, b.longitude));
  return free[0];
}

// Line from where the bus is NOW to the officer helping (solid) or the nearest free officer (dashed)
function drawHelpLine() {
  if (helpLine) { helpLine.remove(); helpLine = null; }
  const i = state.incidents[state.selectedId];
  if (!i || i.bus_latitude == null || !state.activeIds.includes(i.id)) return;
  let officer = null, suggestion = false;
  if (i.officer_id && !i.no_response) {
    officer = state.officers.find(o => o.id === i.officer_id);
  } else if (isActionable(i) || i.no_response) {
    officer = nearestFreeOfficer(i.bus_latitude, i.bus_longitude);
    suggestion = true;
  }
  if (!officer || officer.latitude == null) return;
  const km = distanceKm(i.bus_latitude, i.bus_longitude, officer.latitude, officer.longitude).toFixed(1);
  helpLine = L.polyline([[i.bus_latitude, i.bus_longitude], [officer.latitude, officer.longitude]], {
    color: suggestion ? "#dc3545" : "#fd7e14", weight: 4, dashArray: suggestion ? "8 8" : null,
  }).addTo(map).bindTooltip(`${suggestion ? "Nearest free: " : ""}${esc(officer.name)} &middot; ${km} km`,
                            { permanent: true, direction: "center" });
}

// ---------- Incident list ----------
function card(i, isNew) {
  const extra = i.no_response ? `<span class="badge text-bg-danger">No reply!</span>`
    : i.status === "assigned" && i.waiting_seconds != null ? `<small class="text-secondary">waiting ${i.waiting_seconds}s</small>` : "";
  return `
    <div class="card incident-card st-${esc(i.status)} ${i.no_response ? "st-new" : ""} mb-2 shadow-sm ${i.id === state.selectedId ? "selected" : ""} ${isNew ? "flash" : ""}"
         data-id="${i.id}">
      <div class="card-body py-2 d-flex justify-content-between align-items-center">
        <div>
          <div class="fw-semibold">${esc(i.bus_id)}</div>
          <small class="text-secondary">${esc(i.time)}${i.officer_name ? " &middot; " + esc(i.officer_name) : ""}</small>
        </div>
        <div class="text-end">${badge(i)}<div>${extra}</div></div>
      </div>
    </div>`;
}

function renderLists(active, recent, newIds) {
  $("active-list").innerHTML = active.length
    ? active.map(i => card(i, newIds.includes(i.id))).join("")
    : `<div class="text-secondary">All clear. Nothing needs attention.</div>`;
  $("recent-list").innerHTML = recent.map(i =>
    `<div class="d-flex justify-content-between border-bottom py-1" data-id="${i.id}" role="button">
       <span>${esc(i.time)} ${esc(i.bus_id)}</span>${badge(i)}</div>`).join("") || `<div class="text-secondary">None in the last 24 hours.</div>`;
  document.querySelectorAll("[data-id]").forEach(el =>
    el.addEventListener("click", () => select(Number(el.dataset.id))));
}

function select(id) {
  state.selectedId = id;
  state.renderedKey = null;
  document.querySelectorAll(".incident-card").forEach(c =>
    c.classList.toggle("selected", Number(c.dataset.id) === id));
  renderDetail();
  const i = state.incidents[id];
  if (i && i.bus_latitude != null) map.setView([i.bus_latitude, i.bus_longitude], 13);
  drawHelpLine();
}

// ---------- Selected incident ----------
function timeline(i) {
  const t = i.timeline;
  const closed = i.status === "resolved" || i.status === "dismissed";
  const steps = [
    { label: "Reported", sub: "by the bus", done: true, time: t.reported },
    { label: "Sent", sub: i.officer_name ? "to " + i.officer_name : "to an officer", done: !!t.sent, time: t.sent },
    { label: "On the way", sub: "officer replied", done: !!t.on_way, time: t.on_way },
    i.status === "dismissed"
      ? { label: "Dismissed", sub: "false alarm", done: true, time: t.closed }
      : { label: "Resolved", sub: i.real_fight === true ? "real fight" : i.real_fight === false ? "not a fight" : "",
          done: i.status === "resolved", time: t.closed },
  ];
  // The step we're waiting for is highlighted
  const current = closed ? -1 : steps.findIndex(s => !s.done);
  return `
    <div class="stepper mb-3">
      ${steps.map((s, n) => `
        <div class="step ${s.done ? "done" : ""} ${n === current ? "current" : ""}">
          <div class="step-dot">${s.done ? "&#10003;" : n + 1}</div>
          <div class="step-label">${esc(s.label)}</div>
          <div class="step-sub">${esc(s.sub)}</div>
          <div class="step-time">${s.time ? esc(s.time) : n === current ? "waiting..." : ""}</div>
        </div>`).join("")}
    </div>
    ${t.response_minutes != null ? `<p class="small mb-1">Officer response time: <b>${t.response_minutes} min</b></p>` : ""}
    ${t.total_minutes != null ? `<p class="small">Total time to close: <b>${t.total_minutes} min</b></p>` : ""}`;
}

// The clip plays once (not on repeat), then stops with a Replay button.
// A clip only starts by itself the first time an incident is opened.
function setupClip(i, was) {
  const v = $("clip");
  if (!v) return;
  state.playedClips = state.playedClips || new Set();
  const status = $("clip-status");
  const show = () => {
    status.textContent = v.ended ? "Clip finished" : v.paused ? "Paused" : "Playing...";
  };
  ["play", "pause", "ended"].forEach(e => v.addEventListener(e, show));
  $("clip-replay").addEventListener("click", () => { v.currentTime = 0; v.play().catch(() => {}); });
  if (was) {
    v.addEventListener("loadedmetadata", () => {
      v.currentTime = was.time;
      if (!was.paused) v.play().catch(() => {});
    }, { once: true });
  } else if (!state.playedClips.has(i.id)) {
    state.playedClips.add(i.id);
    v.play().catch(() => {});
  }
  show();
}

function renderDetail() {
  const i = state.incidents[state.selectedId];
  if (!i) return;
  const key = [i.id, i.status, i.officer_id, i.no_response, i.video_url].join("|");
  if (key === state.renderedKey) {
    const w = $("waiting");                         // just tick the counter
    if (w && i.waiting_seconds != null) w.textContent = i.waiting_seconds;
    return;
  }
  state.renderedKey = key;

  let actions = "";
  const officerPicker = (title) => `
      <label class="form-label" for="note">Note for the officer (optional)</label>
      <textarea id="note" class="form-control mb-3" rows="2" placeholder="e.g. two males, back of the bus"></textarea>
      <div class="d-flex justify-content-between align-items-center mb-2">
        <h3 class="h6 m-0">${title}</h3>
        <button class="btn btn-sm btn-outline-secondary" id="reload-officers">Refresh</button>
      </div>
      <div id="officer-list" class="list-group mb-3"><div class="list-group-item text-secondary">Finding officers...</div></div>`;

  if (i.status === "analysing") {
    actions = `<div class="alert alert-secondary">Preparing the clip...</div>`;
  } else if (isActionable(i)) {
    actions = officerPicker("Send to an officer (nearest to the bus first)") +
      `<button class="btn btn-outline-secondary w-100" id="dismiss-btn">Dismiss - false alarm</button>`;
  } else if (i.status === "assigned" || i.status === "on_way") {
    const call = i.officer_phone ? `<a class="btn btn-sm btn-success" href="tel:${esc(i.officer_phone)}">Call ${esc(i.officer_phone)}</a>` : "";
    if (i.no_response) {
      actions = `
        <div class="alert alert-danger d-flex justify-content-between align-items-center">
          <span><b>No reply from ${esc(i.officer_name)}</b> for <span id="waiting">${i.waiting_seconds}</span>s.
            Call them or send to another officer.</span>${call}
        </div>` + officerPicker("Re-send to another officer");
    } else if (i.status === "assigned") {
      actions = `
        <div class="alert alert-warning d-flex justify-content-between align-items-center">
          <span>Sent to <b>${esc(i.officer_name)}</b>, waiting for reply... <span id="waiting">${i.waiting_seconds ?? 0}</span>s</span>${call}
        </div>`;
    } else {
      actions = `
        <div class="alert alert-primary d-flex justify-content-between align-items-center">
          <span><b>${esc(i.officer_name)}</b> is on the way. Stays open until they tap Resolved.</span>${call}
        </div>`;
    }
  } else {
    actions = `<div class="alert alert-light border">Closed: ${esc(i.status_display)}.
      ${i.officer_note ? `<br>Officer's report: ${esc(i.officer_note)}` : ""}</div>`;
  }

  // Remember where the clip was, so redrawing (e.g. officer taps "On my way") doesn't restart it
  const oldClip = $("clip");
  const clipWas = oldClip && oldClip.getAttribute("src") === i.video_url
    ? { time: oldClip.currentTime, paused: oldClip.paused } : null;

  $("detail").innerHTML = `
    <div class="card-body">
      <div class="d-flex justify-content-between align-items-center mb-2">
        <h2 class="h5 m-0">${esc(i.bus_id)} <small class="text-secondary">#${i.id}</small></h2>${badge(i)}
      </div>
      ${i.video_url ? `
        <div class="mb-3">
          <video id="clip" class="video-box" src="${esc(i.video_url)}" controls muted playsinline></video>
          <div class="d-flex justify-content-between align-items-center small mt-1">
            <span id="clip-status" class="text-secondary"></span>
            <button id="clip-replay" class="btn btn-sm btn-outline-secondary" type="button">&#8635; Replay clip</button>
          </div>
        </div>` : ""}
      <div class="row small mb-3">
        <div class="col-sm-3"><span class="text-secondary">Reported</span><br>${esc(i.date)} ${esc(i.time)}</div>
        <div class="col-sm-3"><span class="text-secondary">AI fight score</span><br>${i.score !== null ? i.score + "%" : "Manual alert"}</div>
        <div class="col-sm-3"><span class="text-secondary">Bus GPS</span><br>
          ${i.bus_latitude != null ? `${i.bus_latitude.toFixed(4)}, ${i.bus_longitude.toFixed(4)}${i.bus_live ? " (live)" : ""}` : "Unknown"}</div>
        <div class="col-sm-3"><span class="text-secondary">Map</span><br>
          ${i.directions_url ? `<a href="${esc(i.directions_url)}" target="_blank" rel="noopener">Show bus in Google Maps</a>` : "-"}</div>
      </div>
      ${i.ai_note ? `<p class="small text-secondary">${esc(i.ai_note)}</p>` : ""}
      ${i.checker_note ? `<p class="small">Control room note: ${esc(i.checker_note)}</p>` : ""}
      ${timeline(i)}
      ${actions}
    </div>`;

  setupClip(i, clipWas);

  if ($("officer-list")) {
    loadOfficers(i.id);
    $("reload-officers").onclick = () => loadOfficers(i.id);
  }
  if ($("dismiss-btn")) {
    $("dismiss-btn").onclick = () => act(`/api/incident/${i.id}/dismiss/`, { note: $("note").value });
  }
}

async function loadOfficers(incidentId) {
  try {
    const data = await getJSON(`/api/incident/${incidentId}/officers/`);
    if (state.selectedId !== incidentId || !$("officer-list")) return;
    $("officer-list").innerHTML = data.officers.map((o, n) => `
      <div class="list-group-item d-flex justify-content-between align-items-center ${o.online ? "" : "text-secondary"}">
        <div>
          <div class="fw-semibold">${esc(o.name)}
            ${o.online ? `<span class="badge text-bg-success">On duty</span>` : `<span class="badge text-bg-secondary">Offline</span>`}
            ${o.busy ? `<span class="badge text-bg-warning">Busy</span>` : ""}
            ${o.current ? `<span class="badge text-bg-danger">Not replying</span>` : ""}
            ${n === 0 && o.online && !o.busy && !o.current ? `<span class="badge text-bg-primary">Nearest</span>` : ""}</div>
          <small>${o.distance_km !== null ? o.distance_km + " km from the bus" : "Location unknown"}
            ${o.last_seen ? " &middot; seen " + esc(o.last_seen) : ""}</small>
        </div>
        <div class="d-flex gap-2">
          ${o.phone ? `<a class="btn btn-sm btn-outline-success" href="tel:${esc(o.phone)}">Call</a>` : ""}
          <button class="btn btn-sm btn-danger" data-officer="${o.id}" ${o.online && !o.current ? "" : "disabled"}>Send</button>
        </div>
      </div>`).join("") || `<div class="list-group-item text-secondary">No officers set up yet.</div>`;
    document.querySelectorAll("[data-officer]").forEach(btn =>
      btn.addEventListener("click", () => act(`/api/incident/${incidentId}/assign/`,
        { officer_id: btn.dataset.officer, note: $("note") ? $("note").value : "" })));
  } catch (e) {
    console.error(e);
  }
}

async function act(url, data) {
  try {
    await postForm(url, data);
    state.renderedKey = null;
    await refresh();
  } catch (e) {
    alert(e.message);
  }
}
