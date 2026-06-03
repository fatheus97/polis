"use strict";
const $ = (id) => document.getElementById(id);
const state = { selected: null, lastFeedTs: 0 };

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
    case "test_result": return p.passed ? "tests passed ✓" : "tests failed ✗";
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
  $("detail").innerHTML = strip + facts + `<div class="timeline">${rows}</div>`;
}

async function selectRun(id) {
  state.selected = id;
  try { renderDetail(await api("GET", `/api/runs/${id}`)); }
  catch (e) { $("detail").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
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

async function refresh() {
  if (!$("live").checked) return;
  try {
    const [ov, runs, feed, jobs] = await Promise.all([
      api("GET", "/api/overview"),
      api("GET", "/api/runs"),
      api("GET", `/api/events?since_ts=${state.lastFeedTs}`),
      api("GET", "/api/jobs"),
    ]);
    renderStats(ov); renderRuns(runs.runs); renderFeed(feed.events); renderJobs(jobs.jobs);
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
  const real = $("runReal").checked;
  if (real && !confirm("This starts a REAL LLM run that costs money on your subscription. Continue?")) return;
  try {
    const r = await api("POST", "/api/run", {
      real, confirm: real,
      architects: Number($("runArchitects").value) || 1,
      constitution_court: $("runCourt").checked,
    });
    toast(`Run queued (${r.job_id}).`); refresh();
  } catch (err) { toast(err.message); }
};

async function loadConfig() {
  try {
    const c = await api("GET", "/api/config");
    $("repoCurrent").textContent = `target repo: ${c.workspace}` +
      (c.managed_default ? "  (managed default)" : `  (configured · ${c.main_branch})`);
  } catch (e) { /* ignore */ }
}
$("repoForm").onsubmit = async (e) => {
  e.preventDefault();
  const ws = $("repoPath").value.trim();
  if (ws && !confirm(`Point Polis at:\n${ws}\n\nThe agents will branch, commit, and merge into this repo. Continue?`)) return;
  try {
    await api("POST", "/api/config", { workspace: ws, main_branch: $("repoBranch").value.trim() || null });
    $("repoPath").value = ""; $("repoBranch").value = ""; toast("Target repo set."); loadConfig();
  } catch (err) { toast(err.message); }
};

loadConfig();
refresh();
setInterval(refresh, 2500);
