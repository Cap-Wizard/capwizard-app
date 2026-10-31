// Which version of CapWizard this is, from the git history: «v1.<commits on main>». Everyone who has
// the same commit of GitHub sees the same number; every new commit pushed raises it by one. A build
// with changes not yet on GitHub gets a «+». The packaged app reads what after-pack wrote at build time.
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

function fromGit(dir) {
  const git = (...a) => execFileSync("git", ["-C", dir, ...a], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  try {
    const count = Number(git("rev-list", "--count", "HEAD"));
    let ahead = 0;
    try { ahead = Number(git("rev-list", "--count", "@{u}..HEAD")); } catch (_) { /* no upstream: nothing to compare */ }
    const dirty = git("status", "--porcelain", "--untracked-files=no") !== "";
    return { versione: `v1.${count}`, commit: git("rev-parse", "--short", "HEAD"), data: git("log", "-1", "--format=%cI"),
             locale: dirty || ahead > 0 };
  } catch (_) {
    return null;
  }
}

const REPO = path.join(__dirname, "..", "..");
const FILE = "build-info.json";

// in the app: the file written at build time; while developing: git, live
function buildInfo(app) {
  if (app && app.isPackaged) {
    try { return JSON.parse(fs.readFileSync(path.join(process.resourcesPath, FILE), "utf8")); } catch (_) { return null; }
  }
  return fromGit(REPO);
}

module.exports = { fromGit, buildInfo, REPO, FILE };
