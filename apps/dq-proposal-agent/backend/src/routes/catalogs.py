"""GET /api/catalogs, /api/catalogs/{c}/schemas — used by the New Run dialog."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth import obo_token_from_request, workspace_client_for_user
from ..clients.workspace import list_catalogs, list_schemas, list_tables
from ..config import Settings, get_settings

router = APIRouter(prefix="/api/catalogs", tags=["catalogs"])


@router.get("", response_model=list[str])
def list_all_catalogs(request: Request, settings: Settings = Depends(get_settings)) -> list[str]:
    ws = workspace_client_for_user(obo_token_from_request(request))
    return list_catalogs(ws)


@router.get("/{catalog}/schemas", response_model=list[str])
def list_all_schemas(catalog: str, request: Request, settings: Settings = Depends(get_settings)) -> list[str]:
    ws = workspace_client_for_user(obo_token_from_request(request))
    return list_schemas(ws, catalog)


@router.get("/{catalog}/schemas/{schema}/tables", response_model=list[str])
def list_schema_tables(catalog: str, schema: str, request: Request,
                       settings: Settings = Depends(get_settings)) -> list[str]:
    ws = workspace_client_for_user(obo_token_from_request(request))
    return [t["name"] for t in list_tables(ws, catalog, schema)]
