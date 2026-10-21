"""query_lineage: read column-level lineage from system.access tables.

Returns upstream sources per column. Used as a high-confidence signal for FK,
cross-system FK, and cross-table consistency invariants.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...clients.sql_warehouse import SqlClient, sql_literal


@dataclass
class LineageEntry:
    target_column: str
    upstream_table: str
    upstream_column: str


def query_column_lineage(sql: SqlClient, table_fqn: str) -> list[LineageEntry]:
    """Returns upstream-table/column pairs per column of `table_fqn`.

    Falls back to empty if the system tables aren't accessible (lineage may not
    be enabled, or the user lacks SELECT on system.access).
    """
    catalog, schema, name = table_fqn.split(".", 2)
    q = f"""
SELECT
  cl.target_column_name AS target_column,
  CONCAT_WS('.', cl.source_table_catalog, cl.source_table_schema, cl.source_table_name) AS upstream_table,
  cl.source_column_name AS upstream_column
FROM system.access.column_lineage cl
WHERE cl.target_table_catalog = {sql_literal(catalog)}
  AND cl.target_table_schema = {sql_literal(schema)}
  AND cl.target_table_name = {sql_literal(name)}
  AND cl.source_table_name IS NOT NULL
"""
    try:
        rows = sql.query(q, wait_timeout="30s")
    except Exception:
        return []
    return [
        LineageEntry(
            target_column=r["target_column"],
            upstream_table=r["upstream_table"],
            upstream_column=r["upstream_column"],
        )
        for r in rows
        if r.get("target_column") and r.get("upstream_table")
    ]


@dataclass
class TableLineageEntry:
    upstream_full_name: str  # 'catalog.schema.table' or volume/path identifier
    upstream_type: str        # 'TABLE' | 'PATH' | 'STREAMING_TABLE' | 'VIEW' | …


def query_table_lineage(sql: SqlClient, table_fqn: str) -> list[TableLineageEntry]:
    """Returns upstream-table-level lineage for `table_fqn`.

    Use this when `query_column_lineage` returns empty — table-level lineage is
    populated even when columns aren't traced (e.g., COPY INTO from a Volume).
    Distinct upstreams only.
    """
    catalog, schema, name = table_fqn.split(".", 2)
    q = f"""
SELECT DISTINCT
  CONCAT_WS('.', tl.source_table_catalog, tl.source_table_schema, tl.source_table_name) AS upstream,
  COALESCE(tl.source_type, 'TABLE') AS upstream_type
FROM system.access.table_lineage tl
WHERE tl.target_table_catalog = {sql_literal(catalog)}
  AND tl.target_table_schema  = {sql_literal(schema)}
  AND tl.target_table_name    = {sql_literal(name)}
  AND (tl.source_table_name IS NOT NULL OR tl.source_path IS NOT NULL)
"""
    try:
        rows = sql.query(q, wait_timeout="30s")
    except Exception:
        return []
    out: list[TableLineageEntry] = []
    for r in rows:
        up = (r.get("upstream") or "").strip(".")
        if not up:
            # Path-only source (volume read). Use source_path-style identifier
            # if we have it; otherwise skip — nothing useful for "root system".
            continue
        out.append(TableLineageEntry(
            upstream_full_name=up,
            upstream_type=r.get("upstream_type") or "TABLE",
        ))
    # De-dup preserving order.
    seen: set[str] = set()
    deduped: list[TableLineageEntry] = []
    for e in out:
        if e.upstream_full_name in seen:
            continue
        seen.add(e.upstream_full_name)
        deduped.append(e)
    return deduped
