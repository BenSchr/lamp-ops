"""Policy check models shared by policy YAML files and lamp_config.yaml."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal, Union, cast

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, create_model

from lamp_ops.policy.presets import PRESETS, Parameters


class CheckConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    severity: Literal["warn", "error"] = "error"
    targets: list[str] = Field(
        default_factory=list, description="Bundle targets to check; empty means all."
    )


class CheckBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, description="Unique check name; defaults the SQL filename.")
    description: str = ""
    config: CheckConfig = Field(default_factory=CheckConfig)


class SqlCheck(CheckBase):
    """Custom SQL check; returned rows are violations."""

    sql: str | None = Field(default=None, description="SQL file relative to this YAML file.")
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Values bound to `$name` placeholders."
    )


class PresetCheck(CheckBase):
    preset: str
    parameters: Parameters


def _preset_check(name: str, model: type[Parameters]) -> Any:
    check = create_model(
        f"{model.__name__}Check",
        __base__=PresetCheck,
        __doc__=model.__doc__,
        preset=(cast(Any, Literal)[name], ...),
        parameters=(model, ...),
    )
    return cast(Any, Annotated)[check, Tag(name)]


def _check_kind(value: Any) -> str:
    preset = value.get("preset") if isinstance(value, dict) else getattr(value, "preset", None)
    return "sql" if preset is None else str(preset)


if TYPE_CHECKING:
    PolicyCheck = SqlCheck | PresetCheck
else:
    PolicyCheck = Annotated[
        Union[  # noqa: UP007 - built from the preset registry at runtime
            tuple(
                [Annotated[SqlCheck, Tag("sql")]]
                + [_preset_check(name, model) for name, (model, _) in PRESETS.items()]
            )
        ],
        Discriminator(
            _check_kind,
            custom_error_type="unknown_policy_preset",
            custom_error_message=f"Unknown policy preset; choose from {', '.join(PRESETS)}",
        ),
    ]


class PolicyPack(BaseModel):
    """A policy YAML file: only the `lamp_policies` list."""

    model_config = ConfigDict(extra="forbid", strict=True)

    lamp_policies: list[PolicyCheck] = Field(default_factory=list)
