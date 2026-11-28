"use client";

import { day, failed, km2, share, WATER } from "@/lib/format";
import { useText, type Text } from "@/lib/i18n";
import type { Tone } from "@/lib/radar";
import type { Failed, PlaceActivation, PlaceData, Verdict } from "@/lib/types";

type Props = {
  /** GET /api/place: null while it is read. */
  place: PlaceData | Failed | null;
  /** Opens an activation, as the list does. */
  onOpen: (code: string) => void;
};

/** The words the card uses for a radar verdict: "to check" asks for a look, it is not a judgment. */
const SAID: Record<Verdict, Tone> = { coherent: "agrees", "to check": "look", "not assessable": "cannot" };

/** The verdicts of the maps used, counted: [tone, how many], in the order agrees, look, cannot. */
function screening(row: PlaceActivation): [Tone, number][] {
  const count = new Map<Tone, number>();
  for (const verdict of Object.values(row.verdicts)) {
    const tone = SAID[verdict] ?? "cannot";
    count.set(tone, (count.get(tone) ?? 0) + 1);
  }
  return (["agrees", "look", "cannot"] as Tone[]).flatMap((tone) => (count.has(tone) ? [[tone, count.get(tone)!]] : []));
}

/** One sentence that never gives the water without the part of the place that was looked at. */
function sentence(row: PlaceActivation, name: string, t: Text): string {
  const p = t.place;
  const whole = row.observed_share >= 0.995;
  const part = share(row.observed_km2, row.place_km2, t.lang);
  if (row.water_km2 <= 0) return whole ? p.wholeDry(name) : p.partDry(part, name);
  const water = km2(row.water_km2, t.lang);
  return whole ? p.whole(water, name) : p.part(water, part, name);
}

/** The water observed over one place (a municipality), activation by activation. */
export default function Place({ place, onOpen }: Props) {
  const t = useText();
  const p = t.place;
  const L = t.lang;
  if (place === null) {
    return (
      <section className="place">
        <p className="waiting">{t.reading}</p>
      </section>
    );
  }
  if (failed(place)) {
    return (
      <section className="place">
        <h3>{p.titleNoName}</h3>
        <p className="muted">{place.error || t.noAnswer}</p>
      </section>
    );
  }
  const name = place.name || p.fallbackName;
  const head = (
    <h3>
      {place.name ? p.title(place.name) : p.titleNoName}
      {place.place_km2 != null && <small>{p.area(km2(place.place_km2, L))}</small>}
    </h3>
  );
  if (!place.configured) {
    return (
      <section className="place">
        {head}
        <p>{p.notConfigured}</p>
        <p className="muted">{p.howConfigure}</p>
      </section>
    );
  }
  return (
    <section className="place">
      {head}
      {!place.saved ? (
        <p>{p.notSaved}</p>
      ) : place.activations.length === 0 ? (
        <p>{p.none(name)}</p>
      ) : (
        <>
          <p className="muted">{p.intro}</p>
          <ul className="place-rows">
            {place.activations.map((row) => {
              const seen = Math.max(0, Math.min(1, row.observed_share));
              const wet = row.place_km2 ? Math.max(0, Math.min(seen, row.water_km2 / row.place_km2)) : 0;
              const whole = seen >= 0.995;
              const pct = share(row.observed_km2, row.place_km2, L);
              const said = screening(row);
              return (
                <li key={row.activation_code}>
                  <button type="button" onClick={() => onOpen(row.activation_code)} aria-label={p.open(row.activation_code)}>
                    <b>{row.activation_code}</b>
                    <span>{row.name}</span>
                    <small>{day(row.event_time, L)}</small>
                  </button>
                  <div className="place-figures">
                    <div>
                      <b style={{ color: WATER }}>{km2(row.water_km2, L)}</b> km²
                      <span>{p.water}</span>
                    </div>
                    <div className={whole ? "" : "partial"}>
                      <b>{pct}</b>
                      <span>{p.observed}</span>
                    </div>
                  </div>
                  <div className="place-bar" role="img" aria-label={p.barLabel(pct)}>
                    <i className="seen" style={{ width: `${100 * seen}%` }} />
                    {wet > 0 && <i className="wet" style={{ width: `max(3px, ${100 * wet}%)` }} />}
                  </div>
                  <p className="place-key" aria-hidden>
                    <span>
                      <i className="swatch" style={{ background: WATER }} /> {p.keyWater}
                    </span>
                    <span>
                      <i className="swatch seen" /> {p.keySeen}
                    </span>
                    {!whole && (
                      <span>
                        <i className="swatch unknown" /> {p.unknown}
                      </span>
                    )}
                  </p>
                  <p className={whole ? "" : "place-partial"}>{sentence(row, name, t)}</p>
                  <p className="muted">
                    {p.maps(row.products.length)}
                    {row.later_products.length > 0 && ` · ${p.later(row.later_products.length)}`}
                  </p>
                  {said.length > 0 && (
                    <p className="place-radar">
                      <span className="muted">{p.radar}</span>
                      {said.map(([tone, n]) => (
                        <span key={tone} className={`verdict ${tone}`}>
                          {t.radar[tone as "agrees" | "look" | "cannot"]}
                          {Object.keys(row.verdicts).length > 1 && ` (${n})`}
                        </span>
                      ))}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
      <p className="muted">{p.caveat}</p>
      {place.credit && <p className="muted">{place.credit}</p>}
    </section>
  );
}
