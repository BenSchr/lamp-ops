---
name: lamp-ops
description: 'USE FOR: install lamp-ops; configure lamp_config.yaml; promote Databricks dashboard or Genie JSON between environments; lint Genie assets with lamp. DO NOT USE FOR: deploying assets to Databricks.'
---

# Lamp Ops

**UTILITY SKILL** — local JSON promotion and Genie lint.

## USE FOR:

- "Set up lamp-ops" / "configure lamp_config.yaml"
- "Promote dashboards from dev to test or prod"
- "Promote Genie agent JSON"
- "Lint Genie sources" / "rub assets"

## DO NOT USE FOR:

- Deploying assets to Databricks; use deployment tooling instead.

## Routing

**INVOKES:** `lamp` (`uv run lamp` in this repository). Online lint requires an
authenticated Databricks profile; let the user choose, never guess.

**FOR SINGLE OPERATIONS:** use a command below; see the README for options.

## Setup

Install `lamp-ops` (`uv sync` here). Create `lamp_config.yaml` in the asset
project; `lamp` searches parent directories, or specify `--config PATH` before
the subcommand. Minimal example:

```yaml
schema_version: 2
root_path: .
settings:
   source_target: dev
   targets:
      - name: test
assets:
   dashboards: {path: dashboards}
   genie: {path: genie}
```

Put sources at `dashboards/dev/sales.lvdash.json` and
`genie/dev/sales.geniespace.json`; omitted `items` discovers all sources.
Asset paths stay inside `root_path`; `dab_resource_path` for bundle descriptions
is relative to the config file.

## Examples

- `lamp promote --dry-run` previews writes; `lamp promote dashboards sales`
   or `lamp promote genie sales` selects one asset. `--force` overwrites newer
   destinations. Replacements change JSON string values, not keys.
- `lamp lint --offline` checks source files without remote metadata; for
   Unity Catalog enrichment, select a profile with `databricks auth profiles`,
   then run `lamp lint --profile NAME`. `lamp rub` aliases `lamp lint`.

## Troubleshooting

Config not found? Pass `--config PATH` before the command. Metadata skipped?
Check authentication; `--offline` intentionally skips remote checks.