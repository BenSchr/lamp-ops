"""Validated, config-relative Lamp settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

CONFIG_NAMES = ("lamp_config.yaml", "lamp_config.yml", ".lamp_config.yaml", ".lamp_config.yml")
AssetMode = Literal["env", "asset"]
Severity = Literal["warn", "error"]


class LampError(ValueError):
    """A concise, user-facing Lamp error."""


class TargetConfig(BaseModel):
    """Shared promotion settings for one target environment."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Target environment name, such as test or prod.")
    replacements: dict[str, str] = Field(
        default_factory=dict,
        description="Literal substitutions applied recursively to JSON string values.",
    )


def default_targets() -> list[TargetConfig]:
    return [TargetConfig(name="test"), TargetConfig(name="prod")]


class SharedSettings(BaseModel):
    """Promotion defaults inherited by every configured asset."""

    model_config = ConfigDict(extra="forbid")

    mode: AssetMode = Field(
        default="env",
        description="Path layout: env is <kind>/<env>/<asset>; asset adds an asset directory.",
    )
    source_target: str = Field(default="dev", description="Source environment name.")
    targets: list[TargetConfig] = Field(
        default_factory=default_targets,
        description="Ordered shared target environments and replacements.",
    )

    @field_validator("targets")
    @classmethod
    def unique_targets(cls, targets: list[TargetConfig]) -> list[TargetConfig]:
        names = [target.name for target in targets]
        if not names:
            raise ValueError("targets must not be empty")
        if len(names) != len(set(names)):
            raise ValueError("target names must be unique")
        return targets


class AssetTargetConfig(BaseModel):
    """Optional target selection and overrides for one named asset."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Target environment name.")
    path: Path | None = Field(
        default=None,
        description="Explicit target file path relative to root_path; derived when omitted.",
    )
    replacements: dict[str, str] = Field(
        default_factory=dict,
        description="Replacements merged over the shared target replacements.",
    )


class AssetConfig(BaseModel):
    """Overrides for one explicitly named asset."""

    model_config = ConfigDict(extra="forbid")

    mode: AssetMode | None = Field(
        default=None,
        description="Override the shared path layout for this asset.",
    )
    source_target: str | None = Field(
        default=None,
        description="Override the shared source environment for this asset.",
    )
    source: Path | None = Field(
        default=None,
        description="Explicit source file path relative to root_path; derived when omitted.",
    )
    targets: list[AssetTargetConfig] | None = Field(
        default=None,
        description="Selected targets and overrides; all shared targets are used when omitted.",
    )
    exclude: bool = Field(default=False, description="Exclude this asset from promotion.")

    @field_validator("targets")
    @classmethod
    def unique_targets(
        cls, targets: list[AssetTargetConfig] | None
    ) -> list[AssetTargetConfig] | None:
        if targets is None:
            return targets
        names = [target.name for target in targets]
        if not names:
            raise ValueError("targets must not be empty when provided")
        if len(names) != len(set(names)):
            raise ValueError("target names must be unique")
        return targets


class AssetGroup(BaseModel):
    """Discovery path, exclusions, and optional overrides for one asset kind."""

    model_config = ConfigDict(extra="forbid")

    path: Path | None = Field(
        default=None,
        description=(
            "Base directory for source discovery and derived targets; defaults to the kind."
        ),
    )
    exclusions: list[str] = Field(
        default_factory=list,
        description="Asset names or root-relative source paths excluded from promotion.",
    )
    items: dict[str, AssetConfig] = Field(
        default_factory=dict,
        description="Optional per-asset overrides keyed by stable name.",
    )

    @field_validator("exclusions")
    @classmethod
    def unique_exclusions(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("entries must be unique")
        return values


class RuleConfig(BaseModel):
    """Settings shared by every Genie lint rule."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Whether the rule is evaluated.")
    severity: Severity = Field(
        default="warn",
        description="Outcome used when failures exceed threshold.",
    )
    threshold: int = Field(
        default=0,
        ge=0,
        description="Number of Genie agents allowed to fail before error severity escalates.",
    )


class AgentDescriptionRuleConfig(RuleConfig):
    minimum_characters: int = Field(default=30, ge=1)
    minimum_words: int = Field(default=5, ge=1)
    recommended_characters: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def validate_recommendation(self) -> AgentDescriptionRuleConfig:
        if self.recommended_characters < self.minimum_characters:
            raise ValueError("recommended_characters must be at least minimum_characters")
        return self


class TableDescriptionsRuleConfig(RuleConfig):
    minimum_coverage: float = Field(default=0.8, ge=0, le=1)
    recommended_coverage: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_coverage(self) -> TableDescriptionsRuleConfig:
        if self.recommended_coverage < self.minimum_coverage:
            raise ValueError("recommended_coverage must be at least minimum_coverage")
        return self


class ColumnDescriptionsRuleConfig(RuleConfig):
    minimum_coverage: float = Field(default=0.5, ge=0, le=1)
    recommended_coverage: float = Field(default=0.8, ge=0, le=1)

    @model_validator(mode="after")
    def validate_coverage(self) -> ColumnDescriptionsRuleConfig:
        if self.recommended_coverage < self.minimum_coverage:
            raise ValueError("recommended_coverage must be at least minimum_coverage")
        return self


class InstructionsRuleConfig(RuleConfig):
    minimum_characters: int = Field(default=51, ge=1)
    maximum_characters: int = Field(default=2000, ge=1)

    @model_validator(mode="after")
    def validate_characters(self) -> InstructionsRuleConfig:
        if self.maximum_characters < self.minimum_characters:
            raise ValueError("maximum_characters must be at least minimum_characters")
        return self


class SourceCountRuleConfig(RuleConfig):
    maximum_sources: int = Field(default=12, ge=1)
    warning_sources: int = Field(default=9, ge=1)

    @model_validator(mode="after")
    def validate_sources(self) -> SourceCountRuleConfig:
        if self.warning_sources > self.maximum_sources:
            raise ValueError("warning_sources must not exceed maximum_sources")
        return self


class SqlGuidanceRuleConfig(RuleConfig):
    minimum_artifacts: int = Field(default=1, ge=1)


class EntityFormatMatchingRuleConfig(RuleConfig):
    minimum_enabled_columns: int = Field(default=1, ge=1)
    entity_warning_columns: int = Field(default=100, ge=1)
    entity_maximum_columns: int = Field(default=120, ge=1)

    @model_validator(mode="after")
    def validate_entity_columns(self) -> EntityFormatMatchingRuleConfig:
        if self.entity_warning_columns > self.entity_maximum_columns:
            raise ValueError("entity_warning_columns must not exceed entity_maximum_columns")
        return self


class BenchmarksRuleConfig(RuleConfig):
    minimum_questions: int = Field(default=10, ge=1)
    recommended_questions: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def validate_questions(self) -> BenchmarksRuleConfig:
        if self.recommended_questions < self.minimum_questions:
            raise ValueError("recommended_questions must be at least minimum_questions")
        return self


class NoisyColumnsRuleConfig(RuleConfig):
    minimum_visible_columns: int = Field(default=20, ge=1)
    warning_ratio: float = Field(default=0.15, ge=0, le=1)
    maximum_ratio: float = Field(default=0.3, ge=0, le=1)
    maximum_visible_per_table: int = Field(default=75, ge=1)

    @model_validator(mode="after")
    def validate_ratios(self) -> NoisyColumnsRuleConfig:
        if self.warning_ratio > self.maximum_ratio:
            raise ValueError("warning_ratio must not exceed maximum_ratio")
        return self


class GenieRulesConfig(BaseModel):
    """Explicit settings for the ten static Genie checks."""

    model_config = ConfigDict(extra="forbid")

    agent_description: AgentDescriptionRuleConfig = Field(
        default_factory=AgentDescriptionRuleConfig
    )
    table_descriptions: TableDescriptionsRuleConfig = Field(
        default_factory=TableDescriptionsRuleConfig
    )
    column_descriptions: ColumnDescriptionsRuleConfig = Field(
        default_factory=ColumnDescriptionsRuleConfig
    )
    instructions: InstructionsRuleConfig = Field(default_factory=InstructionsRuleConfig)
    joins: RuleConfig = Field(default_factory=RuleConfig)
    source_count: SourceCountRuleConfig = Field(default_factory=SourceCountRuleConfig)
    sql_guidance: SqlGuidanceRuleConfig = Field(default_factory=SqlGuidanceRuleConfig)
    entity_format_matching: EntityFormatMatchingRuleConfig = Field(
        default_factory=EntityFormatMatchingRuleConfig
    )
    benchmarks: BenchmarksRuleConfig = Field(default_factory=BenchmarksRuleConfig)
    noisy_columns: NoisyColumnsRuleConfig = Field(default_factory=NoisyColumnsRuleConfig)


class GenieLintConfig(BaseModel):
    """Static Genie lint configuration."""

    model_config = ConfigDict(extra="forbid")

    exclusions: list[str] = Field(
        default_factory=list,
        description="Genie names or config-root-relative paths excluded from linting.",
    )
    rules: GenieRulesConfig = Field(default_factory=GenieRulesConfig)


class LintConfig(BaseModel):
    """Asset lint settings."""

    model_config = ConfigDict(extra="forbid")
    genie: GenieLintConfig = Field(default_factory=GenieLintConfig)


class AssetsConfig(BaseModel):
    """Supported asset-type settings."""

    model_config = ConfigDict(extra="forbid")
    dashboards: AssetGroup = Field(default_factory=AssetGroup)
    genie: AssetGroup = Field(default_factory=AssetGroup)


class LampConfig(BaseModel):
    """Root model for lamp_config.yaml."""

    model_config = ConfigDict(
        extra="forbid",
        title="Lamp Configuration",
        json_schema_extra={"$id": "https://schemas.lamp-ops.dev/lamp-config-2.0.json"},
    )

    schema_version: Literal[2] = Field(default=2, description="Configuration schema version.")
    root_path: Path = Field(default=Path("."), description="Root relative to this config file.")
    dab_resource_path: Path | None = Field(
        default=None,
        description="Bundle resource YAML directory, relative to the configuration file.",
    )
    settings: SharedSettings = Field(
        default_factory=SharedSettings,
        description="Promotion defaults shared by all assets.",
    )
    assets: AssetsConfig = Field(default_factory=AssetsConfig)
    lint: LintConfig = Field(default_factory=LintConfig)

    _config_path: Path = PrivateAttr()
    _root: Path = PrivateAttr()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def config_path(self) -> Path:
        return self._config_path

    def resolve_path(self, value: Path) -> Path:
        if value.is_absolute():
            raise LampError(f"Configured paths must be relative: {value}")
        resolved = (self.root / value).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as error:
            raise LampError(f"Configured path escapes root_path: {value}") from error
        return resolved


def find_config(start: Path | None = None) -> Path:
    """Find a configuration in the current directory or an ancestor."""
    start = start or Path.cwd()
    current = start.resolve()
    while True:
        for name in CONFIG_NAMES:
            candidate = current / name
            if candidate.is_file():
                return candidate
        if current.parent == current:
            raise LampError(f"Could not find lamp_config.yaml (or variant) from {start.resolve()}")
        current = current.parent


def load_config(path: Path | None = None) -> LampConfig:
    """Load YAML and resolve its safe root without changing process state."""
    config_path = (path or find_config()).resolve()
    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LampError(f"Config file not found: {config_path}") from error
    except yaml.YAMLError as error:
        raise LampError(f"Invalid YAML in {config_path}: {error}") from error
    if not isinstance(raw, dict):
        raise LampError("Configuration root must be a YAML mapping")
    try:
        config = LampConfig.model_validate(raw)
    except ValueError as error:
        raise LampError(f"Invalid configuration: {error}") from error
    if config.root_path.is_absolute():
        raise LampError(f"root_path must be relative to {config_path.parent}")
    root = (config_path.parent / config.root_path).resolve()
    try:
        root.relative_to(config_path.parent.resolve())
    except ValueError as error:
        raise LampError("root_path must not escape the configuration directory") from error
    config._config_path = config_path
    config._root = root
    return config
