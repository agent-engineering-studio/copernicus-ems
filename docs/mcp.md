# MCP server for agents

`copernicus-ems` can run as a [Model Context Protocol](https://modelcontextprotocol.io/)
server, so an LLM agent such as Claude can answer questions like *"Which
floods were mapped in Italy this year, and how did the one in Emilia-Romagna
evolve?"* from the published layers, not from memory.

```bash
pip install 'copernicus-ems[mcp]'
copernicus-ems mcp            # stdio; --dir to choose the data directory
```

## Connect it

=== "Claude Code"

    ```bash
    claude mcp add copernicus-ems -- copernicus-ems mcp
    # without installing it first:
    claude mcp add copernicus-ems -- uvx --from 'copernicus-ems[mcp]' copernicus-ems mcp
    ```

=== "Claude Desktop"

    ```json
    {
      "mcpServers": {
        "copernicus-ems": {
          "command": "uvx",
          "args": ["--from", "copernicus-ems[mcp]", "copernicus-ems", "mcp"]
        }
      }
    }
    ```

## Tools

| Tool | Returns |
|---|---|
| `list_activations(country, categories, since, until, limit)` | activations newest first: code, name, category, event time, countries |
| `activation_summary(code, hazard)` | per product: type, observed time, event km², observed (AOI) km², bbox; per AOI: largest and latest delineation |
| `compare_products(code, aoi_number, hazard)` | one AOI's products in time order, with the change from the previous product **of the same type** |
| `export_observed_events(code, hazard, with_masks, file_format)` | writes GeoJSON or GeoParquet files (events, observation masks) and returns their **paths** |
| `find_scenes(code, aoi_number, days, sensors, max_cloud_cover, min_coverage, per_product)` | per product: the images it was made from, and the open Sentinel-1/2 passes over its AOI with time gap, AOI coverage, cloud cover, download URLs ([details](scenes.md)) |

`hazard` is `flood` (default), `landslide` or `all`. All tools are
read-only towards Rapid Mapping; only `export_observed_events` writes, in
`<data directory>/exports/`.

A prompt, **`situation_report(code)`**, asks the agent for a factual report
built only from these tools.

## Design

- **Facts, not geometry.** EMSR664 is 6,334 polygons; its
  `activation_summary` is about 4 KB of JSON. Geometry goes to files, for
  GIS tools, through `export_observed_events`.
- **Numbers are computed, never generated.** Areas are geodesic, from the
  layers (the [`geo`](ml.md) module); the model only picks the calls and
  writes the prose.
- **The semantics travel with the server.** Its instructions tell the agent
  what the data means: outside the AOIs is *unknown*, not dry;
  `observed_time` is when the water was seen; products of one AOI are not
  summed; a grading map (`GRA`) after a delineation (`DEL`) is not the
  flood receding, so changes are only computed within one product type.
- **Errors an agent can act on**: a bad code, date or AOI comes back as a
  tool error saying what was expected.
- **Cached**: layers are read from the data directory after the first
  call, as everywhere else in the library.

## Example

On EMSR664 (Emilia-Romagna, May 2023), `compare_products("EMSR664", 1)`:

| Product | Type | Observed | Event km² | Change |
|---|---|---|---|---|
| AOI01_DEL_PRODUCT | DEL | 17 May 16:56 | 87.92 | — |
| AOI01_DEL_MONIT01 | DEL | 18 May 16:55 | 112.06 | +24.14 |
| AOI01_DEL_MONIT02 | DEL | 20 May 05:19 | 87.69 | −24.37 |
| AOI01_DEL_MONIT03 | DEL | 21 May 16:50 | 44.74 | −42.95 |
| AOI01_GRA_PRODUCT | GRA | 22 May 10:27 | 7.70 | — |

> Contains modified Copernicus Emergency Management Service information, © European Union.
