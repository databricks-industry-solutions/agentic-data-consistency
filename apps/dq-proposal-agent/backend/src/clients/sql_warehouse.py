"""SQL Statement Execution wrapper for the configured SQL warehouse."""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState


def sql_literal(value: str | None) -> str:
    """Quote a Python string as a Databricks SQL string literal.

    Spark interprets backslash escapes inside single-quoted literals (`\\n` →
    newline, `\\t` → tab, `\\\\` → `\\`), so we must double backslashes BEFORE
    doubling quotes — otherwise embedded JSON or anything containing `\\n`
    silently corrupts on round-trip. Returns NULL (unquoted) for None.
    """
    if value is None:
        return "NULL"
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _coerce(value: Any, type_name: str | None) -> Any:
    """Convert SQL Statement Execution string returns into Python types."""
    if value is None:
        return None
    if type_name in {"BIGINT", "INT", "INTEGER", "SHORT", "BYTE"}:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if type_name in {"DOUBLE", "FLOAT", "DECIMAL"}:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if type_name in {"TIMESTAMP", "TIMESTAMP_NTZ"}:
        if isinstance(value, str):
            try:
                # Statement API returns ISO8601 with optional 'Z' or offset
                v = value.replace("Z", "+00:00")
                return datetime.fromisoformat(v)
            except ValueError:
                return value
    if type_name == "DATE":
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value).date()
            except ValueError:
                return value
    if type_name == "BOOLEAN":
        # INLINE/JSON_ARRAY ships booleans as the strings "true"/"false".
        # Python truthiness on non-empty strings would silently flip "false" to True.
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() == "true"
        return bool(value)
    if type_name in {"ARRAY", "MAP", "STRUCT"}:
        # JSON_ARRAY disposition ships complex types as JSON-encoded strings.
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value
    return value


class SqlClient:
    def __init__(self, ws: WorkspaceClient, warehouse_id: str) -> None:
        self.ws = ws
        self.warehouse_id = warehouse_id

    def execute(self, statement: str, *, wait_timeout: str = "30s") -> dict[str, Any]:
        """Execute a write/DDL. Returns the final response dict."""
        return self._run(statement, wait_timeout=wait_timeout)

    def query(self, statement: str, *, wait_timeout: str = "30s") -> list[dict[str, Any]]:
        """Execute a read; return list of rows as dicts keyed by column name."""
        resp = self._run(statement, wait_timeout=wait_timeout)
        manifest = resp.get("manifest") or {}
        result = resp.get("result") or {}
        cols = (manifest.get("schema") or {}).get("columns") or []
        names = [c.get("name") for c in cols]
        types = [c.get("type_name") for c in cols]
        rows = result.get("data_array") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            d = {n: _coerce(v, t) for n, v, t in zip(names, r, types)}
            out.append(d)
        return out

    def _run(self, statement: str, *, wait_timeout: str) -> dict[str, Any]:
        api = self.ws.api_client.do
        body = {
            "warehouse_id": self.warehouse_id,
            "statement": statement,
            "wait_timeout": wait_timeout,
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
        resp = api("POST", "/api/2.0/sql/statements", body=body)
        state = (resp.get("status") or {}).get("state")
        # Poll if PENDING or RUNNING beyond wait_timeout
        while state in {StatementState.PENDING.value, StatementState.RUNNING.value}:
            time.sleep(0.5)
            resp = api("GET", f"/api/2.0/sql/statements/{resp['statement_id']}")
            state = (resp.get("status") or {}).get("state")
        if state == StatementState.FAILED.value:
            err = (resp.get("status") or {}).get("error") or {}
            raise SqlError(err.get("message") or f"Statement failed: {err}")
        if state != StatementState.SUCCEEDED.value:
            raise SqlError(f"Unexpected statement state {state}: {resp}")
        return resp


class SqlError(RuntimeError):
    pass
