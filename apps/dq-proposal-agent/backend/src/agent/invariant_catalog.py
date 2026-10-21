"""Hand-authored fallback SQL templates per invariant type.

These are used when Genie is not configured or when Genie's generated SQL fails
the canonical-shape gate twice. Templates produce the same shape Genie should:
  violations  BIGINT
  sample_ids  ARRAY<STRING>
"""
from __future__ import annotations

from ..clients.sql_warehouse import sql_literal


def pk_unique_sql(table: str, col: str) -> str:
    return (
        f"SELECT COUNT(*) AS violations, "
        f"COLLECT_LIST(CAST({col} AS STRING)) AS sample_ids "
        f"FROM (SELECT {col} FROM {table} "
        f"WHERE {col} IS NOT NULL "
        f"GROUP BY {col} HAVING COUNT(*) > 1 LIMIT 100)"
    )


def fk_integrity_sql(child_table: str, child_col: str, parent_table: str, parent_col: str) -> str:
    return (
        f"SELECT COUNT(*) AS violations, "
        f"SLICE(COLLECT_LIST(CAST(c.{child_col} AS STRING)), 1, 100) AS sample_ids "
        f"FROM {child_table} c LEFT JOIN {parent_table} p "
        f"ON c.{child_col} = p.{parent_col} "
        f"WHERE c.{child_col} IS NOT NULL AND p.{parent_col} IS NULL"
    )


def domain_enum_sql(table: str, col: str, allowed: list[str], pk_col: str | None = None) -> str:
    allowed_sql = ", ".join(sql_literal(v) for v in allowed)
    pk = pk_col or col
    return (
        f"SELECT COUNT(*) AS violations, "
        f"SLICE(COLLECT_LIST(CAST({pk} AS STRING)), 1, 100) AS sample_ids "
        f"FROM {table} "
        f"WHERE {col} IS NOT NULL AND {col} NOT IN ({allowed_sql})"
    )


def numeric_range_sql(
    table: str, col: str, *, min_inclusive: float | None = None,
    max_inclusive: float | None = None, pk_col: str | None = None
) -> str:
    pk = pk_col or col
    parts = []
    if min_inclusive is not None:
        parts.append(f"{col} < {min_inclusive}")
    if max_inclusive is not None:
        parts.append(f"{col} > {max_inclusive}")
    predicate = " OR ".join(parts) if parts else "FALSE"
    return (
        f"SELECT COUNT(*) AS violations, "
        f"SLICE(COLLECT_LIST(CAST({pk} AS STRING)), 1, 100) AS sample_ids "
        f"FROM {table} WHERE {predicate}"
    )


def cross_system_fk_sql(child_table: str, child_col: str, parent_table: str, parent_col: str) -> str:
    # Same shape as fk_integrity; tag is metadata.
    return fk_integrity_sql(child_table, child_col, parent_table, parent_col)


def cross_table_consistency_sql(
    table_a: str, col_a: str, table_b: str, col_b: str, join_predicate: str, entity_key: str
) -> str:
    return (
        f"SELECT COUNT(*) AS violations, "
        f"SLICE(COLLECT_LIST(CAST(a.{entity_key} AS STRING)), 1, 100) AS sample_ids "
        f"FROM {table_a} a JOIN {table_b} b ON {join_predicate} "
        f"WHERE a.{col_a} IS DISTINCT FROM b.{col_b}"
    )
