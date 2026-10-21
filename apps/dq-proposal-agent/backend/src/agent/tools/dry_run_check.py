"""dry_run_check: validates that a candidate SQL returns the canonical shape
  (violations BIGINT, sample_ids ARRAY<STRING>)
and reports the actual violation count + samples.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...clients.sql_warehouse import SqlClient, SqlError


@dataclass
class DryRunResult:
    ok: bool
    violations: int = 0
    sample_ids: list[Any] | None = None
    error: str | None = None


def dry_run_check(sql: SqlClient, candidate_sql: str) -> DryRunResult:
    """Run candidate_sql via the SQL warehouse; verify shape and surface counts."""
    # Strip trailing semicolons and wrap in a guard SELECT to enforce shape.
    candidate = candidate_sql.strip().rstrip(";")
    guard = (
        f"WITH __candidate__ AS ({candidate}) "
        f"SELECT CAST(violations AS BIGINT) AS violations, sample_ids FROM __candidate__ LIMIT 1"
    )
    try:
        rows = sql.query(guard, wait_timeout="50s")
    except SqlError as e:
        return DryRunResult(ok=False, error=str(e))
    except Exception as e:  # noqa: BLE001
        return DryRunResult(ok=False, error=f"{type(e).__name__}: {e}")

    if not rows:
        return DryRunResult(ok=False, error="Candidate SQL produced no rows")

    row = rows[0]
    if "violations" not in row or "sample_ids" not in row:
        return DryRunResult(
            ok=False,
            error=f"Candidate SQL must return columns `violations` and `sample_ids`; got {list(row.keys())}",
        )
    violations = int(row.get("violations") or 0)
    samples = row.get("sample_ids") or []
    if not isinstance(samples, list):
        return DryRunResult(ok=False, error=f"sample_ids must be an array; got {type(samples).__name__}")
    return DryRunResult(ok=True, violations=violations, sample_ids=samples[:100])
