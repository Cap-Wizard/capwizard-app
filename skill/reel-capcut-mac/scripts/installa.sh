#!/bin/bash
# Prepara il Mac per la skill reel-capcut-mac (una volta sola).
set -e
if ! command -v brew >/dev/null; then
  echo "Serve Homebrew: https://brew.sh"; exit 1
fi
command -v ffmpeg >/dev/null || brew install ffmpeg
python3 -m pip install --user --upgrade mlx-whisper numpy
# scarica il modello medium la prima volta (circa 1,5 GB)
python3 - <<'EOF'
import numpy as np, mlx_whisper
mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32), path_or_hf_repo="mlx-community/whisper-medium-mlx", language="it")
print("mlx-whisper medium pronto")
EOF
