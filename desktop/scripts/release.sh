#!/bin/bash
# Builds CapWizard and publishes it as a GitHub release, which the installed apps update from
# (electron/updater.js looks for CapWizard-<version>-arm64.zip and its .sha256 among the assets).
#
#   scripts/release.sh note.md      version from package.json, tag v<version>, pre-release if it has a «-»
set -e
cd "$(dirname "$0")/.."
NOTES="$1"
[ -f "$NOTES" ] || { echo "uso: scripts/release.sh <note della release.md>"; exit 1; }
VER=$(node -p 'require("./package.json").version')
[ -z "$(git status --porcelain)" ] || { echo "Ci sono modifiche non committate: committa prima di fare la release."; exit 1; }
git rev-parse -q --verify "refs/tags/v$VER" >/dev/null && { echo "Esiste già il tag v$VER: alza la versione in package.json."; exit 1; }
npm run dist
ZIP="dist/CapWizard-$VER-arm64.zip"
DMG="dist/CapWizard-$VER-arm64.dmg"
shasum -a 256 "$ZIP" | cut -d' ' -f1 > "$ZIP.sha256"
git tag -a "v$VER" -m "CapWizard $VER"
git push origin HEAD "v$VER"
PRE=""; [[ "$VER" == *-* ]] && PRE="--prerelease"
gh release create "v$VER" $PRE --title "CapWizard $VER" --notes-file "$NOTES" "$DMG" "$ZIP" "$ZIP.sha256"
