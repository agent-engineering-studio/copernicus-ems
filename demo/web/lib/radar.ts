// The radar results in plain words: one short phrase per map, never a judgment of error.

import type { Text } from "./i18n";
import type { RadarEntry, RadarMap } from "./types";

/** The three classes of the radar agreement the page draws; any other is left out. */
export const RADAR_CLASSES = ["confirmed", "map_only", "candidate"] as const;

/** The radar shapes of one product that the map draws: none when no product is selected. */
export function radarOf(radar: RadarMap | null, product: string | null): RadarMap["features"] {
  if (!radar || !product) return [];
  return radar.features.filter(
    (f) => f.properties.product === product && (RADAR_CLASSES as readonly string[]).includes(f.properties.class),
  );
}

/** The families of "not assessable" reasons that `quality._not_assessable` and the agreement write. */
export type Reason = "sameImage" | "resolution" | "gap" | "coverage" | "noPassTime" | "noReference" | "tooLittle";

// The report text starts with, or contains, these words: the first match wins.
const REASONS: [string, Reason][] = [
  ["made from Sentinel-1", "sameImage"],
  ["calibrated at", "resolution"],
  ["radar pass", "gap"],
  ["radar covers", "coverage"],
  ["no pass time", "noPassTime"],
  ["no pre-event", "noReference"],
  ["too little", "tooLittle"],
];

export function reasonOf(entry: Pick<RadarEntry, "reason" | "independent">): Reason | null {
  const text = entry.reason ?? "";
  const found = REASONS.find(([words]) => text.includes(words));
  if (found) return found[1];
  // An agreement alone carries no reason: not independent means made from the same image.
  if (!text && entry.independent === false) return "sameImage";
  return null;
}

export type Tone = "agrees" | "look" | "cannot" | "unchecked";

export type Phrase = {
  tone: Tone;
  /** "il radar conferma", "da guardare", "non valutabile", "non ancora controllata". */
  head: string;
  /** Why, in plain words: one line per flag, or the reason; empty when there is nothing to add. */
  why: string[];
};

/** What the plain page says of one map, from its radar results (undefined: none on disk). */
export function phrase(entry: RadarEntry | undefined, t: Text): Phrase {
  const r = t.radar;
  if (!entry || !entry.verdict) return { tone: "unchecked", head: r.unchecked, why: [] };
  if (entry.verdict === "coherent") {
    // A map made from the same image cannot be confirmed by it, whatever the verdict says.
    if (entry.independent === false) return { tone: "cannot", head: r.cannot, why: [r.reasons.sameImage] };
    return { tone: "agrees", head: r.agrees, why: [] };
  }
  if (entry.verdict === "to check") {
    const why = entry.flags.map((flag) => r.flags[flag.code]).filter((line): line is string => !!line);
    return { tone: "look", head: r.look, why };
  }
  const reason = reasonOf(entry);
  return { tone: "cannot", head: r.cannot, why: reason ? [r.reasons[reason]] : [] };
}
