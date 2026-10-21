"""POST /api/findings/refresh — re-evaluate every approved proposal."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth import obo_token_from_request, workspace_client_for_user
from ..clients.sql_warehouse import SqlClient
from ..config import Settings, get_settings
from ..db.store import Store
from ..findings import refresh_all

router = APIRouter(prefix="/api/findings", tags=["findings"])


@router.post("/refresh")
def refresh(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    """Re-run every approved proposal's validation SQL and append a finding row each.

    Idempotent (each call appends one row per approved proposal). Can be hit
    manually from the UI or wired to a scheduled Databricks Job for periodic
    trend collection.
    """
    ws = workspace_client_for_user(obo_token_from_request(request))
    sql = SqlClient(ws, settings.databricks_warehouse_id)
    store = Store(sql, settings)
    results = refresh_all(sql, store, source="agent_refresh")
    return {
        "evaluated": len(results),
        "results": results,
    }
