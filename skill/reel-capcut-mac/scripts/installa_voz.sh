#!/bin/bash
# Installa Voz (Desert Ant Labs) sul Mac: scarica la versione ufficiale da GitHub, controlla il checksum,
# la mette in ~/.local/share/desertant e crea i comandi desertant e da in ~/.local/bin. Poi scarica il modello (~320 MB).
set -euo pipefail
if command -v desertant >/dev/null 2>&1 || [ -x "$HOME/.local/bin/desertant" ]; then echo "Voz già installato"; exit 0; fi
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || { echo "Voz serve un Mac con chip Apple"; exit 1; }
B=https://github.com/Desert-Ant-Labs/desert-ant-cli/releases/latest/download
A=desertant-darwin-arm64.tar.gz
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
curl -fsSL -o "$T/$A" "$B/$A"; curl -fsSL -o "$T/sums" "$B/checksums.txt"
[ "$(grep " $A\$" "$T/sums" | cut -d' ' -f1)" = "$(shasum -a 256 "$T/$A" | cut -d' ' -f1)" ] || { echo "Checksum sbagliato: non installo"; exit 1; }
H="$HOME/.local/share/desertant"; mkdir -p "$H" "$HOME/.local/bin"
tar -xzf "$T/$A" -C "$H"
ln -sf "$H/desertant" "$HOME/.local/bin/desertant"; ln -sf "$H/desertant" "$HOME/.local/bin/da"
python3 -c "import wave;w=wave.open('$T/s.wav','wb');w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*16000)"
DESERTANT_NO_UPDATE_CHECK=1 "$H/desertant" voz "$T/s.wav" --quiet >/dev/null 2>&1 || true
echo "Voz pronto: $("$H/desertant" --version)"
