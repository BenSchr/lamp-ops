"""Genie Workbench-inspired static checks 1-10."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from lamp_ops.config import (
    AgentDescriptionRuleConfig,
    BenchmarksRuleConfig,
    ColumnDescriptionsRuleConfig,
    EntityFormatMatchingRuleConfig,
    InstructionsRuleConfig,
    NoisyColumnsRuleConfig,
    RuleConfig,
    SourceCountRuleConfig,
    SqlGuidanceRuleConfig,
    TableDescriptionsRuleConfig,
)

JsonObject = dict[str, Any]
PLACEHOLDERS = {
    "",
    "n/a",
    "na",
    "none",
    "null",
    "todo",
    "tbd",
    "unknown",
    "placeholder",
    "description",
}
VAGUE_DESCRIPTIONS = {
    "id",
    "identifier",
    "key",
    "date",
    "string",
    "number",
    "integer",
    "field",
    "column",
    "timestamp",
    "a string",
    "an id",
    "id field",
    "date field",
    "string field",
    "number field",
}
NOISE_COLUMN = re.compile(
    r"^(?:id|uuid|guid|hash|.*_hash|.*_key|etl_.*|ingest_.*|load_.*|raw_.*|"
    r".*_raw|.*_json|debug_.*|audit_.*|col_\d+|field_\d+|fld_.*|attr_.*)$|"
    r"^(?:created|updated|deleted)_at$",
    re.IGNORECASE,
)
Evaluation = tuple[Literal["pass", "fail", "skip"], str, str | None]


@dataclass(frozen=True)
class Rule:
    id: int
    key: str
    label: str
    evaluate: Callable[[JsonObject, Any], Evaluation]
    remediation: str
    checks: str
    lower_limit: str
    upper_limit: str
    advisory: str


def _items(value: Any) -> list[JsonObject]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item is not None)
    return str(value or "")


def _meaningful(value: Any, minimum_characters: int = 8, minimum_words: int = 2) -> bool:
    text = _text(value).strip()
    normalized = re.sub(r"\s+", " ", text).lower()
    words = re.findall(r"[A-Za-z0-9]+", text)
    return (
        normalized not in PLACEHOLDERS
        and normalized not in VAGUE_DESCRIPTIONS
        and len(text) >= minimum_characters
        and len(words) >= minimum_words
    )


def _sources(data: JsonObject) -> tuple[list[JsonObject], list[JsonObject]]:
    sources = data.get("data_sources", {})
    if not isinstance(sources, dict):
        return [], []
    return _items(sources.get("tables")), _items(sources.get("metric_views"))


def _instructions(data: JsonObject) -> JsonObject:
    value = data.get("instructions", {})
    return value if isinstance(value, dict) else {}


def _column_name(column: JsonObject) -> str:
    return str(column.get("column_name") or column.get("name") or "").strip()


def _visible_columns(source: JsonObject) -> list[JsonObject]:
    by_name: dict[str, JsonObject] = {}
    unnamed: list[JsonObject] = []
    for column in _items(source.get("columns")) + _items(source.get("column_configs")):
        if column.get("exclude") is True:
            continue
        name = _column_name(column)
        if not name:
            unnamed.append(column)
            continue
        key = name.lower()
        existing = by_name.get(key)
        if existing is None or (
            not (existing.get("description") or existing.get("comment"))
            and (column.get("description") or column.get("comment"))
        ):
            by_name[key] = column
    return [*by_name.values(), *unnamed]


def agent_description(data: JsonObject, config: AgentDescriptionRuleConfig) -> Evaluation:
    text = _text(data.get("description")).strip()
    bounds = (
        f"(requires >= {config.minimum_characters} characters and >= {config.minimum_words} "
        f"words; recommended >= {config.recommended_characters} characters)"
    )
    message = f"{len(text)} characters {bounds}"
    if not _meaningful(text, config.minimum_characters, config.minimum_words):
        return "fail", message, None
    advisory = None
    if len(text) < config.recommended_characters:
        advisory = (
            f"Under the recommended {config.recommended_characters} characters; add domain, "
            "audience, and scope details."
        )
    return "pass", message, advisory


def table_descriptions(data: JsonObject, config: TableDescriptionsRuleConfig) -> Evaluation:
    tables, metrics = _sources(data)
    sources = tables + metrics
    if not sources:
        return "fail", "No data sources configured", None
    described = sum(
        _meaningful(source.get("description") or source.get("comment")) for source in sources
    )
    coverage = described / len(sources)
    bounds = (
        f"(requires >= {config.minimum_coverage:.0%}, recommended "
        f"{config.recommended_coverage:.0%})"
    )
    message = f"{described}/{len(sources)} data sources described ({coverage:.0%}) {bounds}"
    if coverage < config.minimum_coverage:
        return "fail", message, None
    advisory = None
    if coverage < config.recommended_coverage:
        advisory = f"Below the recommended {config.recommended_coverage:.0%}; document all tables."
    return "pass", message, advisory


def column_descriptions(data: JsonObject, config: ColumnDescriptionsRuleConfig) -> Evaluation:
    tables, metrics = _sources(data)
    columns = [column for source in tables + metrics for column in _visible_columns(source)]
    described = sum(
        _meaningful(column.get("description") or column.get("comment")) for column in columns
    )
    coverage = described / len(columns) if columns else 0
    bounds = (
        f"(requires >= {config.minimum_coverage:.0%}, recommended "
        f"{config.recommended_coverage:.0%})"
    )
    message = f"{described}/{len(columns)} visible columns described ({coverage:.0%}) {bounds}"
    if coverage < config.minimum_coverage:
        return "fail", message, None
    advisory = None
    if coverage < config.recommended_coverage:
        advisory = f"Below the recommended {config.recommended_coverage:.0%}; document all columns."
    return "pass", message, advisory


def instructions(data: JsonObject, config: InstructionsRuleConfig) -> Evaluation:
    entries = _items(_instructions(data).get("text_instructions"))
    characters = sum(len(_text(entry.get("content"))) for entry in entries)
    bounds = f"(requires > {config.minimum_characters}, recommended <= {config.maximum_characters})"
    message = f"{len(entries)} instruction(s), {characters} characters {bounds}"
    if not entries or characters < config.minimum_characters:
        return "fail", message, None
    advisory = None
    if characters > config.maximum_characters:
        advisory = (
            f"Exceeds the recommended maximum of {config.maximum_characters} characters; move "
            "detailed SQL guidance to Example SQLs or SQL Expressions."
        )
    return "pass", message, advisory


def joins(data: JsonObject, config: RuleConfig) -> Evaluation:
    del config
    tables, metrics = _sources(data)
    count = len(_items(_instructions(data).get("join_specs")))
    if not tables and not metrics:
        return "fail", "No data sources configured", None
    recommended = max(len(tables) - 1, 0)
    message = (
        f"{count} join specification(s) for {len(tables)} table(s) (recommended >= {recommended})"
    )
    if len(tables) > 1 and count == 0:
        return "fail", message, None
    advisory = None
    if len(tables) > 1 and count < recommended:
        advisory = f"Below the recommended {recommended}; relationship coverage may be incomplete."
    return "pass", message, advisory


def source_count(data: JsonObject, config: SourceCountRuleConfig) -> Evaluation:
    tables, metrics = _sources(data)
    count = len(tables) + len(metrics)
    bounds = f"(min 1, max {config.maximum_sources}; caution at {config.warning_sources}+)"
    message = f"{count} source(s) {bounds}"
    if count == 0 or count > config.maximum_sources:
        return "fail", message, None
    advisory = None
    if count >= config.warning_sources:
        advisory = (
            f"Approaching the maximum of {config.maximum_sources}; consider focused agents for "
            "broad domains."
        )
    return "pass", message, advisory


def sql_guidance(data: JsonObject, config: SqlGuidanceRuleConfig) -> Evaluation:
    values = _instructions(data)
    snippets = values.get("sql_snippets", {})
    snippets = snippets if isinstance(snippets, dict) else {}
    counts = {
        "functions": len(_items(values.get("sql_functions"))),
        "expressions": len(_items(snippets.get("expressions"))),
        "measures": len(_items(snippets.get("measures"))),
        "filters": len(_items(snippets.get("filters"))),
        "examples": len(_items(values.get("example_question_sqls"))),
    }
    total = sum(counts.values())
    breakdown = ", ".join(f"{count} {name}" for name, count in counts.items())
    message = f"{total} artifact(s): {breakdown} (requires >= {config.minimum_artifacts})"
    if total < config.minimum_artifacts:
        return "fail", message, None
    hints = []
    if counts["measures"] == 0 and counts["filters"] == 0:
        hints.append("add measures or filters to strengthen SQL guidance")
    examples = _items(values.get("example_question_sqls"))
    missing_guidance = sum(
        1 for example in examples if not _text(example.get("usage_guidance")).strip()
    )
    if examples and missing_guidance / len(examples) > 0.5:
        hints.append(f"{missing_guidance}/{len(examples)} example SQLs lack usage guidance")
    advisory = "; ".join(hints).capitalize() + "." if hints else None
    return "pass", message, advisory


def entity_format_matching(data: JsonObject, config: EntityFormatMatchingRuleConfig) -> Evaluation:
    tables, metrics = _sources(data)
    columns = [column for source in tables + metrics for column in _visible_columns(source)]
    entity = sum(column.get("enable_entity_matching") is True for column in columns)
    formatted = sum(
        column.get("enable_format_assistance") is True
        or column.get("format_assistance_enabled") is True
        for column in columns
    )
    bounds = (
        f"(requires >= {config.minimum_enabled_columns} enabled; caution above "
        f"{config.entity_warning_columns}, hard limit {config.entity_maximum_columns})"
    )
    message = f"{entity} entity-matching, {formatted} format-assistance columns {bounds}"
    if entity + formatted < config.minimum_enabled_columns:
        return "fail", message, None
    advisory = None
    if entity > config.entity_maximum_columns:
        advisory = (
            f"Exceeds the hard limit of {config.entity_maximum_columns}; excess columns are "
            "ignored."
        )
    elif entity > config.entity_warning_columns:
        advisory = f"Approaching the entity limit of {config.entity_maximum_columns}."
    return "pass", message, advisory


def benchmarks(data: JsonObject, config: BenchmarksRuleConfig) -> Evaluation:
    value = data.get("benchmarks", {})
    questions = _items(value.get("questions")) if isinstance(value, dict) else []
    bounds = (
        f"(requires >= {config.minimum_questions}, recommended >= {config.recommended_questions})"
    )
    message = f"{len(questions)} benchmark question(s) {bounds}"
    if len(questions) < config.minimum_questions:
        return "fail", message, None
    advisory = None
    if len(questions) < config.recommended_questions:
        advisory = (
            f"Below the recommended {config.recommended_questions}; add more for broader coverage."
        )
    return "pass", message, advisory


def noisy_columns(data: JsonObject, config: NoisyColumnsRuleConfig) -> Evaluation:
    tables, metrics = _sources(data)
    columns_by_table = [_visible_columns(table) for table in tables]
    columns = [column for group in columns_by_table for column in group]
    noisy = [column for column in columns if NOISE_COLUMN.search(_column_name(column))]
    ratio = len(noisy) / len(columns) if columns else 0
    maximum_visible = max((len(group) for group in columns_by_table), default=0)
    if not tables and not metrics:
        return "fail", "No data sources configured", None
    bounds = (
        f"(fails at >= {config.minimum_visible_columns} columns and >= {config.maximum_ratio:.0%} "
        f"noisy, or any table exposing > {config.maximum_visible_per_table} visible columns; "
        f"caution at >= {config.warning_ratio:.0%} noisy)"
    )
    message = (
        f"{len(noisy)}/{len(columns)} visible columns look noisy ({ratio:.0%}); largest table "
        f"exposes {maximum_visible} visible columns {bounds}"
    )
    exceeds_ratio = len(columns) >= config.minimum_visible_columns and ratio >= config.maximum_ratio
    exceeds_table_limit = maximum_visible > config.maximum_visible_per_table
    if exceeds_ratio or exceeds_table_limit:
        return "fail", message, None
    advisory = None
    if len(columns) >= config.minimum_visible_columns and ratio >= config.warning_ratio:
        advisory = (
            f"Noise ratio is approaching the failing threshold of {config.maximum_ratio:.0%}."
        )
    return "pass", message, advisory


RULES = (
    Rule(
        1,
        "agent_description",
        "Agent description",
        agent_description,
        "Add the agent domain, audience, and scope.",
        "Checks whether the bundle Genie resource description is present, meaningful, and not a "
        "placeholder.",
        "At least {minimum_characters} characters and {minimum_words} words",
        "No blocking upper limit",
        "Description is valid but under {recommended_characters} characters. Recommendation: "
        "include domain, audience, and scope.",
    ),
    Rule(
        2,
        "table_descriptions",
        "Table descriptions",
        table_descriptions,
        "Describe the tables used by the agent.",
        "Checks description coverage for tables and metric views after Unity Catalog comments "
        "enrich missing descriptions.",
        "At least {minimum_coverage:.0%} of data sources have descriptions",
        "No blocking upper limit; ideal is {recommended_coverage:.0%}",
        "Coverage is {minimum_coverage:.0%} to below {recommended_coverage:.0%}. It passes, but "
        "recommends documenting all tables.",
    ),
    Rule(
        3,
        "column_descriptions",
        "Column descriptions",
        column_descriptions,
        "Describe visible table columns.",
        "Checks description coverage across columns represented in the Agent configuration, "
        "after Unity Catalog metadata enrichment.",
        "At least {minimum_coverage:.0%} of relevant/visible columns have descriptions",
        "No blocking upper limit; ideal is {recommended_coverage:.0%}",
        "Coverage is {minimum_coverage:.0%} to below {recommended_coverage:.0%}.",
    ),
    Rule(
        4,
        "instructions",
        "Text instructions",
        instructions,
        "Add meaningful business-context instructions.",
        "Checks whether business context exists in text_instructions. Text from all entries is "
        "considered together.",
        "More than {minimum_characters} total characters",
        "No blocking upper limit",
        "Total text exceeds {maximum_characters} characters, or SQL-like content is found. SQL "
        "should be moved to Example SQLs or SQL Expressions.",
    ),
    Rule(
        5,
        "joins",
        "Join specifications",
        joins,
        "Define joins for agents with multiple ordinary tables.",
        "Checks for explicit Genie join guidance when more than one ordinary table is "
        "configured. Metric views do not trigger the requirement.",
        "With multiple ordinary tables, at least 1 join_spec",
        "No blocking upper limit",
        "Number of join specs is below ordinary table count minus 1, indicating potentially "
        "incomplete relationship coverage.",
    ),
    Rule(
        6,
        "source_count",
        "Data source count",
        source_count,
        "Reduce sources or split the domain across agents.",
        "Checks that the Agent has data and that its source scope is not excessively broad. "
        "Tables and metric views both count.",
        "At least 1 table or metric view",
        "Maximum {maximum_sources} tables plus metric views",
        "{warning_sources}-{maximum_sources} sources triggers a recommendation to split the "
        "domain into more focused Agents.",
    ),
    Rule(
        7,
        "sql_guidance",
        "SQL guidance artifacts",
        sql_guidance,
        "Add SQL functions, snippets, or example SQL.",
        "Checks for at least one SQL function, SQL expression, measure, filter, or example SQL "
        "query.",
        "At least {minimum_artifacts} SQL guidance artifact",
        "No blocking upper limit",
        "Warns when SQL snippets are incomplete, notably when measures or filters are missing. "
        "For example SQLs, a warning appears if more than 50% lack usage_guidance.",
    ),
    Rule(
        8,
        "entity_format_matching",
        "Entity/format matching",
        entity_format_matching,
        "Enable entity matching or format assistance on suitable columns.",
        "Checks whether prompt matching is configured anywhere in the Agent. It does not assess "
        "whether the selected columns are appropriate.",
        "At least {minimum_enabled_columns} column with entity matching or format assistance",
        "No blocking upper limit in the scored rule",
        "More than {entity_warning_columns} entity-matching columns warns that the Agent is "
        "approaching the {entity_maximum_columns}-column limit. Above {entity_maximum_columns}, "
        "excess columns are ignored.",
    ),
    Rule(
        9,
        "benchmarks",
        "Benchmark questions",
        benchmarks,
        "Add representative benchmark questions.",
        "Checks whether the Agent contains enough benchmark questions for evaluation and "
        "optimization.",
        "At least {minimum_questions} benchmark questions",
        "No blocking upper limit",
        "{minimum_questions}-{recommended_questions} questions passes, but recommends adding "
        "more for broader coverage.",
    ),
    Rule(
        10,
        "noisy_columns",
        "Column visibility / noise control",
        noisy_columns,
        "Hide internal and noisy columns.",
        "Checks whether the Agent exposes too many technical, audit, raw, opaque, or "
        "internal-looking columns.",
        "Passes unless both noise conditions are reached",
        "Fails when there are at least {minimum_visible_columns} visible columns and at least "
        "{maximum_ratio:.0%} appear noisy/internal",
        "At least {minimum_visible_columns} visible columns with {warning_ratio:.0%} or more "
        "noisy columns triggers an early warning. A separate warning appears when one table "
        "exposes more than {maximum_visible_per_table} visible columns.",
    ),
)
