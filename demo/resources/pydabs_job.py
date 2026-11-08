from databricks.bundles.core import Bundle, Resources, Variable, variables
from databricks.bundles.jobs import Job


@variables
class Variables:
    dev_catalog: Variable[str]
    prod_catalog: Variable[str]


def load_resources(bundle: Bundle) -> Resources:
    resources = Resources()
    if bundle.target != "setup":
        return resources

    catalogs = [
        bundle.resolve_variable(Variables.dev_catalog),
        bundle.resolve_variable(Variables.prod_catalog),
    ]
    resources.add_job(
        "setup_demo_pydabs",
        Job.from_dict(
            {
                "name": "Lamp Ops - setup (pydabs)",
                "performance_target": "PERFORMANCE_OPTIMIZED",
                "max_concurrent_runs": 1,
                "parameters": [
                    {"name": "catalogs", "default": ",".join(catalogs)},
                    {"name": "schema", "default": "base"},
                ],
                "tasks": [
                    {
                        "task_key": "create_demo_data",
                        "notebook_task": {"notebook_path": "notebooks/setup_demo.py"},
                    }
                ],
            }
        ),
    )
    return resources
