# reel-capcut-mac — installazione

1. Copia questa cartella in `~/.claude/skills/reel-capcut-mac`
   (da Terminale: `mkdir -p ~/.claude/skills && cp -R reel-capcut-mac ~/.claude/skills/`).
2. Apri Claude Code e scrivi: «installa la skill reel-capcut-mac» — eseguirà
   `scripts/installa.sh` (ffmpeg, mlx-whisper, modello medium ~1,5 GB).
3. Per i b-roll collega Magnific a Claude Code come server MCP.
4. Uso: «monta il reel della cartella ~/Downloads/VIDEO per Mario Rossi» (il cliente porta con sé
   profilo e template), oppure «correggi il reel X: <richiesta o screenshot>».
   Funziona uguale da terminale e dall'app CapWizard.
