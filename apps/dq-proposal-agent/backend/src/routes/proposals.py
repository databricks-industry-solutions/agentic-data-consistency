"""GET /api/proposals/{id}/diff

Returns a per-invariant diff sample: actual disagreeing values (not just IDs)
for cross-table-consistency proposals so a reviewer can tell semantic drift
from format mismatch from sync lag without copy-pasting SQL into an editor.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import obo_token_from_request, workspace_client_for_user
from ..clients.sql_warehouse import SqlClient
from ..config import Settings, get_settings
from ..db.schema import InvariantType
from ..db.store import Store


router = APIRouter(prefix="/api/proposals", tags=["proposals"])


def _store(settings: Settings, ws) -> Store:
    return Store(SqlClient(ws, settings.databricks_warehouse_id), settings)


@router.get("/{proposal_id}/diff")
def get_diff(
    proposal_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    limit: int = Query(20, ge=1, le=200),
):
    ws = workspace_client_for_user(obo_token_from_request(request))
    store = _store(settings, ws)
    proposal = store.get_proposal(proposal_id)
    if not proposal:
        raise HTTPException(404, "Proposal not found")

    if proposal.invariant_type != InvariantType.CROSS_TABLE_CONSISTENCY:
        return {
            "supported": False,
            "reason": (
                "diff view is only meaningful for CROSS_TABLE_CONSISTENCY; "
                "other invariants surface enough via sample_ids alone."
            ),
            "rows": [],
        }

    if not proposal.join_predicate:
        # Older proposals predating the join_predicate column. Caller can still
        # use the offending IDs from sample_violations.
        return {
            "supported": False,
            "reason": "proposal has no stored join_predicate (legacy row).",
            "rows": [],
        }

    target_col = (proposal.target_columns or [None])[0]
    partner_col = (proposal.partner_columns or [None])[0]
    if not (target_col and partner_col and proposal.partner_table_fqn):
        raise HTTPException(400, "Proposal missing target/partner column metadata")

    # Pick a usable id column for the row identifier. Prefer the join key on
    # the left side (typical: master_party_id); fall back to target column.
    # join_predicate looks like "a.master_party_id = b.master_party_id" — pull
    # the left-hand column.
    left_key = "*"
    try:
        left = proposal.join_predicate.split("=", 1)[0].strip()
        # strip the `a.` prefix
        if "." in left:
            left = left.split(".", 1)[1]
        left_key = left
    except Exception:  # noqa: BLE001
        pass

    diff_sql = f"""
SELECT a.`{left_key}` AS row_id,
       CAST(a.`{target_col}`  AS STRING) AS target_value,
       CAST(b.`{partner_col}` AS STRING) AS partner_value
FROM   {proposal.target_table_fqn} a
JOIN   {proposal.partner_table_fqn} b ON {proposal.join_predicate}
WHERE  a.`{target_col}` IS DISTINCT FROM b.`{partner_col}`
LIMIT  {int(limit)}
""".strip()

    try:
        rows = store.sql.query(diff_sql, wait_timeout="30s")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"diff query failed: {e}")

    return {
        "supported": True,
        "target_table": proposal.target_table_fqn,
        "target_column": target_col,
        "partner_table": proposal.partner_table_fqn,
        "partner_column": partner_col,
        "join_predicate": proposal.join_predicate,
        "row_id_column": left_key,
        "sql": diff_sql,
        "rows": rows,
    }
