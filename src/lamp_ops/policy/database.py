"""Version-independent summary tables and optional schema-derived resource tables."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lamp_ops.errors import LampError

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection


@dataclass(frozen=True)
class Relation:
    """Documentation for one generic table or view; columns map names to descriptions."""

    name: str
    description: str
    columns: dict[str, str]
    example: str


_IDENTITY = {
    "resource_key": "Unique key `resources.<type>.<name>`; return it to identify violations.",
    "resource_type": "Resource type key from the summary, e.g. `jobs` or `genie_spaces`.",
    "resource_name": "Resource key within its type, as written in the bundle.",
}
_PRINCIPALS = {
    "group_name": "Group principal, or NULL.",
    "user_name": "User principal, or NULL.",
    "service_principal_name": "Service principal application ID, or NULL.",
    "level": "Exact permission level, e.g. `CAN_MANAGE` or `IS_OWNER`.",
}

# The reference in docs/policies.md is generated from these entries and checked against the
# real database, so every relation and column created below must be described here.
RELATIONS = (
    Relation(
        "summary",
        "Exactly one row for the whole summary document.",
        {
            "target": "Effective target: `bundle.target`, or the `--target` option.",
            "config": "The **entire** summary JSON, e.g. `$.workspace`, `$.python`, `$.run_as`.",
        },
        "SELECT field(config, '$.workspace.root_path') AS root_path FROM summary",
    ),
    Relation(
        "bundle",
        "One row per setting of the summary's `bundle` key.",
        {"name": "Setting name, e.g. `name`, `target`, or `git`.", "value": "Setting value JSON."},
        "SELECT field(value, '$') AS bundle_name FROM bundle WHERE name = 'name'",
    ),
    Relation(
        "resources",
        "One row per resource of every type, including types Lamp does not know.",
        {
            **_IDENTITY,
            "config": "The resource JSON as rendered by the summary.",
            "modified_status": "Deployment state from the summary, e.g. `created` or `deleted`; "
            "NULL when the summary omits it. `deleted` resources carry only `id` and `url`.",
        },
        "SELECT resource_key, field(config, '$.name') AS name FROM resources "
        "WHERE resource_type = 'jobs'",
    ),
    Relation(
        "variables",
        "One row per top-level bundle variable; columns follow the bundle schema's variable "
        'fields. Quote `"default"`, a reserved SQL keyword.',
        {
            "name": "Variable name.",
            "value": "Resolved value JSON; NULL when the summary does not resolve it.",
            "default": "Configured default JSON, or NULL.",
            "description": "Variable description, or NULL.",
            "type": "Variable type, e.g. `complex`, or NULL.",
            "lookup": 'Lookup object JSON, e.g. `{"warehouse": "Shared"}`, or NULL.',
            "sensitive": "Whether the variable holds a secret, or NULL when unset.",
            "config": "The complete variable declaration JSON, including any other fields.",
        },
        "SELECT name, field(value, '$') AS resolved FROM variables WHERE \"default\" IS NULL",
    ),
    Relation(
        "presets",
        "One row per Databricks bundle `presets` setting (not Lamp policy presets).",
        {"name": "Setting name, e.g. `tags` or `name_prefix`.", "value": "Setting value JSON."},
        "SELECT t.key, t.value FROM presets, json_each(value) t WHERE name = 'tags'",
    ),
    Relation(
        "permissions",
        "One row per entry of the top-level `permissions` list.",
        dict(_PRINCIPALS),
        "SELECT * FROM permissions WHERE level = 'CAN_MANAGE'",
    ),
    Relation(
        "resource_permissions",
        "One row per entry of each resource's `permissions` list, as rendered by the summary "
        "(which may add `IS_OWNER`). Levels are exact; no hierarchy is applied.",
        {**_IDENTITY, **_PRINCIPALS},
        "SELECT resource_key, level FROM resource_permissions WHERE group_name = 'account users'",
    ),
    Relation(
        "resource_tags",
        "One row per resource tag; a `tags` object and a list of `{key, value}` objects are "
        "both normalized. Tags stored under other fields stay in `resources.config`.",
        {
            **_IDENTITY,
            "key": "Tag key.",
            "value": "Tag value as text.",
        },
        "SELECT resource_key FROM resource_tags WHERE key = 'owner' AND trim(value) = ''",
    ),
)

HELPERS = {
    "resource": (
        "resource(kind)",
        "Table macro: `resources` rows of one type; empty, not an error, for absent types.",
        "SELECT resource_key FROM resource('jobs')",
    ),
    "field": (
        "field(document, path)",
        "Scalar macro: the JSON path or pointer as VARCHAR; NULL when absent.",
        "SELECT field('{\"a\": {\"b\": 1}}', '$.a.b')",
    ),
}

TYPED_METADATA = {
    "_resource_key": "Same as `resources.resource_key`.",
    "_resource_name": "Same as `resources.resource_name`.",
    "_config": "The original resource JSON, including fields the schema does not describe.",
    "_modified_status": "Same as `resources.modified_status`.",
}


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise LampError(f"Invalid JSON in {path}: {error}") from error
    if not isinstance(value, dict):
        raise LampError(f"Expected a JSON object in {path}")
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LampError(f"{label} must be an object")
    return value


def _permissions(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise LampError(f"{label} must be a list of permission objects")
    return value


def create_database(
    connection: DuckDBPyConnection, document: dict[str, Any], schema: dict[str, Any] | None
) -> str | None:
    if "bundle" not in document and "resources" not in document:
        raise LampError("Expected bundle summary JSON containing bundle or resources")
    resources = _object(document.get("resources", {}), "resources")
    target = _object(document.get("bundle", {}), "bundle").get("target")
    if target is not None and not isinstance(target, str):
        raise LampError("bundle.target must be a string")
    connection.execute("CREATE TABLE summary (target VARCHAR, config JSON)")
    connection.execute("INSERT INTO summary VALUES (?, ?)", [target, json.dumps(document)])
    connection.execute(
        "CREATE TABLE resources (resource_key VARCHAR PRIMARY KEY, resource_type VARCHAR, "
        "resource_name VARCHAR, config JSON, modified_status VARCHAR)"
    )
    for kind, entries in resources.items():
        for name, config in _object(entries, f"resources.{kind}").items():
            _object(config, f"resources.{kind}.{name}")
            if "permissions" in config:
                _permissions(config["permissions"], f"resources.{kind}.{name}.permissions")
            # Deployment state the CLI reports, e.g. created or deleted; absent when unchanged.
            status = config.get("modified_status")
            if status is not None and not isinstance(status, str):
                raise LampError(f"resources.{kind}.{name}.modified_status must be a string")
            connection.execute(
                "INSERT INTO resources VALUES (?, ?, ?, ?, ?)",
                [f"resources.{kind}.{name}", kind, name, json.dumps(config), status],
            )
    # Top-level objects become one row per entry, named after their summary key.
    for field in ("bundle", "variables", "presets"):
        entries = _object(document.get(field, {}), field)
        connection.execute(f"CREATE TABLE {field} (name VARCHAR, value JSON)")
        if entries:
            connection.executemany(
                f"INSERT INTO {field} VALUES (?, ?)",
                [(name, json.dumps(value)) for name, value in entries.items()],
            )
    _variables(connection)
    _permissions(document.get("permissions", []), "permissions")
    connection.execute("""
        CREATE VIEW permissions AS
        SELECT p.value ->> 'group_name' AS group_name,
               p.value ->> 'user_name' AS user_name,
               p.value ->> 'service_principal_name' AS service_principal_name,
               p.value ->> 'level' AS level
        FROM summary, LATERAL json_each(config, '$.permissions') p
    """)
    connection.execute("""
        CREATE VIEW resource_permissions AS
        SELECT r.resource_key, r.resource_type, r.resource_name,
               p.value ->> 'group_name' AS group_name,
               p.value ->> 'user_name' AS user_name,
               p.value ->> 'service_principal_name' AS service_principal_name,
               p.value ->> 'level' AS level
        FROM resources r, LATERAL json_each(r.config, '$.permissions') p
    """)
    connection.execute("""
        CREATE VIEW resource_tags AS
        SELECT r.resource_key, r.resource_type, r.resource_name,
               CASE WHEN json_type(r.config, '$.tags') = 'OBJECT'
                    THEN t.key ELSE t.value ->> 'key' END AS key,
               CASE WHEN json_type(r.config, '$.tags') = 'OBJECT'
                    THEN json_extract_string(t.value, '$')
                    ELSE t.value ->> 'value' END AS value
        FROM resources r, LATERAL json_each(r.config, '$.tags') t
    """)
    connection.execute("""
        CREATE MACRO resource(kind) AS TABLE
            SELECT * FROM resources WHERE resource_type = kind;
        CREATE MACRO field(document, path) AS
            json_extract_string(document, path);
    """)
    if schema is not None:
        _typed_resources(connection, schema, resources)
    return target


def _variables(connection: DuckDBPyConnection) -> None:
    # Spread the bundle schema's variable fields into columns; `config` keeps the declaration.
    for name, value in connection.execute("SELECT name, value FROM variables").fetchall():
        variable = _object(json.loads(value), f"variables.{name}")
        for key in ("description", "type"):
            if not isinstance(variable.get(key, ""), str):
                raise LampError(f"variables.{name}.{key} must be a string")
        if not isinstance(variable.get("sensitive", False), bool):
            raise LampError(f"variables.{name}.sensitive must be a boolean")
        if not isinstance(variable.get("lookup", {}), dict):
            raise LampError(f"variables.{name}.lookup must be an object")
    connection.execute("""
        ALTER TABLE variables RENAME TO variable_input;
        CREATE TABLE variables AS
        SELECT name,
               value -> '$.value' AS value,
               value -> '$.default' AS "default",
               value ->> '$.description' AS description,
               value ->> '$.type' AS type,
               value -> '$.lookup' AS lookup,
               CAST(value -> '$.sensitive' AS BOOLEAN) AS sensitive,
               value AS config
        FROM variable_input;
        DROP TABLE variable_input;
    """)


class Schema:
    """Translate the unambiguous subset of the CLI JSON Schema to DuckDB types."""

    def __init__(self, document: dict[str, Any]):
        self.document = document

    def resolve(self, node: dict[str, Any], active: frozenset[str] = frozenset()) -> dict[str, Any]:
        reference = node.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise LampError(f"Only local JSON Schema references are supported: {reference}")
            if reference in active:
                return {}
            value = self.document
            for key in reference[2:].split("/"):
                value = _object(value, reference).get(key.replace("~1", "/").replace("~0", "~"))
                if not isinstance(value, dict):
                    raise LampError(f"Unresolved JSON Schema reference: {reference}")
            return self.resolve(value, active | {reference})
        choices = node.get("oneOf", node.get("anyOf"))
        if choices is not None:
            choices = [
                choice
                for choice in choices
                if choice.get("type") != "null"
                and not (choice.get("type") == "string" and "pattern" in choice)
            ]
            if len(choices) == 1:
                return self.resolve(choices[0], active)
            return {}
        return node

    def shape(self, node: dict[str, Any], active: frozenset[str] = frozenset()) -> Any:
        reference = node.get("$ref")
        if reference in active:
            return "JSON"
        resolved = self.resolve(node)
        active = active | {reference} if reference else active
        kind = resolved.get("type")
        if kind == "object":
            properties = resolved.get("properties", {})
            if properties:
                return {name: self.shape(value, active) for name, value in properties.items()}
            additional = resolved.get("additionalProperties")
            if isinstance(additional, dict):
                inner = self.shape(additional, active)
                if isinstance(inner, str) and inner != "JSON":
                    return f"MAP(VARCHAR, {inner})"
            return "JSON"
        if kind == "array":
            return [self.shape(resolved.get("items", {}), active)]
        return {
            "string": "VARCHAR",
            "boolean": "BOOLEAN",
            "integer": "BIGINT",
            "number": "DOUBLE",
        }.get(kind, "JSON")


def _pad(value: Any, shape: Any) -> Any:
    # Strict DuckDB conversion rejects missing keys as well as invalid values.
    if value is None:
        return None
    if isinstance(shape, dict) and isinstance(value, dict):
        return {key: _pad(value.get(key), nested) for key, nested in shape.items()}
    if isinstance(shape, list) and isinstance(value, list):
        return [_pad(item, shape[0]) for item in value]
    return value


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _typed_resources(
    connection: DuckDBPyConnection, document: dict[str, Any], resources: dict[str, Any]
) -> None:
    schema = Schema(document)
    properties = _object(document.get("properties"), "JSON Schema properties")
    root = schema.resolve(_object(properties.get("resources"), "JSON Schema resources"))
    types = _object(root.get("properties"), "JSON Schema resource types")
    connection.execute("CREATE SCHEMA bundle_resources")
    for kind, node in types.items():
        resource_map = schema.resolve(node)
        resource_schema = resource_map.get("additionalProperties")
        if not isinstance(resource_schema, dict):
            raise LampError(f"JSON Schema resource type {kind} must define additionalProperties")
        shape = schema.shape(resource_schema)
        if not isinstance(shape, dict) or not shape:
            raise LampError(f"JSON Schema resource type {kind} must describe object properties")
        if TYPED_METADATA.keys() & shape.keys():
            raise LampError(f"JSON Schema resource type {kind} uses reserved metadata columns")
        connection.execute("CREATE TEMP TABLE typed_input (name VARCHAR, config JSON)")
        for name, config in resources.get(kind, {}).items():
            connection.execute(
                "INSERT INTO typed_input VALUES (?, ?)",
                [name, json.dumps(_pad(config, shape))],
            )
        shape_sql = json.dumps(shape).replace("'", "''")
        kind_sql = kind.replace("'", "''")
        connection.execute(f"""
            CREATE TABLE bundle_resources.{_identifier(kind)} AS
            SELECT 'resources.{kind_sql}.' || t.name AS _resource_key,
                   t.name AS _resource_name, r.config AS _config,
                   r.modified_status AS _modified_status, typed.*
            FROM (
                SELECT name, json_transform_strict(config, '{shape_sql}') AS typed
                FROM typed_input
            ) t
            LEFT JOIN resources r
              ON r.resource_type = '{kind_sql}' AND r.resource_name = t.name
        """)
        connection.execute("DROP TABLE typed_input")
