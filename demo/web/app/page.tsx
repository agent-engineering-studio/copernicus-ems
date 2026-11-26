import Demo from "@/components/Demo";
import type { Agents } from "@/lib/types";

type Params = Promise<Record<string, string | string[] | undefined>>;

/** Whether the MCP server answers: asked by the page server, inside the network of compose. */
async function agents(): Promise<Agents> {
  const url = process.env.COPERNICUS_EMS_MCP_URL || "http://127.0.0.1:8770/mcp";
  const inside = process.env.COPERNICUS_EMS_MCP;
  if (!inside) return { url, on: false };
  try {
    const answer = await fetch(`${inside}/health`, { cache: "no-store", signal: AbortSignal.timeout(1500) });
    return { url, on: answer.ok };
  } catch {
    return { url, on: false };
  }
}

// Read when the page is asked for, not when it is built: /?code=EMSR664 opens on that activation.
export default async function Page({ searchParams }: { searchParams: Params }) {
  const asked = (await searchParams).code;
  const code = (Array.isArray(asked) ? asked[0] : asked)?.trim().toUpperCase() ?? "";
  return <Demo first={/^EMSR\d{1,5}$/.test(code) ? code : null} agents={await agents()} />;
}
