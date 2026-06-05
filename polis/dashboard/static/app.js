"use strict";
const $ = (id) => document.getElementById(id);
const state = { selected: null, detailRunId: null, lastFeedTs: 0, openReport: null, reportsCache: [], pendingIds: new Set() };

async function api(method, path, body) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const money = (n) => "$" + (Number(n) || 0).toFixed(n >= 100 || n === 0 ? 0 : 4);
function rel(ts) {
  if (!ts) return "";
  const d = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (d < 60) return d + "s ago";
  if (d < 3600) return Math.floor(d / 60) + "m ago";
  return Math.floor(d / 3600) + "h ago";
}
function short(s, n = 9) { return s ? String(s).slice(0, n) : ""; }

function outcomeBadge(r) {
  if (r.in_flight) return `<span class="badge flight">● running</span>`;
  if (r.outcome === "DONE") return `<span class="badge done">✓ merged</span>`;
  if (r.outcome === "ESCALATE") return `<span class="badge escalate">⚠ escalated</span>`;
  return `<span class="badge">—</span>`;
}

function renderStats(ov) {
  $("base").textContent = ov.treasury ? "" : "";
  const t = ov.treasury || { balance: 0, appropriated: 0, spent: 0 };
  const pct = t.appropriated ? Math.min(100, 100 * t.spent / t.appropriated) : 0;
  $("stats").innerHTML = `
    <span class="stat">💰 <b>${money(t.balance)}</b> <span class="muted">/ ${money(t.appropriated)}</span></span>
    <span class="burn" title="spent ${money(t.spent)} of ${money(t.appropriated)}"><span style="width:${pct}%"></span></span>
    <span class="stat" title="runs">▶ <b>${ov.total_runs}</b></span>
    <span class="stat" style="color:var(--live)">● <b>${ov.in_flight}</b></span>
    <span class="stat" style="color:var(--ok)">✓ <b>${ov.done}</b></span>
    <span class="stat" style="color:var(--warn)">⚠ <b>${ov.escalate}</b></span>
    <span class="stat muted">${ov.pending_feedback ?? 0} pending</span>`;
}

function renderRuns(runs) {
  $("runcount").textContent = `(${runs.length})`;
  if (!runs.length) { $("runs").innerHTML = `<p class="muted">No runs yet.</p>`; return; }
  $("runs").innerHTML = runs.map((r) => `
    <div class="run ${r.run_id === state.selected ? "sel" : ""}" data-id="${r.run_id}">
      ${outcomeBadge(r)}
      <div class="ttl">${esc(r.feedback_text || r.prd_title || r.run_id)}</div>
      <div class="meta muted">${esc(r.last_stage || "")} · ${money(r.spend)} · ${rel(r.last_ts)}</div>
    </div>`).join("");
  document.querySelectorAll(".run").forEach((el) =>
    el.onclick = () => selectRun(el.dataset.id));
}

function payloadSummary(kind, p) {
  p = p || {};
  switch (kind) {
    case "intake": return esc(p.text || "");
    case "prd": return `PRD “${esc(p.title)}” (rev ${esc(p.revision ?? 0)})`;
    case "proposal": return `#${esc(p.index)} “${esc(p.title)}”`;
    case "vote": return `voter ${esc(p.voter)} → #${esc(p.choice)}`;
    case "elected": return `winner #${esc(p.winner)} · tally ${esc(JSON.stringify(p.tally))}`;
    case "ruling": return `${p.constitutional ? "constitutional ✓" : "unconstitutional ✗"}${p.reasons ? " · " + esc((p.reasons || []).join("; ")) : ""}`;
    case "amend": return `amended → rev ${esc(p.revision)}`;
    case "hire": return `dev: ${esc(p.discipline || "generalist")}`;
    case "diff": return `attempt ${esc(p.attempt)} · ${(p.files || []).map(esc).join(", ")}`;
    case "test_result": return (p.passed ? "tests passed ✓" : "tests failed ✗")
      + (!p.passed && p.details ? " · " + esc(p.details.trim().slice(-200)) : "");
    case "verdict": return `${p.approved ? "approved ✓" : "rejected ✗"}${(p.reasons || []).length ? " · " + esc(p.reasons.join("; ")) : ""}`;
    case "revise": return `→ attempt ${esc(p.attempt)}: ${esc(p.feedback || "")}`;
    case "merge": return `merged ${esc(short(p.commit, 10))}`;
    case "deploy": return `deploy ${p.ok ? "ok" : "FAILED"}`;
    case "escalate": return `⚠ ${esc(p.reason || "")}`;
    case "spawn": case "release": return `${esc(p.role || "")}`;
    case "done": return `done ${esc(short(p.commit, 10))}`;
    default: return esc(JSON.stringify(p)).slice(0, 200);
  }
}

function renderDetail(d) {
  $("detailHeader").innerHTML = `${outcomeBadge(d)}
    &nbsp;<strong>${esc(d.feedback_text || d.prd_title || d.run_id)}</strong>`;
  const strip = `<div class="strip">` + d.stage_strip.map((s) =>
    `<span class="s ${s.visited ? "on" : ""}">${s.stage}</span>`).join("") + `</div>`;
  const facts = `<p class="muted">${short(d.run_id, 14)} · ${money(d.ledger_spend ?? d.spend)} ·
    attempts ${d.attempts} ${d.discipline ? "· dev: " + esc(d.discipline) : ""}
    ${d.merge_commit ? "· " + short(d.merge_commit, 10) : ""}
    ${d.reason ? "· " + esc(d.reason) : ""}</p>`;
  const rows = d.timeline.map((e) => `
    <div class="ev b-${e.branch}">
      <span class="dot">●</span>
      <span class="kind">${esc(e.kind)}</span>
      <span class="pl">${payloadSummary(e.kind, e.payload)}
        <span class="who muted">${esc(e.actor)}</span></span>
      <span class="cost">${e.cost ? money(e.cost) : ""}</span>
    </div>`).join("");
  let html = strip + facts + `<div class="timeline">${rows}</div>`;
  // ESCALATE hands control to the sovereign: let them add guidance and re-run.
  if (d.outcome === "ESCALATE") {
    html += `<div class="retry">
      <div class="rlabel muted">⚠ Escalated — your call. Add guidance and re-run:</div>
      <textarea id="retryGuidance" rows="3"
        placeholder="e.g. make the folder dialog server-side; keep tests hermetic and fast"></textarea>
      <button id="retryBtn" data-run="${esc(d.run_id)}">↻ Re-run with this guidance</button>
    </div>`;
  }
  // Preserve the timeline's scroll across the 2.5s in-flight re-render (only for the SAME
  // run; a fresh selection starts at the top). Stick to the bottom if you were already there.
  const same = d.run_id === state.detailRunId;
  state.detailRunId = d.run_id;
  const oldTl = same ? $("detail").querySelector(".timeline") : null;
  const prevTop = oldTl ? oldTl.scrollTop : 0;
  const wasBottom = oldTl && (oldTl.scrollTop + oldTl.clientHeight >= oldTl.scrollHeight - 30);
  $("detail").innerHTML = html;
  const newTl = $("detail").querySelector(".timeline");
  if (newTl && same) newTl.scrollTop = wasBottom ? newTl.scrollHeight : prevTop;
  const rb = $("retryBtn");
  if (rb) rb.onclick = () => retryRun(rb.dataset.run, $("retryGuidance").value);
}

async function retryRun(runId, guidance) {
  if (!guidance.trim()) return toast("Add some guidance first.");
  const rb = $("retryBtn");
  try {
    const r = await api("POST", `/api/runs/${runId}/retry`, { guidance });
    if (rb) rb.disabled = true;  // one re-run per click — don't queue (and pay for) a second
    toast(`Re-running with your guidance (${short(r.job_id, 6)}).`);
    refresh();
  } catch (e) { toast(e.message); }
}

async function selectRun(id) {
  state.selected = id;
  try { localStorage.setItem("polis.selected", id); } catch (e) { /* private mode */ }
  try { renderDetail(await api("GET", `/api/runs/${id}`)); }
  catch (e) {
    // Drop a stale stored id (e.g. a cleared DB / new instance) so it doesn't error every reload.
    try { localStorage.removeItem("polis.selected"); } catch (_) { /* private mode */ }
    $("detail").innerHTML = `<p class="muted">${esc(e.message)}</p>`;
  }
}

function renderFeed(events) {
  if (!events.length) return;
  state.lastFeedTs = events[events.length - 1].ts;
  const feed = $("feed");
  const atBottom = feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 30;
  feed.insertAdjacentHTML("beforeend", events.map((e) => `
    <div class="f b-${e.branch}"><span class="k dot">${esc(e.kind)}</span>
      <span class="muted">${esc(e.actor)}</span>
      <span>${payloadSummary(e.kind, e.payload)}</span></div>`).join(""));
  while (feed.childElementCount > 200) feed.removeChild(feed.firstChild);
  if (atBottom) feed.scrollTop = feed.scrollHeight;
}

function renderJobs(jobs) {
  $("jobs").innerHTML = jobs.slice(-4).reverse().map((j) =>
    `<div>job ${short(j.job_id, 6)} · ${j.status}${j.outcome ? " · " + j.outcome : ""}${j.error ? " · " + esc(j.error) : ""}</div>`).join("");
}

let toastTimer;
function toast(msg) {
  let t = $("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.style.display = "block";
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.style.display = "none", 2500);
}

function ticketBadge(r) {
  if (r.ticket_status === "pending") return `<span class="badge flight">● distilling</span>`;
  if (r.ticket_status === "error") return `<span class="badge escalate">⚠ clerk error</span>`;
  if (r.ticket && r.ticket.severity) return `<span class="badge done">${esc(r.ticket.severity)}</span>`;
  return "";
}
function reportDetail(r) {
  let h = "";
  if (r.screenshot) h += `<img class="shot" src="/api/reports/${esc(r.id)}/screenshot" alt="screenshot">`;
  if (r.ticket) {
    const t = r.ticket;
    h += `<div class="ticket"><strong>${esc(t.title || "")}</strong>` +
      (t.severity ? ` <span class="muted">[${esc(t.severity)}]</span>` : "") +
      `<div>${esc(t.description || "")}</div>` +
      ((t.repro_steps || []).length ? `<div class="muted">repro: ${esc((t.repro_steps || []).join(" → "))}</div>` : "") +
      (t.expected ? `<div class="muted">expected: ${esc(t.expected)}</div>` : "") +
      (t.actual ? `<div class="muted">actual: ${esc(t.actual)}</div>` : "") + `</div>`;
  } else if (r.ticket_status === "pending") {
    h += `<div class="muted">distilling ticket…</div>`;
  }
  if (r.ticket_error) h += `<div class="muted">clerk error: ${esc(r.ticket_error)}</div>`;
  if (r.feedback_id && state.pendingIds.has(r.feedback_id))
    h += `<button class="runticket" data-fb="${esc(r.feedback_id)}">▶ Run this ticket</button>`;
  else if (r.feedback_id)
    h += `<div class="muted">✓ ticket already queued or run</div>`;
  h += `<details><summary class="muted">captured state</summary><pre class="state">${esc(JSON.stringify(r.state || {}, null, 2))}</pre></details>`;
  h += `<p class="muted">${esc(r.url || "")} · ${r.feedback_id ? "feedback " + esc(r.feedback_id) : "no feedback item — distilling or Clerk off"}</p>`;
  return `<div class="rdetail">${h}</div>`;
}
function renderReports(list) {
  $("reportcount").textContent = list.length ? `(${list.length})` : "";
  if (!list.length) { $("reports").innerHTML = `<p class="muted">No reports yet.</p>`; return; }
  $("reports").innerHTML = list.map((r) => `
    <div class="report ${r.id === state.openReport ? "open" : ""}" data-id="${r.id}">
      <div class="rhead">
        ${ticketBadge(r)}
        <span class="ttl">${esc(r.text || "(no description)")}</span>
        <span class="meta muted">${esc(r.submitted_by || "")} · ${rel(r.created_at)}${r.screenshot ? " · 📷" : ""}</span>
      </div>
      ${r.id === state.openReport ? reportDetail(r) : ""}
    </div>`).join("");
  document.querySelectorAll(".report .rhead").forEach((el) => {
    el.onclick = () => {
      const id = el.parentNode.dataset.id;
      state.openReport = state.openReport === id ? null : id;
      renderReports(state.reportsCache);
    };
  });
  document.querySelectorAll(".report .runticket").forEach((el) =>
    el.onclick = (ev) => { ev.stopPropagation(); runTicket(el.dataset.fb); });
}

async function refresh() {
  if (!$("live").checked) return;
  try {
    // First load (incl. after a hard refresh) fills the feed to its cap; then poll incrementally.
    const evUrl = state.lastFeedTs ? `/api/events?since_ts=${state.lastFeedTs}` : `/api/events?tail=200`;
    const [ov, runs, feed, jobs, reports, feedback] = await Promise.all([
      api("GET", "/api/overview"),
      api("GET", "/api/runs"),
      api("GET", evUrl),
      api("GET", "/api/jobs"),
      api("GET", "/api/reports"),
      api("GET", "/api/feedback"),
    ]);
    state.pendingIds = new Set((feedback.pending || []).map((p) => p.id));
    renderStats(ov); renderRuns(runs.runs); renderFeed(feed.events); renderJobs(jobs.jobs);
    state.reportsCache = reports.reports; renderReports(state.reportsCache);
    if (state.selected && runs.runs.some((r) => r.run_id === state.selected && r.in_flight))
      selectRun(state.selected);
  } catch (e) { /* server momentarily busy; next tick */ }
}

// --- forms ---
$("budgetForm").onsubmit = async (e) => {
  e.preventDefault();
  try { await api("POST", "/api/budget", { amount: Number($("budgetAmount").value) }); toast("Funded."); $("budgetAmount").value = ""; refresh(); }
  catch (err) { toast(err.message); }
};
$("feedbackForm").onsubmit = async (e) => {
  e.preventDefault();
  let directives = null;
  const raw = $("feedbackDirectives").value.trim();
  if (raw) { try { directives = JSON.parse(raw); } catch { return toast("directives must be valid JSON"); } }
  try { await api("POST", "/api/feedback", { text: $("feedbackText").value, directives }); toast("Feedback submitted."); $("feedbackText").value = ""; $("feedbackDirectives").value = ""; refresh(); }
  catch (err) { toast(err.message); }
};
$("runForm").onsubmit = async (e) => {
  e.preventDefault();
  // Real-vs-stub is decided by config (real_runs), not here.
  try {
    const r = await api("POST", "/api/run", {
      architects: Number($("runArchitects").value) || 1,
      constitution_court: $("runCourt").checked,
    });
    toast(`Run queued (${r.job_id}).`); refresh();
  } catch (err) { toast(err.message); }
};

async function loadBranches(path) {
  const sel = $("repoBranchSelect");
  sel.innerHTML = '<option value="">— branch —</option>';
  if (!path) return;
  try {
    const { branches } = await api("GET", `/api/branches?path=${encodeURIComponent(path)}`);
    branches.forEach((b) => {
      const opt = document.createElement("option");
      opt.value = b; opt.textContent = b;
      sel.appendChild(opt);
    });
  } catch (e) { /* leave text fallback editable */ }
}

async function loadConfig() {
  try {
    const c = await api("GET", "/api/config");
    $("repoCurrent").textContent = `target repo: ${c.workspace}` +
      (c.managed_default ? "  (managed default)" : `  (configured · ${c.main_branch})`);
    $("cfgTesting").checked = !!c.testing_mode;
    $("cfgTicketizer").checked = !!c.ticketizer;
    $("cfgAutoRun").checked = !!c.auto_run;
    if (!c.managed_default && c.workspace) loadBranches(c.workspace);
  } catch (e) { /* ignore */ }
}
function wireFlag(id, key) {
  $(id).onchange = async () => {
    try { await api("POST", "/api/config", { [key]: $(id).checked }); toast(`${key} ${$(id).checked ? "on" : "off"}`); }
    catch (e) { toast(e.message); loadConfig(); }
  };
}
["cfgTesting:testing_mode", "cfgTicketizer:ticketizer", "cfgAutoRun:auto_run"]
  .forEach((s) => { const [id, key] = s.split(":"); wireFlag(id, key); });

async function runTicket(feedbackId) {
  // Real-vs-stub is decided by config (real_runs), not here.
  try {
    const r = await api("POST", "/api/run", { feedback_id: feedbackId });
    toast(`Run queued (${short(r.job_id, 6)}).`); refresh();
  } catch (e) { toast(e.message); }
}
async function applyRepo(path, branch) {
  await api("POST", "/api/config", { workspace: path, main_branch: branch || null });
  toast("Target repo set.");
  loadConfig();
  repoNotice("");
}

function repoNotice(msg, opts) {
  const el = $("repoNotice");
  if (!msg) { el.innerHTML = ""; el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `<span class="err">${esc(msg)}</span>` +
    (opts && opts.initPath
      ? ` <button type="button" id="repoInitBtn" class="secondary">Initialise as git repo</button>`
      : "");
  const b = $("repoInitBtn");
  if (b) b.onclick = () => initRepo(opts.initPath);
}

async function initRepo(path) {
  try {
    await api("POST", "/api/repo-init", { path });
    await applyRepo(path, null);
  } catch (e) { repoNotice(e.message); }
}

$("repoForm").onsubmit = async (e) => {
  e.preventDefault();
  const ws = $("repoPath").value.trim();
  if (!ws) return;
  if (!confirm(`Point Polis at:\n${ws}\n\nThe agents will branch, commit, and merge into this repo. Continue?`)) return;
  try {
    const st = await api("GET", `/api/repo-status?path=${encodeURIComponent(ws)}`);
    if (!st.is_git) return repoNotice(`"${st.path}" is not a git repository.`, { initPath: st.path });
    await applyRepo(st.path, $("repoBranchSelect").value);
    $("repoPath").value = "";
    $("repoBranchSelect").innerHTML = '<option value="">— branch —</option>';
  } catch (err) { repoNotice(err.message); }
};

$("browsePath").onclick = async () => {
  try {
    const r = await fetch("/api/browse", { method: "POST" });
    if (r.status !== 200) return;   // 204 → no dialog; text field stays editable as fallback
    const { path, is_git } = await r.json();
    $("repoPath").value = path;
    if (is_git) { await loadBranches(path); await applyRepo(path, $("repoBranchSelect").value); }
    else repoNotice(`"${path}" is not a git repository.`, { initPath: path });
  } catch (e) { /* ignore */ }
};

$("repoPath").onblur = () => { const p = $("repoPath").value.trim(); if (p) loadBranches(p); };

// Restore UI state across reloads (incl. Ctrl-Shift-R): the selected run + run-row options.
try {
  const a = localStorage.getItem("polis.arch"); if (a) $("runArchitects").value = a;
  $("runCourt").checked = localStorage.getItem("polis.court") === "1";
  state.selected = localStorage.getItem("polis.selected") || null;
} catch (e) { /* private mode */ }
const persist = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } };
$("runArchitects").onchange = () => persist("polis.arch", $("runArchitects").value);
$("runCourt").onchange = () => persist("polis.court", $("runCourt").checked ? "1" : "");

loadConfig();
if (state.selected) selectRun(state.selected);  // re-open the previously selected run's timeline
refresh();
setInterval(refresh, 2500);
