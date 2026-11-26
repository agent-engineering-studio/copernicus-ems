"use client";

import { useText } from "@/lib/i18n";
import type { Agents as AgentsData } from "@/lib/types";

const TOOLS = [
  "list_activations",
  "activation_summary",
  "compare_products",
  "export_observed_events",
  "find_scenes",
] as const;

/** For AI agents: the same data as MCP tools, whether the server is on, how to connect. */
export default function Agents({ agents }: { agents: AgentsData }) {
  const t = useText();
  return (
    <section className="agents">
      <h3>
        {t.agentsTitle}{" "}
        <small className={agents.on ? "on" : "off"}>{agents.on ? t.mcpOn : t.mcpOff}</small>
      </h3>
      <p>{t.agentsText}</p>
      <ul className="tools">
        {TOOLS.map((tool) => (
          <li key={tool}>
            <code>{tool}</code> <span>{t.tools[tool]}</span>
          </li>
        ))}
      </ul>
      <details open={agents.on}>
        <summary>{agents.on ? t.connectHttp : t.startMcp}</summary>
        <pre>
          <code>
            {agents.on
              ? `claude mcp add --transport http copernicus-ems ${agents.url}`
              : "make up\n# or: docker compose --profile mcp up -d"}
          </code>
        </pre>
      </details>
      <details>
        <summary>{t.connectStdio}</summary>
        <pre>
          <code>{"claude mcp add copernicus-ems -- uvx --from 'copernicus-ems[mcp]' copernicus-ems mcp"}</code>
        </pre>
      </details>
      <p className="muted">{t.agentsTry}</p>
    </section>
  );
}
