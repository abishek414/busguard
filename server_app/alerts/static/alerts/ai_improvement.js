// AI improvement page (system manager): examples checked by people, the Retrain button,
// live progress while a retrain runs, and the training history.

const REFRESH_MS = 3000;
let lastKey = null;
let wasRunning = false;

function $(id) { return document.getElementById(id); }

async function refresh() {
  try {
    const l = await getJSON("/api/ai/status/");
    const key = JSON.stringify(l);
    if (key !== lastKey) { lastKey = key; render(l); }
  } catch (e) { console.error(e); }
}

function render(l) {
  const run = l.running;
  const latest = l.history[0];

  // Tell the manager when a retrain has just finished
  if (wasRunning && !run && latest) {
    beep(1, 660);
    alertBox(latest.status === "activated"
      ? `New model v${latest.version} activated (${latest.old_accuracy}% → ${latest.new_accuracy}%).`
      : `Retrain finished: ${latest.status_display}.`, latest.status === "activated" ? "success" : "warning");
  }
  wasRunning = !!run;

  $("ai-model").textContent = l.model_ready ? `Version ${l.model_version}` : "No trained model";
  $("ai-model-note").textContent = l.model_accuracy != null
    ? `${l.model_accuracy}% correct on its test photos` : "";
  $("ai-fight").textContent = l.fight;
  $("ai-normal").textContent = l.normal;
  $("ai-new").textContent = l.new_since_last;

  const examples = l.fight + l.normal;
  let action;
  if (run) {
    action = `<div class="alert alert-info py-2 mb-0">
        <span class="spinner-border spinner-border-sm me-1"></span>${esc(run.progress || "Training...")}
        <div class="small text-secondary mt-1">This takes a few minutes. The AI keeps working while it trains.</div>
      </div>`;
  } else {
    const ready = l.model_ready && l.base_ready && examples > 0;
    const why = !l.model_ready ? "There is no trained model on this server yet."
      : !l.base_ready ? "Retraining isn't set up on this server yet (original training photos missing)."
      : examples === 0 ? "No examples yet: incidents count once the control room dismisses them or an officer resolves them."
      : "";
    action = `<button id="retrain-btn" class="btn btn-primary w-100" ${ready ? "" : "disabled"}>
        Retrain AI with ${examples} example${examples === 1 ? "" : "s"}</button>
      ${why ? `<div class="small text-secondary mt-2">${esc(why)}</div>` : ""}`;
  }
  $("ai-action").innerHTML = action;

  const btn = $("retrain-btn");
  if (btn) btn.onclick = async () => {
    if (!confirm("Retrain the AI now? This takes a few minutes.")) return;
    enableSound();
    btn.disabled = true;
    try {
      await postForm("/api/ai/retrain/");
      wasRunning = true;
      await refresh();
    } catch (e) { alert(e.message); btn.disabled = false; }
  };

  const badge = s => ({ activated: "success", kept_old: "secondary", failed: "danger", running: "info" }[s] || "light");
  $("ai-history").innerHTML = l.history.length ? l.history.map(r => `
    <tr>
      <td class="text-nowrap">${esc(r.started)}</td>
      <td><span class="badge text-bg-${badge(r.status)}">${esc(r.status_display)}</span>
        ${r.version && r.status === "activated" ? ` v${r.version}` : ""}
        ${r.status === "failed" && r.error ? `<div class="text-danger">${esc(r.error)}</div>` : ""}</td>
      <td>${r.new_fight} fights, ${r.new_normal} false alarms</td>
      <td>${r.old_accuracy != null ? r.old_accuracy + "%" : "-"} &rarr; ${r.new_accuracy != null ? r.new_accuracy + "%" : "-"}</td>
    </tr>`).join("")
    : `<tr><td colspan="4" class="text-secondary">No retraining yet.</td></tr>`;
}

function alertBox(text, kind) {
  const div = document.createElement("div");
  div.className = `alert alert-${kind} alert-dismissible`;
  div.innerHTML = `${esc(text)}<button type="button" class="btn-close" onclick="this.parentElement.remove()"></button>`;
  document.querySelector("main").prepend(div);
}

refresh();
setInterval(refresh, REFRESH_MS);
