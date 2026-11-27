"use client";

import L from "leaflet";
import { useEffect, useRef } from "react";
import { CANDIDATE, LOOKED, WATER, km2 } from "@/lib/format";
import type { Text } from "@/lib/i18n";
import { radarOf } from "@/lib/radar";
import type { MapData, RadarMap, RadarShape, Row } from "@/lib/types";

type Props = {
  rows: Row[];
  selected: string | null;
  /** The masks and the water of the selected activation, once read. */
  shapes: MapData | null;
  /** One product alone, or every product. */
  product: string | null;
  /** The radar classes of the selected product, once read: null when there are none. */
  radar: RadarMap | null;
  text: Text;
  onSelect: (code: string) => void;
};

/** How each class looks. A map made from the same image gets no "confirmed" look: its water is all alike. */
function radarStyle(shape: RadarShape): L.PathOptions {
  if (shape.class === "candidate")
    return { color: CANDIDATE, weight: 1, dashArray: "3 3", fillColor: CANDIDATE, fillOpacity: 1, className: "radar-candidate" };
  if (shape.independent === false) return { stroke: false, fillColor: WATER, fillOpacity: 0.6 };
  if (shape.class === "confirmed") return { stroke: false, fillColor: WATER, fillOpacity: 0.85 };
  return { stroke: false, fillColor: WATER, fillOpacity: 0.22 };
}

// Italy, with its seas: where the map opens and goes back to.
const ITALY: L.LatLngBoundsExpression = [
  [36.4, 6.6],
  [47.1, 18.6],
];

export default function MapView(props: Props) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const waterLayer = useRef<L.GeoJSON | null>(null);
  const onSelect = useRef(props.onSelect);
  useEffect(() => {
    onSelect.current = props.onSelect;
  });

  useEffect(() => {
    const m = L.map(box.current!, {
      zoomSnap: 0.25,
      // The map is in a page that scrolls: the wheel moves the page, the buttons zoom.
      scrollWheelZoom: false,
      zoomControl: false,
      minZoom: 4,
      maxZoom: 16,
    });
    m.fitBounds(ITALY);
    L.control.zoom({ position: "bottomright" }).addTo(m);
    L.control.scale({ imperial: false, position: "bottomright" }).addTo(m);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · ' +
        '<a href="https://rapidmapping.emergency.copernicus.eu/">Copernicus EMS</a> © European Union',
    }).addTo(m);
    // The masks under the radar, the radar under the outline of the water, the dots over everything.
    for (const [name, z] of [
      ["masks", 400],
      ["radar", 405],
      ["water", 410],
      ["dots", 450],
    ] as const) {
      m.createPane(name).style.zIndex = String(z);
    }
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
    };
  }, []);

  // One dot per activation, while none is open: an open one is drawn whole.
  const { rows, selected, text: t } = props;
  useEffect(() => {
    const m = map.current;
    if (!m || selected) return;
    const group = L.layerGroup().addTo(m);
    for (const row of rows) {
      if (!row.point) continue;
      const [lon, lat] = row.point;
      L.circleMarker([lat, lon], {
        pane: "dots",
        radius: 7,
        color: "#ffffff",
        weight: 2,
        fillColor: WATER,
        fillOpacity: 0.9,
      })
        .bindTooltip(`<b>${row.code}</b> · ${row.event_time.slice(0, 10)}<br>${row.name}`, {
          className: "tip",
          direction: "top",
        })
        .on("click", () => onSelect.current(row.code))
        .addTo(group);
    }
    return () => {
      group.remove();
    };
  }, [rows, selected]);

  // Back to Italy when nothing is selected.
  useEffect(() => {
    if (!selected) map.current?.fitBounds(ITALY, { animate: false });
  }, [selected]);

  // The activation: dashed boxes where the satellite looked, blue where it saw water.
  const { shapes, product, radar } = props;
  // With the radar on the map, the water is only outlined: the radar classes fill it.
  const radarOn = radarOf(radar, product).length > 0;
  useEffect(() => {
    const m = map.current;
    if (!m || !shapes) return;
    const chosen = shapes.features.filter((f) => !product || f.properties.product === product);
    if (!chosen.length) return;
    const masks = L.geoJSON(
      { type: "FeatureCollection", features: chosen.filter((f) => f.properties.kind === "mask") } as GeoJSON.FeatureCollection,
      {
        pane: "masks",
        style: { color: LOOKED, weight: 1.5, dashArray: "6 5", fillColor: LOOKED, fillOpacity: 0.06 },
        onEachFeature: (f, shape) =>
          shape.bindTooltip(`<b>${f.properties.aoi}</b> · ${f.properties.product}<br>${t.tipMask}`, {
            sticky: true,
            className: "tip",
          }),
      },
    ).addTo(m);
    const water = L.geoJSON(
      { type: "FeatureCollection", features: chosen.filter((f) => f.properties.kind === "flood") } as GeoJSON.FeatureCollection,
      {
        pane: "water",
        style: { color: WATER, weight: 0.6, fillColor: WATER, fillOpacity: 0.6 },
        onEachFeature: (f, shape) =>
          shape.bindTooltip(
            `<b>${f.properties.aoi}</b> · ${f.properties.product}<br>${t.tipFlood(km2(f.properties.km2 ?? 0, t.lang))}`,
            { sticky: true, className: "tip" },
          ),
      },
    ).addTo(m);
    m.fitBounds(masks.getBounds(), { padding: [24, 24], animate: false });
    waterLayer.current = water;
    return () => {
      masks.remove();
      water.remove();
      waterLayer.current = null;
    };
  }, [shapes, product, t]);

  // With the radar on, the water keeps only its outline: no fill to hide the radar or catch its pointer.
  // Restyled, not rebuilt, so the map does not move when the radar arrives.
  useEffect(() => {
    waterLayer.current?.setStyle(radarOn ? { fill: false, weight: 1 } : { fill: true, weight: 0.6 });
  }, [radarOn, shapes, product, t]);

  // How far the radar backs the selected map: confirmed, map only, candidate.
  useEffect(() => {
    const m = map.current;
    const chosen = radarOf(radar, product);
    if (!m || !chosen.length) return;
    const r = t.radar;
    const label = (shape: RadarShape) =>
      shape.class === "candidate"
        ? r.legendCandidate
        : shape.independent === false
          ? `${t.legendWater} · ${r.legendNotIndependent}`
          : shape.class === "confirmed"
            ? r.legendConfirmed
            : r.legendMapOnly;
    const layer = L.geoJSON({ type: "FeatureCollection", features: chosen } as GeoJSON.FeatureCollection, {
      pane: "radar",
      style: (f) => radarStyle(f!.properties as RadarShape),
      onEachFeature: (f, shape) => {
        const p = f.properties as RadarShape;
        const area = p.km2 == null ? "" : ` · ${km2(p.km2, t.lang)} km²`;
        shape.bindTooltip(`<b>${p.product}</b><br>${label(p)}${area}`, { sticky: true, className: "tip" });
      },
    }).addTo(m);
    return () => {
      layer.remove();
    };
  }, [radar, product, t]);

  return (
    <>
      {/* The hatch of the radar candidates: orange stripes, never the blue of mapped water. */}
      <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden focusable="false">
        <defs>
          <pattern id="radar-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill={CANDIDATE} fillOpacity="0.12" />
            <line x1="0" y1="0" x2="0" y2="6" stroke={CANDIDATE} strokeWidth="2.5" />
          </pattern>
        </defs>
      </svg>
      <div ref={box} className="map" aria-label={t.mapLabel} />
    </>
  );
}
