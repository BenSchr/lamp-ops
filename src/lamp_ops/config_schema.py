"""Lamp configuration JSON Schema output."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field, create_model

from lamp_ops.config import LampConfig
from lamp_ops.policy.models import PolicyCheck

# LampConfig keeps lamp_policies untyped so loading it skips the policy models; the schema
# still types every check through this subclass.
_SchemaConfig = create_model(
    "_SchemaConfig",
    __base__=LampConfig,
    __doc__=LampConfig.__doc__,
    lamp_policies=(
        list[PolicyCheck],
        Field(
            default_factory=list,
            description=LampConfig.model_fields["lamp_policies"].description,
        ),
    ),
)


def generate_schema() -> dict[str, Any]:
    return _SchemaConfig.model_json_schema()


def schema_json() -> str:
    return json.dumps(generate_schema(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
