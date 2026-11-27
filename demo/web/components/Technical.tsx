"use client";

import { useEffect, useState } from "react";
import Agents from "@/components/Agents";
import { DOCS, Footer, Header } from "@/components/Frame";
import { ask, day, failed, good, km2 } from "@/lib/format";
import { useAnswer, useCodeInAddress, useLang } from "@/lib/hooks";
import { LangContext, TEXT, useText } from "@/lib/i18n";
import { phrase } from "@/lib/radar";
import type { Agents as AgentsData, CardData, Failed, Listing, RadarData, RadarEntry, Row } from "@/lib/types";

/** A number with two decimals in the language of the page; "—" when there is none. */
function fixed(value: number | null | undefined, lang: string, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toLocaleString(lang === "it" ? "it-IT" : "en-GB", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Which of the rows have radar results on disk: one small question per row, after the list is read. */
function useMarks(rows: Row[]): Record<string, boolean> {
  const [marks, setMarks] = useState<Record<string, boolean>>({});
  const key = rows.map((row) => row.code).join(",");
  useEffect(() => {
    if (!key) return;
    const stop = new AbortController();
    for (const code of key.split(",")) {
      ask<RadarData>(`/api/activation/${code}/radar`, stop.signal)
        .then((radar) => setMarks((was) => ({ ...was, [code]: radar.checked > 0 })))
        // On purpose: a failed request only drops the optional mark; the row stays and opens as usual.
        .catch(() => {});
    }
    return () => stop.abort();
  }, [key]);
  return marks;
}

const SCORES = ["csi", "pod", "far", "bias"] as const;

/** The files of a product: its two reports, and the agreement polygons and raster when they were drawn. */
function filesOf(entry: RadarEntry): string[] {
  const files = [...entry.files];
  const agreement = files.find((name) => name.endsWith("_agreement.json"));
  if (agreement && entry.computed) {
    files.push(agreement.replace(/\.json$/, ".geojson"), agreement.replace(/\.json$/, ".tif"));
  }
  return files.sort();
}

function python(code: string): string {
  return `from copernicus_ems import default_directory
from copernicus_ems.agreement import agree_pairs
from copernicus_ems.quality import check_pairs

# The pairs that "copernicus-ems quality ${code} --fetch" saved under <directory>/${code}/sentinel-1/.
directory = default_directory()
for q in check_pairs(directory, ["${code}"]):  # writes *_quality.json
    print(q.product, q.resolution_m, q.verdict, q.reason, [f.code for f in q.flags])
for a in agree_pairs(directory, ["${code}"]):  # writes *_agreement.json, .geojson, .tif
    print(a.product, a.independent, a.km2, a.scores.get("csi"), a.scores.get("pod"))`;
}

function RadarRow({ name, aoi, entry }: { name: string; aoi: string; entry: RadarEntry | undefined }) {
  const t = useText();
  const L = t.lang;
  const said = phrase(entry, t);
  const dash = <td className="num muted">—</td>;
  // A map made from the same radar image: its numbers are shown, greyed, never read as a confirmation.
  const num = entry?.independent === false ? "num muted" : "num";
  const area = (value: number | undefined) =>
    entry?.km2 ? <td className={num}>{fixed(value, L)}</td> : dash;
  return (
    <tr>
      <th>
        {aoi}
        <small>{name}</small>
      </th>
      <td className="radar">
        <span className={`verdict ${said.tone}`}>{said.head}</span>
        {said.why.map((line) => (
          <small key={line}>{line}</small>
        ))}
      </td>
      {entry ? (
        <>
          <td>
            <code>{entry.verdict ?? "—"}</code>
            {entry.reason && <small>{entry.reason}</small>}
          </td>
          <td className="flags">
            {entry.flags.length === 0 && <span className="muted">—</span>}
            {entry.flags.map((flag) => (
              <div key={flag.code}>
                <code>{flag.code}</code>
                <small>{t.radar.flags[flag.code] ?? flag.text}</small>
                <small>
                  {Object.entries(flag.numbers)
                    .map(([what, value]) => `${what} ${fixed(value, L, 3)}`)
                    .join(" · ")}
                </small>
              </div>
            ))}
          </td>
          <td className="num">{fixed(entry.gap_hours, L, 1)}</td>
          <td>{entry.independent === null ? "—" : entry.independent ? t.tech.yes : t.tech.sameImage}</td>
          {/* Same image: the agreement proves nothing, so no "confirmed" or "map only" number. */}
          {entry.independent === false ? dash : area(entry.km2?.confirmed)}
          {entry.independent === false ? dash : area(entry.km2?.map_only)}
          {area(entry.km2?.candidate)}
          {area(entry.km2?.radar_scattered)}
          {SCORES.map((score) => (
            <td key={score} className={num}>
              {fixed(entry.scores?.[score], L)}
            </td>
          ))}
        </>
      ) : (
        Array.from({ length: 12 }, (_, k) => (
          <td key={k} className="num muted">
            —
          </td>
        ))
      )}
    </tr>
  );
}

function Activation({ card, asked }: { card: CardData; asked: RadarData | Failed | null }) {
  const t = useText();
  const L = t.lang;
  const radar = good(asked);
  const results = radar?.results ?? {};
  const named = card.products.filter((row) => results[row.product]);
  return (
    <>
      <section>
        <h3>{t.tech.cardTitle}</h3>
        <p>
          <b>{km2(card.flooded_km2, L)} km²</b> {t.flooded} · <b>{km2(card.observed_km2, L)} km²</b> {t.looked}{" "}
          {t.inAois(card.aois)}
        </p>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t.product}</th>
                <th>{t.seen}</th>
                <th>{t.sensor}</th>
                <th className="num">{t.tech.features}</th>
                <th className="num">{t.tech.flooded}</th>
                <th className="num">{t.tech.observed}</th>
              </tr>
            </thead>
            <tbody>
              {card.products.map((row) => (
                <tr key={row.product}>
                  <th>
                    {row.aoi}
                    <small>
                      {row.product} · {t.kinds[row.type] ?? row.type}
                    </small>
                  </th>
                  <td className="when">{day(row.observed_time, L)}</td>
                  <td>{row.sensors.join(", ") || "—"}</td>
                  <td className="num">{row.features}</td>
                  <td className="num">{fixed(row.flooded_km2, L)}</td>
                  <td className="num">{fixed(row.observed_km2, L)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {card.without_layer > 0 && <p className="muted">{t.withoutLayer(card.without_layer)}</p>}
        <h4>{t.pythonTitle}</h4>
        <pre>
          <code>{card.python}</code>
        </pre>
      </section>

      <section>
        <h3>{t.tech.radarTitle}</h3>
        <p className="muted">{t.tech.radarIntro}</p>
        {asked === null ? (
          <p className="waiting">{t.radar.readingRadar}</p>
        ) : failed(asked) ? (
          <div className="alert alert-warning" role="alert">
            {asked.error || t.noAnswer}
          </div>
        ) : (
          <>
            <p>
              <b>{t.tech.checked(asked.checked, asked.products)}</b>
            </p>
            <div className="table-scroll">
              <table className="radar-table">
                <thead>
                  <tr>
                    <th rowSpan={2}>{t.product}</th>
                    <th rowSpan={2}>{t.tech.said}</th>
                    <th rowSpan={2}>{t.tech.verdict}</th>
                    <th rowSpan={2}>{t.tech.flags}</th>
                    <th rowSpan={2} className="num">
                      {t.tech.gap}
                    </th>
                    <th rowSpan={2}>{t.tech.independent}</th>
                    <th colSpan={4} className="group">
                      {t.tech.km2}
                    </th>
                    <th rowSpan={2} className="num">
                      CSI
                    </th>
                    <th rowSpan={2} className="num">
                      POD
                    </th>
                    <th rowSpan={2} className="num">
                      FAR
                    </th>
                    <th rowSpan={2} className="num">
                      bias
                    </th>
                  </tr>
                  <tr>
                    <th className="num">{t.tech.confirmed}</th>
                    <th className="num">{t.tech.mapOnly}</th>
                    <th className="num">{t.tech.candidate}</th>
                    <th className="num">{t.tech.scattered}</th>
                  </tr>
                </thead>
                <tbody>
                  {card.products.map((row) => (
                    <RadarRow key={row.product} name={row.product} aoi={row.aoi} entry={results[row.product]} />
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted">{t.tech.scoresNote}</p>
            <p className="muted">{t.tech.sameImageNote}</p>
          </>
        )}
      </section>

      <section>
        <h3>{t.tech.commandsTitle}</h3>
        <p>{t.tech.commandsText}</p>
        <pre>
          <code>{radar?.command ?? `make radar CODE=${card.code}`}</code>
        </pre>
        <p>{t.tech.stepByStep}</p>
        <pre>
          <code>{`copernicus-ems quality ${card.code} --fetch\ncopernicus-ems radar ${card.code}`}</code>
        </pre>
        <h4>{t.tech.pythonRadar}</h4>
        <pre>
          <code>{python(card.code)}</code>
        </pre>
      </section>

      <section>
        <h3>{t.tech.filesTitle}</h3>
        {named.length === 0 ? (
          <p className="muted">{t.tech.noFiles}</p>
        ) : (
          <>
            <p>{t.tech.filesText(card.code)}</p>
            <ul className="files">
              {named.map((row) => (
                <li key={row.product}>
                  <b>{row.product}</b>
                  {filesOf(results[row.product]).map((name) => (
                    <code key={name}>{name}</code>
                  ))}
                </li>
              ))}
            </ul>
          </>
        )}
        <p>
          <a href={`${DOCS}quality/`}>{t.tech.docsQuality} ↗</a>
          <br />
          <a href={`${DOCS}radar/`}>{t.tech.docsRadar} ↗</a>
        </p>
      </section>

      <section>
        <h3>{t.tech.limitsTitle}</h3>
        <ul>
          {t.tech.limits.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>
    </>
  );
}

export default function Technical({ first, agents }: { first: string | null; agents: AgentsData }) {
  const [lang, setLang] = useLang();
  const t = TEXT[lang];
  const [code, setCode] = useState<string | null>(first);

  const listing = useAnswer<Listing>(code ? null : "/api/activations");
  const card = useAnswer<CardData>(code && `/api/activation/${code}`);
  const radar = useAnswer<RadarData>(code && `/api/activation/${code}/radar`);
  const rows = good(listing)?.activations ?? [];
  const marks = useMarks(rows);
  useCodeInAddress(code);

  const mapLink = code ? `/?code=${encodeURIComponent(code)}&lang=${lang}` : `/?lang=${lang}`;
  const shown = good(card);
  return (
    <LangContext.Provider value={t}>
      <title>{code ? `${code} · ${t.tech.title} · copernicus-ems` : `${t.tech.title} · copernicus-ems`}</title>
      <Header lang={lang} onLang={setLang} />

      <main id="content" className="container-xxl">
        <article className="sheet tech" aria-labelledby="tech-title">
          <nav className="tech-nav">
            <a href={mapLink}>{t.tech.toMap}</a>
            {code && (
              <button type="button" className="btn btn-outline-primary btn-xs" onClick={() => setCode(null)}>
                {t.back}
              </button>
            )}
          </nav>
          <header>
            {shown && (
              <small>
                {shown.code} · {t.category(shown.category)} · {t.event} {day(shown.event_time, lang)}
              </small>
            )}
            <h2 id="tech-title">{shown ? `${t.tech.title} · ${shown.name}` : t.tech.title}</h2>
          </header>

          {code === null ? (
            <>
              <section>
                <p>{t.tech.intro}</p>
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
                    <p className="muted">{t.tech.pick}</p>
                    <ul className="list">
                      {rows.map((row) => (
                        <li key={row.code}>
                          <button type="button" onClick={() => setCode(row.code)}>
                            <b>{row.code}</b>
                            <span>
                              {row.name}
                              {marks[row.code] && (
                                <span className="mark" title={t.tech.markLabel}>
                                  {t.tech.mark}
                                  <span className="visually-hidden"> ({t.tech.markLabel})</span>
                                </span>
                              )}
                            </span>
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
            </>
          ) : card === null ? (
            <p className="waiting">{t.reading}</p>
          ) : failed(card) ? (
            <div className="alert alert-danger" role="alert">
              {card.error || t.noAnswer}
            </div>
          ) : (
            <Activation card={card} asked={radar} />
          )}

          <Agents agents={agents} />
        </article>
      </main>

      <Footer />
    </LangContext.Provider>
  );
}
