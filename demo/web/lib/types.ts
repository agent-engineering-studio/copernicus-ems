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
