# 0.0.2 (2026-11-10)

## Breaking changes

- `lamp config schema` no longer has `--force`. `--output-path` (like the report
  outputs of `lamp lint` and `lamp policy`) replaces an existing file but never a
  directory.
- The configuration JSON Schema `$id` is now
  `https://schemas.lamp-ops.dev/lamp-config.json`, and the
  `x-lamp-schema-version` key is gone. Regenerate saved schemas with
  `lamp config schema`.

## Features

- `lamp policy` checks `databricks bundle summary` JSON with DuckDB SQL; every
  returned row is a violation. No Lamp configuration, credentials, or bundle hooks
  are needed.
  - Policies come from SQL files and YAML packs (`--policy-path`,
    `LAMP_POLICY_PATH`, or `policy_path` and inline `lamp_policies` in the Lamp
    configuration), with severities, target filters, and bound `--param` values.
  - Built-in presets: `deny_resource`, `require_tags`, `require_preset_tags`,
    `require_permissions`, `require_bundle_permissions`, `require_mutators`,
    `require_fields`, `forbid_fields`, and `allowed_field_values`.
  - Query tables named after the summary keys (`summary`, `bundle`, `resources`,
    `variables`, `presets`, `permissions`) plus the `resource_permissions` and
    `resource_tags` helper views. `resources.modified_status` exposes deployment
    state, and resource presets skip resources the deployment deletes. `variables`
    spreads the bundle variable fields (`value`, `"default"`, `description`, `type`,
    `lookup`, `sensitive`) into columns and keeps the declaration in `config`.
  - `--schema` adds typed `bundle_resources.<type>` tables from the Databricks
    bundle JSON Schema; `--store` saves the query database for exploration.
  - Text output lists warnings and errors; `--verbose` adds passing checks and
    skipped checks with the reason. JSON reports include a `reason` per check.
- Native bundle mutators `lamp_ops.bundles:mutate_genie_spaces` and
  `lamp_ops.bundles:mutate_dashboards` retarget Genie agents and dashboards at
  bundle load time from the `lamp_replacements` bundle variable, without
  writing files or reading the Lamp configuration. They need the optional
  `databricks-bundles` package (`lamp-ops[bundles]` or a separate install).
- `lamp lint --path` lints Genie JSON files, or directories searched recursively
  for `*.geniespace.json`, without a Lamp configuration or the `<path>/<env>/`
  layout, so mutator-only bundles can be linted. The new `lint.path` key
  does the same from a configuration; a configuration, when found, still tunes
  rules and exclusions. `--dab-resource-path` overrides `dab_resource_path`.
- `lamp lint --target`/`-t` (default: the new `lint.target` key) lints the
  promoted copies of configured assets for that target, and prefers that target's
  bundle `genie_spaces` for descriptions. The text report now labels it `Target:`.
- `LAMP_LINT_PATH` and `LAMP_LINT_TARGET` set `lamp lint --path` and `--target`
  (over `lint.path` and `lint.target`), so CI can inject them. The README lists
  every Lamp environment variable, generated from the CLI options.
- Without `dab_resource_path`, `lamp lint` reads agent descriptions from the
  nearest `resources` directory around each Genie file (up to 3 levels up and 2
  down, within the project), so typical bundles need no resource path. Spaces a
  discovered directory does not declare fall back to the JSON `description`.
- `lamp lint` and `lamp policy` JSON reports share one envelope
  (`schema_version`, `command`, `generated_at`).
- The configuration JSON Schema also validates policy YAML files and types every
  preset's parameters.
- Unknown top-level configuration keys are ignored, so configurations written for
  other Lamp versions still load. 0.0.1's `schema_version` key is no longer used
  and can be removed; the JSON Schema still flags unknown keys in editors.
- Commands other than `lamp policy` start without loading the policy models or
  DuckDB, so bundle mutators and other commands start faster.
- DuckDB is now a core dependency.

## Bug fixes

- `lamp promote` now sorts Genie `data_sources.tables` by `identifier` and
  `data_sources.volumes` by `path` after replacements, as the Genie API requires.
  Previously, sources were written in their original order, so a promoted agent
  could be rejected on deployment.
- `lamp lint` now finds Genie spaces declared under
  `targets.<name>.resources.genie_spaces` in `dab_resource_path`, preferring the
  asset's source target over top-level `resources`. Previously only top-level
  resources were indexed, so the agent description check was skipped.

# 0.0.1 (2026-09-23)

Initial setup