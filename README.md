<p align="center"><img src="assets/logo.png" alt="CapWizard" width="220"></p>

# CapWizard

Generatore automatico di progetti CapCut per video social brevi.

> **Montaggio con Claude Code.** Il bottone «Genera» dell'app non usa più il motore
> Python: avvia il Claude Code del Mac (`claude -p`, con il login dell'utente) con la
> skill `reel-capcut-mac` e il profilo del cliente scelto. Claude analizza le clip,
> decide i tagli, scrive il progetto CapCut e lo verifica; l'app mostra i suoi messaggi
> e l'avanzamento a passi. Un **profilo cliente** è una skill `reel-<cliente>` (es.
> `reel-andrea-leidi`) che nomina il suo `TEMPLATE …`, oppure una skill con
> `metadata: capwizard-template: …`: lo stesso file vale nell'app e da solo in Claude Code.
> Vedi [agent.py](src/capwizard_app/agent.py). Il motore descritto sotto resta per la CLI.
Da una cartella di video genera un draft già pronto. **Il montaggio lo decide Claude**:
Whisper trascrive, poi l'AI sceglie ogni cosa e spiega perché.

- **guarda ogni clip** — dai fotogrammi, dall'audio e dalla luminosità misurata sul
  video capisce se è parlato rivolto al pubblico, b-roll o uno scarto; con un
  copione, a quale punto del copione corrisponde ogni ripresa («tapparelle su»,
  «luci accese»…) e in quale tratto l'azione accade davvero
- **regia** — prima di tagliare legge i testi: il copione, il parlato del girato e
  il video del template (i suoi sottotitoli, i testi a schermo, come usa i suoni).
  Da lì decide formato, tono, struttura e una consegna per ogni fase. Le
  indicazioni del copione sono **vincolanti**: quali riprese e in che ordine, la
  durata dei tagli («CUT da 0,5s» → ogni taglio dura 0,5s), la musica, i suoni, i
  testi a schermo. Ciò che il copione chiede e il materiale non ha (una ripresa
  mancante, l'audio del trend, un suono) finisce nell'elenco «DA FARE A MANO IN
  CAPCUT» in fondo al log
- **cosa tenere e cosa scartare** — dietro le quinte ("vai", "rifacciamo", "3 2 1"),
  frasi fuori tema, retake e ripetizioni (anche tra clip diverse), false partenze,
  filler, errori; legge insieme tutte le clip del progetto
- **b-roll** — se qualcuno parla, l'AI decide per ogni ripresa se va da sola a
  tutto schermo tra un blocco di parlato e l'altro (apertura, sequenze a tagli
  rapidi, stacchi) oppure sopra le frasi che illustra, su una traccia video muta;
  se non parla nessuno, costruisce un montaggio solo visivo (ordine, tratto e
  durata di ogni inquadratura). Parlato e b-roll possono stare nella stessa cartella
- **silenzi** — l'AI sceglie la soglia di pausa per il parlante e il ritmo; ogni
  silenzio tagliato viene registrato
- **testi** — titolo iniziale e testi di enfasi solo dove servono
- **suoni** — dai suoni del template, l'AI decide dove e se usarli (transizioni solo
  sui cambi di scena reali, enfasi solo sui momenti chiave) e se tenere la musica
- **kit del template** — i testi del template che non sono sottotitoli vengono letti a
  blocchi (titolo d'apertura, titoletto, elenco che entra voce per voce) insieme ai
  suoni che il template fa partire con ciascuno. A ogni video l'AI dice a cosa serve
  ogni blocco e lo riscrive, mantenendo font, colori, posizione e animazioni; colore
  ed effetti del template vengono estesi a tutto il video. Nell'app, sotto il menu
  del template, un riquadro mostra cosa contiene il kit e cosa manca, con la guida
  «Come si prepara un template»
- **fine frase** (di default, `--no-sentence-transitions` per toglierlo) — sulla clip che
  chiude una frase l'inquadratura si stringe nell'ultimo secondo (100% → 115%), poi
  una transizione porta alla clip dopo con il woosh del template. La transizione è
  quella del template o, se non ne ha, quella che usi di più nei tuoi progetti CapCut
- **zoom** (opzionale, `--ai-zoom` o l'interruttore nell'app) — zoom in / zoom out
  leggeri (6–18%) sulle frasi chiave, scelti dall'AI; nel draft sono keyframe di
  scala come quelli fatti a mano in CapCut, e continuano attraverso i tagli dei silenzi
- **recensione finale** del montato con voto e suggerimento
- **sottotitoli identici al template** — ogni sottotitolo è una copia completa di
  uno vero del template (modello di testo, animazione, effetto, font, posizione),
  con il suo testo e i suoi tempi parola per parola; anche lunghezza delle frasi,
  maiuscole e punteggiatura seguono quelli del template
- stabilizzazione opzionale

**Senza Claude collegato l'app non genera** (nessun ripiego automatico).
Serve il CLI `claude` loggato (`claude` → `/login`) oppure `ANTHROPIC_API_KEY`.

Ogni scelta viene scritta nel log con il motivo e salvata in un **report Markdown**
(con la trascrizione annotata) in `~/Library/Application Support/CapWizard/reports/`.

## Setup

Questo repository è l'**app** (interfaccia grafica + backend per Electron). Il
motore che monta i video è il pacchetto `capwizard`, nel repository
[Cap-Wizard/capwizard-mcp](https://github.com/Cap-Wizard/capwizard-mcp), e viene
installato come dipendenza.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
# per lavorare sul motore in parallelo, con il repo clonato accanto:
.venv/bin/pip install -e ../capwizard-mcp
```

Serve `ffmpeg`/`ffprobe` nel PATH (`brew install ffmpeg`).
Per la stabilizzazione di qualità superiore (opzionale): `brew install ffmpeg` con `vidstab`,
altrimenti il software usa il filtro `deshake` integrato.

## GUI

```bash
.venv/bin/capwizard-gui
```

Finestra a 4 tab:

| Tab | Cosa contiene |
|---|---|
| **Input** | Cartella o file video, nome progetto, template CapCut (default *CASA RIFUGIO*), script opz. |
| **Tagli e ritmo** | Pacing (normal/fast/aggressive), parole per sottotitolo, durata max sottotitolo |
| **Stile & effetti** | Switch: ridistribuisci SFX, aggiungi testi di enfasi (+ quantità), stabilizzazione |
| **Output** | Modello Whisper, lingua, formato verticale/orizzontale |

## CLI

```bash
# minimo
.venv/bin/capwizard /path/cartella --name "Reel 23"

# con template + ritmo serrato + durata target
.venv/bin/capwizard /path/cartella --name "Reel 23" \
  --template "COSA COMPRO CON 200K" --pacing fast --ai-target-duration 30

# modello Claude più accurato, copione di riferimento
.venv/bin/capwizard /path/cartella --name "Reel 23" \
  --template "COSA COMPRO CON 200K" --ai-model opus --script copione.docx
```

### Opzioni principali

- `--template, -t` — nome di un draft CapCut esistente. Effetti, overlay e stile sottotitoli sono ereditati; i suoni del template li gestisce l'AI.
- `--ai-model` — `sonnet` (default) / `opus` / `haiku`: il modello Claude che monta.
- `--ai-target-duration` — durata a cui l'AI deve puntare (default: la decide l'AI).
- `--pacing` — `normal` / `fast` (default) / `aggressive`: ritmo desiderato, passato all'AI.
- `--script` — copione (txt/md/docx): l'AI ne ricava la regia (struttura, indicazioni
  tra parentesi) e lo usa per capire cosa è fuori copione. Le durate scritte nel
  copione vengono ignorate: la durata la decidi solo tu con `--ai-target-duration`.
- `--no-fillers` / `--aggressive-fillers` — cosa chiedere all'AI sugli intercalari.
- `--no-ai-sounds` — lascia l'audio del template com'era, senza decisioni dell'AI.
- `--no-emphasis` / `--emphasis-count` — testi di enfasi (default max 4, li sceglie l'AI).
- `--intro-title` — titolo iniziale scelto da te (altrimenti lo sceglie l'AI).
- `--no-ai-review` — salta la recensione finale.
- `--stabilize` — stabilizza i video con vidstab/deshake (risultati in cache).
- `--manual-subtitles` — non scrivere i sottotitoli come quelli del template: la
  dimensione dei blocchi la danno `--subtitle-max-words` / `--subtitle-max-duration`
  (lo stile resta quello del template).
- `--model` — Whisper: `tiny|base|small|medium|large-v3` (default `large-v3`).
- `--language` — codice lingua (default `it`).

Tutto il resto del template (overlay video, filtri, regolazioni colore, text overlay non-sottotitoli) viene preservato; ciò che cade oltre la nuova durata del video viene troncato/scartato.

## App desktop (Electron) — nuova UI

La UI in-app è stata rifatta come **app desktop Electron** con backend Python.

```
desktop/                    # guscio Electron
├── electron/
│   ├── main.js             # crea la finestra, lancia il backend Python come sidecar
│   └── preload.js          # espone i dialog nativi al renderer
└── renderer/               # UI (HTML/CSS/JS, dark theme)
    ├── index.html
    ├── styles.css
    └── app.js              # parla col backend via REST + WebSocket
```

Il backend è un server **FastAPI** ([server.py](src/capwizard_app/server.py)) che avvolge
il pipeline esistente ed espone:
- `GET /api/health` · `GET /api/system` · `GET /api/templates`
- `POST /api/generate` → avvia un job, ritorna `job_id`
- `WS /api/ws/jobs/{job_id}` → stream dei log in tempo reale
- `GET /api/projects` · `GET /api/projects/{id}` · `DELETE /api/projects/{id}`
- `GET /api/ai/status` · `POST /api/ai/test`

### Modalità locale (uso privato, senza login)

In `desktop/renderer/app.js` c'è `const LOCAL_MODE = true;`: con `true` l'app
salta login e controllo abbonamento e parte diretta sulla Home — per uso privato
finché il backend cloud non è configurato. Mettilo a `false` per attivare il gate
SaaS.

### Modalità blocco (batch)

Nell'editor c'è il toggle **"Modalità blocco"**: scegli una **cartella madre** e
ogni sottocartella che contiene video diventa un progetto separato, tutti con lo
stesso template, stile sottotitoli e preferenze. Il nome di ogni progetto è il
nome della sottocartella (con un prefisso opzionale). Endpoint:
`GET /api/subfolders?path=…` (anteprima) e `POST /api/batch`.

### Schermate

- **Home** — griglia dei progetti creati dal programma (registro in
  `capwizard.registry` del motore, salvato in app-data). Ogni card ha
  *Apri* (in Finder/Explorer) e *Duplica*.
- **Duplica** — riapre l'editor pre-riempito con tutte le impostazioni del
  progetto: basta cambiare le clip / qualche preferenza e rigenerare.
- **Editor** — il form di generazione + console log in tempo reale.
- **Wizard AI** — procedura guidata (Stato → Setup → Test) che rileva il
  `claude` CLI o `ANTHROPIC_API_KEY`, spiega come configurarli e testa la
  connessione.

### Sviluppo

```bash
# 1. backend Python (una volta)
python3 -m venv .venv
.venv/bin/pip install -e .

# 2. app desktop
cd desktop
npm install
npm start        # lancia Electron, che a sua volta avvia il backend
```

In dev, `main.js` lancia il backend con l'interprete della `.venv`. Nella build
impacchettata userà un sidecar PyInstaller in `resources/backend/` (da fare).

### Distribuzione (TODO)

- Sidecar Python via **PyInstaller** (un binario per OS)
- `ffmpeg` bundlato per-OS; modello Whisper scaricato al primo avvio
- `electron-builder` → `.dmg` (Mac) + `.exe`/NSIS (Windows)
- Code signing: Apple Developer ID + notarization; certificato Windows
- Auto-update

## Architettura Python

```
src/capwizard_app/
├── server.py       # backend FastAPI per Electron (REST + WebSocket), avvolge capwizard.pipeline
└── gui.py          # vecchia GUI customtkinter
```

Il resto (pipeline, AI, trascrizione, scrittura dei draft CapCut, CLI `capwizard`)
è il motore in [Cap-Wizard/capwizard-mcp](https://github.com/Cap-Wizard/capwizard-mcp).

## Note di fragilità

Il formato `.draft` di CapCut non è ufficiale. Il template-mode è meno fragile
del from-scratch: clona lo schema da un draft esistente prodotto dalla tua
versione di CapCut. Se aggiorni CapCut e qualcosa rompe, di solito basta
ricreare il draft template nella nuova versione di CapCut e rilanciare.
