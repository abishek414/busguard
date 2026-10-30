// Officer phone screen: shares GPS location and checks for assigned incidents.

const POLL_MS = 3000;
const LOCATION_EVERY_MS = 10000;
const $ = id => document.getElementById(id);

// Small map: me (blue) and the incidents I'm sent to (red)
const map = makeMap("map", 12);
const meDot = new DotLayer(map);
const jobDots = new DotLayer(map);
let jobLine = null;
let jobs = [];

// Draws me + the bus (where it is NOW - it keeps moving) and the distance between us.
// recentre=true zooms the map to fit both (only when something new happens).
function updateOfficerMap(recentre = false) {
  const p = state.position;
  meDot.update(p ? [{ key: "me", lat: p.lat, lon: p.lon, colour: "blue", radius: 9, label: "You" }] : []);
  jobDots.update(jobs.map(i => ({ key: "i" + i.id, lat: i.bus_latitude, lon: i.bus_longitude, colour: "red",
                                  radius: 10, permanent: true,
                                  label: `${esc(i.bus_id)}${i.bus_live ? " (live)" : ""}` })));
  if (jobLine) { jobLine.remove(); jobLine = null; }
  const job = jobs.find(i => i.bus_latitude != null);
  if (p && job) {
    const km = distanceKm(p.lat, p.lon, job.bus_latitude, job.bus_longitude);
    jobLine = L.polyline([[p.lat, p.lon], [job.bus_latitude, job.bus_longitude]],
                         { color: "#dc3545", weight: 4, dashArray: "8 8" })
      .addTo(map).bindTooltip(`${km.toFixed(1)} km`, { permanent: true, direction: "center" });
    const link = document.getElementById("dir-" + job.id);
    // Directions go to where the bus is now, starting from the officer's position
    if (link) link.href = `${job.directions_url}&origin=${p.lat},${p.lon}&travelmode=driving`;
    const el = document.getElementById("dist-" + job.id);
    if (el) el.textContent = `Bus is ${km.toFixed(1)} km away in a straight line` +
      ` (about ${Math.max(1, Math.round(km / 40 * 60))} min by car)`;
    if (recentre) map.fitBounds(jobLine.getBounds(), { padding: [30, 30], maxZoom: 15 });
  } else if (p && recentre) {
    map.setView([p.lat, p.lon], 14);
  }
}

const state = {
  onDuty: false,
  position: null,       // {lat, lon}
  lastSent: 0,
  watchId: null,
  seen: new Set(),
  renderedKey: null,
  pollTimer: null,
  firstLoad: true,
};

$("duty-btn").addEventListener("click", () => (state.onDuty ? endDuty() : startDuty()));
async function startDuty() {
  enableSound();
  beep(1, 660);
  if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
  state.onDuty = true;
  updateDutyUI();
  if (navigator.geolocation) {
    state.watchId = navigator.geolocation.watchPosition(
      pos => {
        state.position = { lat: pos.coords.latitude, lon: pos.coords.longitude };
        showLocation();
        sendLocation(false);
      },
      err => {
        $("location-text").textContent = "GPS error: " + err.message +
          (location.protocol !== "https:" && !["localhost", "127.0.0.1"].includes(location.hostname)
            ? " (phones only share GPS on an https:// address)" : " (allow location for this site)");
      },
      { enableHighAccuracy: true, maximumAge: 10000 }
    );
  } else {
    $("location-text").textContent = "This browser can't share GPS.";
  }
  await sendLocation(true);
  poll();
  state.pollTimer = setInterval(poll, POLL_MS);
  // Keep the location fresh even if the phone isn't moving
  state.heartbeat = setInterval(() => sendLocation(true), LOCATION_EVERY_MS);
}

async function endDuty() {
  state.onDuty = false;
  if (state.watchId !== null) navigator.geolocation.clearWatch(state.watchId);
  state.watchId = null;
  clearInterval(state.pollTimer);
  clearInterval(state.heartbeat);
  await postForm("/api/officer/location/", { on_duty: "false" }).catch(console.error);
  updateDutyUI();
  $("jobs").innerHTML = `<div class="text-secondary">Off duty.</div>`;
}

function updateDutyUI() {
  $("duty-badge").className = "badge " + (state.onDuty ? "text-bg-success" : "text-bg-secondary");
  $("duty-badge").textContent = state.onDuty ? "On duty" : "Off duty";
  $("duty-btn").textContent = state.onDuty ? "End duty" : "Start duty";
  $("duty-btn").className = "btn w-100 btn-lg " + (state.onDuty ? "btn-outline-danger" : "btn-primary");
}

function showLocation() {
  const p = state.position;
  $("location-text").textContent = p
    ? `Sharing location: ${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`
    : "Waiting for GPS...";
  updateOfficerMap(!showLocation.done);
  showLocation.done = true;
}

async function sendLocation(force) {
  if (!state.onDuty) return;
  if (!force && Date.now() - state.lastSent < LOCATION_EVERY_MS) return;
  const data = { on_duty: "true" };
  if (state.position) { data.latitude = state.position.lat; data.longitude = state.position.lon; }
  try {
    await postForm("/api/officer/location/", data);
    state.lastSent = Date.now();
  } catch (e) { console.error(e); }
}

async function poll() {
  try {
    const data = await getJSON("/api/officer/feed/");
    const fresh = data.incidents.filter(i => !state.seen.has(i.id));
    fresh.forEach(i => state.seen.add(i.id));
    if (fresh.length && !state.firstLoad) {
      beep(4, 988);
      if (navigator.vibrate) navigator.vibrate([400, 200, 400, 200, 400]);
      const b = $("alert-banner");
      b.textContent = `New incident: ${fresh[0].bus_id}. Please respond.`;
      b.classList.remove("d-none");
      setTimeout(() => b.classList.add("d-none"), 15000);
      if ("Notification" in window && Notification.permission === "granted") {
        try { new Notification("BusGuard: new incident", { body: `${fresh[0].bus_id} - please respond` }); }
        catch (e) { /* some phones only allow this from an installed app */ }
      }
    }
    state.firstLoad = false;
    const changed = JSON.stringify(jobs.map(i => i.id)) !== JSON.stringify(data.incidents.map(i => i.id));
    jobs = data.incidents;
    render(data.incidents);
    updateOfficerMap(changed);   // every poll, because the bus keeps moving
  } catch (e) { console.error(e); }
}

function render(incidents) {
  const key = incidents.map(i => i.id + i.status).join(",");
  if (key === state.renderedKey) return;
  state.renderedKey = key;

  if (!incidents.length) {
    $("jobs").innerHTML = `<div class="text-secondary">No incidents assigned. Stay safe.</div>`;
    return;
  }
  $("jobs").innerHTML = incidents.map(i => `
    <div class="card shadow-sm mb-3 border-${i.status === "assigned" ? "danger" : "primary"}">
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-center mb-2">
          <h3 class="h6 m-0">${esc(i.bus_id)}</h3>${badge(i)}
        </div>
        <video class="video-box mb-2" src="${esc(i.video_url)}" controls muted playsinline></video>
        <p class="small mb-1">Reported ${esc(i.time)}${i.checker_note ? `<br>Control room: <b>${esc(i.checker_note)}</b>` : ""}</p>
        <p class="small fw-semibold text-danger mb-2" id="dist-${i.id}"></p>
        ${i.directions_url ? `<a class="btn btn-outline-primary w-100 mb-2" id="dir-${i.id}" href="${esc(i.directions_url)}" target="_blank" rel="noopener">Navigate to the bus (Google Maps)</a>
          <p class="small text-secondary mb-2">Opens turn-by-turn directions from where you are to where the bus is right now.</p>` : ""}
        ${i.status === "assigned" ? `<button class="btn btn-primary w-100 mb-2" data-onway="${i.id}">On my way</button>` : ""}
        <input class="form-control mb-2" id="note-${i.id}" placeholder="What happened? (optional)">
        <div class="small fw-semibold mb-1">Resolved - was it a real fight?</div>
        <div class="d-flex gap-2">
          <button class="btn btn-success flex-fill" data-resolve="${i.id}" data-real="yes">Resolved: real fight</button>
          <button class="btn btn-outline-success flex-fill" data-resolve="${i.id}" data-real="no">Resolved: not a fight</button>
        </div>
        <p class="small text-secondary mt-2 mb-0">This stays open in the control room until you tap Resolved.
          Your answer helps the AI learn.</p>
      </div>
    </div>`).join("");

  document.querySelectorAll("[data-onway]").forEach(b =>
    b.addEventListener("click", () => act(`/api/incident/${b.dataset.onway}/on-way/`, {})));
  document.querySelectorAll("[data-resolve]").forEach(b =>
    b.addEventListener("click", () => act(`/api/incident/${b.dataset.resolve}/resolve/`,
      { note: $("note-" + b.dataset.resolve).value, real_fight: b.dataset.real })));
}

async function act(url, data) {
  try {
    await postForm(url, data);
    state.renderedKey = null;
    await poll();
  } catch (e) { alert(e.message); }
}
