// The words of the page, in Italian and in English.

import { createContext, useContext } from "react";

export const LANGS = ["it", "en"] as const;
export type Lang = (typeof LANGS)[number];

const it = {
  lang: "it" as Lang,
  title: "copernicus-ems · dove è arrivata davvero l'acqua",
  owner: "Agent Engineering Studio · demo",
  tagline: "Dopo un'alluvione, dove è arrivata davvero l'acqua: le mappe satellitari di Copernicus EMS",
  language: "Lingua",
  skip: "Vai al contenuto",
  mapLabel: "Mappa delle attivazioni Copernicus EMS",
  // The three steps that say what the library is for.
  whatTitle: "A cosa serve",
  steps: [
    [
      "Un disastro, un'attivazione",
      "Quando un'alluvione colpisce, il servizio europeo Copernicus EMS viene attivato: ogni emergenza ha un codice (EMSR664) e una o più aree di interesse.",
    ],
    [
      "Il satellite guarda",
      "Per ogni area gli analisti usano immagini satellitari. Il riquadro tratteggiato è dove il satellite ha guardato: la maschera di osservazione.",
    ],
    [
      "Si disegna l'acqua vista",
      "In blu l'acqua osservata. Dentro il riquadro, dove non c'è blu era asciutto; fuori, nessuno ha guardato, ed è un'altra cosa.",
    ],
  ] as [string, string][],
  whyText:
    "copernicus-ems legge queste mappe in Python, senza chiavi né account, e le dà come GeoJSON con le loro maschere: la verità a terra per verificare un modello di allagamento, un bollettino o una mappa di rischio.",
  pick: "Scegli un'emergenza sulla mappa o nell'elenco.",
  listTitle: (country: string, n: number) => `${n} emergenze · ${country === "Italy" ? "Italia" : country}`,
  fromDisk: "dal disco (copernicus-ems init)",
  live: "dal servizio, in diretta",
  category: (c: string) => ({ Flood: "Alluvione", Storm: "Tempesta" })[c] ?? c,
  reading: "Leggo…",
  readingMap: "Disegno le aree…",
  noAnswer: "Il servizio non risponde. Riprova tra poco.",
  back: "← Tutte le emergenze",
  close: "Chiudi",
  event: "Evento",
  seenTitle: "Cosa hanno visto i satelliti",
  flooded: "acqua osservata",
  looked: "area guardata",
  inAois: (n: number) => (n === 1 ? "in 1 area" : `in ${n} aree`),
  floodedNote:
    "Per ogni area, la mappa con più acqua: i prodotti di monitoraggio riguardano lo stesso luogo in giorni diversi, sommarli conterebbe due volte la stessa acqua.",
  productsTitle: "Le mappe, una per prodotto",
  productsHint: "Tocca una mappa per vederla da sola.",
  all: "Tutte",
  product: "Prodotto",
  seen: "Visto",
  water: "Acqua",
  sensor: "Sensore",
  kinds: { DEL: "delineazione", GRA: "danni", FEP: "prima stima" } as Record<string, string>,
  withoutLayer: (n: number) =>
    n === 1 ? "1 prodotto non ha uno strato dell'evento osservato." : `${n} prodotti non hanno uno strato dell'evento osservato.`,
  pythonTitle: "Gli stessi numeri, in Python",
  official: "La mappa ufficiale su Copernicus EMS",
  legendWater: "Acqua osservata",
  legendLooked: "Dove il satellite ha guardato",
  legendOut: "Fuori: non osservato (non vuol dire asciutto)",
  legendDot: "Un'emergenza: toccala",
  tipFlood: (km2: string) => `acqua osservata · ${km2} km²`,
  tipMask: "area guardata dal satellite",
  dataTitle: "I dati",
  dataText:
    "Copernicus Emergency Management Service, Rapid Mapping © Unione europea. L'API pubblica elenca le attivazioni dal 2023. Mappa di sfondo © OpenStreetMap.",
  limitTitle: "Il limite",
  limitText:
    "Una mappa satellitare è un limite inferiore: allagamenti brevi, urbani o sotto la vegetazione possono non comparire. Non è un servizio di allerta: per il pericolo valgono le comunicazioni della Protezione Civile.",
  howTitle: "Com'è fatta",
  howText:
    "Una pagina Next.js e Leaflet sopra una piccola API Python (Starlette) che chiama la libreria. Ogni scheda mostra le righe di Python che danno i suoi numeri.",
  docs: "Documentazione",
  code: "Codice su GitHub",
  agentsTitle: "Per gli agenti AI",
  mcpOn: "server MCP acceso",
  mcpOff: "server MCP spento",
  agentsText:
    "Gli stessi dati, come strumenti per un agente (Claude o un altro client MCP): risponde con le mappe pubblicate, non a memoria.",
  tools: {
    list_activations: "le emergenze di un paese, di un tipo, in un periodo",
    activation_summary: "cosa ha visto un'attivazione: km² d'acqua e area guardata, per prodotto",
    compare_products: "come è cambiata l'acqua in un'area, prodotto dopo prodotto",
    export_observed_events: "salva gli strati in GeoJSON o GeoParquet",
    find_scenes: "le immagini Sentinel-1 e -2 su un'area, nei giorni dell'evento",
  } as Record<string, string>,
  connectHttp: "Collega Claude Code a questo server",
  startMcp: "Accendi il server MCP",
  connectStdio: "Oppure senza Docker, sul tuo computer",
  agentsTry: "Prova a chiedere: «Quali alluvioni sono state mappate in Italia nel 2023, e quanta acqua ha visto EMSR664 in Emilia-Romagna?»",
};

export type Text = typeof it;

const en: Text = {
  lang: "en",
  title: "copernicus-ems · where the water actually was",
  owner: "Agent Engineering Studio · demo",
  tagline: "After a flood, where the water actually was: the satellite maps of Copernicus EMS",
  language: "Language",
  skip: "Skip to content",
  mapLabel: "Map of the Copernicus EMS activations",
  whatTitle: "What it is for",
  steps: [
    [
      "A disaster, an activation",
      "When a flood strikes, the European Copernicus EMS service is activated: each emergency gets a code (EMSR664) and one or more areas of interest.",
    ],
    [
      "The satellite looks",
      "For each area, analysts use satellite images. The dashed box is where the satellite looked: the observation mask.",
    ],
    [
      "The water seen is drawn",
      "In blue, the water observed. Inside the box, no blue means dry; outside it, nobody looked, which is not the same thing.",
    ],
  ],
  whyText:
    "copernicus-ems reads these maps in Python, with no key and no account, and gives them as GeoJSON with their masks: ground truth to check a flood model, a bulletin or a risk map.",
  pick: "Pick an emergency on the map or in the list.",
  listTitle: (country, n) => `${n} emergencies · ${country}`,
  fromDisk: "from disk (copernicus-ems init)",
  live: "from the service, live",
  category: (c) => c,
  reading: "Reading…",
  readingMap: "Drawing the areas…",
  noAnswer: "The service does not answer. Try again in a moment.",
  back: "← All emergencies",
  close: "Close",
  event: "Event",
  seenTitle: "What the satellites saw",
  flooded: "water observed",
  looked: "area looked at",
  inAois: (n) => (n === 1 ? "in 1 area" : `in ${n} areas`),
  floodedNote:
    "For each area, the map with the most water: monitoring products show the same place on different days, and adding them up would count the same water twice.",
  productsTitle: "The maps, one per product",
  productsHint: "Tap a map to see it alone.",
  all: "All",
  product: "Product",
  seen: "Seen",
  water: "Water",
  sensor: "Sensor",
  kinds: { DEL: "delineation", GRA: "grading", FEP: "first estimate" },
  withoutLayer: (n) =>
    n === 1 ? "1 product has no observed-event layer." : `${n} products have no observed-event layer.`,
  pythonTitle: "The same numbers, in Python",
  official: "The official map on Copernicus EMS",
  legendWater: "Water observed",
  legendLooked: "Where the satellite looked",
  legendOut: "Outside: not observed (which does not mean dry)",
  legendDot: "An emergency: tap it",
  tipFlood: (km2) => `water observed · ${km2} km²`,
  tipMask: "area the satellite looked at",
  dataTitle: "The data",
  dataText:
    "Copernicus Emergency Management Service, Rapid Mapping © European Union. The public API lists activations from 2023. Background map © OpenStreetMap.",
  limitTitle: "The limit",
  limitText:
    "A satellite map is a lower bound: short, urban or under-canopy floods may not show. This is not an alert service: for danger, the official word is the Civil Protection's.",
  howTitle: "How it is made",
  howText:
    "A Next.js and Leaflet page over a small Python API (Starlette) that calls the library. Every card shows the lines of Python that give its numbers.",
  docs: "Documentation",
  code: "Code on GitHub",
  agentsTitle: "For AI agents",
  mcpOn: "MCP server on",
  mcpOff: "MCP server off",
  agentsText:
    "The same data, as tools for an agent (Claude or any MCP client): it answers from the published maps, not from memory.",
  tools: {
    list_activations: "the emergencies of a country, of a kind, in a period",
    activation_summary: "what an activation saw: km² of water and area looked at, per product",
    compare_products: "how the water in an area changed, product after product",
    export_observed_events: "saves the layers as GeoJSON or GeoParquet",
    find_scenes: "the Sentinel-1 and -2 images of an area, in the days of the event",
  },
  connectHttp: "Connect Claude Code to this server",
  startMcp: "Start the MCP server",
  connectStdio: "Or without Docker, on your computer",
  agentsTry: "Try asking: “Which floods were mapped in Italy in 2023, and how much water did EMSR664 see in Emilia-Romagna?”",
};

export const TEXT: Record<Lang, Text> = { it, en };

export const LangContext = createContext<Text>(it);
export const useText = () => useContext(LangContext);
