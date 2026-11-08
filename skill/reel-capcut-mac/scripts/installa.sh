#!/bin/bash
# Prepara il Mac per la skill reel-capcut-mac (una volta sola).
set -e
if ! command -v brew >/dev/null; then
  echo "Serve Homebrew: https://brew.sh"; exit 1
fi
command -v ffmpeg >/dev/null || brew install ffmpeg
python3 -m pip install --user --upgrade numpy
# trascrizione: Voz di Desert Ant (dal 07/11/2026, al posto di mlx-whisper); scarica il modello (~320 MB)
bash "$(dirname "$0")/installa_voz.sh"
