# Lamp Ops bundle demo

A two-stage DAB project: provision data first, then deploy its consumers.
All three targets use the `private` profile. Deployment mode is deliberately unset:
none uses development mode or its automatic resource prefixing.

| Target | Catalog | Resources |
| --- | --- | --- |
| `setup` | `lamp_ops_dev`, `lamp_ops_prod` | `setup_demo` job (one task for both catalogs: creates catalog, `base` schema, data) |
| `dev` | `lamp_ops_dev` | Sales dashboard and Genie agent |
| `prod` | `lamp_ops_prod` | Sales dashboard and Genie agent |

## Prerequisites

- Databricks CLI 1.22.1 or later and an authenticated `private` profile.
- From the repository root, run `mise run sync` to install Lamp and the native
  bundle hooks into the repository's `.venv`.
- Permission to create catalogs, schemas, jobs, dashboards, and Genie agents.
  Default Storage creates catalogs only through the UI or SQL, not the catalogs API,
  so the setup notebook creates the catalog and schema instead of DAB resources.
  Deployment state is isolated per user and target; the fixed catalog names are
  metastore-wide, so use one deploying identity for this demo.
- Serverless notebook compute supporting metric views (YAML 1.1 requires
  Runtime 17.2+), and `CAN USE` on a serverless SQL warehouse.
- The warehouse defaults to a lookup by name, `Serverless Starter Warehouse`.
  Override with `--var warehouse_id=<id>` on bundle commands when needed.

## Validate

From this directory, without deploying or running any compute:

```bash
databricks bundle validate --strict -t setup
databricks bundle validate --strict -t dev
databricks bundle validate --strict -t prod
```

In the repository's mise environment, use `mise exec -- databricks ...` if the
CLI is not on your shell's PATH.

## Check bundle policies

The demo policy pack at [policies](policies/) demonstrates resource presets,
target-specific checks, and custom SQL. It ensures catalogs and schemas are
created by the setup notebook rather than DAB resources, bundle presets and setup
jobs carry the demo tag, setup jobs have bounded concurrency, bundle managers remain
configured, consumer permissions are present, and jobs and assets stay in their respective stages.

After generating a bundle summary, run the checks from this directory;
`.lamp_config.yaml` points `policy_path` at `policies`:

```bash
databricks bundle summary -t setup -o json > /tmp/lamp-ops-setup-summary.json
lamp policy --input /tmp/lamp-ops-setup-summary.json

# After setup has run, check the consumer targets as well.
databricks bundle summary -t dev -o json > /tmp/lamp-ops-dev-summary.json
lamp policy --input /tmp/lamp-ops-dev-summary.json
```

From the repository root, `mise run demo-policy [target]` does this for one
target and opens the stored query tables in the DuckDB UI.

Repeat for `prod` as needed. The checked-in pack is for
demonstration; CI should supply its mandatory policy pack from a protected,
platform-owned source rather than relying on application-repository checks.

## Deploy in dependency order

```bash
# Deploy the setup job; running it creates both catalogs, schemas, tables, and views.
databricks bundle deploy -t setup
databricks bundle run -t setup setup_demo

# Only after setup_demo succeeds:
databricks bundle deploy -t dev
databricks bundle summary -t dev

databricks bundle deploy -t prod
databricks bundle summary -t prod
```

`setup` also gets `setup_demo_pydabs`, the same notebook job defined in Python
([resources/pydabs_job.py](resources/pydabs_job.py)) to exercise Python resources:
`databricks bundle run -t setup setup_demo_pydabs`.

Targets have separate deployment state. `dev` and `prod` deliberately do not own
the catalog, schema, or setup job: DAB cannot order execution across targets.
Deploying a job does not run it. Always wait for setup to succeed before
deploying `dev` or `prod`. The job passes the `dev_catalog` and `prod_catalog`
variables as its `catalogs` parameter, and one notebook run sets up both, so
compute starts only once. It runs on billable serverless compute with
`PERFORMANCE_OPTIMIZED` for the fastest setup; switch `performance_target` to
`STANDARD` to trade speed for cost. It has no schedule.

The [setup notebook](notebooks/setup_demo.py) creates 900 synthetic orders across
90 days, three regions, and three products, plus two governed metric views:

- `sales_metrics`: revenue, order count, and average order value by date, region,
  and product.
- `product_metrics`: revenue and units sold by date and product.

Revenue is in USD. A synthetic Europe discount produces a visible dip. The notebook
checks row count, order count, positive revenue, and total units after creation.
Re-running it **replaces the demo orders table and both metric views**.
`bundle destroy` removes only the job; drop the catalogs manually to clean up.

## Lamp promotion demo

By default, the bundle reads the JSON definitions under [dashboards/dev](dashboards/dev/)
and [genie/dev](genie/dev/) (`asset_dir: dev`). Lamp's native bundle mutators use the
`lamp_replacements` bundle variable in [databricks.yml](databricks.yml) to return
inline definitions; its default maps `lamp_ops_dev` to `lamp_ops_prod`, and `dev`
clears it, so the source stays unchanged. The mutators never read
[.lamp_config.yaml](.lamp_config.yaml); `lamp promote` and `lamp lint` do. Prod references become `lamp_ops_prod.base.*`;
source files are never modified.

To compare this with CLI file promotion, run the following commands from this directory:

```bash
# Mutator path: dev JSON rewritten inline at bundle load time
databricks bundle validate --strict -t prod -o json

# CLI path: write promoted files, then deploy that folder
lamp promote --dry-run
lamp promote --force
databricks bundle validate --strict -t prod --var asset_dir=prod -o json
```

Both commands should render identical dashboard and Genie payloads. Deploy either
variant with `databricks bundle deploy -t prod [--var asset_dir=prod]`.
The mutator still runs on promoted files, but the files no longer contain any
`lamp_ops_dev` references, so it makes no changes. `lamp_ops_test` is not
provisioned by this bundle.

`lamp lint` reads the agent description from the `dev` target's resources in
[resources/genie_agents](resources/genie_agents/). Unity Catalog metadata checks need the data setup to have run; use
`lamp lint --offline` before deployment.

The dashboard uses metric-view asset datasets; widget queries call `MEASURE()`
instead of duplicating metric formulas. The date filter covers both datasets.
The Genie agent exposes only `sales_metrics`, avoiding overlapping fact sources.
Try:

- How has revenue changed over the last 90 days?
- Which region generates the most revenue?
- Which products sell best by revenue?

Strict bundle validation checks configuration and reference resolution, not SQL
execution, dashboard rendering, or Genie answers. Verify those in the workspace
after running setup and before using the demo with an audience.
