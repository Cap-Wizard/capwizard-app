"use strict";

const BACKEND = "http://127.0.0.1:8765";
const WS_BACKEND = "ws://127.0.0.1:8765";

// LOCAL_MODE = true → nessun login né controllo abbonamento (uso privato).
// Metti false quando il backend cloud è pronto per la vendita.
const LOCAL_MODE = true;

const state = {
  backendReady: false,
  aiReady: false,          // Claude verified with a real request — required to edit
  ffmpeg: false,
  running: false,
  clients: [],             // client profiles (skills with one client's rules)
  client: null,            // chosen client, or null
  otherTemplate: false,    // "Altro template…": no client, template picked by hand
  steps: [],               // progress steps of an editing run
  jobId: null,
  batch: false,
  batchCount: 0,
  projects: [],
  currentProject: null,
  att: [],                 // screenshots attached to the next change request
  modRunning: null,        // id of the project Claude is changing
  wizardStep: 1,
  authMode: "login",       // login | register
  authToken: null,
  cloudUrl: "http://127.0.0.1:8799",
  email: null,
};

// Offline grace: how long the app keeps working after the last confirmed
// active subscription check, when the cloud can't be reached.
const OFFLINE_GRACE_DAYS = 7;

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

window.addEventListener("DOMContentLoaded", async () => {
  wireEditor();
  wireProjects();
  wireWizard();
  wireAuth();

  // Resolve the cloud licensing URL (Electron injects it; browser dev falls back)
  if (window.api && window.api.cloudUrl) {
    try { state.cloudUrl = await window.api.cloudUrl(); } catch (_) {}
  }

  // Electron tells us when the sidecar is up; we also poll as a fallback.
  if (window.api && window.api.onBackendReady) {
    window.api.onBackendReady(() => bootBackend());
  }
  bootBackend();          // sidecar readiness (pills + editing), runs regardless

  if (LOCAL_MODE) {
    showView("editor");   // private use: skip the login/subscription gate
  } else {
    startAuthGate();      // decides the first visible screen
  }
});

let _booting = false;

async function bootBackend() {
  if (_booting || state.backendReady) return;
  _booting = true;
  for (let i = 0; i < 120; i++) {          // ~60s of retries
    try {
      const r = await fetch(`${BACKEND}/api/health`);
      if (r.ok) {
        state.backendReady = true;
        setPill("pill-backend", "ok", "backend");
        await loadSystem();
        await loadTemplates();
        await loadClients();
        await loadProjects();
        check();
        checkClaude();                      // real request, takes a few seconds
        _booting = false;
        return;
      }
    } catch (_) { /* backend not up yet */ }
    await new Promise((res) => setTimeout(res, 500));
  }
  setPill("pill-backend", "err", "backend");
  _booting = false;
}

// ---------------------------------------------------------------------------
// Views
// ---------------------------------------------------------------------------

function showView(name) {
  for (const v of ["view-auth", "view-subscribe", "view-home", "view-editor"]) {
    $(v).hidden = v !== `view-${name}`;
  }
  const inApp = name === "home" || name === "editor";
  $("views").hidden = !inApp;
  $("v-new").classList.toggle("on", name === "editor");
  $("v-proj").classList.toggle("on", name === "home");
  document.body.classList.toggle("projs-view", name === "home");
  // account controls only when past the gate (and not in local/private mode)
  const authed = inApp && !LOCAL_MODE;
  $("tb-email").hidden = !authed || !state.email;
  $("tb-account").hidden = !authed;
  $("tb-logout").hidden = !authed;
  if (state.email) $("tb-email").textContent = state.email;
  if (name === "home" && state.backendReady) loadProjects();
}

function setPill(id, cls, label) {
  const p = $(id);
  p.className = `pill ${cls}`;
  p.innerHTML = `<i></i>${escapeHtml(label)}`;
}

// ---------------------------------------------------------------------------
// Data loads
// ---------------------------------------------------------------------------

// Claude is mandatory: verify it with a real request and gate editing on it.
async function checkClaude() {
  setPill("pill-ai", "warn", "Claude…");
  try {
    const r = await (await fetch(`${BACKEND}/api/ai/test`, { method: "POST" })).json();
    state.aiReady = !!r.ok;
    setPill("pill-ai", r.ok ? "ok" : "err", r.ok ? "Claude collegato" : "Claude da collegare");
    if (!r.ok) addLog(`Claude non è collegato: ${r.message}. Premi ⚙ in alto per collegarlo.`, "err");
  } catch (_) {
    state.aiReady = false;
    setPill("pill-ai", "err", "Claude da collegare");
  }
  check();
}

async function loadSystem() {
  try {
    const sys = await (await fetch(`${BACKEND}/api/system`)).json();
    state.ffmpeg = !!sys.ffmpeg;
    setPill("pill-ffmpeg", sys.ffmpeg ? "ok" : "err", "ffmpeg");
  } catch (e) { console.error("system", e); }
}

async function loadTemplates() {
  try {
    const data = await (await fetch(`${BACKEND}/api/templates`)).json();
    const sel = $("template");
    sel.innerHTML = "";
    const none = document.createElement("option");
    none.value = ""; none.textContent = "Scegli il template…";
    sel.appendChild(none);
    for (const t of data.templates) {
      const o = document.createElement("option");
      o.value = t.name; o.textContent = t.name;
      sel.appendChild(o);
    }
  } catch (e) { console.error("templates", e); }
}

async function loadClients() {
  try {
    const data = await (await fetch(`${BACKEND}/api/clients`)).json();
    state.clients = data.clients || [];
    state.steps = data.steps || [];
  } catch (e) { console.error("clients", e); }
  renderChips();
  renderSteps(0);
}

// ---------------------------------------------------------------------------
// Step 1: client
// ---------------------------------------------------------------------------

function renderChips() {
  const box = $("chips");
  box.innerHTML = "";
  for (const c of state.clients) {
    const chip = document.createElement("button");
    chip.className = "chip" + (state.client && state.client.id === c.id ? " on" : "");
    chip.style.setProperty("--c", c.colore);
    chip.innerHTML = `<i style="background:${c.colore}"></i>${escapeHtml(c.nome)}`;
    chip.onclick = () => chooseClient(c);
    box.appendChild(chip);
  }
  const other = document.createElement("button");
  other.className = "chip other" + (state.otherTemplate ? " on" : "");
  other.textContent = "Altro template…";
  other.onclick = () => chooseClient(null);
  box.appendChild(other);
  $("client-hint").textContent = state.clients.length ? ""
    : "Nessun profilo cliente: è una skill reel-<cliente> che nomina il suo TEMPLATE. Intanto scegli un template a mano.";
}

async function chooseClient(c) {
  state.client = c;
  state.otherTemplate = !c;
  document.documentElement.style.setProperty("--c", c ? c.colore : "#7c6cff");
  $("s1-sel").textContent = c ? c.nome : "template a mano";
  $("template-box").hidden = !!c;
  if (c) {
    $("template").value = c.template_ok ? c.template : "";
    $("client-hint").textContent = c.template_ok ? ""
      : `Il template «${c.template}» del profilo non c'è tra i progetti CapCut.`;
  } else {
    $("client-hint").textContent = "";
  }
  renderChips();
  loadKit();
  suggestName();
  check();

  $("rules-title").textContent = c ? `Regole · ${c.nome}` : "Regole del cliente";
  $("btn-client-open").hidden = !c;
  if (!c) {
    $("rules").innerHTML = '<div class="empty">Senza profilo cliente Claude segue solo le regole generali e il template.</div>';
    return;
  }
  try {
    const r = await (await fetch(`${BACKEND}/api/clients/${encodeURIComponent(c.id)}/rules`)).json();
    if (state.client === c) $("rules").innerHTML = md(r.text || "") || '<div class="empty">Il profilo è vuoto.</div>';
  } catch (_) { /* rules are informative only */ }
}

// What the chosen template offers, and what to add to make it a full kit.
async function loadKit() {
  const name = $("template").value;
  $("kit-details").hidden = !name;
  if (!name) return;
  try {
    const kit = await (await fetch(`${BACKEND}/api/template-kit?name=${encodeURIComponent(name)}`)).json();
    if ($("template").value !== name) return;          // another one was picked meanwhile
    const box = $("kit-checks");
    box.innerHTML = "";
    if (!kit.ok) { $("kit-count").textContent = ""; box.textContent = kit.error || "Template non leggibile."; return; }
    $("kit-count").textContent = `· ${kit.found} su ${kit.total}`;
    for (const c of kit.checks) {
      const row = document.createElement("div");
      row.className = `kit-row ${c.ok ? "ok" : "missing"}`;
      row.innerHTML = `<span class="kit-mark">${c.ok ? "✓" : "○"}</span><div><span class="kit-label"></span><span class="kit-detail"></span></div>`;
      row.querySelector(".kit-label").textContent = c.label;
      row.querySelector(".kit-detail").textContent = c.ok ? (c.detail ? ` — ${c.detail}` : "") : ` — manca. ${c.hint}`;
      box.appendChild(row);
    }
  } catch (e) { $("kit-details").hidden = true; console.error("kit", e); }
}

// ---------------------------------------------------------------------------
// Step 2: clips
// ---------------------------------------------------------------------------

async function setFolder(path) {
  $("input-path").value = path;
  $("input-path-label").textContent = path;
  $("s2-info").textContent = "carico…";
  $("thumbs").innerHTML = "";
  suggestName();
  check();
  if (state.batch) previewBatch();
  try {
    const i = await (await fetch(`${BACKEND}/api/folder-info?path=${encodeURIComponent(path)}`)).json();
    if ($("input-path").value !== path) return;
    $("s2-info").textContent = i.ok && i.n
      ? `${i.n} clip · ${Math.floor(i.durata / 60)}:${String(i.durata % 60).padStart(2, "0")} min` : "nessuna clip trovata";
    for (const t of i.thumbs || []) {
      const img = document.createElement("img");
      img.src = t;
      $("thumbs").appendChild(img);
    }
  } catch (_) { $("s2-info").textContent = ""; }
}

// Name = client + folder, until the user writes their own.
function suggestName() {
  const el = $("name");
  if (state.batch || (el.value && !el.dataset.auto)) return;
  const folder = $("input-path").value.split("/").filter(Boolean).pop() || "";
  const name = [state.client ? state.client.nome.toUpperCase() : "", folder].filter(Boolean).join(" - ");
  el.value = name;
  el.dataset.auto = name ? "1" : "";
}

async function previewBatch() {
  const path = $("input-path").value.trim();
  if (!path) return;
  try {
    const data = await (await fetch(`${BACKEND}/api/subfolders?path=${encodeURIComponent(path)}`)).json();
    const folders = data.folders || [];
    state.batchCount = folders.length;
    const el = $("batch-preview");
    el.hidden = false;
    el.textContent = folders.length
      ? `Farò ${folders.length} progetti: ${folders.map((f) => f.name).join(", ")}`
      : "Nessuna sottocartella con video qui.";
  } catch (_) { state.batchCount = 0; }
  check();
}

function updateBatchUI() {
  const b = state.batch;
  $("sw-batch").classList.toggle("on", b);
  $("name-label").textContent = b ? "Prefisso dei nomi (facoltativo)" : "Nome del nuovo progetto";
  $("name").placeholder = b ? "es. CHIRONE" : "es. CLIENTE - Video 1";
  $("batch-preview").hidden = true;
  state.batchCount = 0;
  if (b && $("input-path").value) previewBatch();
  check();
}

// ---------------------------------------------------------------------------
// Readiness
// ---------------------------------------------------------------------------

function check() {
  const hasTemplate = !!$("template").value;
  const hasFolder = !!$("input-path").value;
  const hasName = state.batch || !!$("name").value.trim();
  $("s1").classList.toggle("done", hasTemplate);
  $("s2").classList.toggle("done", hasFolder && (!state.batch || state.batchCount > 0));
  $("s3").classList.toggle("done", hasName && !state.batch);
  const miss = [];
  if (!hasTemplate) miss.push(state.otherTemplate ? "template" : "cliente");
  if (!hasFolder) miss.push("cartella delle clip");
  else if (state.batch && !state.batchCount) miss.push("sottocartelle con video");
  if (!hasName) miss.push("nome del progetto");
  const ready = state.backendReady && state.aiReady && !state.running && !state.modRunning && !miss.length;
  $("btn-generate").disabled = !ready;
  $("missing").textContent = state.running ? "Montaggio in corso…"
    : !state.backendReady ? "Avvio del motore dell'app…"
    : miss.length ? `Manca: ${miss.join(", ")}`
    : !state.aiReady ? "Collega Claude con ⚙ in alto"
    : "Tutto pronto ✓";
  if (!state.running) {
    $("btn-generate").textContent = state.batch
      ? (state.batchCount ? `MONTA ${state.batchCount} VIDEO` : "MONTA TUTTI") : "INIZIA A EDITARE";
  }
}

function isOn(id) { return $(id).classList.contains("on"); }

function wireEditor() {
  $("v-new").onclick = () => showView("editor");
  $("v-proj").onclick = () => showView("home");

  $("drop").onclick = async () => {
    const p = window.api && await window.api.selectFolder();
    if (p) setFolder(p);
  };
  // dropping a folder (or a clip: its folder) anywhere on the window
  document.addEventListener("dragover", (e) => {
    e.preventDefault();
    if (!$("view-editor").hidden || state.currentProject) document.body.classList.add("dragging");
  });
  document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) document.body.classList.remove("dragging"); });
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    document.body.classList.remove("dragging");
    if (!$("view-home").hidden) {          // screenshots for a change request
      for (const f of e.dataTransfer.files) {
        const p = window.api && window.api.pathForFile ? window.api.pathForFile(f) : "";
        if (p) attach({ path: p });
      }
      return;
    }
    if ($("view-editor").hidden || state.running) return;
    const f = e.dataTransfer.files[0];
    const p = f && window.api && window.api.pathForFile ? window.api.pathForFile(f) : "";
    if (!p) return;
    setFolder(/\.(mp4|mov|m4v|mkv|webm|mts)$/i.test(p) ? p.replace(/\/[^/]+$/, "") : p);
  });

  $("template").onchange = () => { loadKit(); check(); };
  $("name").oninput = () => { $("name").dataset.auto = ""; check(); };
  $("btn-script").onclick = async () => {
    const p = window.api && await window.api.selectScript();
    if (p) $("script-path").value = p;
  };
  document.querySelectorAll(".switch[data-sw]").forEach((row) => {
    row.onclick = () => $(row.dataset.sw).classList.toggle("on");
  });
  $("row-batch").onclick = () => { state.batch = !state.batch; updateBatchUI(); };

  $("btn-client-open").onclick = () => { if (state.client) window.api.openPath(state.client.path); };
  $("btn-kit-guide").onclick = () => { $("kit-guide").hidden = false; };
  $("kit-guide-close").onclick = $("kit-guide-done").onclick = () => { $("kit-guide").hidden = true; };

  $("btn-generate").onclick = onGenerate;
  $("btn-stop").onclick = async () => {
    if (!state.jobId) return;
    $("btn-stop").disabled = true;
    setStatus("mi fermo…");
    try { await fetch(`${BACKEND}/api/jobs/${state.jobId}/stop`, { method: "POST" }); } catch (_) {}
  };
  $("btn-open").onclick = () => {
    const p = $("result-path").dataset.path;
    if (p) window.api.openPath(p);
  };
  $("btn-report").onclick = () => {
    const p = $("result-path").dataset.report;
    if (p) window.api.openPath(p);
  };
}

// ---------------------------------------------------------------------------
// Editing run
// ---------------------------------------------------------------------------

function buildRequest() {
  const target = $("ai-target").value;
  return {
    input_path: $("input-path").value.trim(),
    name: $("name").value.trim(),
    template: $("template").value || null,
    client: state.client ? state.client.id : null,
    notes: $("notes").value.trim() || null,
    script_path: $("script-path").value.trim() || null,
    intro_title: $("intro-title").value.trim() || null,
    ai_model: $("ai-model").value,
    ai_target_duration: target === "auto" ? null : parseFloat(target),
    ai_zoom: isOn("sw-zoom"),
    sentence_transitions: isOn("sw-transitions"),
    aggressive_fillers: isOn("sw-aggr"),
  };
}

async function onGenerate() {
  state.running = true;
  check();
  $("console").innerHTML = "";
  $("result-box").classList.remove("show");
  $("btn-generate").classList.add("running");
  $("btn-generate").innerHTML = '<span class="spinner"></span>Claude sta montando…';
  setStatus("in corso");
  renderSteps(1);

  let endpoint = "/api/generate";
  const body = buildRequest();
  if (state.batch) {
    endpoint = "/api/batch";
    body.parent_folder = body.input_path;
    body.name_prefix = body.name;
    delete body.input_path;
    delete body.name;
  }
  addLog(`Lavoro: ${state.batch ? "un video per sottocartella" : body.name} · `
    + `${state.client ? state.client.nome : body.template}`, "step");

  let jobId;
  try {
    const r = await fetch(`${BACKEND}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    jobId = (await r.json()).job_id;
    state.jobId = jobId;
    $("btn-stop").hidden = false;
    $("btn-stop").disabled = false;
  } catch (e) {
    addLog(`Il motore dell'app non risponde (${e}). Chiudi tutte le finestre di CapWizard e riaprila.`, "err");
    finishRun();
    return;
  }

  // The log arrives live over a WebSocket. If that stream stalls or drops, the
  // job must not look "still working" forever: a slow poll of the job's status
  // fills in whatever lines are missing and closes the run when it has ended.
  let shown = 0;
  let ended = false;
  const showLines = (lines, from) => {
    for (let i = Math.max(from, shown); i < from + lines.length; i++) addLog(lines[i - from]);
    shown = Math.max(shown, from + lines.length);
  };
  const end = (msg) => {
    if (ended) return;
    ended = true;
    clearInterval(poll);
    renderSteps(msg.step || 0, msg.status === "done");
    if (msg.status === "done") {
      setStatus("finito");
      if (msg.results && msg.results.length) showBatchResult(msg.results);
      else showResult(msg.result_path, msg.report_path);
      loadProjects();
    } else {
      setStatus("interrotto");
      addLog(msg.error || "errore sconosciuto", "err");
    }
    try { ws.close(); } catch (_) { /* already closed */ }
    finishRun();
  };
  const poll = setInterval(async () => {
    try {
      const job = await (await fetch(`${BACKEND}/api/jobs/${jobId}`)).json();
      if (job.error && !job.status) return end({ status: "error", error: job.error });
      showLines(job.logs || [], 0);
      if (job.status === "running") renderSteps(job.step || 0);
      if (job.status === "done" || job.status === "error") end(job);
    } catch (_) { /* backend busy: try again at the next tick */ }
  }, 4000);

  const ws = new WebSocket(`${WS_BACKEND}/api/ws/jobs/${jobId}`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "log") showLines([msg.line], msg.i ?? shown);
    else if (msg.type === "step") renderSteps(msg.step);
    else if (msg.type === "status") end(msg);
    else if (msg.type === "error") end({ status: "error", error: msg.message });
  };
}

function finishRun() {
  state.running = false;
  state.jobId = null;
  $("btn-stop").hidden = true;
  $("btn-generate").classList.remove("running");
  check();
}

function renderSteps(current, finished = false) {
  $("steps").innerHTML = "";
  state.steps.forEach((label, i) => {
    const n = i + 1;
    const el = document.createElement("div");
    el.className = "ps" + (finished || n < current ? " done" : n === current ? " on" : "");
    el.innerHTML = '<div class="bar"></div>';
    el.append(label);
    $("steps").appendChild(el);
  });
}

function setStatus(t) { $("status-tag").textContent = t; }

function kindOf(line) {
  const t = line.trim();
  if (t.startsWith("▶▶") || t.startsWith("━━")) return "section";
  if (t.startsWith("⚙") || t.startsWith("⏱")) return "tool";
  if (t.startsWith("✓")) return "ok";
  if (t.startsWith("ERRORE") || t.startsWith("✘")) return "err";
  if (t.startsWith("☐") || t.includes("⚠")) return "warn";
  if (t.startsWith("🎬") || t.startsWith("▶")) return "step";
  return "info";
}

function addLog(text, kind) {
  const box = $("console");
  const div = document.createElement("div");
  div.className = `k-${kind || kindOf(text)}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function showResult(path, reportPath) {
  if (!path) return;
  $("result-title").textContent = "✓ Progetto creato";
  const el = $("result-path");
  el.textContent = `${path}\nSe non compare in CapCut, chiudilo (⌘Q) e riaprilo.`;
  el.dataset.path = path;
  el.dataset.report = reportPath || "";
  $("btn-report").hidden = !reportPath;
  $("btn-open").textContent = "Apri in Finder";
  $("result-box").classList.add("show");
}

function showBatchResult(results) {
  const ok = results.filter((r) => r.ok);
  const failed = results.filter((r) => !r.ok);
  $("result-title").textContent = `✓ ${ok.length}/${results.length} progetti creati`;
  const el = $("result-path");
  let txt = ok.map((r) => "• " + r.name).join("\n");
  if (failed.length) txt += "\n\nNon riusciti:\n" + failed.map((r) => "✗ " + r.name).join("\n");
  el.textContent = txt;
  const first = ok[0];
  el.dataset.path = first && first.path ? first.path.replace(/\/[^/]+$/, "") : "";
  el.dataset.report = first && first.report ? first.report.replace(/\/[^/]+$/, "") : "";
  $("btn-report").hidden = !el.dataset.report;
  $("btn-open").textContent = "Apri cartella progetti";
  $("result-box").classList.add("show");
}

// ---------------------------------------------------------------------------
// I miei progetti
// ---------------------------------------------------------------------------

function wireProjects() {
  $("psearch").oninput = renderProjects;
}

async function loadProjects() {
  try {
    const data = await (await fetch(`${BACKEND}/api/projects`)).json();
    state.projects = (data.projects || []).sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
    $("n-proj").textContent = state.projects.length;
    renderProjects();
  } catch (e) { console.error("projects", e); }
}

function fmtDate(ts) {
  return new Date((ts || 0) * 1000).toLocaleString("it-IT",
    { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function renderProjects() {
  const q = $("psearch").value.toLowerCase();
  const list = state.projects.filter((p) => `${p.name} ${p.cliente}`.toLowerCase().includes(q));
  const groups = {};
  for (const p of list) (groups[p.cliente] = groups[p.cliente] || []).push(p);
  const box = $("groups");
  box.innerHTML = "";
  for (const [cliente, ps] of Object.entries(groups)) {
    const g = document.createElement("div");
    g.className = "group";
    g.innerHTML = `<h4><i style="background:${ps[0].colore}"></i>${escapeHtml(cliente)}<small>${ps.length}</small></h4>`;
    for (const p of ps) {
      const card = document.createElement("div");
      card.className = "pcard" + (state.currentProject && state.currentProject.id === p.id ? " on" : "");
      card.style.setProperty("--c", p.colore);
      card.innerHTML = `${p.cover ? `<img src="${p.cover}">` : '<div class="noimg"></div>'}
        <div><div class="t">${escapeHtml(p.name)}</div><div class="m">${fmtDate(p.created_at)}</div>
        ${badge(p)}</div>`;
      card.onclick = () => openProject(p);
      g.appendChild(card);
    }
    box.appendChild(g);
  }
  if (!list.length) box.innerHTML = '<div class="empty">Ancora nessun progetto montato dall\'app</div>';
}

function badge(p) {
  if (state.modRunning === p.id) return '<span class="badge run">⏳ modifiche in corso</span>';
  if (!p.exists) return '<span class="badge err">non più in CapCut</span>';
  return '<span class="badge ok">montato</span>';
}

async function openProject(p) {
  if (!state.currentProject || state.currentProject.id !== p.id) state.att = [];
  state.currentProject = p;
  document.documentElement.style.setProperty("--c", p.colore);
  renderProjects();
  const s = p.settings || {};
  let mods = [];
  try { mods = (await (await fetch(`${BACKEND}/api/projects/${p.id}/mods`)).json()).mods || []; } catch (_) {}
  if (state.currentProject !== p) return;
  const running = state.modRunning === p.id;
  const thread = mods.map((m, i) => {
    const last = i === mods.length - 1;
    const reply = m.stato === "in corso" ? (running && last ? "⏳ Sto facendo le modifiche…" : "⚠️ Interrotto prima di finire")
      : m.stato === "errore" ? `❌ Non sono riuscito a finire: ${escapeHtml(m.riassunto || "")}`
      : escapeHtml(m.riassunto || "Fatto.");
    return `<div class="msg me"><div class="when">Tu · ${escapeHtml(m.data)}</div>${escapeHtml(m.testo || "(solo screenshot)")}`
      + (m.thumbs && m.thumbs.length ? `<div class="imgs">${m.thumbs.map((t) => `<img src="${t}">`).join("")}</div>` : "")
      + `</div><div class="msg ai"><div class="when">Claude</div>${reply}`
      + (m.lezione ? `<div class="lesson">📒 ${m.profilo ? `Lezione salvata nel profilo di ${escapeHtml(m.profilo)}`
        : "Lezione (nessun profilo cliente: resta solo qui)"}: ${escapeHtml(m.lezione)}</div>` : "") + "</div>";
  }).join("");
  $("pdet").innerHTML = `
    <div class="phead">${p.cover ? `<img src="${p.cover}">` : ""}
      <div style="flex:1;min-width:0"><div class="m">${escapeHtml(p.cliente)} · template «${escapeHtml(s.template || "—")}»</div>
        <h2>${escapeHtml(p.name)}</h2>
        <div class="m">Montato il ${fmtDate(p.created_at)}${mods.length ? ` · ${mods.length} ${mods.length > 1 ? "modifiche" : "modifica"}` : ""}</div>
        <div class="sum">${escapeHtml(p.riassunto || "")}</div></div>
      <div class="acts">
        <button class="btn2" id="pd-folder" ${p.exists ? "" : "disabled"}>Apri in Finder</button>
        <button class="btn2" id="pd-report" ${p.report_path ? "" : "disabled"}>Leggi il report</button>
        <button class="btn2" id="pd-again">Rifai con questi dati</button>
      </div>
    </div>
    <div class="thread" id="thread">${thread || '<div class="empty">Nessuna modifica ancora. Se il video non va, scrivi qui sotto cosa cambiare e allega gli screenshot della chat del cliente: Claude corregge questo progetto e salva la lezione nel profilo del cliente.</div>'}</div>
    <div id="mlog" ${running ? "" : "hidden"}></div>
    <div class="composer">
      <textarea id="mtext" placeholder="Cosa cambiare? es. «togli il dong», «sottotitoli più in alto», «il cliente vuole il prezzo più grande»… (incolla gli screenshot con ⌘V)"></textarea>
      <div class="att" id="att"></div>
      <div class="cbar"><button class="btn2" id="btn-attach">📎 Allega screenshot</button>
        <span class="hint">oppure trascinali qui o ⌘V</span>
        <button class="send" id="send">APPLICA MODIFICHE</button></div>
    </div>`;
  $("pd-folder").onclick = () => window.api.openPath(p.result_path);
  $("pd-report").onclick = () => window.api.openPath(p.report_path);
  $("pd-again").onclick = () => redoProject(p);
  $("btn-attach").onclick = async () => {
    for (const path of (window.api && await window.api.selectImages()) || []) attach({ path });
  };
  $("mtext").addEventListener("paste", (e) => {
    for (const it of e.clipboardData.items) {
      if (!it.type.startsWith("image/")) continue;
      e.preventDefault();
      const r = new FileReader();
      r.onload = () => attach({ data_url: r.result });
      r.readAsDataURL(it.getAsFile());
    }
  });
  $("thread").querySelectorAll("img").forEach((img) => { img.onclick = () => zoom(img.src); });
  $("send").onclick = sendChange;
  $("pdet").scrollTop = $("pdet").scrollHeight;
  renderAtt();
}

async function attach(body) {
  try {
    const a = await (await fetch(`${BACKEND}/api/attachments`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    })).json();
    if (a.path) { state.att.push(a); renderAtt(); }
  } catch (_) { /* not an image */ }
}

function renderAtt() {
  const box = $("att");
  if (!box) return;
  box.innerHTML = "";
  state.att.forEach((a, i) => {
    const d = document.createElement("div");
    d.innerHTML = `<img src="${a.thumb}"><span>✕</span>`;
    d.querySelector("span").onclick = () => { state.att.splice(i, 1); renderAtt(); };
    box.appendChild(d);
  });
  const p = state.currentProject;
  $("send").disabled = !!(state.running || state.modRunning) || !p || !p.exists || !state.aiReady;
}

function zoom(src) {
  const z = document.createElement("div");
  z.className = "zoom";
  z.innerHTML = `<img src="${src}">`;
  z.onclick = () => z.remove();
  document.body.appendChild(z);
}

async function sendChange() {
  const p = state.currentProject;
  const testo = $("mtext").value.trim();
  if (!testo && !state.att.length) { $("mtext").focus(); return; }
  $("send").disabled = true;
  $("send").textContent = "Avvio…";
  let r;
  try {
    r = await (await fetch(`${BACKEND}/api/projects/${p.id}/modify`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ testo, immagini: state.att.map((a) => a.path), ai_model: $("ai-model").value }),
    })).json();
  } catch (e) { r = { error: String(e) }; }
  if (!r.job_id) {
    $("send").textContent = "APPLICA MODIFICHE";
    $("send").disabled = false;
    alert(r.error || "Modifica non avviata.");
    return;
  }
  state.modRunning = p.id;
  state.att = [];
  await openProject(p);
  followChange(r.job_id, p);
}

// Claude's messages go in the small log under the chat until the change is done.
function followChange(jobId, p) {
  let shown = 0;
  let ended = false;
  const log = (line) => {
    const box = $("mlog");
    if (!box || state.currentProject !== p) return;
    box.hidden = false;
    const div = document.createElement("div");
    div.className = `k-${kindOf(line)}`;
    div.textContent = line;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  };
  const end = async (job) => {
    if (ended) return;
    ended = true;
    clearInterval(poll);
    state.modRunning = null;
    await loadProjects();
    const fresh = state.projects.find((x) => x.id === p.id) || p;
    if (state.currentProject && state.currentProject.id === p.id) {
      const lines = $("mlog") ? [...$("mlog").children].map((d) => d.textContent) : [];
      await openProject(fresh);
      if (job.status !== "done") lines.forEach(log);   // keep the log of a failed change in view
    }
  };
  const poll = setInterval(async () => {
    try {
      const job = await (await fetch(`${BACKEND}/api/jobs/${jobId}`)).json();
      const lines = job.logs || [];
      while (shown < lines.length) log(lines[shown++]);
      if (job.status === "done" || job.status === "error") end(job);
    } catch (_) { /* backend busy: next tick */ }
  }, 1500);
}

// Back to "Nuovo video" with the same folder, client and notes.
function redoProject(p) {
  const s = p.settings || {};
  const c = state.clients.find((x) => x.id === s.client);
  showView("editor");
  chooseClient(c || null);
  if (!c && s.template) { $("template").value = s.template; loadKit(); }
  $("name").value = `${p.name} v2`;
  $("name").dataset.auto = "";
  $("notes").value = s.notes || "";
  $("script-path").value = s.script_path || "";
  $("intro-title").value = s.intro_title || "";
  if (s.input_path) setFolder(s.input_path);
  check();
}

// ---------------------------------------------------------------------------
// tiny markdown → html for the client's rules
// ---------------------------------------------------------------------------

function md(t) {
  let out = "";
  let inTable = false;
  for (const l of t.split("\n")) {
    const h = escapeHtml(l).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
    if (/^\|/.test(l)) {
      if (/^\|[-\s|:]+\|$/.test(l)) continue;
      if (!inTable) { out += "<table>"; inTable = true; }
      out += "<tr>" + h.split("|").slice(1, -1).map((c) => `<td>${c.trim()}</td>`).join("") + "</tr>";
      continue;
    }
    if (inTable) { out += "</table>"; inTable = false; }
    if (/^#{1,4} /.test(l)) out += `<h4>${h.replace(/^#+ /, "")}</h4>`;
    else if (/^\s*[-*] /.test(l)) out += `<li>${h.replace(/^\s*[-*] /, "")}</li>`;
    else if (l.trim()) out += `<p>${h}</p>`;
  }
  return out + (inTable ? "</table>" : "");
}

// ---------------------------------------------------------------------------
// Claude connection wizard
// ---------------------------------------------------------------------------


function wireWizard() {
  $("btn-config-ai").onclick = openWizard;
  $("wizard-close").onclick = closeWizard;
  $("wizard-next").onclick = () => gotoStep(state.wizardStep + 1);
  $("wizard-prev").onclick = () => gotoStep(state.wizardStep - 1);
  $("wizard-done").onclick = () => {
    closeWizard();
    checkClaude();          // refresh the Claude pill and the editing gate
  };
  $("btn-ai-test").onclick = runAiTest;
}

function openWizard() {
  $("ai-wizard").hidden = false;
  gotoStep(1);
  loadAiStatus();
}
function closeWizard() { $("ai-wizard").hidden = true; }

function gotoStep(n) {
  state.wizardStep = Math.max(1, Math.min(3, n));
  document.querySelectorAll(".wizard-step").forEach((el) => {
    el.classList.toggle("active", Number(el.dataset.step) === state.wizardStep);
  });
  document.querySelectorAll(".wizard-pane").forEach((el) => {
    el.hidden = Number(el.dataset.pane) !== state.wizardStep;
  });
  $("wizard-prev").hidden = state.wizardStep === 1;
  $("wizard-next").hidden = state.wizardStep === 3;
  $("wizard-done").hidden = state.wizardStep !== 3;
}

async function loadAiStatus() {
  const box = $("ai-status-box");
  box.className = "status-block";
  box.textContent = "Controllo in corso…";
  try {
    const st = await (await fetch(`${BACKEND}/api/ai/status`)).json();
    if (st.available) {
      box.classList.add("ok");
      const via = st.method === "claude-cli" ? "Claude Code CLI" : "API key Anthropic";
      box.innerHTML = `✓ AI pronta — collegata via <strong>${via}</strong>. Puoi passare al test.`;
    } else {
      box.classList.add("warn");
      box.innerHTML = "⚠ AI non configurata. Segui il passo 2 per attivarla.";
    }
  } catch (e) {
    box.classList.add("warn");
    box.textContent = "Impossibile contattare il backend.";
  }
}

async function runAiTest() {
  const res = $("ai-test-result");
  res.className = "test-result";
  res.textContent = "Test in corso…";
  try {
    const r = await (await fetch(`${BACKEND}/api/ai/test`, { method: "POST" })).json();
    if (r.ok) {
      res.classList.add("ok");
      res.textContent = `✓ Funziona! Claude ha risposto: "${r.message}"`;
    } else {
      res.classList.add("err");
      res.textContent = `✗ ${r.message}`;
    }
  } catch (e) {
    res.classList.add("err");
    res.textContent = `✗ Errore: ${e}`;
  }
}

// ---------------------------------------------------------------------------
// Auth + subscription gate
// ---------------------------------------------------------------------------

async function getToken() {
  if (window.api && window.api.getToken) return window.api.getToken();
  return localStorage.getItem("capwizard_token");
}
async function setToken(t) {
  state.authToken = t;
  if (window.api && window.api.setToken) return window.api.setToken(t);
  localStorage.setItem("capwizard_token", t);
}
async function clearToken() {
  state.authToken = null;
  if (window.api && window.api.clearToken) return window.api.clearToken();
  localStorage.removeItem("capwizard_token");
}

function cacheActive(active) {
  localStorage.setItem("capwizard_sub", JSON.stringify({ active, ts: Date.now() }));
}
function cachedGraceOk() {
  try {
    const c = JSON.parse(localStorage.getItem("capwizard_sub") || "{}");
    if (!c.active) return false;
    return (Date.now() - c.ts) < OFFLINE_GRACE_DAYS * 86400 * 1000;
  } catch (_) { return false; }
}

async function startAuthGate() {
  const token = await getToken();
  if (!token) { showView("auth"); return; }
  state.authToken = token;
  await routeBySubscription();
}

// returns after routing to the correct view
async function routeBySubscription() {
  try {
    const r = await fetch(`${state.cloudUrl}/me`, {
      headers: { Authorization: `Bearer ${state.authToken}` },
    });
    if (r.status === 401) { await clearToken(); showView("auth"); return; }
    const me = await r.json();
    state.email = me.email;
    if (me.active) {
      cacheActive(true);
      showView("editor");
    } else {
      cacheActive(false);
      renderSubInfo(me);
      showView("subscribe");
    }
  } catch (_) {
    // cloud unreachable — honor the offline grace period
    if (cachedGraceOk()) {
      showView("editor");
    } else {
      renderSubInfo(null);
      showView("subscribe");
    }
  }
}

function renderSubInfo(me) {
  const el = $("sub-info");
  if (!me) {
    el.textContent = "Impossibile verificare l'abbonamento (offline). Riprova quando sei online.";
  } else if (me.subscription_status === "past_due") {
    el.textContent = "Pagamento in sospeso — aggiorna il metodo di pagamento.";
  } else if (me.subscription_status === "canceled") {
    el.textContent = "Abbonamento annullato. Riattivalo per continuare.";
  } else {
    el.textContent = "";
  }
}

function wireAuth() {
  $("auth-switch").onclick = () => {
    state.authMode = state.authMode === "login" ? "register" : "login";
    const login = state.authMode === "login";
    $("auth-title").textContent = login ? "Accedi" : "Registrati";
    $("auth-submit").textContent = login ? "Accedi" : "Crea account";
    $("auth-switch-text").textContent = login ? "Non hai un account?" : "Hai già un account?";
    $("auth-switch").textContent = login ? "Registrati" : "Accedi";
    $("auth-error").textContent = "";
  };
  $("auth-submit").onclick = doAuth;
  $("auth-password").onkeydown = (e) => { if (e.key === "Enter") doAuth(); };

  $("btn-subscribe").onclick = doSubscribe;
  $("btn-recheck").onclick = routeBySubscription;
  $("btn-logout-sub").onclick = doLogout;
  $("tb-logout").onclick = doLogout;
  $("tb-account").onclick = doManageBilling;
}

async function doAuth() {
  const email = $("auth-email").value.trim();
  const password = $("auth-password").value;
  const err = $("auth-error");
  err.textContent = "";
  if (!email || !password) { err.textContent = "Inserisci email e password."; return; }

  const path = state.authMode === "login" ? "/auth/login" : "/auth/register";
  $("auth-submit").disabled = true;
  try {
    const r = await fetch(`${state.cloudUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await r.json();
    if (!r.ok) { err.textContent = data.detail || "Errore."; return; }
    await setToken(data.token);
    state.email = email;
    $("auth-password").value = "";
    await routeBySubscription();
  } catch (e) {
    err.textContent = `Impossibile contattare il server (${e}).`;
  } finally {
    $("auth-submit").disabled = false;
  }
}

async function doSubscribe() {
  try {
    const r = await fetch(`${state.cloudUrl}/billing/checkout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${state.authToken}` },
    });
    const data = await r.json();
    if (r.ok && data.url) {
      openExternal(data.url);
    } else {
      $("sub-info").textContent = data.detail || "Impossibile avviare il checkout.";
    }
  } catch (e) {
    $("sub-info").textContent = `Errore: ${e}`;
  }
}

async function doManageBilling() {
  try {
    const r = await fetch(`${state.cloudUrl}/billing/portal`, {
      method: "POST",
      headers: { Authorization: `Bearer ${state.authToken}` },
    });
    const data = await r.json();
    if (r.ok && data.url) openExternal(data.url);
  } catch (_) {}
}

async function doLogout() {
  await clearToken();
  state.email = null;
  localStorage.removeItem("capwizard_sub");
  showView("auth");
}

function openExternal(url) {
  if (window.api && window.api.openExternal) window.api.openExternal(url);
  else window.open(url, "_blank");
}

// ---------------------------------------------------------------------------
// utils
// ---------------------------------------------------------------------------

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
