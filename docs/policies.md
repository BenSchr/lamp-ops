# Bundle policies

`lamp policy` checks the JSON from `databricks bundle summary` with DuckDB SQL.
Every check is a query that returns **violation rows**: no rows means the check
passes. SQL files work on their own; YAML adds descriptions, parameters,
severity, target selection, and reusable presets.

Evaluating a saved summary needs no `lamp_config.yaml`, Databricks credentials,
or bundle mutator, so policies can live entirely outside the application repository.

## Contents

<!-- BEGIN GENERATED TOC -->
- [Contents](#contents)
- [Quick start](#quick-start)
- [Where policies come from](#where-policies-come-from)
- [Writing checks](#writing-checks)
- [Querying the summary](#querying-the-summary)
- [Table reference](#table-reference)
- [Typed resource tables](#typed-resource-tables)
- [Policy presets](#policy-presets)
- [CI enforcement and security](#ci-enforcement-and-security)
- [Testing policy packs](#testing-policy-packs)
<!-- END GENERATED TOC -->

## Quick start

```bash
pip install lamp-ops

# Generate this with your normal Databricks authentication and bundle variables.
databricks bundle summary -t prod -o json > summary.json

lamp policy --input summary.json --policy-path /opt/company-policies \
  --output-path policy-report.json
```

A minimal policy pack is a directory with one SQL file:

```sql
-- policies/no_schemas.sql
SELECT resource_key, 'Schemas are managed by the platform team' AS message
FROM resource('schemas');
```

| Outcome | Exit status |
|---|---|
| All checks pass, or only warnings | 0 |
| Any error-severity violation | 1 |
| Invalid input, SQL, preset, parameter, or schema | 1 |
| Missing or empty pack, or every check skipped by target | 1 |

Text output shows warnings and errors with every column of each violation row,
followed by a summary that also counts passing and skipped checks. Add
`--verbose` to also list passing checks and skipped checks with the reason, for
example `SKIPPED dev_only: target prod is not in the check's targets: dev`.
`--output json` prints the full report, where every check has a `reason` that is
set for skipped checks and `null` otherwise.

`--target` supplies the target when the summary has no `bundle.target`; when
both exist they must agree. Target-filtered checks require a known target.

## Where policies come from

`--policy-path` accepts a directory, a YAML properties file, or a single SQL
file, and can be repeated to combine packs. Lamp uses the **first** source that
is set:

1. `--policy-path` options.
2. `LAMP_POLICY_PATH`: one or more paths separated by the platform path
   separator (`:` on Linux/macOS, `;` on Windows).
3. The Lamp configuration (`--config` or the usual discovery): inline
   `lamp_policies` plus `policy_path`. `policy_path` is relative to the
   configuration file and must stay inside its directory; when unset, a
   `policies` directory beside the configuration is used if it exists.
   Inline `sql` paths are relative to the configuration file, and inline checks are
   validated when `lamp policy` runs, not by other commands that load the configuration.
4. `./policies`.

Sources do not merge across levels, so centrally injected CI policies cannot be
extended or weakened by repository files.

```bash
export LAMP_POLICY_PATH=/opt/company-policies
lamp policy --input summary.json
```

Directories are scanned recursively for `.sql`, `.yaml`, and `.yml` files.
Keep unrelated YAML files outside these directories.

## Writing checks

### SQL checks

A check is exactly one `SELECT` query; CTEs and `UNION`s are allowed. Result
column names must be unique and are otherwise free. Return `resource_key` and
`message` for readable reports, plus any useful details. Bundle-wide or
aggregate findings need no resource identity.

An SQL file without YAML becomes an error-severity check named after the file
(without `.sql`). Unconfigured SQL file names must be unique within a pack.

### YAML properties

Every YAML file holds only a top-level `lamp_policies` list; other keys are
rejected. Properties may be split across any number of files and subfolders:

```text
company-policies/
  _checks.yaml
  bundle_defaults.sql
  jobs/
    _jobs.yaml
    concurrency.sql
  permissions/
    _permissions.yaml
```

```sql
-- jobs/concurrency.sql
SELECT resource_key, 'Too much concurrency' AS message
FROM resource('jobs')
WHERE field(config, '$.max_concurrent_runs')::INTEGER > $maximum;
```

```yaml
# jobs/_jobs.yaml
lamp_policies:
  - name: production_concurrency
    sql: concurrency.sql
    description: Production jobs should not run too many concurrent instances
    parameters:
      maximum: 1
    config:
      severity: error
      targets: [prod]

  - name: development_concurrency
    sql: concurrency.sql
    parameters:
      maximum: 5
    config:
      severity: warn
      targets: [dev]
```

- `sql` paths resolve relative to the YAML file and must stay inside the pack
  directory (or the YAML file's directory when it is passed directly).
- An entry without `sql` configures the unconfigured SQL file of the same `name`.
- A SQL file referenced by `sql` is not also run as an unconfigured check, so one
  template can back several named checks.
- Check names must be unique across all packs.
- `config.severity` is `error` or `warn`; `config.targets` limits a check to
  bundle targets.

### Parameters

YAML `parameters` are defaults. Override them with the repeatable
`--param NAME=JSON`:

```bash
lamp policy --input summary.json --policy-path company-policies \
  --param 'maximum=2' --param 'team="platform"' --param 'teams=["platform","ops"]'
```

Parameters are DuckDB **bound values**, not Jinja or string interpolation: they
cannot substitute identifiers or SQL fragments. Queries receive only the
parameters they reference. CLI overrides that no check uses are errors. Preset
checks must set required parameters in YAML; overrides only replace declared
preset parameters, and misspelled preset parameters are errors.

### Editor schema

Policy checks are part of the Lamp configuration model, so the configuration
JSON Schema validates policy YAML files too, typing each preset's `parameters`:

```bash
lamp config schema --output-path lamp-config.schema.json
```

```yaml
# yaml-language-server: $schema=./lamp-config.schema.json
lamp_policies: []
```

## Querying the summary

Lamp loads the summary into the tables and views described in the
[table reference](#table-reference). They always exist, whatever resource types
the bundle contains. Two naming rules make them predictable:

- **Summary keys keep their names.** `bundle`, `resources`, `variables`,
  `presets`, and `permissions` hold the top-level summary keys of the same name,
  one row per entry. `summary` has one row with the whole document, for every
  other key such as `workspace`, `python`, or `run_as`.
- **Derived helpers are prefixed by their source.** `resource_permissions` and
  `resource_tags` flatten the nested `permissions` and `tags` of every resource.

Resource configuration stays raw JSON in `config` columns; `variables` also spreads
the bundle variable fields (`value`, `"default"`, `lookup`, ...) into columns.

There is **no hard-coded list of resource types**: new Databricks types are
available through `resources` and `resource(type)` without a Lamp update. The
flip side is that a misspelled type is indistinguishable from an absent one, so
test your packs.

### Reading fields

```sql
-- field(json, path) returns VARCHAR, or NULL for an absent path.
SELECT 'bundle' AS resource_key, 'Use a service principal' AS message
FROM summary
WHERE field(config, '$.run_as.service_principal_name') IS NULL;

-- resource(type) works for absent and future types: it returns no rows.
SELECT resource_key
FROM resource('future_resources')
WHERE field(config, '$.description') IS NULL;
```

- JSON array paths are 0-based: `$.tasks[0].task_key`.
- Cast explicitly for numbers and booleans (`field(...)::INTEGER`); invalid casts
  fail the check. Unresolved `${var...}` strings fail numeric casts too.
- DuckDB's JSON functions and `->` / `->>` operators also work; parenthesize the
  operators in comparisons because of their precedence.
- `field(...) <> 'x'` is unknown, not true, when the field is missing. Use
  `IS DISTINCT FROM`, `IS NULL`, or `coalesce`. `json_exists` distinguishes a
  missing property from an explicit JSON `null`.

### Iterating nested lists and maps

`json_each` turns a JSON array or object into rows with `key` and `value`:

```sql
SELECT r.resource_key, t.value ->> 'task_key' AS task_key,
       'Task timeout too high' AS message
FROM resource('jobs') r, json_each(r.config, '$.tasks') t
WHERE (t.value ->> 'timeout_seconds')::INTEGER > $maximum;
```

### Correlated conditions

Conditional checks are ordinary SQL. Scheduled, high-criticality jobs must give
the platform group `CAN_MANAGE`:

```sql
SELECT r.resource_key, 'Platform access required' AS message
FROM resource('jobs') r
WHERE json_exists(r.config, '$.schedule')
  AND field(r.config, '$.tags.criticality') = 'high'
  AND NOT EXISTS (
    SELECT 1
    FROM resource_permissions p
    WHERE p.resource_key = r.resource_key
      AND p.group_name = $platform_group
      AND p.level = 'CAN_MANAGE'
  );
```

The correlated subquery matches group and level on the **same** permission entry.

### Deleted resources

Resources removed from the bundle but still in deployment state appear with
`modified_status = 'deleted'` and carry only `id` and `url`; the next deploy
removes them. Resource presets skip them, but custom SQL can use them, for
example to guard against deletions:

```sql
SELECT resource_key, 'This deployment deletes the resource' AS message
FROM resources
WHERE modified_status = 'deleted' AND resource_type IN ('dashboards', 'genie_spaces');
```

### Permissions semantics

`permissions` is the top-level `permissions` list; `resource_permissions` holds
each resource's list as rendered by the summary. They can differ: a bundle may declare
`CAN_MANAGE` while the summary lists `IS_OWNER` on its jobs. Levels are matched
exactly; Lamp does not reimplement Databricks permission inheritance. Write
custom SQL if your organization wants hierarchy-aware matching.

### Exploring interactively

`--store PATH` writes the query database to a DuckDB file instead of memory:

```bash
lamp policy --input summary.json --policy-path /opt/company-policies \
  --store exploration.duckdb

duckdb exploration.duckdb   # separately installed DuckDB CLI
```

```sql
SHOW ALL TABLES;
SELECT resource_key, config FROM resource('jobs');
DESCRIBE SELECT * FROM resource_tags;
```

The file holds the input data (and typed tables with `--schema`), not check
results; use `--output-path` for the report. It is written even when checks
fail, replaces an existing file, and may be incomplete after an initialization
error. It contains the entire summary, including identities and workspace
details, so treat it as sensitive.

## Table reference

Generated from the database Lamp builds, so names and types match what your
queries see. All relations live in the default schema.

<!-- BEGIN GENERATED POLICY TABLES -->
| Relation | Kind | Columns |
|---|---|---|
| [`summary`](#summary) | table | `target`, `config` |
| [`bundle`](#bundle) | table | `name`, `value` |
| [`resources`](#resources) | table | `resource_key`, `resource_type`, `resource_name`, `config`, `modified_status` |
| [`variables`](#variables) | table | `name`, `value`, `default`, `description`, `type`, `lookup`, `sensitive`, `config` |
| [`presets`](#presets) | table | `name`, `value` |
| [`permissions`](#permissions) | view | `group_name`, `user_name`, `service_principal_name`, `level` |
| [`resource_permissions`](#resource_permissions) | view | `resource_key`, `resource_type`, `resource_name`, `group_name`, `user_name`, `service_principal_name`, `level` |
| [`resource_tags`](#resource_tags) | view | `resource_key`, `resource_type`, `resource_name`, `key`, `value` |

### `summary`

Exactly one row for the whole summary document.

| Column | Type | Description |
|---|---|---|
| `target` | `VARCHAR` | Effective target: `bundle.target`, or the `--target` option. |
| `config` | `JSON` | The **entire** summary JSON, e.g. `$.workspace`, `$.python`, `$.run_as`. |

```sql
SELECT field(config, '$.workspace.root_path') AS root_path FROM summary;
```

### `bundle`

One row per setting of the summary's `bundle` key.

| Column | Type | Description |
|---|---|---|
| `name` | `VARCHAR` | Setting name, e.g. `name`, `target`, or `git`. |
| `value` | `JSON` | Setting value JSON. |

```sql
SELECT field(value, '$') AS bundle_name FROM bundle WHERE name = 'name';
```

### `resources`

One row per resource of every type, including types Lamp does not know.

| Column | Type | Description |
|---|---|---|
| `resource_key` | `VARCHAR` | Unique key `resources.<type>.<name>`; return it to identify violations. |
| `resource_type` | `VARCHAR` | Resource type key from the summary, e.g. `jobs` or `genie_spaces`. |
| `resource_name` | `VARCHAR` | Resource key within its type, as written in the bundle. |
| `config` | `JSON` | The resource JSON as rendered by the summary. |
| `modified_status` | `VARCHAR` | Deployment state from the summary, e.g. `created` or `deleted`; NULL when the summary omits it. `deleted` resources carry only `id` and `url`. |

```sql
SELECT resource_key, field(config, '$.name') AS name FROM resources WHERE resource_type = 'jobs';
```

### `variables`

One row per top-level bundle variable; columns follow the bundle schema's variable fields. Quote `"default"`, a reserved SQL keyword.

| Column | Type | Description |
|---|---|---|
| `name` | `VARCHAR` | Variable name. |
| `value` | `JSON` | Resolved value JSON; NULL when the summary does not resolve it. |
| `default` | `JSON` | Configured default JSON, or NULL. |
| `description` | `VARCHAR` | Variable description, or NULL. |
| `type` | `VARCHAR` | Variable type, e.g. `complex`, or NULL. |
| `lookup` | `JSON` | Lookup object JSON, e.g. `{"warehouse": "Shared"}`, or NULL. |
| `sensitive` | `BOOLEAN` | Whether the variable holds a secret, or NULL when unset. |
| `config` | `JSON` | The complete variable declaration JSON, including any other fields. |

```sql
SELECT name, field(value, '$') AS resolved FROM variables WHERE "default" IS NULL;
```

### `presets`

One row per Databricks bundle `presets` setting (not Lamp policy presets).

| Column | Type | Description |
|---|---|---|
| `name` | `VARCHAR` | Setting name, e.g. `tags` or `name_prefix`. |
| `value` | `JSON` | Setting value JSON. |

```sql
SELECT t.key, t.value FROM presets, json_each(value) t WHERE name = 'tags';
```

### `permissions`

One row per entry of the top-level `permissions` list.

| Column | Type | Description |
|---|---|---|
| `group_name` | `VARCHAR` | Group principal, or NULL. |
| `user_name` | `VARCHAR` | User principal, or NULL. |
| `service_principal_name` | `VARCHAR` | Service principal application ID, or NULL. |
| `level` | `VARCHAR` | Exact permission level, e.g. `CAN_MANAGE` or `IS_OWNER`. |

```sql
SELECT * FROM permissions WHERE level = 'CAN_MANAGE';
```

### `resource_permissions`

One row per entry of each resource's `permissions` list, as rendered by the summary (which may add `IS_OWNER`). Levels are exact; no hierarchy is applied.

| Column | Type | Description |
|---|---|---|
| `resource_key` | `VARCHAR` | Unique key `resources.<type>.<name>`; return it to identify violations. |
| `resource_type` | `VARCHAR` | Resource type key from the summary, e.g. `jobs` or `genie_spaces`. |
| `resource_name` | `VARCHAR` | Resource key within its type, as written in the bundle. |
| `group_name` | `VARCHAR` | Group principal, or NULL. |
| `user_name` | `VARCHAR` | User principal, or NULL. |
| `service_principal_name` | `VARCHAR` | Service principal application ID, or NULL. |
| `level` | `VARCHAR` | Exact permission level, e.g. `CAN_MANAGE` or `IS_OWNER`. |

```sql
SELECT resource_key, level FROM resource_permissions WHERE group_name = 'account users';
```

### `resource_tags`

One row per resource tag; a `tags` object and a list of `{key, value}` objects are both normalized. Tags stored under other fields stay in `resources.config`.

| Column | Type | Description |
|---|---|---|
| `resource_key` | `VARCHAR` | Unique key `resources.<type>.<name>`; return it to identify violations. |
| `resource_type` | `VARCHAR` | Resource type key from the summary, e.g. `jobs` or `genie_spaces`. |
| `resource_name` | `VARCHAR` | Resource key within its type, as written in the bundle. |
| `key` | `VARCHAR` | Tag key. |
| `value` | `VARCHAR` | Tag value as text. |

```sql
SELECT resource_key FROM resource_tags WHERE key = 'owner' AND trim(value) = '';
```

### Helpers

| Helper | Description |
|---|---|
| `resource(kind)` | Table macro: `resources` rows of one type; empty, not an error, for absent types. |
| `field(document, path)` | Scalar macro: the JSON path or pointer as VARCHAR; NULL when absent. |

### `bundle_resources.<type>`

Only with `--schema`: one table per resource type in the schema, with these metadata columns followed by one column per schema property.

| Column | Type | Description |
|---|---|---|
| `_resource_key` | `VARCHAR` | Same as `resources.resource_key`. |
| `_resource_name` | `VARCHAR` | Same as `resources.resource_name`. |
| `_config` | `JSON` | The original resource JSON, including fields the schema does not describe. |
| `_modified_status` | `VARCHAR` | Same as `resources.modified_status`. |
<!-- END GENERATED POLICY TABLES -->

## Typed resource tables

Supply a Databricks bundle JSON Schema with `--schema` to get regular columns,
nested structs, lists, and maps per resource type:

```bash
# Capture the schema with the CLI version that produced the summary.
databricks bundle schema > bundle.schema.json

lamp policy --input summary.json --policy-path company-policies \
  --schema bundle.schema.json
```

```sql
SELECT _resource_key, 'Missing owner' AS message
FROM bundle_resources.jobs
WHERE trim(coalesce(tags['owner'], '')) = '';

SELECT _resource_key, task.task_key, 'Task timeout too high' AS message
FROM bundle_resources.jobs, UNNEST(tasks) AS nested(task)
WHERE task.timeout_seconds > $maximum;

-- Exists for every type in the schema, even without instances.
SELECT _resource_key, 'Schemas are prohibited' AS message
FROM bundle_resources.schemas;
```

Each table starts with the metadata columns listed under
[`bundle_resources.<type>`](#bundle_resourcestype). Inspect the rest with
`DESCRIBE bundle_resources.jobs`. DuckDB lists are 1-based (`tasks[1]`), unlike
0-based JSON paths.

**Type mapping.** Local `$ref`s and nullable or `${var...}` substitution unions
are resolved. Unambiguous types become `VARCHAR`, `BOOLEAN`, `BIGINT`, `DOUBLE`,
`STRUCT`, lists, or scalar-valued maps; recursive, ambiguous, or unsupported
definitions stay `JSON`. External references are rejected. This is a type
projection, **not** a JSON Schema validator.

**Failures are explicit.** Values that do not fit their type, including
unresolved substitutions in numeric or boolean fields, fail the run instead of
becoming NULL. Resolve them first or query the generic tables. Fields and types
unknown to an older schema remain in the generic tables and `_config`.

**Getting the schema.** Lamp never runs the CLI or downloads anything. CLI
releases since v0.299.1 attach the schema as a release asset:

```bash
curl --fail --location \
  https://github.com/databricks/cli/releases/download/v1.23.0/jsonschema.json \
  --output bundle.schema.json
```

Older tags (v0.230.0 and later) contain it at
`https://raw.githubusercontent.com/databricks/cli/v<version>/bundle/schema/jsonschema.json`.
Use your pinned CLI version and archive the schema with the summary.

## Policy presets

Presets are built-in parameterized checks configured in YAML. They encode no
organization-specific choices: each pack decides which types, keys, values,
fields, and principals to require. They are **Lamp policy presets**, unrelated to
Databricks bundle `presets`.

- Resource presets need a non-empty `resource_types`; `["*"]` selects all types.
- Absent types produce no violations.
- Resources with `modified_status: deleted` are skipped.
- Field presets take scalar JSON paths or pointers without wildcards; use SQL
  when any or every element of a nested array must satisfy a condition.

```yaml
lamp_policies:
  - name: platform_managed_only
    preset: deny_resource
    parameters:
      resource_types: [schemas, catalogs, clusters]

  - name: ownership_tags
    preset: require_tags
    parameters:
      resource_types: [jobs]
      keys: [owner, cost_center]
      key_values:
        environment: prod

  - name: bundle_default_tags
    preset: require_preset_tags
    parameters:
      keys: [cost_center]

  # Pair with platform-owned Python mutators that apply further governance.
  - name: platform_mutators
    preset: require_mutators
    parameters:
      mutators: [company_platform.bundles:apply_defaults]

  - name: platform_access
    preset: require_permissions
    parameters:
      resource_types: [jobs, pipelines]
      permissions:
        - group_name: data-platform
          level: CAN_MANAGE

  - name: global_permission_defaults
    preset: require_bundle_permissions
    parameters:
      permissions:
        - group_name: data-platform
          level: CAN_MANAGE

  - name: service_principal_identity
    preset: require_fields
    parameters:
      resource_types: [jobs]
      fields: [$.run_as.service_principal_name]

  - name: no_user_identity
    preset: forbid_fields
    parameters:
      resource_types: [jobs]
      fields: [$.run_as.user_name]

  - name: serverless_pipelines
    preset: allowed_field_values
    parameters:
      resource_types: [pipelines]
      field: $.serverless
      values: [true]
```

Other useful platform checks, such as approved compute policies, runtime
versions, task timeouts, naming conventions, or deployment identity, can usually
be expressed with the field presets or a short SQL check.

### Preset reference

<!-- BEGIN GENERATED POLICY PRESETS -->
### `deny_resource`

Report every resource of a prohibited type.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `resource_types` | `list[str]` | yes | Bundle resource types such as `jobs` or `schemas`; `*` selects all types. |

### `require_tags`

Require tags on every selected resource as rendered after bundle presets.

Set `keys`, `key_values`, or both.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `resource_types` | `list[str]` | yes | Bundle resource types such as `jobs` or `schemas`; `*` selects all types. |
| `keys` | `list[str]` | no | Tag keys that must exist with a non-blank value. |
| `key_values` | `dict[str, str]` | no | Tag keys whose value must equal the given string. |

### `require_preset_tags`

Require tags in the bundle-level `presets.tags` that Databricks applies to resources.

Set `keys`, `key_values`, or both.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `keys` | `list[str]` | no | Tag keys that must exist with a non-blank value. |
| `key_values` | `dict[str, str]` | no | Tag keys whose value must equal the given string. |

### `require_permissions`

Require every listed permission on every selected resource.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `resource_types` | `list[str]` | yes | Bundle resource types such as `jobs` or `schemas`; `*` selects all types. |
| `permissions` | `list[Permission]` | yes | Required entries; matching is exact, without a permission-level hierarchy. |

### `require_bundle_permissions`

Require every listed permission in the bundle-level `permissions` list.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `permissions` | `list[Permission]` | yes | Required entries; matching is exact, without a permission-level hierarchy. |

### `require_mutators`

Require bundle Python mutators, e.g. platform mutators that apply governance defaults.

Checks `python.mutators` and the legacy `experimental.python.mutators`.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `mutators` | `list[str]` | yes | Exact `module:function` entries, e.g. `lamp_ops.bundles:mutate_dashboards`. |

### `require_fields`

Require each field to resolve to a non-null value on every selected resource.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `resource_types` | `list[str]` | yes | Bundle resource types such as `jobs` or `schemas`; `*` selects all types. |
| `fields` | `list[str]` | yes | Scalar JSON paths (`$.run_as.user_name`) or pointers; no wildcards. |

### `forbid_fields`

Forbid each field on every selected resource, even when its value is null.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `resource_types` | `list[str]` | yes | Bundle resource types such as `jobs` or `schemas`; `*` selects all types. |
| `fields` | `list[str]` | yes | Scalar JSON paths (`$.run_as.user_name`) or pointers; no wildcards. |

### `allowed_field_values`

Require a scalar field to exist and equal one of the allowed JSON values.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `resource_types` | `list[str]` | yes | Bundle resource types such as `jobs` or `schemas`; `*` selects all types. |
| `field` | `str` | yes | Scalar JSON path or pointer, e.g. `$.max_concurrent_runs`. |
| `values` | `list[str \| int \| float \| bool \| None]` | yes | Allowed JSON scalar values; types must match. |

### Permission entries

One permission entry; set exactly one principal.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `group_name` | `str \| None` | no | Group principal. |
| `user_name` | `str \| None` | no | User principal. |
| `service_principal_name` | `str \| None` | no | Service principal application ID. |
| `level` | `str` | yes | Exact permission level, e.g. `CAN_MANAGE`. |
<!-- END GENERATED POLICY PRESETS -->

## CI enforcement and security

Keep mandatory packs under platform-team control. A protected workflow
retrieves a pinned pack, checks the summary, and deploys only on success:

```bash
set -euo pipefail
# CI installs a pinned lamp-ops release and retrieves a pinned policy pack.
databricks bundle summary -t prod -o json > "$RUNNER_TEMP/summary.json"
lamp policy --input "$RUNNER_TEMP/summary.json" \
  --policy-path /opt/company-policies --output-path "$RUNNER_TEMP/policy-report.json"
databricks bundle deploy -t prod
```

- An explicit `--policy-path` cannot be overridden by repository configuration.
  The workflow must also control `--param` values and, when used,
  `LAMP_POLICY_PATH`. Only that workflow should hold production deploy access.
- Generating the summary may execute project Python code; do not expose unrelated
  secrets or policy-publishing credentials to it.
- The summary is the local bundle enriched with deployment state, not a live
  workspace audit or an immutable plan. Use the same checkout, target, variables,
  identity, and dependency versions for summary and deployment; Python loaders
  must be deterministic.
- Summaries and reports can contain identities and workspace details: restrict
  access and retention, and never write reports over policy files or the input.

Checks run in an isolated DuckDB connection, one `SELECT` per check, with
external access and extension auto-loading/installing disabled and settings
locked. This is defense in depth, **not a sandbox for hostile SQL**: policy packs
are trusted CI code, and resource or time limits belong to the CI runner.

## Testing policy packs

Test packs through the CLI with real JSON fixtures. Cover a compliant bundle,
violations, missing fields, absent resource types, parameter overrides, and
conditions that should not apply. With typed mode, include your pinned schema
and optional nested fields. Keep fixture values synthetic: summaries and
violation rows can contain configuration values that should not be published.
