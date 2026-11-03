#!/bin/bash
# Builds CapWizard and publishes it as a GitHub release, which the installed apps update from
# (electron/updater.js looks for CapWizard-<version>-arm64.zip and its .sha256 among the assets).
#
#   scripts/release.sh note.md      version from package.json, tag v<version>, pre-release if it has a «-»
#   SKIP_BUILD=1 scripts/release.sh note.md    publish the build already in dist/ (after trying it)
set -e
cd "$(dirname "$0")/.."
NOTES="$1"
[ -f "$NOTES" ] || { echo "uso: scripts/release.sh <note della release.md>"; exit 1; }
VER=$(node -p 'require("./package.json").version')
[ -z "$(git status --porcelain)" ] || { echo "Ci sono modifiche non committate: committa prima di fare la release."; exit 1; }
git rev-parse -q --verify "refs/tags/v$VER" >/dev/null && { echo "Esiste già il tag v$VER: alza la versione in package.json."; exit 1; }
[ -n "$SKIP_BUILD" ] || npm run dist
ZIP="dist/CapWizard-$VER-arm64.zip"
DMG="dist/CapWizard-$VER-arm64.dmg"
[ -f "$ZIP" ] && [ -f "$DMG" ] || { echo "Mancano $DMG o $ZIP: lancia npm run dist."; exit 1; }
shasum -a 256 "$ZIP" | cut -d' ' -f1 > "$ZIP.sha256"
git tag -a "v$VER" -m "CapWizard $VER"
git push origin HEAD "v$VER"
PRE=""; [[ "$VER" == *-* ]] && PRE="--prerelease"
gh release create "v$VER" $PRE --title "CapWizard $VER" --notes-file "$NOTES" "$DMG" "$ZIP" "$ZIP.sha256"
