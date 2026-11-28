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
  /** Where an agent on this machine connects: http://127.0.0.1:8769/mcp. */
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

/** A name the model read in the official text, with the words it read it from. */
export type Quoted = { name: string; quote: string };

/**
 * `<data directory>/<CODE>/description.json`, as `copernicus-ems describe` wrote it:
 * a description written by a language model from the official text and the library's numbers.
 */
export type DescriptionFile = {
  activation: string;
  generated_at: string;
  provider: "claude" | "local";
  model: string;
  prompt_version: number;
  inputs_hash: string;
  source: {
    code: string;
    name: string;
    reason: string;
    bulletins: { title: string; url: string }[];
    related: string[];
  };
  facts: Record<string, unknown>;
  /** null when the checks rejected it: `reason` says why. */
  description: { it: string; en: string } | null;
  reason: string | null;
  places: Quoted[];
  rivers: Quoted[];
  related_events: { date: string; what: string; quote: string }[];
  casualties: { deaths: number; quote: string } | null;
  /** What the checks removed from the model's answer, and why. */
  dropped: { field: string; value: unknown; why: string }[];
};

/** No file on disk, or one that could not be read. */
export type NoDescription = { available: false };

export type DescriptionData = DescriptionFile | NoDescription;

/** What one saved activation observed over the place: `copernicus_ems.places.PlaceWater`. */
export type PlaceActivation = {
  activation_code: string;
  name: string;
  event_time: string;
  place_km2: number;
  /** The part of the place a first-issue product looked at. */
  observed_km2: number;
  /** `observed_km2` over `place_km2`, 0..1: the rest was not looked at, which is not "dry". */
  observed_share: number;
  /** The water observed over the place: a lower bound. */
  water_km2: number;
  /** The water as a share of the observed part, never of the whole place. */
  water_share: number | null;
  observed_from: string | null;
  observed_to: string | null;
  /** The first-issue products that looked at the place; `wet_products` those that saw water there. */
  products: string[];
  wet_products: string[];
  /** Monitoring products over the place: the same place on a later day, not added to the water. */
  later_products: string[];
  /** The radar verdict of the products used, where the check was run. */
  verdicts: Record<string, Verdict>;
  attribution: string;
  caveat: string;
};

/** GET /api/place: the water each saved activation observed over one place (a municipality). */
export type PlaceData = {
  /** False when there is no usable boundary file. */
  configured: boolean;
  /** False when `copernicus-ems init` has not run: there are no activations to read. */
  saved: boolean;
  name: string | null;
  place_km2: number | null;
  boundary: GeoJSON.Feature<GeoJSON.Geometry, { kind: "place" }> | null;
  /** The credit of the boundary file. */
  credit: string | null;
  /** Newest first. Empty: no saved activation looked at the place, which is not "no water". */
  activations: PlaceActivation[];
  caveat: string;
  python: string;
};
