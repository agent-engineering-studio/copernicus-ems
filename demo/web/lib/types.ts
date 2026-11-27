// The answers of demo/api.py, as the page reads them.

export type Failed = { error: string };

export type Row = {
  code: string;
  name: string;
  category: string;
  event_time: string;
  closed: boolean;
  /** Where its areas of interest are, [lon, lat]; null if the detail did not answer. */
  point: [number, number] | null;
  /** True when `copernicus-ems init` saved it: it is read from disk. */
  saved: boolean;
};

export type Listing = { country: string; source: "disk" | "live"; activations: Row[] };

export type ProductRow = {
  product: string;
  aoi: string;
  type: string;
  observed_time: string | null;
  sensors: string[];
  features: number;
  flooded_km2: number;
  observed_km2: number;
};

export type CardData = {
  code: string;
  name: string;
  category: string;
  event_time: string;
  countries: string[];
  closed: boolean;
  products: ProductRow[];
  /** Products with no observed-event layer (ortho-images only, or never delivered). */
  without_layer: number;
  aois: number;
  flooded_km2: number;
  observed_km2: number;
  saved: boolean;
  link: string;
  python: string;
};

export type Shape = {
  kind: "mask" | "flood";
  product: string;
  aoi: string;
  observed_time: string | null;
  km2?: number;
};

export type MapData = GeoJSON.FeatureCollection<GeoJSON.Geometry, Shape>;

/** The MCP server of the library, as the page server found it when the page was asked for. */
export type Agents = {
  /** Where an agent on this machine connects: http://127.0.0.1:8770/mcp. */
  url: string;
  /** True when its /health answered: `make up` (profile mcp) started it. */
  on: boolean;
};

/** What the radar check says of a map: the words of `copernicus-ems quality`. */
export type Verdict = "coherent" | "to check" | "not assessable";

export type RadarFlag = { code: string; text: string; numbers: Record<string, number> };

/** One product's radar results, as read from the files beside its Sentinel-1 pair. */
export type RadarEntry = {
  verdict: Verdict | null;
  reason: string | null;
  flags: RadarFlag[];
  /** False when the map was made from the same radar image: then it cannot confirm it. */
  independent: boolean | null;
  gap_hours: number | null;
  km2: { dry: number; confirmed: number; map_only: number; candidate: number; radar_scattered: number } | null;
  scores: Record<string, number | null> | null;
  computed: boolean | null;
  files: string[];
  resolution_m: number | null;
};

export type RadarData = {
  /** Products with results on disk, of `products` in the card. */
  checked: number;
  products: number;
  /** What makes the missing ones: `make radar CODE=…`. */
  command: string;
  results: Record<string, RadarEntry>;
};

export type RadarShape = {
  class: "confirmed" | "map_only" | "candidate" | string;
  km2: number | null;
  product: string;
  independent: boolean | null;
};

export type RadarMap = GeoJSON.FeatureCollection<GeoJSON.Geometry, RadarShape>;
