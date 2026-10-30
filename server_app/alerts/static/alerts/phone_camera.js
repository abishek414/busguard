// Phone camera page: the phone plays the part of the bus camera.
//
//  * Every second: take a small photo, send it (and the phone's GPS) to /api/frame/.
//    Azure's AI scores it and replies. When the reply says record=true, send a clip.
//  * The clip: the phone is always recording short pieces in the background, so the clip
//    starts a few seconds BEFORE the alert (like the camera program's 3 seconds before).
//    Two recorders take turns, each starting a new piece every 6 seconds.
//  * "Send alert now" sends a clip straight away (like a driver's panic button).

const PHOTO_EVERY_MS = 1000;
const PHOTO_WIDTH = 320;          // small photo, about 10 KB
const PIECE_MS = 6000;            // each background recorder starts a new piece this often
const VIDEO_BITRATE = 1_000_000;  // keeps a 20-second clip around 2-3 MB

const video = document.getElementById("video");
const aiBox = document.getElementById("ai");
const recBox = document.getElementById("rec");
const recLeft = document.getElementById("rec-left");
const msg = document.getElementById("msg");
const startBtn = document.getElementById("start");
const alertBtn = document.getElementById("alert");
const flipBtn = document.getElementById("flip");

let stream = null;
let running = false;
let facing = "environment";       // back camera first
let position = null;              // latest GPS {latitude, longitude}
let gpsWatch = null;
let photoTimer = null;
let wakeLock = null;
let sendingPhoto = false;
let recording = false;            // a clip is being recorded or uploaded
let recorders = [];
let pieceTimer = null;
const canvas = document.createElement("canvas");

function dot(id, state) {
  document.getElementById(id).className = "pc-dot " + (state === true ? "on" : state === false ? "off" : "");
}

function say(text) { msg.textContent = text; }

// ---------- camera ----------

async function openCamera() {
  if (stream) stream.getTracks().forEach(t => t.stop());
  stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: { facingMode: facing, width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 15 } },
  });
  video.srcObject = stream;
  await video.play().catch(() => {});
  dot("dot-cam", true);
}

// ---------- GPS ----------

function startGps() {
  if (!navigator.geolocation) { dot("dot-gps", false); return; }
  gpsWatch = navigator.geolocation.watchPosition(
    pos => {
      position = { latitude: pos.coords.latitude, longitude: pos.coords.longitude };
      dot("dot-gps", true);
    },
    err => { dot("dot-gps", false); say("GPS: " + err.message); },
    { enableHighAccuracy: true, maximumAge: 5000 });
}

// ---------- photos to Azure ----------

function takePhoto() {
  return new Promise(resolve => {
    const w = video.videoWidth, h = video.videoHeight;
    if (!w || !h) return resolve(null);
    canvas.width = PHOTO_WIDTH;
    canvas.height = Math.round(PHOTO_WIDTH * h / w);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(resolve, "image/jpeg", 0.6);
  });
}

function addGps(form) {
  if (position) {
    form.append("latitude", position.latitude);
    form.append("longitude", position.longitude);
  }
}

async function sendPhoto() {
  if (sendingPhoto || !running) return;
  sendingPhoto = true;
  try {
    const photo = await takePhoto();
    if (!photo) return;
    const form = new FormData();
    form.append("image", photo, "frame.jpg");
    addGps(form);
    const r = await fetch("/api/frame/", { method: "POST", headers: { "X-Bus-Key": BUS.key }, body: form });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || "Server error " + r.status);
    dot("dot-net", true);
    showScore(data);
    if (data.record && !recording) sendClip(false);
  } catch (e) {
    dot("dot-net", false);
    aiBox.className = "pc-ai";
    aiBox.textContent = "Can't reach Azure";
    say(e.message);
  } finally {
    sendingPhoto = false;
  }
}

function showScore(data) {
  if (data.fight) {
    aiBox.className = "pc-ai fight";
    aiBox.textContent = `POSSIBLE FIGHT ${data.score}%` + (data.hits ? ` (${data.hits} of last ${data.window})` : "");
  } else {
    aiBox.className = "pc-ai normal";
    aiBox.textContent = `Azure AI: normal ${data.score}%`;
  }
}

// ---------- background recording (for the "before" part of the clip) ----------

function recorderType() {
  const options = ["video/mp4", "video/webm;codecs=vp8", "video/webm"];
  return options.find(t => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || "";
}

function newPiece() {
  const type = recorderType();
  const rec = new MediaRecorder(stream, { ...(type ? { mimeType: type } : {}), videoBitsPerSecond: VIDEO_BITRATE });
  const piece = { rec, chunks: [], startedAt: Date.now() };
  rec.ondataavailable = e => { if (e.data && e.data.size) piece.chunks.push(e.data); };
  rec.start(1000);
  return piece;
}

function stopPiece(piece) {
  return new Promise(resolve => {
    if (piece.rec.state === "inactive") return resolve(piece);
    piece.rec.onstop = () => resolve(piece);
    piece.rec.stop();
  });
}

function startBackgroundRecording() {
  if (!window.MediaRecorder) { say("This browser can't record video; photos still work."); return; }
  recorders = [newPiece()];
  // The second recorder starts half a piece later, so one of them always has
  // between 3 and 6 seconds of "before" footage.
  setTimeout(() => {
    if (running && !recording && recorders.length === 1) {
      try { recorders.push(newPiece()); } catch (e) { /* one recorder is fine too */ }
    }
  }, PIECE_MS / 2);
  pieceTimer = setInterval(() => {
    if (recording) return;
    recorders = recorders.map(p => {
      if (Date.now() - p.startedAt < PIECE_MS) return p;
      stopPiece(p);            // throw this piece away and start a fresh one
      return newPiece();
    });
  }, 250);
}

function stopBackgroundRecording() {
  clearInterval(pieceTimer);
  recorders.forEach(stopPiece);
  recorders = [];
}

// ---------- the clip ----------

async function sendClip(manual) {
  if (recording || !recorders.length) return;
  recording = true;
  alertBtn.disabled = true;

  // Keep the recorder that started earliest: it already holds the seconds before the alert
  recorders.sort((a, b) => a.startedAt - b.startedAt);
  const piece = recorders[0];
  recorders.slice(1).forEach(stopPiece);
  recorders = [piece];

  const before = (Date.now() - piece.startedAt) / 1000;
  const endAt = piece.startedAt + BUS.clipSeconds * 1000;
  recBox.classList.remove("d-none");
  say(manual ? "Manual alert: recording..." : `AI asked for a clip (includes ${before.toFixed(0)} s before).`);

  await new Promise(resolve => {
    const t = setInterval(() => {
      const left = Math.max(0, Math.ceil((endAt - Date.now()) / 1000));
      recLeft.textContent = left + " s";
      if (left <= 0 || !running) { clearInterval(t); resolve(); }
    }, 250);
  });
  await stopPiece(piece);
  recorders = [];
  recBox.classList.add("d-none");

  const type = piece.rec.mimeType || "video/webm";
  const blob = new Blob(piece.chunks, { type });
  const form = new FormData();
  form.append("video", blob, type.includes("mp4") ? "clip.mp4" : "clip.webm");
  if (manual) form.append("manual", "1");
  addGps(form);
  say(`Sending clip (${(blob.size / 1e6).toFixed(1)} MB)...`);
  try {
    const r = await fetch("/api/upload/", { method: "POST", headers: { "X-Bus-Key": BUS.key }, body: form });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || "Server error " + r.status);
    say(`Clip sent to the control room (incident #${data.id}).`);
  } catch (e) {
    say("Clip upload failed: " + e.message);
  }

  recording = false;
  if (running) {
    alertBtn.disabled = false;
    startBackgroundRecording();
  }
}

// ---------- keep the screen on ----------

async function keepScreenOn() {
  try {
    if ("wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen");
  } catch (e) { /* not supported: just keep the screen on by hand */ }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && running) keepScreenOn();
});

// ---------- buttons ----------

async function start() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    say("The camera only works on an https:// address (use the Azure address), or on this computer at 127.0.0.1.");
    return;
  }
  startBtn.disabled = true;
  try {
    await openCamera();
  } catch (e) {
    dot("dot-cam", false);
    say("Camera: " + e.message + ". Allow camera access for this site and try again.");
    startBtn.disabled = false;
    return;
  }
  running = true;
  aiBox.textContent = "Connecting to Azure...";
  if (BUS.useGps) startGps(); else dot("dot-gps", null);
  keepScreenOn();
  startBackgroundRecording();
  photoTimer = setInterval(sendPhoto, PHOTO_EVERY_MS);
  sendPhoto();
  startBtn.textContent = "Stop";
  startBtn.className = "btn btn-secondary btn-lg";
  startBtn.disabled = false;
  alertBtn.disabled = false;
  flipBtn.disabled = false;
  say("Running. Photos are going to Azure.");
}

function stop() {
  running = false;
  clearInterval(photoTimer);
  stopBackgroundRecording();
  if (gpsWatch !== null) navigator.geolocation.clearWatch(gpsWatch);
  gpsWatch = null;
  if (stream) stream.getTracks().forEach(t => t.stop());
  stream = null;
  if (wakeLock) wakeLock.release().catch(() => {});
  wakeLock = null;
  ["dot-cam", "dot-gps", "dot-net"].forEach(id => dot(id, null));
  aiBox.className = "pc-ai";
  aiBox.textContent = "Stopped";
  startBtn.textContent = "Start";
  startBtn.className = "btn btn-success btn-lg";
  alertBtn.disabled = true;
  flipBtn.disabled = true;
  say("Stopped.");
}

startBtn.addEventListener("click", () => (running ? stop() : start()));
alertBtn.addEventListener("click", () => sendClip(true));
flipBtn.addEventListener("click", async () => {
  if (recording) return say("Wait until the clip is sent.");
  facing = facing === "environment" ? "user" : "environment";
  stopBackgroundRecording();
  try { await openCamera(); } catch (e) { say("Camera: " + e.message); }
  startBackgroundRecording();
});
