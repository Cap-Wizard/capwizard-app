// Updates from the GitHub releases of CapWizard, for the app installed from the DMG.
//
// electron-updater (Squirrel.Mac) only updates apps signed with an Apple Developer ID, and CapWizard
// is signed ad hoc. So the app does it itself: it reads the releases of Cap-Wizard/capwizard-app, and
// when one is newer than itself it downloads that release's zip, checks it against the release's
// .sha256, unpacks it next to the running app and, once the app has quit, a small script swaps the
// bundle (keeping the old one until the new one is in place) and, if asked, opens it again.
//
// «chiedi»: a notice offers the update and the user starts it. «automatico»: it downloads by itself.
// Either way a downloaded update goes in when the app quits, or right away with «Riavvia e aggiorna». The choice lives in userData/updates.json.
"use strict";

const { app, net } = require("electron");
const { spawn, execFileSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const REPO = "Cap-Wizard/capwizard-app";
// To try the whole flow without publishing anything: CAPWIZARD_UPDATE_FEED = a JSON shaped like GitHub's
// releases list, CAPWIZARD_UPDATE_FROM = the version to pretend to be, CAPWIZARD_UPDATE_TARGET = the .app to replace.
const FEED = process.env.CAPWIZARD_UPDATE_FEED || `https://api.github.com/repos/${REPO}/releases?per_page=20`;
const EVERY = 6 * 3600 * 1000;

let state = { fase: "fermo", versione: "", nuova: null, pct: 0, errore: "", controllato: null, modo: "chiedi" };
let ready = null;          // { app: unpacked new bundle, version }
let send = () => {};

// ---------------------------------------------------------------------------
// versions: 0.2.0-alpha.2 < 0.2.0-alpha.10 < 0.2.0 < 0.2.1
// ---------------------------------------------------------------------------

function parse(v) {
  const [main, pre] = String(v).replace(/^v/, "").split("-", 2);
  return { main: main.split(".").map((n) => parseInt(n, 10) || 0), pre: pre ? pre.split(".") : [] };
}

function compare(a, b) {
  const x = parse(a), y = parse(b);
  for (let i = 0; i < 3; i++) if ((x.main[i] || 0) !== (y.main[i] || 0)) return (x.main[i] || 0) - (y.main[i] || 0);
  if (!x.pre.length || !y.pre.length) return y.pre.length - x.pre.length;     // a release beats its alphas
  for (let i = 0; i < Math.max(x.pre.length, y.pre.length); i++) {
    const p = x.pre[i], q = y.pre[i];
    if (p === undefined || q === undefined) return p === undefined ? -1 : 1;
    const np = /^\d+$/.test(p), nq = /^\d+$/.test(q);
    if (np && nq && +p !== +q) return +p - +q;
    if (np !== nq) return np ? -1 : 1;
    if (!np && p !== q) return p < q ? -1 : 1;
  }
  return 0;
}

// ---------------------------------------------------------------------------
// settings and state
// ---------------------------------------------------------------------------

const settingsFile = () => path.join(app.getPath("userData"), "updates.json");

function loadMode() {
  try { return JSON.parse(fs.readFileSync(settingsFile(), "utf8")).modo === "automatico" ? "automatico" : "chiedi"; } catch (_) { return "chiedi"; }
}

function setMode(modo) {
  state.modo = modo === "automatico" ? "automatico" : "chiedi";
  try { fs.writeFileSync(settingsFile(), JSON.stringify({ modo: state.modo })); } catch (_) { /* read-only: keep it for this session */ }
  update({});
  if (state.modo === "automatico" && state.fase === "disponibile") download();
  return state;
}

function update(patch) {
  Object.assign(state, patch);
  send({ ...state });
}

// the .app the user runs (only the packaged app can update itself)
function bundlePath() {
  return process.env.CAPWIZARD_UPDATE_TARGET || path.resolve(process.execPath, "..", "..", "..");
}

function canInstall() {
  if (!app.isPackaged && !process.env.CAPWIZARD_UPDATE_FEED) return "Questa è la versione di sviluppo: si aggiorna con git pull.";
  const b = bundlePath();
  try {
    fs.accessSync(path.dirname(b), fs.constants.W_OK);
    fs.accessSync(b, fs.constants.W_OK);
  } catch (_) {
    return `Non posso sostituire l'app in ${path.dirname(b)}: scarica la nuova versione dalla pagina della release.`;
  }
  if (b.startsWith("/Volumes/")) return "L'app è aperta dal DMG: trascinala in Applicazioni, poi potrà aggiornarsi da sola.";
  return "";
}

// ---------------------------------------------------------------------------
// check, download, install
// ---------------------------------------------------------------------------

async function check() {
  if (["scarico", "pronto"].includes(state.fase)) return state;
  update({ fase: "controllo", errore: "" });
  try {
    const res = await net.fetch(FEED, { headers: { Accept: "application/vnd.github+json", "User-Agent": "CapWizard" } });
    if (!res.ok) throw new Error(`GitHub risponde ${res.status}`);
    const arch = process.arch === "x64" ? "x64" : "arm64";
    const best = (await res.json())
      .filter((r) => !r.draft && compare(r.tag_name, state.versione) > 0)
      .map((r) => {
        const v = r.tag_name.replace(/^v/, "");
        const zip = r.assets.find((a) => a.name === `CapWizard-${v}-${arch}.zip`);
        const sum = r.assets.find((a) => a.name === `CapWizard-${v}-${arch}.zip.sha256`);
        return { versione: v, titolo: r.name || r.tag_name, note: r.body || "", pagina: r.html_url,
                 zip: zip && zip.browser_download_url, peso: zip ? zip.size : 0, sha: sum && sum.browser_download_url };
      })
      .sort((a, b) => compare(b.versione, a.versione))[0] || null;
    update({ fase: best ? "disponibile" : "aggiornato", nuova: best, controllato: new Date().toISOString() });
    if (best && state.modo === "automatico" && best.zip && best.sha && !canInstall()) download();
  } catch (e) {
    update({ fase: "errore", errore: `Non riesco a controllare gli aggiornamenti: ${e.message}` });
  }
  return state;
}

async function download() {
  const r = state.nuova;
  if (!r || state.fase === "scarico" || state.fase === "pronto") return state;
  const why = canInstall() || (!r.zip || !r.sha ? "Questa release non ha il file per l'aggiornamento automatico: scaricala dalla sua pagina." : "");
  if (why) { update({ fase: "errore", errore: why }); return state; }
  update({ fase: "scarico", pct: 0, errore: "" });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "capwizard-update-"));
  try {
    const want = (await (await net.fetch(r.sha)).text()).trim().split(/\s+/)[0].toLowerCase();
    const res = await net.fetch(r.zip);
    if (!res.ok || !res.body) throw new Error(`download non riuscito (${res.status})`);
    const zip = path.join(dir, "update.zip");
    const out = fs.createWriteStream(zip);
    const hash = crypto.createHash("sha256");
    const total = Number(res.headers.get("content-length")) || r.peso || 1;
    let got = 0;
    for await (const chunk of res.body) {
      hash.update(chunk);
      if (!out.write(chunk)) await new Promise((ok) => out.once("drain", ok));
      got += chunk.length;
      update({ pct: Math.min(99, Math.round(got * 100 / total)) });
    }
    await new Promise((ok, ko) => out.end((e) => (e ? ko(e) : ok())));
    if (hash.digest("hex") !== want) throw new Error("il file scaricato non corrisponde alla release (checksum diverso)");
    execFileSync("ditto", ["-x", "-k", zip, path.join(dir, "app")]);
    const newApp = path.join(dir, "app", "CapWizard.app");
    const plist = path.join(newApp, "Contents", "Info.plist");
    const version = execFileSync("plutil", ["-extract", "CFBundleShortVersionString", "raw", plist], { encoding: "utf8" }).trim();
    if (version !== r.versione) throw new Error(`nel pacchetto c'è la ${version}, non la ${r.versione}`);
    execFileSync("codesign", ["--verify", "--deep", newApp]);      // nothing changed in the bundle since it was built
    fs.unlinkSync(zip);
    ready = { app: newApp, version };
    update({ fase: "pronto", pct: 100 });
  } catch (e) {
    fs.rmSync(dir, { recursive: true, force: true });
    update({ fase: "errore", errore: `Aggiornamento non riuscito: ${e.message}` });
  }
  return state;
}

// swap the bundle once this process has quit; reopen = start the new version right away
function scheduleSwap(reopen) {
  if (!ready) return false;
  const target = bundlePath();
  const script = path.join(os.tmpdir(), `capwizard-swap-${process.pid}.sh`);
  fs.writeFileSync(script, `#!/bin/sh
while kill -0 ${process.pid} 2>/dev/null; do sleep 0.3; done
T=${JSON.stringify(target)}; N=${JSON.stringify(ready.app)}
rm -rf "$T.old"
if mv "$T" "$T.old" && mv "$N" "$T"; then rm -rf "$T.old"; else [ -d "$T.old" ] && [ ! -d "$T" ] && mv "$T.old" "$T"; fi
xattr -dr com.apple.quarantine "$T" 2>/dev/null
${reopen ? 'open "$T"' : ""}
rm -f "$0"
`);
  spawn("/bin/sh", [script], { detached: true, stdio: "ignore" }).unref();
  ready = null;
  return true;
}

function installNow() {
  if (!scheduleSwap(true)) return false;
  app.quit();
  return true;
}

// ---------------------------------------------------------------------------

function start(onState) {
  send = onState;
  state.versione = process.env.CAPWIZARD_UPDATE_FROM || app.getVersion();
  state.modo = loadMode();
  // whatever has been downloaded (by itself, or because the user said «Aggiorna») goes in when the app quits
  app.on("before-quit", () => { if (ready) scheduleSwap(false); });
  if (!app.isPackaged && !process.env.CAPWIZARD_UPDATE_FEED) {
    update({ fase: "sviluppo" });
    return;
  }
  setTimeout(check, 15000);
  setInterval(check, EVERY);
}

module.exports = { start, check, download, installNow, setMode, getState: () => ({ ...state }), compare };
