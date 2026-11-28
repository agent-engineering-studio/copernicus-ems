// Server only: read by the pages on the server, never shipped to the browser (it reads the environment).

import type { Agents } from "./types";

/** Whether the MCP server answers: asked by the page server, inside the network of compose. */
export async function agents(): Promise<Agents> {
  const url = process.env.COPERNICUS_EMS_MCP_URL || "http://127.0.0.1:8769/mcp";
  const inside = process.env.COPERNICUS_EMS_MCP;
  if (!inside) return { url, on: false };
  try {
    const answer = await fetch(`${inside}/health`, { cache: "no-store", signal: AbortSignal.timeout(1500) });
    return { url, on: answer.ok };
  } catch {
    return { url, on: false };
  }
}
