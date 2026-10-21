"""Per-InvariantType prompts sent to Genie to produce validation SQL.

Every prompt requires Genie's SQL to return EXACTLY two columns:
  violations  bigint   — count of offending rows
  sample_ids  array<string> — up to 100 offending row identifiers

The orchestrator validates Genie's SQL against this contract via dry_run_check.
"""
from __future__ import annotations

from ..db.schema import InvariantType


_SHAPE_REQUIREMENT = (
    "The SELECT must return exactly two columns: `violations` (BIGINT count) "
    "and `sample_ids` (ARRAY<STRING> of up to 100 offending row identifiers). "
    "Do not return any other columns. Use `COLLECT_LIST(CAST(<id> AS STRING))` "
    "limited to 100 elements for sample_ids."
)


def pk_unique(table: str, col: str) -> str:
    return (
        f"In the {_schema(table)} schema, find rows where {table}.{col} is not unique "
        f"(values appearing more than once). {_SHAPE_REQUIREMENT}"
    )


def fk_integrity(child_table: str, child_col: str, parent_table: str, parent_col: str) -> str:
    return (
        f"In the {_schema(child_table)} schema, find rows in {child_table} "
        f"whose {child_col} does not match any {parent_col} in {parent_table}. "
        f"Treat NULL {child_col} as not-a-violation. "
        f"sample_ids should contain the offending {child_col} values cast to string. "
        f"{_SHAPE_REQUIREMENT}"
    )


def domain_enum(table: str, col: str, allowed: list[str], pk_col: str | None = None) -> str:
    allowed_sql = ", ".join(f"'{v}'" for v in allowed)
    pk = pk_col or col
    return (
        f"In the {_schema(table)} schema, find rows where {table}.{col} is not in the set "
        f"({allowed_sql}). Ignore NULL {col}. sample_ids should contain {pk} cast to string. "
        f"{_SHAPE_REQUIREMENT}"
    )


def numeric_range(table: str, col: str, *, min_inclusive: float | None = None,
                  max_inclusive: float | None = None, pk_col: str | None = None) -> str:
    pk = pk_col or col
    bounds = []
    if min_inclusive is not None:
        bounds.append(f"{col} < {min_inclusive}")
    if max_inclusive is not None:
        bounds.append(f"{col} > {max_inclusive}")
    predicate = " OR ".join(bounds) if bounds else f"{col} IS NULL"
    return (
        f"In the {_schema(table)} schema, find rows in {table} where {predicate}. "
        f"sample_ids should contain {pk} cast to string. {_SHAPE_REQUIREMENT}"
    )


def cross_system_fk(child_table: str, child_col: str, parent_table: str, parent_col: str) -> str:
    return (
        f"In the {_schema(child_table)} schema, find rows in {child_table} "
        f"whose {child_col} does not match any {parent_col} in {parent_table}. "
        f"This is a cross-system check: {child_table} and {parent_table} are typically "
        f"loaded from independent source systems, so orphans indicate a sync gap. "
        f"sample_ids should contain {child_col} cast to string. {_SHAPE_REQUIREMENT}"
    )


def cross_table_consistency(
    table_a: str, col_a: str, table_b: str, col_b: str, join_predicate: str, entity_key: str
) -> str:
    return (
        f"In the {_schema(table_a)} schema, find cases where {table_a}.{col_a} disagrees with "
        f"{table_b}.{col_b} for the same logical entity, joined via {join_predicate}. "
        f"Use IS DISTINCT FROM for NULL-safe comparison so that one-side-NULL also counts. "
        f"sample_ids should contain the {entity_key} of mismatched rows cast to string. "
        f"{_SHAPE_REQUIREMENT}"
    )


def _schema(table: str) -> str:
    parts = table.split(".")
    if len(parts) >= 2:
        return ".".join(parts[:-1])
    return table


PROMPT_BUILDERS = {
    InvariantType.PK_UNIQUE: pk_unique,
    InvariantType.FK_INTEGRITY: fk_integrity,
    InvariantType.DOMAIN_ENUM: domain_enum,
    InvariantType.NUMERIC_RANGE: numeric_range,
    InvariantType.CROSS_SYSTEM_FK: cross_system_fk,
    InvariantType.CROSS_TABLE_CONSISTENCY: cross_table_consistency,
}
