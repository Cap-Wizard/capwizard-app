---
name: reel-capcut-mac
metadata:
  version: "0.2.0-alpha.2"
description: Monta reel verticali (Instagram/TikTok) in CapCut direttamente sul Mac di Nicola con Claude Code - analisi delle clip con Whisper sul chip Apple, tagli secondo le sue regole, progetto CapCut generato dal template del cliente, b-roll Magnific, profilo del cliente e lezioni. Usala quando Nicola chiede di montare reel da una cartella di clip o di correggere un reel già montato (anche da screenshot della chat del cliente).
---

# Reel verticali in CapCut, da Claude Code sul Mac

Rispondi sempre in italiano. L'utente è Nicola (Nik), videomaker che produce reel brevi per
clienti in CapCut desktop per Mac, con un progetto-template per ogni cliente. Ogni cliente ha
un **profilo** (vedi «Il profilo del cliente»): leggilo sempre, prevale su questa skill dove è
più preciso.

Questa skill è il motore di CapWizard e funziona uguale in due modi:
- **da terminale** (Nicola scrive in chat): chiedi solo ciò che è indispensabile (cartella
  delle clip e cliente, se non si capiscono), il resto lo decidi tu;
- **dall'app CapWizard** (il messaggio dice «Lavori in automatico dall'app CapWizard»): non
  fare domande; l'app ti dà i dati del lavoro, fa lei copia di sicurezza, registro e storico,
  e ti chiede di scrivere un `risultato.json`. Segui il suo messaggio dove è più preciso.

Il montaggio lo fai **tu**: analizzi le clip, decidi ogni taglio con le regole di Nicola e
generi il progetto CapCut dal template. Ogni scelta ha un motivo, che riporti a Nicola.
Il video montato non lo vedi: ti fidi solo di audio, trascrizioni e fotogrammi.

Gli script sono in `scripts/` accanto a questo file: `SKILL_DIR` è la «Base directory» che
Claude Code indica quando carica la skill (di solito `~/.claude/skills/reel-capcut-mac`).

## 0. Preparazione (una volta sola)
```bash
bash "$SKILL_DIR/scripts/installa.sh"     # ffmpeg (Homebrew), mlx-whisper, modello medium
```
- Whisper gira sul chip Apple con `mlx-whisper` (modello `mlx-community/whisper-medium-mlx`,
  cambiabile con la variabile `REEL_WHISPER`); se manca usa `faster-whisper` su CPU.
- L'audio di ogni clip viene estratto una volta e tenuto in cache (`~/.cache/reel-capcut`).
- Cartelle: clip dove dice Nicola; progetti CapCut in
  `~/Movies/CapCut/User Data/Projects/com.lveditor.draft`.
- Lavori lunghi: lanciali in background e intanto prosegui (es. b-roll mentre trascrivi).
- Magnific (b-roll) deve essere collegato a Claude Code come server MCP; se non c'è, dillo e
  prosegui senza b-roll.

## Il profilo del cliente
È una skill con le regole di un solo cliente: `~/.claude/skills/reel-<cliente>/SKILL.md`
(es. `reel-chirone`, `reel-andrea-leidi`). Ci sono anche le copie sincronizzate da claude.ai in
`~/.claude/skills/synced/*/*/SKILL.md`: se esistono entrambe vale quella locale.
- Si riconosce dall'intestazione: `metadata: capwizard-template: <TEMPLATE>` (e
  `capwizard-cliente`, `capwizard-colore`), oppure è una skill `reel-*` che nomina un template.
- Trovalo dal nome del cliente detto da Nicola o dal template; se non c'è, chiedi il template
  (da terminale) e lavora senza profilo.
- Leggilo **tutto** prima di analizzare le clip: kit e misure, volumi, girato tipico, b-roll,
  sezione «## Lezioni».
- Per scriverci (lezioni) usa sempre la copia locale: se il profilo è solo in `synced/`, prima
  copia la sua cartella in `~/.claude/skills/<nome>` (altrimenti una sincronizzazione la perde).

## Cartelle di CapWizard
Tutto in `~/Library/Application Support/CapWizard` (le stesse dell'app):
- `lavori/<nome progetto> <MMGG-HHMM>/` = la tua `<lavoro>`: analisi, config, piano, report;
- `backup/<cartella progetto> <MMGG-HHMMSS>/` = copie di sicurezza prima di una correzione;
- `projects.json` e `modifiche/` = registro e storico dell'app (scrivili solo con
  `scripts/registra.py`).

## 1. Raccogli le informazioni
Servono **cartella delle clip** e **cliente** (quindi il suo profilo e il **template** = nome
della sua cartella in `com.lveditor.draft`). Facoltativi: copione, durata, titolo, nome
progetto, note di Nicola (vincolanti).
- Il resto lo decidi tu. Nome progetto = titolo del copione o cartella del girato; non deve
  esistere già (« v2»). Più cartelle = più video: fai prima il primo, Nicola lo controlla, poi gli
  altri con le correzioni del primo.
- **Rileggi il template ogni volta**: Nicola lo aggiorna (volumi, titoli, suoni) anche a lavoro
  iniziato. Il template deve essere chiuso in CapCut (niente `.locked` nella cartella).
- Serve anche un **progetto modello**: un progetto vero di Nicola con parlato e una transizione
  Bagliore II (da lì si copia la struttura dei segmenti video). Cercalo tra i progetti recenti
  (traccia video `flag 0` con `attribute 0`, volume 1, materiale `transitions` «Bagliore II»).
- Copione su carta: i numeri a mano accanto alle parti sono di solito l'ordine di ripresa.
  Senza copione l'ordine delle clip (numeri) è di solito l'ordine del discorso.
- La durata si imposta solo se Nicola la chiede.

## 2. Analizza le clip
```bash
cd "$SKILL_DIR/scripts"
python3 analizza.py "<cartella clip>" "<lavoro>/<video>" --mosaico
```
Per ogni clip: durata reale, rotazione e come metterla in CapCut, parole con tempi e livello,
tratti di voce piena con trascrizione. Nel riassunto le parole **deboli** sono tra [parentesi]:
suggeritore, conti alla rovescia, voci lontane. Apri `mosaico.jpg` con Read.

### Voce di chi parla e suggeritore
Dietro la camera spesso c'è chi **suggerisce la battuta** prima che venga detta, e Whisper la
trascrive: la frase c'è ma chi è in camera ha la bocca chiusa.
- Chi parla ha il microfono addosso: voce **forte** (circa −13…−28 dB); suggeritore e «3, 2, 1»
  più bassi (−30…−45). Usa solo i **tratti forti**.
- A volte il suggeritore è forte quasi quanto chi parla: lo schema è «dice / suggerisce /
  ripete». `controlla_tagli.py` segnala ripetizioni e tratti più bassi dentro uno spezzone:
  tieni la ripetizione di chi parla.
- Con chi parla piano la soglia taglia parole morbide: usa i tratti per riconoscere le take e
  fissa inizio e fine sulle parole trascritte.

### Rotazione (ogni clip, può cambiare nella stessa cartella)
- rotation 90 = normale; −90 = capovolta → `rot: 180`;
- nessun tag e file 1920×1080 = orizzontale con l'immagine girata → `rot: 270` e in
  `landscape` (materiale 1920×1080, scala ×1,778). Fai controllare il verso a Nicola.
Verifica sempre su un fotogramma.

## 3. Leggi il template
Ricava il kit: titolo (spesso una **clip composta** con parola grande, riga piccola, emoji e
animazione di gruppo), sottotitoli (stile, max caratteri, maiuscolo), suoni e a cosa sono
abbinati, musica, filtri, transizione, **volumi**. Annota testo di esempio, caratteri, scala e
posizione di ogni testo. I nomi del kit vanno nel config (`kit`, vedi sotto).

**Template creati dall'app** («TEMPLATE <CLIENTE>», nella nota del cliente «creato dall'app con lo
schema universale»): hanno sempre la stessa forma, quindi sai già dove sta ogni pezzo.
- 0,0-3,0 s **titolo**: emoji in alto, riga piccola bianca, riga grande nel colore del cliente.
- 3,2-5,2 s **titoletto**: una riga nel colore del cliente.
- 5,4-9,0 s **elenco**: tre voci che entrano a 5,4 / 6,0 / 6,6 s, ultima parola nel colore del cliente.
- 9,2-13,2 s **sottotitoli**: quattro da 1 s, bianchi con la parola chiave nel colore del cliente.
- **Musica** per tutta la durata, un **suono** sull'entrata di titolo, titoletto e ogni voce
  dell'elenco, **filtri/regolazione** per tutta la durata.
Le parole sono segnaposto: riscrivile sempre. Se Nicola ha modificato il template in CapCut,
vale quello che trovi (stessi tempi = stesso ruolo). Lo schema è in `src/capwizard_app/template_maker.py`.

## 4. Decidi il montaggio e scrivi il config
Applica le «Regole di montaggio». Scrivi `config.json` (formato sotto): spezzoni, pause da
togliere, transizioni, regia (titoletti, elenchi, coperture), emoji, correzioni dei nomi.

## 5. Controlla i tagli
```bash
python3 controlla_tagli.py config.json --applica --parole
```
Corregge le **code** (mai tagliare la fine della frase), segnala attacchi sporchi, parole perse
nelle pause tolte, voce più bassa (suggeritore?) e ripetizioni; salva le parole di ogni spezzone
per i sottotitoli. Risolvi ogni «!» prima di andare avanti e rilancia finché è pulito.

## 6. Costruisci il progetto
```bash
python3 costruisci_draft.py "<lavoro>/<video>/out" "NOME PROGETTO" config.json
mkdir "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/NOME PROGETTO"
cp "<lavoro>/<video>/out/draft_info.json" "<lavoro>/<video>/out/draft_meta_info.json" \
   "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/NOME PROGETTO/"
python3 livella_voce.py "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/NOME PROGETTO" --applica
python3 verifica.py "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/NOME PROGETTO" config.json
```
- `livella_voce.py` porta la voce di tutti gli spezzoni allo stesso livello (loudness LUFS per
  spezzone, obiettivo la mediana del progetto, scarti sotto 1 dB lasciati): tra una clip e
  l'altra la voce non deve mai saltare. Gli spezzoni «voce variabile dentro lo spezzone» vanno
  divisi o segnalati a Nicola nel «DA FARE A MANO».
- Solo i due file nella cartella nuova: **mai** copiare la cartella del template.
- `out/plan.json` ha il riepilogo (spezzoni, testi e scale, sottotitoli, suoni, emoji, b-roll):
  usalo per il piano e per il messaggio a Nicola.
- Se il progetto non compare nella home di CapCut: Nicola deve chiudere CapCut (Cmd+Q) e
  riaprirlo. Non toccare `root_meta_info.json`.

## 7. Report, registro e messaggio a Nicola
- Scrivi `<lavoro>/report.md` con queste sezioni (l'app le legge):
  ```markdown
  # NOME PROGETTO

  Cliente: <cliente> · template «<TEMPLATE>» · durata <secondi> s

  ## Cosa ha fatto Claude
  <5-8 righe: cosa hai tolto, struttura, titoli, elenchi, b-roll>

  ## DA FARE A MANO IN CAPCUT
  - <una riga per cosa> (oppure «- niente»)
  ```
- Da terminale registra il progetto nell'app (dall'app no, lo fa lei):
  ```bash
  python3 registra.py nuovo "<cartella progetto>" --nome "NOME PROGETTO" --template "TEMPLATE" \
      --cliente <skill del profilo> --clip "<cartella clip>" --report "<lavoro>/report.md"
  ```
- Messaggio a Nicola, breve: progetto, durata, cosa hai tolto, struttura, titoli, elenchi,
  b-roll, cosa va guardato (clip ruotate, take dubbie, testi lunghi), dove sta
  `piano-montaggio.md` (accanto alle clip). Distingui ciò che hai verificato da ciò che va
  guardato. **Se CapCut chiede di ricollegare le clip parlate, prima di farlo deve dirtelo** (i
  b-roll si possono ricollegare liberamente).

## 8. Dopo che Nicola l'ha aperto: correzioni
Le richieste arrivano da Nicola o dal cliente, spesso come **screenshot della chat**: guardali
tutti con Read. Si corregge **lo stesso progetto** (niente copie «v2»).
1. Il progetto deve essere chiuso in CapCut (niente `.locked`).
2. **Sempre**, dopo la prima apertura: `python3 verifica.py <progetto> config.json`. CapCut a
   volte riporta a 0 l'inizio di alcuni spezzoni (tornano i «3, 2, 1», clip «silenziose»): con
   `--correggi` li rimette giusti in tutte le copie.
3. Se Nicola dice di aver sistemato lui, non toccare il progetto: salva solo la lezione.
4. Copia di sicurezza (dall'app la fa lei):
   ```bash
   B="$HOME/Library/Application Support/CapWizard/backup/<cartella progetto> $(date +%m%d-%H%M%S)"
   rsync -a --exclude .locked "<progetto>/" "$B/"
   ```
5. Ritrova il lavoro del montaggio (`report_path` del progetto in `projects.json`, oppure la
   cartella più recente in `lavori/` con quel nome): config, piano e cosa avevi fatto.
   Confronta il draft con la tua versione per trovare i **ritocchi a mano** di Nicola (scale,
   volumi, testi) e conservali; se esprimono un criterio (es. tutti i Title impact a 0,151)
   mettili nel config (`vol`, `scale_override`) e applicali a tutto.
6. Applica **solo** ciò che è chiesto: non toccare tagli, testi, suoni o musica se non è
   richiesto. Se togli o sposti un taglio, risincronizza sottotitoli e suoni.
7. Dopo l'apertura esistono altre copie della timeline: ogni modifica va in `draft_info.json`,
   `.bak`, `template-2.tmp` e in `Timelines/<id>/` (stesso `id` del draft). Poi `verifica.py`.
   Se hai aggiunto, tolto o cambiato spezzoni parlati, o la voce «salta» (Nicola: «in certi
   punti è molto più alta»), rilancia `livella_voce.py <progetto>` e poi con `--applica`:
   tiene i volumi già messi come base e corregge solo gli scarti.
8. **Lezione nel profilo del cliente** (copia locale, vedi sopra): nella sezione «## Lezioni»
   (creala in fondo se manca) aggiungi `- GG/MM/AAAA: <cosa voleva e come l'hai risolto>`. Se è
   una preferenza che vale anche per i prossimi video («meno suoni», «sottotitoli più in
   alto»), scrivila anche come regola nella sezione giusta del profilo e correggi le regole
   vecchie che contraddice. Non toccare l'intestazione tra i `---`. Una regola che vale per
   tutti i clienti va invece in questa skill.
9. Da terminale salva la correzione nello storico dell'app:
   ```bash
   python3 registra.py modifica "<progetto>" --testo "<cosa è stato chiesto>" \
       --riassunto "<cosa hai cambiato>" --lezione "<una riga>" [--immagini <screenshot>...]
   ```

## Cose da non fare
- Non sovrascrivere progetti esistenti, non modificare il template, non scrivere un progetto
  aperto in CapCut (`.locked`).
- Non dire che il video «va bene» se Nicola non l'ha visto.
- Non cancellare file di Nicola: sposta in una cartella nascosta e diglielo.

---

## Il file di configurazione (config.json)

```json
{
 "projects_root": "~/Movies/CapCut/User Data/Projects/com.lveditor.draft",
 "template": "TEMPLATE MARIO ROSSI",
 "modello": "Un progetto vero con parlato e Bagliore II",
 "clipdir": "~/Downloads/VIDEO/3/CLIP",
 "brolldir": "~/Downloads/VIDEO/3/BROLL",
 "ext": ".MP4",
 "kit": {"big_text": "PIANO", "small_text": "QUINTO", "music": "The Driving",
         "impact": "Title impact", "woosh": "swish", "pop": "pop!", "glitter": "Glitter",
         "transition": "Bagliore II", "big_scale": 2.0, "small_scale": 1.5513, "small_y": -0.375,
         "big_max": 9, "small_max": 17, "sub_ch": 23},
 "rot": {"C1422": 270, "C1456": 180},
 "landscape": ["C1422"],
 "cuts": [["C1404", 2.69, 5.10, "Il primo consiglio è semplice", []],
          ["C1405", 2.11, 4.64, "Qui da Mario Rossi trovi", [[3.75, 4.22]]]],
 "trans_after": [3, 7, 10],
 "auto_subs": true,
 "words": {},
 "gap_words": "drop",
 "fix": {"MARIO ROSI": "MARIO ROSSI"},
 "vol": {"Title impact": 0.151},
 "woosh_trans_vol": 0.251,
 "scale_override": {"10": 1.13},
 "emoji": [["🔍", 5, 2.40, 1]],
 "regia": "titoletto('IL PRIMO','CONSIGLIO','💡',0,seg_end(1),'Titolo')\n..."
}
```
- `kit`: testi di esempio e nomi dei suoni del template del cliente (si cercano per inizio del
  nome), misure fisse dei titoletti, caratteri massimi.
- `cuts`: `[clip, da, a, testo detto, pause_da_togliere]` in secondi della clip; le pause sono
  `[inizio, fine]` dentro lo spezzone. Numerazione degli spezzoni da 1, nell'ordine.
- `trans_after`: spezzoni da cui si esce con transizione + zoom 100→115% nell'ultimo secondo +
  woosh.
- `clipdur`: facoltativo (lo script legge la durata reale dei file).
- `words`: parole per spezzone (le scrive `controlla_tagli.py --parole`), servono a
  `auto_subs` per generare i sottotitoli (righe ≤ `sub_ch`, bilanciate, mai sopra titoli ed
  elenchi). `fix`: correzioni delle parole storpiate da Whisper (anche più parole → più parole).
  `gap_words: "shift"` sposta le parole cadute in una pausa tolta all'inizio del pezzo dopo
  (default `drop` le scarta).
- `emoji`: `[emoji, spezzone, secondo nella clip, lato]`: emoji libere centrate sopra i
  sottotitoli; saltate se cade un titoletto.
- `regia`: righe Python eseguite dallo script con queste funzioni:
  - `tl(n, s)` → tempo in timeline del secondo `s` della clip dello spezzone `n`;
    `seg_start(n)`, `seg_end(n)`, `TOTAL`;
  - `titoletto(riga_piccola, parola_grande, emoji, inizio, fine, etichetta)`: clona la clip
    composta del titolo (la prima etichetta 'Titolo' parte da 0);
  - `elenco([(voce, inizio), ...], fine, etichetta)`: voci impilate, una quando viene detta;
  - `sub(n, testo, da, a)`: sottotitolo a mano (se non usi `auto_subs`);
  - `copertura(file_broll, inizio, fine, da_nel_file)`: b-roll sopra il parlato, muto.

---

## Regole di montaggio

### 1. Ogni scelta ha un motivo
Scritto nel piano. In fondo sempre «DA FARE A MANO IN CAPCUT».

### 2. La regia viene prima dei tagli
Fonti: copione, poi parlato e immagini, poi template (da cui prendi la costruzione, mai il
contenuto). Struttura: hook → sviluppo → call to action.

### 3. Il copione
Indicazioni di regia vincolanti; sovraimpressioni del copione = testi del kit (adattale il meno
possibile e segnalalo); sottotitoli = ciò che viene **detto**; durata del copione e «Caption»
non contano.

### 4. Cosa tagliare nel parlato
- Dietro le quinte: «vai», «3, 2, 1», «no aspetta», «ok», «rifacciamo», domande a chi riprende,
  commenti, imprecazioni, **voce del suggeritore**, silenzio dopo il conto alla rovescia.
- Retake: la versione migliore, di solito l'ultima; se subito dopo dice di aver sbagliato,
  segnala la take. Frasi senza senso in un reel autonomo («come detto ora»): toglile e dillo.
- Intercalari (ehm, uhm). Cioè/tipo/diciamo solo se richiesto.
- Pause oltre 0,30-0,35s dentro la frase: via, ma solo dove è davvero silenzio (nessuna parola
  persa: lo controlla `controlla_tagli.py`).
- **Attacco** sulla prima sillaba della voce piena, 0,05-0,1s prima.
- **Coda: mai tagliare la fine della frase.** Dopo l'ultima parola + 0,08s, allungando finché la
  voce scende al rumore di fondo; stop alla prima pausa vera (≥ 0,2s). Le ultime sillabe sono
  più deboli: una soglia fissa le taglia (errore già fatto più volte).

### 5. B-roll
- **Copertura** (preferita): sopra chi parla, audio del parlato sotto, sulla parola che nomina la
  cosa, 1,5-4s, mai nei primi 2s né sulla call to action, tra due coperture si rivede il volto.
- **Inserto** a tutto schermo solo se il copione lo chiede, 1-1,2s.
- **B-roll Magnific** quando il girato è solo parlato: `kling-25`, `1080p`, `9:16`, 5s; prompt in
  inglese con ambiente e colori del cliente; negativePrompt «text, logos, watermark, distorted
  faces, extra fingers, dark lighting». 3-5 per video sugli argomenti specifici. Lanciali appena
  hai la trascrizione (girano mentre monti). Scarica con curl in `<cartella video>/BROLL/
  BRn_tema_AI.mp4`, guarda 5 fotogrammi per clip e **scarta quelli venuti male**. Prompt
  rifiutato («risk control»): riformula più neutro. Escono colorati e a 24fps (togliere la LUT).
- Durata del materiale = durata reale del file (lo fa lo script): con una diversa CapCut non
  trova i b-roll.
- Cambiare una ripresa con l'IA (Higgsfield): fotogramma → `nano_banana_2` (2 varianti, prompt
  preciso su quale elemento) → `kling3_0_turbo` 9:16 5s → upscale → controllo → MP4 accanto
  alle clip.

### 6. Sottotitoli
Pacchetto del template, stesso maiuscolo e punteggiatura, righe bilanciate ≤ max caratteri,
mai mentre c'è un titolo o un elenco.
- **Preset sottotitoli di CapCut** (testo dentro un `text_templates` di tipo subtitle, es.
  «短文本边框弹出Ⅰ», box colore cliente che salta sulla parola detta): clona wrapper, testo e
  riferimenti (effetto + animazione) come in `costruisci_draft.caption`, con `words` = tempo vero di
  ogni parola. Un sottotitolo per gruppo di 2-4 parole: **mai** il karaoke finto fatto con più
  segmenti uguali con una parola colorata ciascuno.

### 7. Testi a schermo
- Titolo d'apertura sempre: dice subito di cosa si parla e cosa lo rende speciale.
- **Titolo in più clip composte** una dopo l'altra (es. prima il nome dell'attività, poi il gancio
  con un'emoji per lato), ognuna con la sua animazione di gruppo (Zoom 1, Shake 3…): rifai la
  stessa sequenza del template con le stesse animazioni e cambia solo le parole. CTA e chiusura
  (📍 + nome) in clip composta vanno riusate allo stesso modo.
- **Titoletti con box pieno** per i dati da leggere (orari, menù, nome del titolare): mentre ci
  sono prendono il posto del sottotitolo.
- Titoletti (riga piccola + parola grande + emoji) circa ogni 5-6s sulle frasi chiave; misure
  fisse; testo lungo → accorcia, non rimpicciolire.
- **Elenchi detti a voce sempre a schermo**, una voce quando viene detta; più di 4 voci → due
  blocchi. Segnala a Nicola quali elenchi hai messo.
- Emoji: nei titoletti centrata poco sopra la riga piccola; quelle libere centrate sopra i
  sottotitoli, mai sopra un titoletto.
- Controllo dimensioni: testo con più del 90% dei caratteri dell'esempio →
  `scala = scala_template × 0,9 × car_template / car_nuovi`; se Nicola ha ridimensionato a mano,
  `scala = scala_sua × car_vecchi / car_nuovi`.

### 8. Suoni e musica
Title impact + woosh sui titoletti, magic reveal a inizio elenco, pop sulle voci, woosh 0,2s
prima delle transizioni; **volumi del template** (e quelli corretti da Nicola); almeno 1s tra due
suoni; musica del template tagliata alla durata.
**Mappa suoni del template**: ripeti quale suono va su quale momento (entrata del titolo, gancio,
transizione, titoletto, nome, CTA, chiusura) con gli stessi nomi e volumi; la mappa del cliente o
dell'agenzia è anche nella nota «Stile …» del vault. I brani della libreria lunghi pochi secondi
(es. Bell ding) sono effetti, non musica. Musica udibile: circa 15 dB sotto la voce.
**Musica SEMPRE dalla libreria Musica di CapCut** (Audio > Music: materiale `type: "music"` con
`music_id` e file nella cache di CapCut). Mai mp3 scaricati (Download, Spotimate, YouTube) né brani
commerciali. Per cambiarla copia il materiale audio da un altro progetto che usa quel brano della libreria.

### 9. Zoom e transizioni
Se il template ha le sue transizioni (es. **Hot Flash**) usa quelle, solo sui cambi forti (fine del
titolo, prima della CTA), ognuna con il suo woosh; le regole qui sotto valgono quando non ne ha.
Inquadrature al **100%**: niente zoom alternati tipo 140% (le riprese sono già strette). A fine
frase zoom 100→115% nell'ultimo secondo + transizione (Bagliore II) + woosh; mai nei primi 2s,
almeno 2,5s tra due transizioni.

### 10. Colore ed effetti
Filtri ed effetti del template su tutto il video (le clip IA non sono in log).

### 11. Comunicazione
Fatti, cosa hai verificato, cosa va guardato, cosa resta da fare, un giudizio onesto.

---

## Note tecniche sul formato CapCut (Mac 9.x, non ufficiale)
- Progetto nuovo = cartella con solo `draft_info.json` e `draft_meta_info.json`.
- Materiali video: `media_path`, `unique_id`, `local_material_id` vuoti; `stable.stable_level 0`
  (lo stabilizzatore ereditato ritaglia l'immagine e fa ripartire gli spezzoni da 0); durata
  reale del file.
- Traccia principale `flag 0`, `attribute 0`; coperture su traccia `flag 2`, `attribute 1`,
  volume 0; titoli in clip composta su traccia `flag 2` sopra le coperture.
- Clip composta: materiale `drafts` con la bozza annidata in `draft`; per clonarla si
  sostituiscono tutti gli UUID della bozza, `combination_id` nuovo, id della bozza nei
  percorsi; il segnaposto `##_draftpath_placeholder_0E685133-…_##` resta uguale; la cartella
  `subdraft/` la ricrea CapCut.
- Tempi in microsecondi; keyframe in tempo della clip sorgente; `clip.transform.y` in mezzi
  canvas (più negativo = più in basso); `clip.rotation` in gradi.
- Transizione Bagliore II: effect_id 6952697917563998722, sul segmento da cui si esce.

## Problemi già incontrati
- Progetto che non si apre: cartella copiata dal template.
- Video scollegati: `media_path` copiati. B-roll non trovati: durata del materiale sbagliata.
- «3, 2, 1» ricomparsi / prime clip silenziose: inizi azzerati da CapCut → `verifica.py --correggi`.
- Immagine zoomata o ritagliata: stabilizzatore ereditato o zoom alternati.
- Clip capovolta o coricata: rotazione diversa o assente.
- Frasi del suggeritore nel video: voce bassa trascritta, o suggeritore forte quasi quanto chi
  parla.
- Fine frase tagliata: soglia di volume fissa sulla coda.
- Parola persa togliendo una pausa: parola detta piano dentro la «pausa».
- Progetto nuovo non in elenco: riavviare CapCut.
- Testo fuori schermo: riga più lunga dell'esempio alla stessa scala.
- File «._C0867.MP4»: nascosti di macOS, da saltare.
