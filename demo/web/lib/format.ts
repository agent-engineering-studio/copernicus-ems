import type { Failed } from "./types";

/** The colours of the map: the water seen, and the place looked at. */
export const WATER = "#0b63ce";
export const LOOKED = "#5a6772";
/** Water the radar may see and the map does not have: never the blue of mapped water. */
export const CANDIDATE = "#c25700";

export const failed = (value: unknown): value is Failed =>
  typeof value === "object" && value !== null && "error" in value;

/** The answer when there is one: null while it is read, or if it could not be. */
export const good = <T,>(value: T | Failed | null): T | null => (value === null || failed(value) ? null : value);

export async function ask<T>(path: string, signal?: AbortSignal): Promise<T> {
  const answer = await fetch(path, { signal, cache: "no-store" });
  const body = await answer.json().catch(() => ({ error: `HTTP ${answer.status}` }));
  if (!answer.ok) throw new Error(body.error ?? `HTTP ${answer.status}`);
  return body as T;
}

const DAY: Record<string, Intl.DateTimeFormat> = {};
/** "16 May 2023", in the language of the page and the time of Italy. */
export function day(stamp: string | null, lang: string): string {
  if (!stamp) return "—";
  DAY[lang] ??= new Intl.DateTimeFormat(lang === "it" ? "it-IT" : "en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "Europe/Rome",
  });
  return DAY[lang].format(new Date(stamp));
}

/** Hours between two times, as "+29 h". */
export function after(from: string, to: string | null): string | null {
  if (!to) return null;
  const hours = (new Date(to).getTime() - new Date(from).getTime()) / 3_600_000;
  return `${hours >= 0 ? "+" : "−"}${Math.abs(hours).toFixed(0)} h`;
}

/** A number of km² the way a person reads it: 0.4, 12, 1,846. */
export function km2(value: number, lang: string): string {
  const digits = value < 10 ? 1 : 0;
  return value.toLocaleString(lang === "it" ? "it-IT" : "en-GB", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function share(part: number, whole: number, lang: string): string {
  if (!whole) return "—";
  const value = (100 * part) / whole;
  return `${value.toLocaleString(lang === "it" ? "it-IT" : "en-GB", {
    maximumFractionDigits: value < 1 ? 2 : 1,
  })} %`;
}
