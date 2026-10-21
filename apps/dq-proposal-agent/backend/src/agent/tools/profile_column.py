"""profile_column: distinct count, null count, top-K, min/max for a column.

Two paths, in order:
1. **Lakehouse Monitor reuse** — when a UC monitor exists on the table, read the
   most recent row in its `*_profile_metrics` table. Free / fast; no fresh SQL
   scans of the source data.
2. **Sampled SELECT fallback** — three queries against the SQL warehouse over a
   TABLESAMPLE of the source table.

The two paths produce the same ColumnProfile shape; only `source` differs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from databricks.sdk import WorkspaceClient

from ...clients.sql_warehouse import SqlClient, sql_literal


@dataclass
class ColumnProfile:
    table_fqn: str
    column: str
    row_count: int
    null_count: int
    distinct_count: int
    top_k: list[tuple[str, int]]
    min_value: Any | None
    max_value: Any | None
    source: str  # 'monitor' | 'sample'


# Cache: table_fqn -> (profile_metrics_table | None).
# Avoids re-asking the SDK for the same monitor 80x in one run.
_MONITOR_CACHE: dict[str, str | None] = {}


def _lookup_monitor_metrics_table(ws: WorkspaceClient, table_fqn: str) -> str | None:
    """Returns the fully-qualified `*_profile_metrics` table name if a UC
    Quality Monitor exists on `table_fqn`. None otherwise (cached)."""
    if table_fqn in _MONITOR_CACHE:
        return _MONITOR_CACHE[table_fqn]
    metrics: str | None = None
    try:
        monitor = ws.quality_monitors.get(table_name=table_fqn)
        metrics = getattr(monitor, "profile_metrics_table_name", None)
        # The SDK returns this sentinel when the caller doesn't have SELECT on
        # the monitor's output schema — UC redacts the real path. Treat it as
        # "no monitor available" so we fall through to sampled SELECT instead
        # of erroring.
        if metrics and "__db_internal_permission_denied" in metrics:
            metrics = None
    except Exception:  # noqa: BLE001
        metrics = None
    _MONITOR_CACHE[table_fqn] = metrics
    return metrics


def _try_monitor_profile(
    ws: WorkspaceClient,
    sql: SqlClient,
    table_fqn: str,
    column: str,
    *,
    freshness_hours: int,
) -> ColumnProfile | None:
    """Read the latest snapshot row for `column` from the monitor metrics table.
    Returns None if no monitor, missing column, or stale data."""
    metrics_table = _lookup_monitor_metrics_table(ws, table_fqn)
    if not metrics_table:
        return None
    q = f"""
SELECT
  CAST(window.end                           AS STRING)              AS window_end,
  CAST(count                                AS BIGINT)              AS row_count,
  CAST(num_nulls                            AS BIGINT)              AS null_count,
  CAST(distinct_count                       AS BIGINT)              AS distinct_count,
  CAST(min                                  AS STRING)              AS min_v,
  CAST(max                                  AS STRING)              AS max_v,
  TO_JSON(frequent_items)                                            AS frequent_items_json
FROM {metrics_table}
WHERE column_name = {sql_literal(column)}
  AND slice_key   IS NULL
  AND window.end >= CURRENT_TIMESTAMP() - INTERVAL {int(freshness_hours)} HOURS
ORDER BY window.end DESC
LIMIT 1
"""
    try:
        rows = sql.query(q, wait_timeout="20s")
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    r = rows[0]
    import json
    items_json = r.get("frequent_items_json") or "[]"
    try:
        items = json.loads(items_json) if isinstance(items_json, str) else items_json
    except (TypeError, ValueError):
        items = []
    top_k: list[tuple[str, int]] = []
    if isinstance(items, list):
        for it in items[:20]:
            if not isinstance(it, dict):
                continue
            v = it.get("item")
            c = it.get("count") or 0
            if v is None:
                continue
            top_k.append((str(v), int(c)))
    return ColumnProfile(
        table_fqn=table_fqn,
        column=column,
        row_count=int(r.get("row_count") or 0),
        null_count=int(r.get("null_count") or 0),
        distinct_count=int(r.get("distinct_count") or 0),
        top_k=top_k,
        min_value=r.get("min_v"),
        max_value=r.get("max_v"),
        source="monitor",
    )


def profile_column(
    sql: SqlClient,
    table_fqn: str,
    column: str,
    *,
    sample_size: int = 50000,
    ws: WorkspaceClient | None = None,
    monitor_freshness_hours: int = 168,  # 7 days
) -> ColumnProfile:
    """Profile a column.

    If `ws` is supplied and a Lakehouse Monitor exists on the table with fresh
    metrics, returns immediately from the monitor (source='monitor'). Otherwise
    falls through to a sampled SELECT (source='sample').
    """
    if ws is not None:
        cached = _try_monitor_profile(
            ws, sql, table_fqn, column, freshness_hours=monitor_freshness_hours,
        )
        if cached is not None:
            return cached

    n_rows_q = f"SELECT COUNT(*) AS n FROM {table_fqn}"
    n = int(sql.query(n_rows_q)[0]["n"])
    if n == 0:
        return ColumnProfile(table_fqn, column, 0, 0, 0, [], None, None, "sample")

    sample_clause = "" if n <= sample_size else f"TABLESAMPLE ({int(sample_size)} ROWS)"
    stats_q = (
        f"SELECT "
        f"COUNT(*) AS n_sample, "
        f"COUNT(DISTINCT `{column}`) AS distinct_count, "
        f"SUM(CASE WHEN `{column}` IS NULL THEN 1 ELSE 0 END) AS null_count, "
        f"CAST(MIN(`{column}`) AS STRING) AS min_v, "
        f"CAST(MAX(`{column}`) AS STRING) AS max_v "
        f"FROM {table_fqn} {sample_clause}"
    )
    s = sql.query(stats_q)[0]

    topk_q = (
        f"SELECT CAST(`{column}` AS STRING) AS v, COUNT(*) AS c "
        f"FROM {table_fqn} {sample_clause} "
        f"WHERE `{column}` IS NOT NULL "
        f"GROUP BY `{column}` ORDER BY c DESC LIMIT 20"
    )
    topk = sql.query(topk_q)

    return ColumnProfile(
        table_fqn=table_fqn,
        column=column,
        row_count=n,
        null_count=int(s.get("null_count") or 0),
        distinct_count=int(s.get("distinct_count") or 0),
        top_k=[(r["v"], int(r["c"])) for r in topk],
        min_value=s.get("min_v"),
        max_value=s.get("max_v"),
        source="sample",
    )
