"use client";

import { useEffect, useState } from "react";
import { ask } from "./format";
import { LANGS, type Lang } from "./i18n";
import type { Failed } from "./types";

const wrong = (error: unknown): Failed => ({ error: error instanceof Error ? error.message : "" });

/** ?lang= first, then the choice made before, then Italian for who reads Italian, English for the rest. */
export function useLang(): [Lang, (lang: Lang) => void] {
  const [lang, setLang] = useState<Lang>("it");
  useEffect(() => {
    let kept: string | null = null;
    try {
      kept = localStorage.getItem("lang");
    } catch {}
    const asked = new URL(window.location.href).searchParams.get("lang");
    setLang(
      LANGS.find((one) => one === asked) ??
        LANGS.find((one) => one === kept) ??
        (navigator.language.startsWith("it") ? "it" : "en"),
    );
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  const choose = (next: Lang) => {
    setLang(next);
    // a ?lang= in the address follows the choice, so that a reload keeps it
    const url = new URL(window.location.href);
    if (url.searchParams.has("lang")) {
      url.searchParams.set("lang", next);
      window.history.replaceState(null, "", url);
    }
    try {
      localStorage.setItem("lang", next);
    } catch {}
  };
  return [lang, choose];
}

/** Ask once; the answer to an older question is dropped. */
export function useAnswer<T>(path: string | null): T | Failed | null {
  const [found, setFound] = useState<{ path: string; value: T | Failed } | null>(null);
  useEffect(() => {
    if (!path) return;
    const stop = new AbortController();
    ask<T>(path, stop.signal)
      .then((value) => setFound({ path, value }))
      .catch((error) => stop.signal.aborted || setFound({ path, value: wrong(error) }));
    return () => stop.abort();
  }, [path]);
  return found?.path === path ? found.value : null;
}

/** The address says what is open: a link to it opens the same activation. */
export function useCodeInAddress(code: string | null) {
  useEffect(() => {
    const url = new URL(window.location.href);
    if (code) url.searchParams.set("code", code);
    else url.searchParams.delete("code");
    window.history.replaceState(null, "", url);
  }, [code]);
}
