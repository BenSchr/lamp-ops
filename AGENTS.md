# lamp-ops

> Keep this file up to date: if anything relevant at this level changes
> (project conventions, tooling, layer structure, important documentation links etc.), update this
> AGENTS.md as part of that change.

## Layout

- `src/lamp_ops/cli.py` owns root options, concise error handling, and command registration.
- `src/lamp_ops/commands/` contains thin public Typer handlers only.
- `src/lamp_ops/errors.py` owns `LampError`, the user-facing error every module raises.
- `src/lamp_ops/config.py` owns validated `lamp_config.yaml` settings and safe path resolution
  (`resolve_path` for `root_path`, `resolve_config_path` for config-file-relative paths).
- `src/lamp_ops/promotion.py` owns discovery, immutable plans, JSON writes,
  and Genie source ordering shared with bundle mutators.
- `src/lamp_ops/replacements.py` owns schema-independent literal JSON string substitutions.
- `src/lamp_ops/bundles.py` owns optional native DAB Genie and dashboard mutators;
  `databricks-bundles` is optional (the `bundles` extra or a separate install) and
  must stay aligned with the CLI.
- `src/lamp_ops/linting/results.py` owns generic `pass|warn|error` result models.
- `src/lamp_ops/linting/genie/` owns flat Genie static rules and scanning.
- `src/lamp_ops/reporting.py` owns terminal and JSON rendering; every JSON report shares one
  envelope (`schema_version`, `command`, `generated_at`), and file outputs replace existing files.
- `src/lamp_ops/policy/` owns external SQL/YAML checks, parameterized presets,
  and optional DuckDB tables for whole-bundle summary JSON. See `docs/policies.md`.
  Preset model docstrings and `Field` descriptions generate the `docs/policies.md`
  preset reference; `RELATIONS`, `HELPERS`, and `TYPED_METADATA` in `policy/database.py`
  generate its table reference, checked against a real database (`mise run readme`).
  Describe every new table, view, column, or macro there. Keep contributor and mise
  instructions in the README's Development section, not the user-facing policy guide.
  `policy/models.py` defines the discriminated `PolicyCheck` union for policy YAML
  files. `LampConfig.lamp_policies` stays untyped so config loading (and bundle
  mutators) skip the policy models; `policy/loader.py` validates inline checks with the
  same model, and `config_schema.py` types them in the JSON Schema. Import policy
  modules lazily from commands and keep them out of `config.py`. The loader compiles
  checks into SQL with bound parameters for `policy/runner.py`.
- `tests/` uses Typer runner and real temporary files; do not mock the filesystem.
- `demo/` is a staged DAB project: `setup` deploys one notebook job that creates
  both catalogs (Default Storage needs SQL, not DAB), `base` schemas, and data; `dev`/`prod`
  deploy its dashboard and Genie agent after setup has run. All targets use profile
  `private` and no development mode. See `demo/README.md` for deployment order.
- `.claude-plugin/plugin.json` defines the Claude Code plugin; `skills/lamp-ops/`
	holds its canonical skill. `scripts/sync_skill.py` copies it to
	`src/lamp_ops/.agents/skills/lamp-ops/`
	for Library Skills discovery from the installed Python package.

## Conventions

- Lamp configuration is YAML; discovery supports `lamp_config.yaml`, `lamp_config.yml`,
  `.lamp_config.yaml`, and `.lamp_config.yml` in each directory (in that order).
- `root_path` is relative to the config file; asset paths are relative to `root_path`
  and must remain inside it.
- Optional top-level `policy_path` (relative to the config file, inside its directory; defaults to
  an existing `policies/` beside it) plus inline `lamp_policies` are the `lamp policy` source
  only when neither `--policy-path` nor `LAMP_POLICY_PATH` is set.
- Top-level `dab_resource_path` is relative to the config file (not `root_path`),
  stays inside its directory, and indexes bundle `genie_spaces` YAML for linting.
- Each asset group may set `path`; omitted `items` are auto-discovered below that path.
- Asset mode `env` derives `<path>/<env>/<asset>.<suffix>`; mode `asset` derives
	`<path>/<asset>/<env>/<asset>.<suffix>`. Global settings may be overridden per named asset.
- Bundle mutators never read Lamp configuration: the required complex `lamp_replacements`
  bundle variable (per target) is their only replacement source, applied to every Genie
  space and dashboard. Hooks return inline JSON without source-file writes. Literal
  replacements are case-sensitive, longest-first, and may chain.
- Genie promotion and mutated bundle resources sort tables by `identifier` and volumes
  by `path` after substitutions; other arrays preserve their order.
- Genie lint rules use `severity: warn|error` and default to `warn`.
- `lamp lint` runs all asset linters on configured source targets only and enriches Genie
	source metadata from Unity Catalog; `--profile`/`-p` selects Databricks auth and
	`--offline` skips metadata-dependent rules.
- Rich concise output always includes warnings, errors, and skipped checks; `--verbose`
	additionally includes passing checks.
- Add public handlers in their own `commands/` module and register them only after the root app exists.
- `lamp policy` needs no Lamp configuration or workspace authentication; DuckDB is a
  core dependency, imported lazily so other commands start without it. Policies return
  violation rows. Generic tables cover all resource types; an explicit `--schema` generates typed resource tables without CLI/network calls.
  `--policy-path` overrides `LAMP_POLICY_PATH`, then the config source, then `./policies`;
  `--store` saves the query database to a DuckDB file for exploration.
- Use `typing.Annotated` for Typer parameters and keep business logic outside command modules.
- `.devcontainer/postCreateCommand.sh` runs `mise run sync` after mise-managed tools are updated.
- Update generated README sections with `mise run readme`.
- Bump versions with `mise run bump [level]` (default `patch`) and add a matching
  `CHANGELOG.md` entry.
- After editing the plugin skill, run `mise run skill-sync` and check it with
	`mise run skill-check`.

## Verification

Run `mise run verify`, or run its tasks individually:

- `mise run test`
- `mise run lint`
- `mise run format-check`
- `mise run typecheck`
- `mise run spelling` (code folders only)
- `mise run readme-check`
- `mise run skill-check`
- `mise run schema-check` (regenerate with `mise run schema` after config or policy model changes)
