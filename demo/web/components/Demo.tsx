"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import Agents from "@/components/Agents";
import Card from "@/components/Card";
import { LOOKED, WATER, ask, day, failed } from "@/lib/format";
import { LANGS, LangContext, TEXT, type Lang } from "@/lib/i18n";
import type { Agents as AgentsData, CardData, Failed, Listing, MapData } from "@/lib/types";

// Leaflet needs a window: the map is drawn in the browser only.
const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

const DOCS = "https://agentengineering.it/copernicus-ems/";
const CODE = "https://github.com/agent-engineering-studio/copernicus-ems";

const wrong = (error: unknown): Failed => ({ error: error instanceof Error ? error.message : "" });
const good = <T,>(value: T | Failed | null): T | null => (value === null || failed(value) ? null : value);

/** ?lang= first, then the choice made before, then Italian for who reads Italian, English for the rest. */
function useLang(): [Lang, (lang: Lang) => void] {
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
    try {
      localStorage.setItem("lang", next);
    } catch {}
  };
  return [lang, choose];
}

/** Ask once; the answer to an older question is dropped. */
function useAnswer<T>(path: string | null): T | Failed | null {
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

export default function Demo({ first, agents }: { first: string | null; agents: AgentsData }) {
  const [lang, setLang] = useLang();
  const t = TEXT[lang];
  const [code, setCode] = useState<string | null>(first);
  const [product, setProduct] = useState<string | null>(null);

  const listing = useAnswer<Listing>("/api/activations");
  const card = useAnswer<CardData>(code && `/api/activation/${code}`);
  const shapes = useAnswer<MapData>(code && `/api/activation/${code}/map`);
  const rows = good(listing)?.activations ?? [];

  // The address says what is open: a link to it opens the same activation.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (code) url.searchParams.set("code", code);
    else url.searchParams.delete("code");
    window.history.replaceState(null, "", url);
  }, [code]);
  useEffect(() => {
    const close = (e: KeyboardEvent) => e.key === "Escape" && setCode(null);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);

  const open = (next: string | null) => {
    setCode(next);
    setProduct(null);
  };

  return (
    <LangContext.Provider value={t}>
      <title>{code ? `${code} · ${t.title}` : t.title}</title>
      <a className="visually-hidden-focusable skip" href="#content">
        {t.skip}
      </a>
      <header className="it-header-wrapper">
        <div className="it-header-slim-wrapper">
          <div className="container-xxl">
            <div className="it-header-slim-wrapper-content">
              <span className="navbar-brand">{t.owner}</span>
              <div className="it-header-slim-right-zone">
                <div className="langs" role="radiogroup" aria-label={t.language}>
                  {LANGS.map((one) => (
                    <button key={one} role="radio" aria-checked={lang === one} onClick={() => setLang(one)}>
                      {one.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
        <div className="it-nav-wrapper">
          <div className="it-header-center-wrapper">
            <div className="container-xxl">
              <div className="it-header-center-content-wrapper">
                <div className="it-brand-wrapper">
                  <div className="it-brand-text">
                    <h1 className="it-brand-title">copernicus-ems</h1>
                    <div className="it-brand-tagline">{t.tagline}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </header>

      <main id="content" className="container-xxl">
        <div className="row g-3">
          <div className="col-lg-8">
            <div className="stage">
              <MapView
                rows={rows}
                selected={code}
                shapes={code ? good(shapes) : null}
                product={product}
                text={t}
                onSelect={open}
              />
              <div className="panel legend">
                <p>
                  <i className="swatch" style={{ background: WATER }} /> {t.legendWater}
                </p>
                <p>
                  <i className="swatch dashed" style={{ borderColor: LOOKED }} /> {t.legendLooked}
                </p>
                <p className="muted">{t.legendOut}</p>
                {!code && (
                  <p>
                    <i className="swatch dot" style={{ background: WATER }} /> {t.legendDot}
                  </p>
                )}
                {code && shapes === null && <p className="waiting">{t.readingMap}</p>}
              </div>
            </div>
          </div>

          <div className="col-lg-4">
            {code ? (
              <Card card={card} product={product} onProduct={setProduct} onClose={() => open(null)} />
            ) : (
              <div className="sheet">
                <section className="what">
                  <h2>{t.whatTitle}</h2>
                  <ol className="steps">
                    {t.steps.map(([title, text], k) => (
                      <li key={k}>
                        <b>{title}</b>
                        <span>{text}</span>
                      </li>
                    ))}
                  </ol>
                  <p>{t.whyText}</p>
                </section>
                <section>
                  {listing === null ? (
                    <p className="waiting">{t.reading}</p>
                  ) : failed(listing) ? (
                    <div className="alert alert-danger" role="alert">
                      {listing.error || t.noAnswer}
                    </div>
                  ) : (
                    <>
                      <h3>
                        {t.listTitle(listing.country, rows.length)}{" "}
                        <small>{listing.source === "disk" ? t.fromDisk : t.live}</small>
                      </h3>
                      <p className="muted">{t.pick}</p>
                      <ul className="list">
                        {rows.map((row) => (
                          <li key={row.code}>
                            <button type="button" onClick={() => open(row.code)}>
                              <b>{row.code}</b>
                              <span>{row.name}</span>
                              <small>
                                {day(row.event_time, lang)} · {t.category(row.category)}
                              </small>
                            </button>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </section>
                <Agents agents={agents} />
              </div>
            )}
          </div>
        </div>
      </main>

      <footer className="it-footer">
        <div className="it-footer-main">
          <div className="container-xxl">
            <div className="row py-4">
              <section className="col-md-4">
                <h2 className="h6">{t.dataTitle}</h2>
                <p>{t.dataText}</p>
              </section>
              <section className="col-md-4">
                <h2 className="h6">{t.limitTitle}</h2>
                <p>{t.limitText}</p>
              </section>
              <section className="col-md-4">
                <h2 className="h6">{t.howTitle}</h2>
                <p>{t.howText}</p>
              </section>
            </div>
          </div>
        </div>
        <div className="it-footer-small-prints clearfix">
          <div className="container-xxl">
            <ul className="it-footer-small-prints-list list-inline mb-0 d-flex flex-column flex-md-row">
              <li className="list-inline-item">
                <a href={DOCS}>copernicus-ems · {t.docs}</a>
              </li>
              <li className="list-inline-item">
                <a href={CODE}>{t.code}</a>
              </li>
            </ul>
          </div>
        </div>
      </footer>
    </LangContext.Provider>
  );
}
