// Without an Apple Developer certificate the app is not signed, and once
// electron-builder has changed the bundle Electron's own ad-hoc signature no
// longer matches: macOS would call the downloaded app "damaged". Sign the
// whole bundle ad hoc again, before the DMG is made.
const { execFileSync } = require("child_process");
const path = require("path");

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "darwin") return;
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  execFileSync("codesign", ["--force", "--deep", "--sign", "-", app], { stdio: "inherit" });
};
