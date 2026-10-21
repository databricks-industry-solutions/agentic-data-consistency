"""Admin / housekeeping endpoints. POST /api/admin/clear-history wipes run state."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import obo_token_from_request, workspace_client_for_user
from ..clients import report_writer
from ..clients.sql_warehouse import SqlClient
from ..config import Settings, get_settings
from ..db.store import Store


router = APIRouter(prefix="/api/admin", tags=["admin"])


class ClearHistoryRequest(BaseModel):
    confirm: bool = False


@router.post("/clear-history")
def clear_history(
    body: ClearHistoryRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    if not body.confirm:
        raise HTTPException(400, "Pass confirm=true to wipe state.")
    ws = workspace_client_for_user(obo_token_from_request(request))
    store = Store(SqlClient(ws, settings.databricks_warehouse_id), settings)
    table_counts = store.clear_history()
    reports_deleted = report_writer.delete_all_reports(
        ws, settings.app_catalog, settings.app_schema,
    )
    return {
        "tables_wiped": table_counts,
        "report_files_deleted": reports_deleted,
        "alerts_note": (
            "DBSQL Alerts created from prior approvals were NOT deleted. "
            "Remove them from the workspace SQL → Alerts panel if desired."
        ),
    }
