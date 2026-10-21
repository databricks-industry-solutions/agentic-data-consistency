"""Evaluate approved proposals and record findings in telco_bss.dq_app.dq_finding.

Two entry points:
  - evaluate_proposal(...) — run one proposal's SQL, append a finding row.
  - refresh_all(...) — run every APPROVED proposal, append a row each. Used by
    a backend endpoint / scheduled job to maintain trend data for the dashboard.
"""
from __future__ import annotations

import json
from typing import Any

from .agent.tools.dry_run_check import dry_run_check
from .clients.sql_warehouse import SqlClient
from .db.store import Store


def evaluate_proposal(
    sql: SqlClient,
    store: Store,
    *,
    proposal_id: str,
    invariant_type: str,
    target_table_fqn: str,
    target_columns: list[str],
    generated_sql: str,
    alert_id: str | None,
    source: str,
) -> dict[str, Any]:
    """Execute the proposal's validation SQL, append a finding, return summary."""
    result = dry_run_check(sql, generated_sql)
    violations = int(result.violations or 0) if result.ok else 0
    samples = result.sample_ids or []
    finding_id = store.insert_finding(
        proposal_id=proposal_id,
        alert_id=alert_id,
        invariant_type=invariant_type,
        target_table_fqn=target_table_fqn,
        target_columns=target_columns,
        violations=violations,
        sample_ids=samples,
        source=source,
    )
    return {
        "finding_id": finding_id,
        "proposal_id": proposal_id,
        "violations": violations,
        "ok": result.ok,
        "error": result.error,
    }


def refresh_all(sql: SqlClient, store: Store, source: str = "agent_refresh") -> list[dict[str, Any]]:
    """Re-evaluate every APPROVED proposal once; append a finding row per evaluation."""
    out: list[dict[str, Any]] = []
    for row in store.approved_proposals():
        target_cols = json.loads(row.get("target_columns_json") or "[]")
        out.append(evaluate_proposal(
            sql=sql,
            store=store,
            proposal_id=row["id"],
            invariant_type=row["invariant_type"],
            target_table_fqn=row["target_table_fqn"],
            target_columns=target_cols,
            generated_sql=row["generated_sql"],
            alert_id=row.get("alert_id"),
            source=source,
        ))
    return out
