"""POST /api/runs, GET /api/runs, GET /api/runs/{id}"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from ..agent.runner import AgentRunner
from ..agent.tools.query_ontology import OntologyIndex
from ..auth import obo_token_from_request, user_email_from_request, workspace_client_for_user
from ..clients.genie_mcp import GenieClient
from ..clients.sql_warehouse import SqlClient
from ..config import Settings, get_settings
from ..db.schema import (
    Proposal,
    ProposalRun,
    RunRequest,
    RunStatus,
    RunSummary,
)
from ..db.store import Store, new_id

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _store(settings: Settings, ws) -> Store:
    sql = SqlClient(ws, settings.databricks_warehouse_id)
    return Store(sql, settings)


def _execute_run(run: ProposalRun, token: str | None, settings: Settings,
                 table_filter: list[str] | None, dedup_already_accepted: bool | None,
                 confidence_floor: float | None) -> None:
    """Background entry point. Re-creates per-request clients (workspace/sql/genie/store)
    inside the worker thread because BackgroundTasks runs after the response is sent
    and we don't want to share request-scoped objects."""
    ws = workspace_client_for_user(token)
    sql = SqlClient(ws, settings.databricks_warehouse_id)
    store = Store(sql, settings)
    genie = GenieClient(ws, settings.genie_space_id)
    runner = AgentRunner(
        ws=ws, sql=sql, genie=genie, store=store, settings=settings,
        ontology=OntologyIndex(),
    )
    try:
        runner.run(
            run,
            table_filter=table_filter,
            dedup_already_accepted=dedup_already_accepted,
            confidence_floor=confidence_floor,
        )
    except Exception as e:  # noqa: BLE001
        run.status = RunStatus.FAILED
        run.completed_at = datetime.now(timezone.utc)
        try:
            store.update_run(run)
        except Exception as inner:  # noqa: BLE001
            print(f"[run {run.id}] failed AND could not persist failure: {e!r} / {inner!r}")
        else:
            print(f"[run {run.id}] failed: {e!r}")


@router.post("", response_model=ProposalRun)
def create_run(
    body: RunRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
) -> ProposalRun:
    token = obo_token_from_request(request)
    email = user_email_from_request(request)
    ws = workspace_client_for_user(token)
    store = _store(settings, ws)
    store.ensure_schema()

    run = ProposalRun(
        id=new_id(),
        target_catalog=body.catalog,
        target_schema=body.target_schema,
        requested_by=email,
        status=RunStatus.QUEUED,
        started_at=datetime.now(timezone.utc),
    )
    store.insert_run(run)

    if body.run_async:
        background_tasks.add_task(
            _execute_run, run, token, settings, body.table_filter,
            body.dedup_already_accepted, body.confidence_floor,
        )
        return run

    sql = SqlClient(ws, settings.databricks_warehouse_id)
    genie = GenieClient(ws, settings.genie_space_id)
    runner = AgentRunner(
        ws=ws, sql=sql, genie=genie, store=store, settings=settings,
        ontology=OntologyIndex(),
    )
    try:
        run, _proposals = runner.run(
            run,
            table_filter=body.table_filter,
            dedup_already_accepted=body.dedup_already_accepted,
            confidence_floor=body.confidence_floor,
        )
    except Exception as e:  # noqa: BLE001
        run.status = RunStatus.FAILED
        run.completed_at = datetime.now(timezone.utc)
        store.update_run(run)
        raise HTTPException(status_code=500, detail=str(e))
    return run


@router.get("", response_model=list[RunSummary])
def list_runs(request: Request, settings: Settings = Depends(get_settings)) -> list[RunSummary]:
    ws = workspace_client_for_user(obo_token_from_request(request))
    store = _store(settings, ws)
    return store.list_runs()


@router.get("/{run_id}", response_model=ProposalRun)
def get_run(run_id: str, request: Request, settings: Settings = Depends(get_settings)) -> ProposalRun:
    ws = workspace_client_for_user(obo_token_from_request(request))
    store = _store(settings, ws)
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/{run_id}/proposals", response_model=list[Proposal])
def list_proposals(run_id: str, request: Request, settings: Settings = Depends(get_settings)) -> list[Proposal]:
    ws = workspace_client_for_user(obo_token_from_request(request))
    store = _store(settings, ws)
    return store.list_proposals(run_id)


@router.post("/{run_id}/cancel")
def cancel_run(run_id: str, request: Request, settings: Settings = Depends(get_settings)) -> dict[str, str]:
    """Best-effort cooperative cancel. Runner checks the flag at each checkpoint."""
    ws = workspace_client_for_user(obo_token_from_request(request))
    store = _store(settings, ws)
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status not in (RunStatus.QUEUED, RunStatus.RUNNING):
        return {"status": run.status.value, "message": "Run is not active; nothing to cancel."}
    store.request_cancel(run_id)
    return {"status": "CANCEL_REQUESTED", "message": "Cancel flag set; runner will exit at next checkpoint."}
