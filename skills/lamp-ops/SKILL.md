---
name: lamp-ops
description: 'USE FOR: install lamp-ops; configure lamp_config.yaml; promote Databricks dashboard or Genie JSON between environments; configure Lamp bundle mutators; lint Genie assets with lamp; check bundle SQL policies. DO NOT USE FOR: deploying assets to Databricks.'
---

# Lamp Ops

**UTILITY SKILL** — local JSON promotion and Genie lint.

## USE FOR:

- "Set up lamp-ops" / "configure lamp_config.yaml"
- "Promote dashboards from dev to test or prod"
- "Promote Genie agent JSON"
- "Configure bundle mutators for Genie agents or dashboards"
- "Lint Genie sources" / "rub assets"
- "Check bundle policies from a central SQL/YAML pack"

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

## Bundle mutators

The hooks need the optional `databricks-bundles` package (at least 1.22.1):
install `lamp-ops[bundles]`, or `databricks-bundles` separately at the version
matching the Databricks CLI. Register `lamp_ops.bundles:mutate_genie_spaces` and/or
`lamp_ops.bundles:mutate_dashboards` under `python.mutators` in `databricks.yml`,
with `python.venv_path: .venv`.

The hooks never read `lamp_config.yaml`. The required complex bundle variable
`lamp_replacements`, set per target, is their only replacement source and applies
to every Genie space and dashboard in the bundle (file-backed or inline). An empty
map changes nothing, so the source target needs no entries; a missing variable
fails the bundle load. Lamp exclusions and per-asset settings apply only to
`lamp promote`. Replacements are case-sensitive literal JSON-string substitutions,
longest search first, and may chain.

Hooks return inline serialized JSON, clearing `file_path` only on changed
resources, without writing source files or deploying. Use an unmodified authoring
target for `bundle generate --resource` workflows. See the README's bundle
mutators section for configuration examples and limitations.

## Troubleshooting

Config not found? Pass `--config PATH` before the command. Metadata skipped?
Check authentication; `--offline` intentionally skips remote checks.

## Bundle policies

Run `lamp policy --input summary.json --policy-path
/path/to/central/checks` on JSON from `databricks bundle summary -o json`.
This command requires no Lamp configuration, mutator, or live authentication.
SQL checks return violation rows; YAML configures severity, target filters,
bound parameters, and reusable presets under the top-level `lamp_policies` key.
`--param 'maximum=2'` overrides a
parameter. `--output json` and `--output-path report.json` produce structured reports.
`LAMP_POLICY_PATH` supplies policy paths when `--policy-path` is omitted; explicit
flags replace the environment setting. Otherwise, a Lamp config contributes inline
`lamp_policies` plus `policy_path` (default: `policies/` beside the config); without
a config, `./policies` is used.
`--store exploration.duckdb` saves the query database for manual exploration,
even when checks report violations, replacing an existing file.

Generic tables include the full `summary`, its top-level `bundle`, `variables`,
`presets`, `permissions`, and `resources` of any type, plus `resource_permissions`
and `resource_tags`. `resource('jobs')` works even when no jobs exist;
`field(config, '$.name')` extracts a scalar. Explicit `--schema bundle.schema.json`
adds typed `bundle_resources.<type>` tables, including empty resource types.
Lamp never obtains this schema automatically; users may capture
`databricks bundle schema` or download the file from a pinned CLI release tag.
`lamp config schema` types `lamp_policies` and each preset's parameters for config and policy YAML.
Use `require_mutators` to fail bundles that drop required platform Python mutators.
See `docs/policies.md` in the repository for the complete contract and presets.