"""GET / POST /api/runs/{id}/report

GET  returns the current markdown report (regenerates lazily if none stored).
POST forces regeneration against the latest proposal state (post-approvals).

Both accept an optional ?floor= query parameter to filter low-confidence
proposals from the per-table breakdown and SQL appendix. When omitted, the
settings default (Settings.confidence_floor) is used.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response

from ..agent import report as report_builder
from ..auth import obo_token_from_request, workspace_client_for_user
from ..clients import report_writer
from ..clients.sql_warehouse import SqlClient
from ..config import Settings, get_settings
from ..db.store import Store


router = APIRouter(prefix="/api/runs", tags=["reports"])


def _store(settings: Settings, ws) -> Store:
    return Store(SqlClient(ws, settings.databricks_warehouse_id), settings)


def _effective_floor(query_floor: float | None, settings: Settings) -> float:
    if query_floor is None:
        return settings.confidence_floor
    return max(0.0, min(1.0, query_floor))


def _build_and_store(
    ws, store: Store, settings: Settings, run_id: str, floor: float,
) -> tuple[str, str]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    proposals = store.list_proposals(run_id)
    markdown = report_builder.build_markdown(run, proposals, confidence_floor=floor)
    report_writer.ensure_volume(store.sql, settings.app_catalog, settings.app_schema)
    path = report_writer.write_markdown(
        ws,
        app_catalog=settings.app_catalog,
        app_schema=settings.app_schema,
        run_id=run_id,
        markdown=markdown,
    )
    if run.report_path != path:
        run.report_path = path
        store.update_run(run)
    return markdown, path


@router.get("/{run_id}/report")
def get_report(
    run_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    floor: float | None = Query(None, ge=0.0, le=1.0),
):
    """If ?floor is supplied we always regenerate so the markdown reflects the
    requested threshold; without it, the stored copy is returned when present."""
    ws = workspace_client_for_user(obo_token_from_request(request))
    store = _store(settings, ws)
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    eff_floor = _effective_floor(floor, settings)

    if floor is None and run.report_path:
        try:
            md = report_writer.read_markdown(ws, run.report_path)
            return {"path": run.report_path, "markdown": md, "regenerated": False,
                    "confidence_floor": eff_floor}
        except Exception:  # noqa: BLE001
            pass  # fall through and regenerate

    md, path = _build_and_store(ws, store, settings, run_id, eff_floor)
    return {"path": path, "markdown": md, "regenerated": True,
            "confidence_floor": eff_floor}


@router.post("/{run_id}/report")
def regenerate_report(
    run_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    floor: float | None = Query(None, ge=0.0, le=1.0),
):
    ws = workspace_client_for_user(obo_token_from_request(request))
    store = _store(settings, ws)
    eff_floor = _effective_floor(floor, settings)
    md, path = _build_and_store(ws, store, settings, run_id, eff_floor)
    return {"path": path, "markdown": md, "regenerated": True,
            "confidence_floor": eff_floor}


@router.get("/{run_id}/report.md", response_class=PlainTextResponse)
def download_markdown(
    run_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    floor: float | None = Query(None, ge=0.0, le=1.0),
) -> Response:
    ws = workspace_client_for_user(obo_token_from_request(request))
    store = _store(settings, ws)
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    eff_floor = _effective_floor(floor, settings)
    if floor is None and run.report_path:
        try:
            md = report_writer.read_markdown(ws, run.report_path)
        except Exception:  # noqa: BLE001
            md, _ = _build_and_store(ws, store, settings, run_id, eff_floor)
    else:
        md, _ = _build_and_store(ws, store, settings, run_id, eff_floor)
    return PlainTextResponse(
        md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="dq-report-{run_id}.md"'},
    )
