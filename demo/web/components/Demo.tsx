"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import Card from "@/components/Card";
import { Footer, Header } from "@/components/Frame";
import { LOOKED, WATER, day, failed, good } from "@/lib/format";
import { useAnswer, useCodeInAddress, useLang } from "@/lib/hooks";
import { LangContext, TEXT } from "@/lib/i18n";
import { radarOf } from "@/lib/radar";
import type { CardData, Listing, MapData, RadarData, RadarMap } from "@/lib/types";

// Leaflet needs a window: the map is drawn in the browser only.
const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

export default function Demo({ first }: { first: string | null }) {
  const [lang, setLang] = useLang();
  const t = TEXT[lang];
  const [code, setCode] = useState<string | null>(first);
  const [product, setProduct] = useState<string | null>(null);

  const listing = useAnswer<Listing>("/api/activations");
  const card = useAnswer<CardData>(code && `/api/activation/${code}`);
  const shapes = useAnswer<MapData>(code && `/api/activation/${code}/map`);
  const radar = useAnswer<RadarData>(code && `/api/activation/${code}/radar`);
  // The radar layer of one product only, when one is selected: a large activation never loads them all.
  const radarShapes = useAnswer<RadarMap>(
    code && product && `/api/activation/${code}/radar/map?product=${encodeURIComponent(product)}`,
  );
  // An agreement that could not be computed has no layer to show.
  const computed = product !== null && good(radar)?.results[product]?.computed !== false;
  const drawn = computed ? radarOf(good(radarShapes), product) : [];
  const sameImage = drawn.some((f) => f.properties.independent === false);
  const has = (klass: string) => drawn.some((f) => f.properties.class === klass);
  const candidates = has("candidate");
  // Only the classes on the map get a legend line; a same-image map has no "confirmed" look.
  const confirmedOn = !sameImage && has("confirmed");
  const mapOnlyOn = !sameImage && has("map_only");
  const rows = good(listing)?.activations ?? [];

  useCodeInAddress(code);
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
      <Header lang={lang} onLang={setLang} />

      <main id="content" className="container-xxl">
        <div className="row g-3">
          <div className="col-lg-8">
            <div className="stage">
              <MapView
                rows={rows}
                selected={code}
                shapes={code ? good(shapes) : null}
                product={product}
                radar={computed ? good(radarShapes) : null}
                text={t}
                onSelect={open}
              />
              <div className="panel legend">
                {confirmedOn && (
                  <p>
                    <i className="swatch" style={{ background: WATER }} /> {t.radar.legendConfirmed}
                  </p>
                )}
                {mapOnlyOn && (
                  <p>
                    <i className="swatch light" style={{ background: WATER }} /> {t.radar.legendMapOnly}
                  </p>
                )}
                {!confirmedOn && !mapOnlyOn && (
                  <p>
                    <i className="swatch" style={{ background: WATER }} /> {t.legendWater}
                  </p>
                )}
                {sameImage && <p className="muted">{t.radar.legendNotIndependent}</p>}
                {candidates && (
                  <p>
                    <i className="swatch hatch" /> {t.radar.legendCandidate}
                  </p>
                )}
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
                {code && product && radarShapes === null && <p className="waiting">{t.radar.readingRadar}</p>}
              </div>
            </div>
          </div>

          <div className="col-lg-4">
            {code ? (
              <Card card={card} radar={radar} product={product} onProduct={setProduct} onClose={() => open(null)} />
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
              </div>
            )}
          </div>
        </div>
      </main>

      <Footer toTech />
    </LangContext.Provider>
  );
}
