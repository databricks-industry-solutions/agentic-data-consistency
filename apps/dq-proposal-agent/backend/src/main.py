"""FastAPI entry point for the dq-proposal-agent app."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import credential_source, obo_token_from_request, workspace_client_for_user
from .clients.sql_warehouse import SqlClient
from .config import get_settings
from .db.store import Store
from .mlflow_setup import configure as configure_mlflow
from .routes import admin, catalogs, decisions, findings, proposals, reports, runs


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.databricks_warehouse_id:
        try:
            ws = workspace_client_for_user(None)
            sql = SqlClient(ws, settings.databricks_warehouse_id)
            store = Store(sql, settings)
            store.ensure_schema()
            try:
                n = store.mark_stale_runs_as_failed()
                if n:
                    print(f"[startup] marked {n} stale RUNNING/QUEUED run(s) as FAILED")
            except Exception as e:  # noqa: BLE001
                print(f"[startup] stale-run cleanup skipped: {e}")
        except Exception as e:  # noqa: BLE001
            # Don't crash startup — just log; the first /runs call will retry.
            print(f"[startup] ensure_schema skipped: {e}")

    configure_mlflow(
        experiment_name=settings.mlflow_experiment_name,
        catalog=settings.app_catalog,
        schema=settings.app_schema,
        table_prefix=settings.mlflow_trace_table_prefix,
        sql_warehouse_id=settings.databricks_warehouse_id or None,
    )
    yield


app = FastAPI(title="dq-proposal-agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router)
app.include_router(decisions.router)
app.include_router(catalogs.router)
app.include_router(findings.router)
app.include_router(reports.router)
app.include_router(admin.router)
app.include_router(proposals.router)


@app.get("/api/health")
def health(request: Request) -> dict[str, str]:
    s = get_settings()
    return {
        "status": "ok",
        "warehouse_id": s.databricks_warehouse_id,
        "genie_configured": "yes" if s.genie_space_id else "no",
        "app_namespace": s.app_namespace,
        "auth_mode": s.auth_mode,
        # Which credential this request runs as: "user" (OBO), "app_sp", or a
        # local-dev credential ("pat", "profile", "sdk_default"). See auth.credential_source.
        "effective_identity": credential_source(obo_token_from_request(request)),
    }


# Serve the built frontend if it exists. Two locations supported:
#   - backend/static/    (used by Databricks App deployment, sits next to src/)
#   - frontend/dist/     (local dev convenience)
#
# The frontend uses BrowserRouter, so a refresh or shared link on a client route
# (e.g. /runs/<id>) reaches the server. Serve index.html for those paths and let
# the router take over. Unknown /api/* paths and missing files (anything with an
# extension, e.g. a stale /assets/*.js) still 404.
class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            last = path.rsplit("/", 1)[-1]
            if e.status_code != 404 or path.startswith("api/") or "." in last:
                raise
            return await super().get_response("index.html", scope)


_backend_root = Path(__file__).resolve().parent.parent
for candidate in (_backend_root / "static", _backend_root.parent / "frontend" / "dist"):
    if candidate.exists():
        app.mount("/", SPAStaticFiles(directory=str(candidate), html=True), name="static")
        break
