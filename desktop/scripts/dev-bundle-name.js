// In development `npm start` runs the app inside node_modules' generic Electron.app, so macOS
// calls it "Electron" in the menu bar, the Dock and ⌘-Tab. This turns that bundle into
// CapWizard.app — the Dock takes the name from the folder, the menu bar from Info.plist — with
// CapWizard's icon, points the electron package at it (path.txt), and signs it ad hoc again: on
// Apple Silicon a bundle whose Info.plist no longer matches its signature isn't allowed to start.
// Runs after `npm install` (which may bring back a fresh Electron.app). The packaged app is named
// by electron-builder instead.
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

if (process.platform !== "darwin") process.exit(0);
const root = path.join(__dirname, "..");
const pkg = path.join(root, "node_modules", "electron");
const fresh = path.join(pkg, "dist", "Electron.app");
const bundle = path.join(pkg, "dist", "CapWizard.app");
const lsregister = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister";

if (fs.existsSync(fresh)) {
  fs.rmSync(bundle, { recursive: true, force: true });
  fs.renameSync(fresh, bundle);
  try { execFileSync(lsregister, ["-u", fresh], { stdio: "ignore" }); } catch (_) {}
}
const plist = path.join(bundle, "Contents", "Info.plist");
if (!fs.existsSync(plist)) process.exit(0);
fs.writeFileSync(path.join(pkg, "path.txt"), "CapWizard.app/Contents/MacOS/Electron");

for (const key of ["CFBundleName", "CFBundleDisplayName"]) {
  execFileSync("plutil", ["-replace", key, "-string", "CapWizard", plist]);
}
const icon = path.join(root, "build", "icon.icns");
if (fs.existsSync(icon)) fs.copyFileSync(icon, path.join(bundle, "Contents", "Resources", "electron.icns"));
execFileSync("codesign", ["--force", "--deep", "--sign", "-", bundle], { stdio: "ignore" });
// make Finder / the Dock read the new name and icon instead of their cached ones
execFileSync(lsregister, ["-f", bundle], { stdio: "ignore" });
console.log("Electron di sviluppo: ora è CapWizard.app");
