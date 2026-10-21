"""Persist markdown reports to a Unity Catalog volume.

Volume layout: /Volumes/<app_catalog>/<app_schema>/reports/<run_id>.md
The volume is created on first write (idempotent).
"""
from __future__ import annotations

import io

from databricks.sdk import WorkspaceClient

from ..clients.sql_warehouse import SqlClient


_VOLUME_NAME = "reports"


def volume_root(app_catalog: str, app_schema: str) -> str:
    return f"/Volumes/{app_catalog}/{app_schema}/{_VOLUME_NAME}"


def report_path(app_catalog: str, app_schema: str, run_id: str) -> str:
    return f"{volume_root(app_catalog, app_schema)}/{run_id}.md"


def ensure_volume(sql: SqlClient, app_catalog: str, app_schema: str) -> None:
    sql.execute(
        f"CREATE VOLUME IF NOT EXISTS {app_catalog}.{app_schema}.{_VOLUME_NAME} "
        f"COMMENT 'dq-proposal-agent recommendation reports (markdown).'"
    )


def write_markdown(
    ws: WorkspaceClient,
    *,
    app_catalog: str,
    app_schema: str,
    run_id: str,
    markdown: str,
) -> str:
    """Upload the markdown report to the reports volume and return its volume path."""
    path = report_path(app_catalog, app_schema, run_id)
    ws.files.upload(
        file_path=path,
        contents=io.BytesIO(markdown.encode("utf-8")),
        overwrite=True,
    )
    return path


def read_markdown(ws: WorkspaceClient, path: str) -> str:
    resp = ws.files.download(file_path=path)
    body = resp.contents.read() if hasattr(resp.contents, "read") else resp.contents
    return body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)


def delete_all_reports(ws: WorkspaceClient, app_catalog: str, app_schema: str) -> int:
    """Remove every .md under the reports volume. Returns number of files deleted."""
    root = volume_root(app_catalog, app_schema)
    deleted = 0
    try:
        entries = ws.files.list_directory_contents(directory_path=root)
    except Exception:  # noqa: BLE001
        return 0
    for entry in entries:
        path = getattr(entry, "path", None)
        if not path or not path.endswith(".md"):
            continue
        try:
            ws.files.delete(file_path=path)
            deleted += 1
        except Exception as e:  # noqa: BLE001
            print(f"[delete_all_reports] {path}: {e!r}")
    return deleted
