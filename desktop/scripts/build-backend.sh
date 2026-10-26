#!/bin/bash
# Builds the Python backend as one binary (PyInstaller) into desktop/backend-dist/,
# which electron-builder ships in the app as resources/backend.
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
"$PY" -m pip install -q -e "$ROOT" pyinstaller
cd "$ROOT/desktop"
rm -rf backend-dist pyi-build
"$PY" -m PyInstaller --noconfirm --onefile --name capwizard-server \
  --distpath backend-dist --workpath pyi-build --specpath pyi-build \
  --collect-submodules uvicorn --collect-submodules capwizard_app \
  --add-data "$ROOT/src/capwizard_app/tools:capwizard_app/tools" \
  "$ROOT/src/capwizard_app/server.py"
rm -rf pyi-build
echo "backend: $ROOT/desktop/backend-dist/capwizard-server"
