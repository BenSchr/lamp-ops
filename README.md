# Lamp Ops

<p align="center"><img src="assets/logo_wordmark.png" alt="Lamp Ops logo" width="560"></p>

`lamp` promotes Databricks AI/BI dashboard and Genie JSON between configured
environments and statically checks asset configuration.

## Contents

<!-- BEGIN GENERATED TOC -->
- [Contents](#contents)
- [Setup](#setup)
- [Configuration](#configuration)
- [Promotion](#promotion)
- [Linting](#linting)
  - [Genie lint rule reference](#genie-lint-rule-reference)
- [AI usage](#ai-usage)
- [Development](#development)
- [CLI reference](#cli-reference)
<!-- END GENERATED TOC -->

## Setup

Install the locked development environment with `uv sync`; the devcontainer runs
`mise run sync` automatically when it is created. Run the CLI with `uv run lamp`;
it searches the current directory and its ancestors for
`lamp_config.yaml`, `lamp_config.yml`, `.lamp_config.yaml`, or `.lamp_config.yml`
(in that order within each directory). Override discovery with the root option
`--config/-c` before the command name.

## Configuration

Asset paths in the configuration are relative to `root_path` and may not
be absolute or escape it. Set a base path for each asset kind and omit
`items` to discover every canonical source automatically:

```yaml
assets:
  dashboards:
    path: ./dashboards
  genie:
    path: ./geniexy
```

Shared `settings` apply to every discovered asset.

For bundle-managed Genie descriptions, set the top-level `dab_resource_path` to a
directory **relative to the config file**, even when `root_path` points to
`src`. Bundle resource YAML often lives beside `src`, not inside it: with
`root_path: src`, the path below finds `<config-dir>/resources/genie_agents`,
whereas resolving it under `root_path` would incorrectly look in
`<config-dir>/src/resources/genie_agents`. The directory must still stay inside
the config directory:

```yaml
dab_resource_path: resources/genie_agents
```

The short `mode` names choose the derived layout:

- `env` (default): `<path>/dev/sales.lvdash.json`
- `asset`: `<path>/sales/dev/sales.lvdash.json`

Genie uses the same layouts with `.geniespace.json`. Add an `items` entry only
when one asset needs an override such as `mode`, `source`, target `path`, or
replacement values. An item target list selects that asset's destinations;
replacement maps merge over same-named global targets.

Generate editor schema support with:

```text
uv run lamp config schema --output-path lamp_config.schema.json
```

For YAML Language Server, associate the generated schema in editor settings
instead of adding a non-standard `$schema` key to YAML:

```json
{
	"yaml.schemas": {
		"./lamp_config.schema.json": "lamp_config.yaml"
	}
}
```

## Promotion

`lamp promote` plans dashboards and Genie assets together. The asset-specific
commands accept optional identifiers or config-root-relative source paths.

```text
uv run lamp promote --dry-run
uv run lamp promote dashboards sales_insights
uv run lamp promote genie --force
```

Each task is classified before writing:

- **create** — the target does not exist.
- **update** — the source is newer, or `--force` was supplied.
- **skip** — the target is at least as new as the source.

Replacements affect JSON string values only; object keys and non-string values
are preserved. `--dry-run` prints the exact plan without writes.

## Linting

```text
uv run lamp lint --verbose --output-path reports/lint.json
uv run lamp lint --profile development
uv run lamp lint --offline
uv run lamp rub
```

`lamp lint` runs every available asset linter; future asset linters do not add
another CLI subcommand. By default it uses the Databricks SDK authentication
chain to enrich table and metric-view descriptions from Unity Catalog; pass
`--profile` (or `-p`) to select a named profile. Lint checks only each asset's
configured source target (normally `dev`), not its promotion targets. The
terminal report shows the source target and file path above each results table.
Shared Unity Catalog sources are fetched once per run.
Use `--offline` to avoid remote calls; metadata-dependent rules are then marked
`skipped` with a reason. Results are `pass`, `warn`, `error`, or `skipped`.
Bundle YAML files (`.yml` or `.yaml`) under `dab_resource_path` are indexed once
per lint run. The agent description is read from `resources.genie_spaces` by
matching its `file_path` to the JSON source, then by resource key to the asset
name. When the bundle path is unset or no matching resource is found, the
description check is skipped (unless a legacy JSON description exists and no
bundle path was configured). A matched resource with an empty description fails.
Every rule defaults to `severity: warn`; set `severity: error` when failure must
produce a non-zero exit. Concise output always includes warnings, errors, and
skipped checks, while `--verbose` also shows passing checks.

Rule criteria use descriptive settings instead of overloading `threshold`:

```yaml
lint:
  genie:
    rules:
      source_count:
        severity: error
        threshold: 0
        maximum_sources: 12
        warning_sources: 9
      benchmarks:
        severity: warn
        threshold: 0
        minimum_questions: 10
        recommended_questions: 20
```

`threshold` always counts failing Genie agents tolerated for that rule before
`severity: error` escalates the failures to errors. Settings such as
`maximum_sources` and `minimum_questions` define what makes one agent fail.

### Genie lint rule reference

<!-- BEGIN GENERATED GENIE LINT REFERENCE -->
| ID | Rule | Configuration key | Lower limit / pass condition | Upper limit / fail condition | What it checks | Non-blocking warning | Remediation |
|---:|---|---|---|---|---|---|---|
| 1 | Agent description | `agent_description` | At least 30 characters and 5 words | No blocking upper limit | Checks whether the bundle Genie resource description is present, meaningful, and not a placeholder. | Description is valid but under 100 characters. Recommendation: include domain, audience, and scope. | Add the agent domain, audience, and scope. |
| 2 | Table descriptions | `table_descriptions` | At least 80% of data sources have descriptions | No blocking upper limit; ideal is 100% | Checks description coverage for tables and metric views after Unity Catalog comments enrich missing descriptions. | Coverage is 80% to below 100%. It passes, but recommends documenting all tables. | Describe the tables used by the agent. |
| 3 | Column descriptions | `column_descriptions` | At least 50% of relevant/visible columns have descriptions | No blocking upper limit; ideal is 80% | Checks description coverage across columns represented in the Agent configuration, after Unity Catalog metadata enrichment. | Coverage is 50% to below 80%. | Describe visible table columns. |
| 4 | Text instructions | `instructions` | More than 51 total characters | No blocking upper limit | Checks whether business context exists in text_instructions. Text from all entries is considered together. | Total text exceeds 2000 characters, or SQL-like content is found. SQL should be moved to Example SQLs or SQL Expressions. | Add meaningful business-context instructions. |
| 5 | Join specifications | `joins` | With multiple ordinary tables, at least 1 join_spec | No blocking upper limit | Checks for explicit Genie join guidance when more than one ordinary table is configured. Metric views do not trigger the requirement. | Number of join specs is below ordinary table count minus 1, indicating potentially incomplete relationship coverage. | Define joins for agents with multiple ordinary tables. |
| 6 | Data source count | `source_count` | At least 1 table or metric view | Maximum 12 tables plus metric views | Checks that the Agent has data and that its source scope is not excessively broad. Tables and metric views both count. | 9-12 sources triggers a recommendation to split the domain into more focused Agents. | Reduce sources or split the domain across agents. |
| 7 | SQL guidance artifacts | `sql_guidance` | At least 1 SQL guidance artifact | No blocking upper limit | Checks for at least one SQL function, SQL expression, measure, filter, or example SQL query. | Warns when SQL snippets are incomplete, notably when measures or filters are missing. For example SQLs, a warning appears if more than 50% lack usage_guidance. | Add SQL functions, snippets, or example SQL. |
| 8 | Entity/format matching | `entity_format_matching` | At least 1 column with entity matching or format assistance | No blocking upper limit in the scored rule | Checks whether prompt matching is configured anywhere in the Agent. It does not assess whether the selected columns are appropriate. | More than 100 entity-matching columns warns that the Agent is approaching the 120-column limit. Above 120, excess columns are ignored. | Enable entity matching or format assistance on suitable columns. |
| 9 | Benchmark questions | `benchmarks` | At least 10 benchmark questions | No blocking upper limit | Checks whether the Agent contains enough benchmark questions for evaluation and optimization. | 10-20 questions passes, but recommends adding more for broader coverage. | Add representative benchmark questions. |
| 10 | Column visibility / noise control | `noisy_columns` | Passes unless both noise conditions are reached | Fails when there are at least 20 visible columns and at least 30% appear noisy/internal | Checks whether the Agent exposes too many technical, audit, raw, opaque, or internal-looking columns. | At least 20 visible columns with 15% or more noisy columns triggers an early warning. A separate warning appears when one table exposes more than 75 visible columns. | Hide internal and noisy columns. |

Default configuration:

```yaml
lint:
  genie:
    exclusions: []
    rules:
      agent_description:
        enabled: true
        severity: warn
        threshold: 0
        minimum_characters: 30
        minimum_words: 5
        recommended_characters: 100
      table_descriptions:
        enabled: true
        severity: warn
        threshold: 0
        minimum_coverage: 0.8
        recommended_coverage: 1.0
      column_descriptions:
        enabled: true
        severity: warn
        threshold: 0
        minimum_coverage: 0.5
        recommended_coverage: 0.8
      instructions:
        enabled: true
        severity: warn
        threshold: 0
        minimum_characters: 51
        maximum_characters: 2000
      joins:
        enabled: true
        severity: warn
        threshold: 0
      source_count:
        enabled: true
        severity: warn
        threshold: 0
        maximum_sources: 12
        warning_sources: 9
      sql_guidance:
        enabled: true
        severity: warn
        threshold: 0
        minimum_artifacts: 1
      entity_format_matching:
        enabled: true
        severity: warn
        threshold: 0
        minimum_enabled_columns: 1
        entity_warning_columns: 100
        entity_maximum_columns: 120
      benchmarks:
        enabled: true
        severity: warn
        threshold: 0
        minimum_questions: 10
        recommended_questions: 20
      noisy_columns:
        enabled: true
        severity: warn
        threshold: 0
        minimum_visible_columns: 20
        warning_ratio: 0.15
        maximum_ratio: 0.3
        maximum_visible_per_table: 75
```
<!-- END GENERATED GENIE LINT REFERENCE -->

`rub` is an alias for the same discovery, checks, reporting, and exit-code path
as `lint`.

## AI usage

The [lamp-ops skill](skills/lamp-ops/SKILL.md) teaches AI agents
how to set up `lamp_config.yaml`, preview promotions, and lint Genie sources.
It ships in two forms:

- **Claude Code plugin:** from this repository, start Claude with
  `claude --plugin-dir .` and invoke `/lamp-ops:lamp-ops` (or ask
  Claude to set up or use lamp-ops). This loads the plugin locally without
  installing a marketplace plugin.
- **Library Skills:** in a project with `lamp-ops` installed (for a local
  checkout, `uv add --editable /path/to/lamp_ops`), run
  `uvx library-skills --skill lamp-ops`. It discovers the skill bundled in the
  Python package and installs a managed link in `.agents/skills` for compatible
  agents. For Claude Code, add `--claude` to install in `.claude/skills` too.

Edit the plugin skill as the source of truth, then run `mise run skill-sync`
to copy it into `src/lamp_ops/.agents/skills/lamp-ops/`. `mise run skill-check`
detects drift without changing files; `mise run verify` includes this check.

## Development

Update generated documentation after changing CLI help or README headings:

```text
mise run readme
mise run readme-check
```

Run the complete local verification with `mise run verify`.

## CLI reference

<!-- BEGIN GENERATED CLI REFERENCE -->
### `lamp`

```text
                                                                                                                                                                                                                 
 Usage: lamp [OPTIONS] COMMAND [ARGS]...                                                                                                                                                                         
                                                                                                                                                                                                                 
 Promote and lint Databricks assets.                                                                                                                                                                             
                                                                                                                                                                                                                 
╭─ Options ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --config              -c                <path>  Path to lamp_config.yaml.                                                                                                                                     │
│ --debug                   --no-debug            Show full tracebacks on errors. [default: no-debug]                                                                                                           │
│ --install-completion                            Install completion for the current shell.                                                                                                                     │
│ --show-completion                               Show completion for the current shell, to copy it or customize the installation.                                                                              │
│ --help                -h                        Show this message and exit.                                                                                                                                   │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ lint     Lint all configured assets.                                                                                                                                                                          │
│ rub      Polish assets with the same checks as `lint`.                                                                                                                                                        │
│ promote  Plan and promote configured assets.                                                                                                                                                                  │
│ config   Inspect Lamp configuration support.                                                                                                                                                                  │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

### `lamp promote`

```text
                                                                                                                                                                                                                 
 Usage: lamp promote [OPTIONS] COMMAND [ARGS]...                                                                                                                                                                 
                                                                                                                                                                                                                 
 Plan and promote configured assets.                                                                                                                                                                             
                                                                                                                                                                                                                 
╭─ Options ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --dry-run      --no-dry-run      Print the plan without writing files. [default: no-dry-run]                                                                                                                  │
│ --force        --no-force        Update targets regardless of modification time. [default: no-force]                                                                                                          │
│ --help     -h                    Show this message and exit.                                                                                                                                                  │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ dashboards  Promote configured dashboard assets.                                                                                                                                                              │
│ genie       Promote configured Genie assets.                                                                                                                                                                  │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

### `lamp promote dashboards`

```text
                                                                                                                                                                                                                 
 Usage: lamp promote dashboards [OPTIONS] [assets]...                                                                                                                                                            
                                                                                                                                                                                                                 
 Promote configured dashboard assets.                                                                                                                                                                            
                                                                                                                                                                                                                 
╭─ Arguments ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│   assets      <str>  Optional asset names or paths.                                                                                                                                                           │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Options ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --dry-run      --no-dry-run      Print the plan without writing files. [default: no-dry-run]                                                                                                                  │
│ --force        --no-force        Update regardless of modification time. [default: no-force]                                                                                                                  │
│ --help     -h                    Show this message and exit.                                                                                                                                                  │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

### `lamp promote genie`

```text
                                                                                                                                                                                                                 
 Usage: lamp promote genie [OPTIONS] [assets]...                                                                                                                                                                 
                                                                                                                                                                                                                 
 Promote configured Genie assets.                                                                                                                                                                                
                                                                                                                                                                                                                 
╭─ Arguments ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│   assets      <str>  Optional asset names or paths.                                                                                                                                                           │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Options ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --dry-run      --no-dry-run      Print the plan without writing files. [default: no-dry-run]                                                                                                                  │
│ --force        --no-force        Update regardless of modification time. [default: no-force]                                                                                                                  │
│ --help     -h                    Show this message and exit.                                                                                                                                                  │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

### `lamp lint`

```text
                                                                                                                                                                                                                 
 Usage: lamp lint [OPTIONS] [assets]...                                                                                                                                                                          
                                                                                                                                                                                                                 
 Lint all configured assets.                                                                                                                                                                                     
                                                                                                                                                                                                                 
╭─ Arguments ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│   assets      <str>  Optional asset names or paths.                                                                                                                                                           │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Options ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --verbose      -v                   Also show passing checks.                                                                                                                                                 │
│ --output       -o      <text|json>  Console report format: text or json. [default: text]                                                                                                                      │
│ --output-path          <path>       Write a structured report to this path.                                                                                                                                   │
│ --profile      -p      <str>        Databricks CLI profile for Unity Catalog metadata.                                                                                                                        │
│ --offline                           Skip Unity Catalog metadata checks.                                                                                                                                       │
│ --help         -h                   Show this message and exit.                                                                                                                                               │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

### `lamp rub`

```text
                                                                                                                                                                                                                 
 Usage: lamp rub [OPTIONS] [assets]...                                                                                                                                                                           
                                                                                                                                                                                                                 
 Polish assets with the same checks as `lint`.                                                                                                                                                                   
                                                                                                                                                                                                                 
╭─ Arguments ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│   assets      <str>  Optional asset names or paths.                                                                                                                                                           │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Options ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --verbose      -v                   Also show passing checks.                                                                                                                                                 │
│ --output       -o      <text|json>  Console report format: text or json. [default: text]                                                                                                                      │
│ --output-path          <path>       Write a structured report to this path.                                                                                                                                   │
│ --profile      -p      <str>        Databricks CLI profile for Unity Catalog metadata.                                                                                                                        │
│ --offline                           Skip Unity Catalog metadata checks.                                                                                                                                       │
│ --help         -h                   Show this message and exit.                                                                                                                                               │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

### `lamp config schema`

```text
                                                                                                                                                                                                                 
 Usage: lamp config schema [OPTIONS]                                                                                                                                                                             
                                                                                                                                                                                                                 
 Print or write the complete lamp_config.yaml JSON Schema.                                                                                                                                                       
                                                                                                                                                                                                                 
╭─ Options ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --output-path                    <path>  Write schema to this path.                                                                                                                                           │
│ --force            --no-force            Overwrite an existing output file. [default: no-force]                                                                                                               │
│ --help         -h                        Show this message and exit.                                                                                                                                          │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```
<!-- END GENERATED CLI REFERENCE -->