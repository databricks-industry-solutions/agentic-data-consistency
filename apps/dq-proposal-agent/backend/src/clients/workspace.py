"""Helpers around the Databricks WorkspaceClient that don't deserve their own module."""
from __future__ import annotations

from databricks.sdk import WorkspaceClient


def list_schemas(ws: WorkspaceClient, catalog: str) -> list[str]:
    return [s.name for s in ws.schemas.list(catalog_name=catalog) if s.name]


def list_catalogs(ws: WorkspaceClient) -> list[str]:
    return [c.name for c in ws.catalogs.list() if c.name]


def list_tables(ws: WorkspaceClient, catalog: str, schema: str) -> list[dict[str, str]]:
    out = []
    for t in ws.tables.list(catalog_name=catalog, schema_name=schema):
        if not t.name:
            continue
        out.append({
            "name": t.name,
            "full_name": f"{catalog}.{schema}.{t.name}",
            "type": (t.table_type.value if t.table_type else ""),
            "comment": t.comment or "",
        })
    return out
