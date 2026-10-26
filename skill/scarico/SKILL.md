---
name: "scarico"
description: "Scarico del girato per i clienti Kora: rinomina le clip (CODICE - 0001), nomina le cartelle Shooting/B-Roll e smista le clip nei video del PDP tramite clip nere e trascrizione."
---

# Scarico Kora

Procedura per scaricare e organizzare il girato di uno shooting per un cliente dell'agenzia Kora, seguendo il PDF "Gestione dei file per i videomaker" (LEGGI QUI PRIMA DELLO SCARICO).

Funziona al meglio in Claude Code sul Mac di Nik, dove l'SSD (`/Volumes/Extreme SSD/...`) e ffmpeg sono accessibili direttamente. Se lavori da una sessione cloud e il terminale del device non vede la cartella, non provare a caricare decine di GB: di' a Nik di lanciarla da Claude Code nel Terminale.

## Regole di nomenclatura Kora

- **Clip**: `CODICE - n° sequenziale` → `GISE - 0001.MP4` (4 cifre, estensione originale mantenuta).
- **Cartella shooting**: `CODICE - n° Shooting - MMAA` → `GISE - 10° Shooting - 0526`.
- **Cartella B-Roll**: `B-Roll - CODICE - n°S` → `B-Roll - GISE - 10°S`.
- **Struttura in `_KORA`**:
  ```
  _KORA/
    GIMO - [Ginkgo Bergamo]/
      _B-Roll - GIMO/
      GIMO - [Shooting]/
        GIMO - 1° Shooting - 0325/
        GIMO - 2° Shooting - 0325/
  ```

## Codici cliente

| Codice | Cliente |
|---|---|
| FOUP | Fox Group |
| GIMO | Ginkgo Bergamo |
| GINA | Ginkgo Verona |
| GISE | Giappugliese |
| GISY | Gipsy |
| GOGE | Golden Village |
| INTE | Inari Garbagnate |
| INTU | Inari Cantù |
| PISA | Piazza dei Sapori |

Se il cliente non è in tabella, chiedi il codice a Nik. Non inventarlo.

## Procedura

### 1. Inventario
- Elenca tutte le clip (ricorsivamente se sono già divise in sottocartelle), ignorando i file nascosti / `._*`.
- Ordina per numero camera (es. `C2751.MP4` → 2751). Questo ordine è la base della numerazione.
- Annota il cliente (dal percorso, es. `X FRANK/GIPSY/...`, o chiedi) e quindi il codice.

### 2. Rinomina clip
- Numerazione **unica e progressiva su tutto lo shooting**, in ordine di numero camera, anche se le clip sono in sottocartelle diverse (così i nomi non si duplicano quando si spostano).
- Le clip nere prendono anche loro un numero nella sequenza (es. 0030), così la numerazione segue esattamente l'ordine C.
- Prima di rinominare controlla che nessun nome di destinazione esista già; usa `mv -n` (o uno script Python con `os.rename` e un controllo `os.path.exists` prima di ogni mossa), non sovrascrivere e non cancellare niente. Rinomina e spostamento si possono fare in un solo passaggio, direttamente in `cartella titolo/CODICE - 0001.MP4`.
- Scrivi `_rinomina_log.csv` nella cartella dello shooting con le colonne: cartella, originale, nuovo, (titolo video se presente). Serve per annullare e per risalire al numero C.

### 3. Smistamento per PDP (quando Nik passa il PDP / piano editoriale)
Il PDP elenca i video con titolo e script. Una cartella per ogni video, col **nome uguale al titolo del video** nel PDP (senza il numero d'ordine, salvo diversa richiesta).

- **Leggere il PDP**: di solito è un Google Doc. Leggilo con il connettore Google Docs (`read_doc` con l'ID del link). Il JSON è grosso e finisce in un file: estrai il testo con un piccolo script Python che percorre `tabs[].documentTab.body.content` (paragrafi e tabelle) e salvalo in un .txt nello scratchpad.
- **Clip nere separatrici**: alla fine di ogni video Nik gira una clip nera molto corta. Nella pratica durano **0,5 s** (Sony, 10 bit). Trovale così:
  1. `ffprobe` su tutte le clip → candidate quelle sotto ~1 s (le clip vere sotto i 4 s sono comunque b-roll da 1,5–3,5 s, non nere).
  2. Conferma con `signalstats`: sulle clip a 10 bit lo YAVG di una nera è **~65–90** (il nero video a 10 bit è 64, non 0); quelle normali stanno sopra i 350.
  3. A volte la "nera" è l'obiettivo coperto con la mano (YAVG ~200): se dura 0,5 s e cade a cavallo fra due video, contala come separatrice ma segnalala.
  Le clip tra due nere, in ordine C, sono lo stesso video.
- **Separatrici mancanti**: capita che fra due video la nera non ci sia (es. due video brevi senza parlato girati di fila). Se un gruppo contiene due argomenti, cerca il punto di stacco guardando i frame (vedi sotto) e segnalalo come dubbio nella tabella.
- **Trascrizione**: sul Mac di Nik c'è già **`mlx_whisper`** (Python di sistema, `import mlx_whisper`) con i modelli in cache (`mlx-community/whisper-medium-mlx`, anche large-v3). Non installare altro. Scrivi uno script nello scratchpad che, per ogni clip: estrae il wav (`ffmpeg -vn -ac 1 -ar 16000`), chiama `mlx_whisper.transcribe(wav, path_or_hf_repo="mlx-community/whisper-medium-mlx", language="it", condition_on_previous_text=False)`, salva il testo in `tx/<clip>.txt`, cancella il wav e salta le clip già fatte (così si può riprendere). Lancialo **in background**: ~180 clip richiedono parecchi minuti. Quando Nik chiede se ha finito, controlla quanti .txt ci sono e se il processo è ancora vivo (`pgrep -fl`) prima di rispondere.
  - Whisper sulle clip mute o quasi mute inventa frasi tipo "Sottotitoli creati dalla comunità Amara.org", "Grazie per la visione" o testo in altre lingue: ignorale, vanno considerate senza parlato.
  - Nelle trascrizioni ci sono tanti "3, 2, 1, vai!" e ripetizioni dei take: è normale, cerca le frasi chiave.
- **Abbinamento**: confronta la trascrizione di ogni gruppo con le frasi chiave degli script (hook, nomi dei piatti, CTA). I video senza parlato (tipo "Quando scopri che…") si riconoscono per esclusione o dal contenuto: in quel caso estrai qualche frame (`ffmpeg -ss <t> -frames:v 1 -vf scale=-2:360`), affiancali in un'unica immagine con `hstack` e guardala (un solo Read invece di tanti).
- Le clip senza parlato (b-roll) restano nel gruppo in cui cadono.
- Le clip nere vanno in `_CLIP NERE`, non si cancellano. Se Nik dice di eliminarle, spostale nel **Cestino** con `/usr/bin/trash` (recuperabili), mai `rm`; diglielo, così il Cestino lo svuota lui.
- **Il girato può discostarsi dal PDP**:
  - un video girato con un contenuto diverso dallo script (es. "Fiorentina la sera" girato come "costata di manzo" perché la fiorentina non c'era) → segnalalo; se Nik conferma, la cartella prende il **titolo nuovo** in base a quello che è stato girato;
  - un video girato che nel PDP non c'è (es. "Dolci alla zucca") → cartella a parte, chiedi il nome a Nik;
  - un video del PDP che non trovi nel girato → elencalo come mancante.
- Se un gruppo non corrisponde a nessuno script, o un titolo sembra girato due volte, segnalalo come dubbio.

### 4. Conferma prima di toccare i file
Prima di rinominare o spostare qualsiasi file mostra a Nik una tabella:

| Gruppo | Titolo | Clip (C) | Nuovi nomi | Estratto trascrizione |

più l'elenco delle clip nere, dei casi dubbi (con la tua scelta di default, così Nik può dire solo "ok"), dei video fuori PDP e di quelli mancanti. Chiedi nello stesso messaggio anche il numero di shooting, se serve. Procedi solo dopo il suo ok; se corregge solo un punto, applica la correzione e richiedi l'ok prima di toccare i file.

### 5. Esecuzione
1. Rinomina (step 2).
2. Crea le cartelle per titolo e sposta le clip (step 3).
3. Se Nik indica il numero di shooting, rinomina (se dice di lasciarla com'è, es. `OTTOBRE`, non toccarla) la cartella dello shooting e quella dei B-Roll secondo le regole (mese/anno `MMAA` dalla data dello shooting).
4. Verifica finale: conta i file prima e dopo (devono coincidere) e mostra un riepilogo per cartella (primo → ultimo nome, numero di clip).

## Note
- Nik parla in italiano informale: rispondi così, in modo breve.
- Non cancellare mai girato. Niente sovrascritture. Al massimo Cestino, e solo se lo chiede Nik.
- File temporanei (wav, trascrizioni, frame, script) sempre nello scratchpad, mai sull'SSD accanto al girato.
- Il numero di shooting e la data non si indovinano: se servono e non li hai, chiedili.