"""Parameterized SQL building blocks, not organization-specific policy decisions."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ResourceSelection(Parameters):
    resource_types: list[str] = Field(
        min_length=1,
        description="Bundle resource types such as `jobs` or `schemas`; `*` selects all types.",
    )

    @model_validator(mode="after")
    def valid_types(self) -> ResourceSelection:
        if any(not kind or kind.strip() != kind for kind in self.resource_types):
            raise ValueError("resource_types must contain non-empty resource type names")
        return self


class DenyResource(ResourceSelection):
    """Report every resource of a prohibited type."""


class TagRequirements(Parameters):
    keys: list[str] = Field(
        default_factory=list, description="Tag keys that must exist with a non-blank value."
    )
    key_values: dict[str, str] = Field(
        default_factory=dict, description="Tag keys whose value must equal the given string."
    )

    @model_validator(mode="after")
    def required_tags(self) -> TagRequirements:
        if not self.keys and not self.key_values:
            raise ValueError("Tag presets need keys or key_values")
        if any(not key for key in [*self.keys, *self.key_values]):
            raise ValueError("Tag keys must not be empty")
        return self


class RequireTags(TagRequirements, ResourceSelection):
    """Require tags on every selected resource as rendered after bundle presets.

    Set `keys`, `key_values`, or both.
    """


class RequirePresetTags(TagRequirements):
    """Require tags in the bundle-level `presets.tags` that Databricks applies to resources.

    Set `keys`, `key_values`, or both.
    """


class Permission(Parameters):
    """One permission entry; set exactly one principal."""

    group_name: str | None = Field(default=None, description="Group principal.")
    user_name: str | None = Field(default=None, description="User principal.")
    service_principal_name: str | None = Field(
        default=None, description="Service principal application ID."
    )
    level: str = Field(min_length=1, description="Exact permission level, e.g. `CAN_MANAGE`.")

    @model_validator(mode="after")
    def principal(self) -> Permission:
        names = [self.group_name, self.user_name, self.service_principal_name]
        if sum(name is not None for name in names) != 1 or "" in names:
            raise ValueError("A permission needs exactly one non-empty principal")
        return self


PERMISSIONS_DESCRIPTION = (
    "Required entries; matching is exact, without a permission-level hierarchy."
)


class RequirePermissions(ResourceSelection):
    """Require every listed permission on every selected resource."""

    permissions: list[Permission] = Field(min_length=1, description=PERMISSIONS_DESCRIPTION)


class RequireBundlePermissions(Parameters):
    """Require every listed permission in the bundle-level `permissions` list."""

    permissions: list[Permission] = Field(min_length=1, description=PERMISSIONS_DESCRIPTION)


class RequireMutators(Parameters):
    """Require bundle Python mutators, e.g. platform mutators that apply governance defaults.

    Checks `python.mutators` and the legacy `experimental.python.mutators`.
    """

    mutators: list[str] = Field(
        min_length=1,
        description="Exact `module:function` entries, e.g. `lamp_ops.bundles:mutate_dashboards`.",
    )


class Fields(ResourceSelection):
    fields: list[str] = Field(
        min_length=1,
        description="Scalar JSON paths (`$.run_as.user_name`) or pointers; no wildcards.",
    )

    @model_validator(mode="after")
    def paths(self) -> Fields:
        if any(not field.startswith(("$", "/")) for field in self.fields):
            raise ValueError("fields must be JSON paths ($.field) or JSON pointers (/field)")
        if any("*" in field for field in self.fields):
            raise ValueError("Field presets require scalar paths; use SQL for wildcard checks")
        return self


class RequireFields(Fields):
    """Require each field to resolve to a non-null value on every selected resource."""


class ForbidFields(Fields):
    """Forbid each field on every selected resource, even when its value is null."""


class AllowedFieldValues(ResourceSelection):
    """Require a scalar field to exist and equal one of the allowed JSON values."""

    field: str = Field(
        min_length=1, description="Scalar JSON path or pointer, e.g. `$.max_concurrent_runs`."
    )
    values: list[str | int | float | bool | None] = Field(
        min_length=1, description="Allowed JSON scalar values; types must match."
    )

    @model_validator(mode="after")
    def scalar_path(self) -> AllowedFieldValues:
        if not self.field.startswith(("$", "/")) or "*" in self.field:
            raise ValueError("field must be a scalar JSON path or pointer; use SQL for wildcards")
        return self


# Resources the deployment is about to delete are no longer declared, so presets skip them.
RESOURCE_FILTER = """
    (list_contains($resource_types::VARCHAR[], '*')
     OR r.resource_type IN (SELECT unnest($resource_types::VARCHAR[])))
    AND r.modified_status IS DISTINCT FROM 'deleted'
"""

DENY = f"""
    SELECT r.resource_key, r.resource_type, r.resource_name,
           'Resource type ' || r.resource_type || ' is prohibited' AS message
    FROM resources r WHERE {RESOURCE_FILTER}
"""

REQUIRE_TAGS = f"""
    WITH required AS (
        SELECT unnest($keys::VARCHAR[]) AS key, NULL::VARCHAR AS expected
        UNION ALL
        SELECT key, json_extract_string(value, '$') FROM json_each($key_values::JSON)
    )
    SELECT r.resource_key, required.key AS tag_key,
           'Required tag ' || required.key ||
           CASE WHEN required.expected IS NULL THEN ' is missing or empty'
                ELSE ' must equal ' || required.expected END AS message
    FROM resources r CROSS JOIN required
    WHERE {RESOURCE_FILTER} AND NOT EXISTS (
        SELECT 1 FROM resource_tags t
        WHERE t.resource_key = r.resource_key AND t.key = required.key
          AND CASE WHEN required.expected IS NULL THEN trim(coalesce(t.value, '')) <> ''
                   ELSE t.value = required.expected END
    )
"""

REQUIRE_PRESET_TAGS = """
    WITH required AS (
        SELECT unnest($keys::VARCHAR[]) AS key, NULL::VARCHAR AS expected
        UNION ALL
        SELECT key, json_extract_string(value, '$') FROM json_each($key_values::JSON)
    )
    SELECT 'presets.tags' AS resource_key, required.key AS tag_key,
           'Required bundle preset tag ' || required.key ||
           CASE WHEN required.expected IS NULL THEN ' is missing or empty'
                ELSE ' must equal ' || required.expected END AS message
    FROM required
    WHERE NOT EXISTS (
        SELECT 1 FROM presets p, json_each(p.value) t
        WHERE p.name = 'tags' AND t.key = required.key
          AND CASE WHEN required.expected IS NULL
                   THEN trim(coalesce(json_extract_string(t.value, '$'), '')) <> ''
                   ELSE json_extract_string(t.value, '$') = required.expected END
    )
"""

PERMISSION_MATCH = """
    p.level = (required.value ->> 'level')
    AND p.group_name IS NOT DISTINCT FROM (required.value ->> 'group_name')
    AND p.user_name IS NOT DISTINCT FROM (required.value ->> 'user_name')
    AND p.service_principal_name IS NOT DISTINCT FROM
        (required.value ->> 'service_principal_name')
"""

REQUIRE_PERMISSIONS = f"""
    SELECT r.resource_key, required.value AS required_permission,
           'Missing required permission: ' || required.value::VARCHAR AS message
    FROM resources r CROSS JOIN json_each($permissions::JSON) required
    WHERE {RESOURCE_FILTER} AND NOT EXISTS (
        SELECT 1 FROM resource_permissions p
        WHERE p.resource_key = r.resource_key AND {PERMISSION_MATCH}
    )
"""

REQUIRE_BUNDLE_PERMISSIONS = f"""
    SELECT 'bundle' AS resource_key, required.value AS required_permission,
           'Missing bundle permission: ' || required.value::VARCHAR AS message
    FROM json_each($permissions::JSON) required
    WHERE NOT EXISTS (SELECT 1 FROM permissions p WHERE {PERMISSION_MATCH})
"""

REQUIRE_MUTATORS = """
    SELECT 'python.mutators' AS resource_key, required AS mutator,
           'Required Python mutator ' || required || ' is missing' AS message
    FROM unnest($mutators::VARCHAR[]) wanted(required)
    WHERE NOT EXISTS (
        SELECT 1 FROM summary s, json_each(coalesce(s.config -> '$.python.mutators', '[]')) m
        WHERE m.value ->> '$' = required
        UNION ALL
        SELECT 1 FROM summary s,
            json_each(coalesce(s.config -> '$.experimental.python.mutators', '[]')) m
        WHERE m.value ->> '$' = required
    )
"""

REQUIRE_FIELDS = f"""
    SELECT r.resource_key, field AS field_path,
           'Required field ' || field || ' is missing or null' AS message
    FROM resources r CROSS JOIN unnest($fields::VARCHAR[]) required(field)
    WHERE {RESOURCE_FILTER}
      AND coalesce(json_type(r.config, field), 'NULL') = 'NULL'
"""

FORBID_FIELDS = f"""
    SELECT r.resource_key, field AS field_path,
           'Field ' || field || ' is prohibited' AS message
    FROM resources r CROSS JOIN unnest($fields::VARCHAR[]) forbidden(field)
    WHERE {RESOURCE_FILTER} AND json_exists(r.config, field)
"""

ALLOWED_VALUES = f"""
    SELECT r.resource_key, $field AS field_path,
           'Field ' || $field || ' is missing or has a disallowed value' AS message
    FROM resources r
    WHERE {RESOURCE_FILTER} AND NOT EXISTS (
        SELECT 1 FROM json_each($values::JSON) allowed
        WHERE json_extract(r.config, $field) = allowed.value
    )
"""

PRESETS: dict[str, tuple[type[Parameters], str]] = {
    "deny_resource": (DenyResource, DENY),
    "require_tags": (RequireTags, REQUIRE_TAGS),
    "require_preset_tags": (RequirePresetTags, REQUIRE_PRESET_TAGS),
    "require_permissions": (RequirePermissions, REQUIRE_PERMISSIONS),
    "require_bundle_permissions": (RequireBundlePermissions, REQUIRE_BUNDLE_PERMISSIONS),
    "require_mutators": (RequireMutators, REQUIRE_MUTATORS),
    "require_fields": (RequireFields, REQUIRE_FIELDS),
    "forbid_fields": (ForbidFields, FORBID_FIELDS),
    "allowed_field_values": (AllowedFieldValues, ALLOWED_VALUES),
}


def compile_preset(
    name: str, parameters: Parameters, overrides: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    model, sql = PRESETS[name]
    inherited = {key: value for key, value in overrides.items() if key in model.model_fields}
    merged = {**parameters.model_dump(mode="json", exclude_none=True), **inherited}
    validated = model.model_validate(merged).model_dump(mode="json", exclude_none=True)
    for key in ("permissions", "values", "key_values"):
        if key in validated:
            validated[key] = json.dumps(validated[key])
    return sql, validated
