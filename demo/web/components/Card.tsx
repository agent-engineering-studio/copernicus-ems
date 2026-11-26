"use client";

import { after, day, failed, km2, share, WATER } from "@/lib/format";
import { useText } from "@/lib/i18n";
import type { CardData, Failed } from "@/lib/types";

type Props = {
  card: CardData | Failed | null;
  product: string | null;
  onProduct: (product: string | null) => void;
  onClose: () => void;
};

export default function Card({ card, product, onProduct, onClose }: Props) {
  const t = useText();
  if (card === null) {
    return (
      <div className="sheet">
        <p className="waiting">{t.reading}</p>
      </div>
    );
  }
  if (failed(card)) {
    return (
      <div className="sheet">
        <button type="button" className="btn btn-outline-primary btn-xs" onClick={onClose}>
          {t.back}
        </button>
        <div className="alert alert-danger mt-3" role="alert">
          {card.error || t.noAnswer}
        </div>
      </div>
    );
  }
  const L = t.lang;
  return (
    <article className="sheet" aria-labelledby="card-title">
      <header>
        <button type="button" className="btn btn-outline-primary btn-xs" onClick={onClose}>
          {t.back}
        </button>
        <small>
          {card.code} · {t.category(card.category)} · {t.event} {day(card.event_time, L)}
        </small>
        <h2 id="card-title">{card.name}</h2>
      </header>

      <section>
        <h3>{t.seenTitle}</h3>
        <div className="big">
          <div>
            <b style={{ color: WATER }}>{km2(card.flooded_km2, L)}</b> km²
            <span>{t.flooded}</span>
          </div>
          <div>
            <b>{km2(card.observed_km2, L)}</b> km²
            <span>
              {t.looked} {t.inAois(card.aois)}
            </span>
          </div>
        </div>
        <div className="bar-share" aria-hidden>
          <i style={{ width: `${Math.max(0.5, Math.min(100, (100 * card.flooded_km2) / (card.observed_km2 || 1)))}%` }} />
        </div>
        <p className="muted">
          {share(card.flooded_km2, card.observed_km2, L)} · {t.floodedNote}
        </p>
      </section>

      <section>
        <h3>
          {t.productsTitle} <small>{t.productsHint}</small>
        </h3>
        <table>
          <thead>
            <tr>
              <th>{t.product}</th>
              <th>{t.seen}</th>
              <th className="num">{t.water}</th>
            </tr>
          </thead>
          <tbody>
            <tr className={product === null ? "active" : ""} onClick={() => onProduct(null)}>
              <th colSpan={3}>
                <button type="button" className="link" aria-pressed={product === null}>
                  {t.all}
                </button>
              </th>
            </tr>
            {card.products.map((row) => (
              <tr
                key={row.product}
                className={product === row.product ? "active" : ""}
                onClick={() => onProduct(product === row.product ? null : row.product)}
              >
                <th>
                  <button type="button" className="link" aria-pressed={product === row.product}>
                    {row.aoi}
                  </button>
                  <small>
                    {row.product} · {t.kinds[row.type] ?? row.type}
                    {row.sensors.length ? ` · ${row.sensors.join(", ")}` : ""}
                  </small>
                </th>
                <td className="when">
                  {day(row.observed_time, L)}
                  <small>{after(card.event_time, row.observed_time)}</small>
                </td>
                <td className="num">
                  {km2(row.flooded_km2, L)} km²
                  <small>{share(row.flooded_km2, row.observed_km2, L)}</small>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {card.without_layer > 0 && <p className="muted">{t.withoutLayer(card.without_layer)}</p>}
      </section>

      <section>
        <details>
          <summary>{t.pythonTitle}</summary>
          <pre>
            <code>{card.python}</code>
          </pre>
        </details>
      </section>

      <footer>
        <a href={card.link} target="_blank" rel="noreferrer">
          {t.official} ↗
        </a>
      </footer>
    </article>
  );
}
