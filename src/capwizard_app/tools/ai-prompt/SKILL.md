---
name: ai-prompt
description: Come scrivere i prompt per i modelli AI della sezione AI di CapWizard (Kling, Veo, Nano Banana, Seedream, Bria, Beeble, Runway, Wan, Flux) e le lezioni imparate dai risultati del team. Usala ogni volta che scrivi un prompt per generare o modificare immagini e video, dentro o fuori da CapWizard.
---

# Prompt per i modelli AI

CapWizard la legge prima di ogni generazione: Claude guarda le immagini o il video, legge la
richiesta (spesso due parole in italiano) e scrive il prompt vero per il modello scelto. Dopo i
risultati (pollice su o giù, «Modifica») Claude aggiunge qui quello che ha imparato. La skill sta
nella repo condivisa `Cap-Wizard/video-edit-brain`: le lezioni di uno valgono per tutti.

## Come si scrive un prompt

- Sempre in inglese, frasi semplici e concrete. Niente elenchi di aggettivi («cinematic, 8k,
  masterpiece»): descrivi cosa si vede e cosa succede.
- Parti da quello che si vede davvero nelle immagini: luogo, materiali, luce, ora del giorno,
  persone. Non inventare cose che contraddicono le immagini.
- La richiesta dell'utente ha la precedenza: se dice «lento», «drone», «tramonto», va nel prompt.
- Video: un solo movimento di camera chiaro per tratta (direzione, velocità, altezza), poi cosa
  cambia nella scena. Dì cosa deve restare fermo e identico (architettura, volti, testo, loghi).
- Persone: chiedi di tenere volto, corpo, vestiti e labiale identici all'originale.
- Lunghezza: 40-120 parole per i video, 30-90 per le immagini. Oltre non aiuta.
- Scrivi solo il prompt: niente spiegazioni, niente virgolette intorno.

## Per tipo di lavoro

**Transizioni A, B to Z** (Kling 3.0, Veo 3.1, Kling 2.5 Turbo: primo e ultimo frame).
Il video deve partire esattamente dal frame A e arrivare esattamente al frame B. Descrivi il
percorso della camera che collega i due posti in modo fisicamente credibile (attraversa una porta,
sale sopra un tetto, gira un angolo) e cosa si vede a metà strada. Evita dissolvenze e morphing:
la camera si muove nello spazio. Il preset scelto (scorrimento, zoom, drone FPV) dà il tipo di
movimento; la nota della tratta dice cosa succede in quella tratta.

**Immagini da prompt** (Nano Banana Pro, Nano Banana 2, Seedream 4.5, Kling Omni Image).
Se c'è la foto di partenza, il prompt dice cosa cambiare e cosa tenere uguale. Le reference
vanno citate per ruolo («the person from the second image», «the style of the third image»).
Indica inquadratura, punto di vista e luce.

**Ambientazione personaggio** (Bria, Beeble SwitchX, Kling 3.0 Omni).
La persona resta identica, labiale compreso: il prompt descrive solo il posto e la luce. Bria
non usa il prompt. Beeble adatta la luce sulla persona: descrivi direzione e colore della luce
del posto. Con «Solo spunto» scrivi anche il prompt del posto vuoto (senza persone, ad altezza
d'occhi, come sfondo per chi parla in camera).

**Modifica** (Kling 3.0 Omni, Wan 2.7, Runway Aleph 2, Flux).
Dì solo cosa cambia e ripeti che tutto il resto (movimento di camera, persone, architettura)
resta com'è.

## Lezioni

Le lezioni di ogni modello sono in `modelli/<id del modello>.md` (per esempio
`modelli/kling-v3.md`). Leggi quella del modello scelto prima di scrivere. Qui sotto quelle che
valgono per tutti i modelli.

### Lezioni generali

(ancora nessuna)
