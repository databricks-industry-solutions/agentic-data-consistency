"""get_table_schema: list columns + types + comments for a UC table."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from databricks.sdk import WorkspaceClient


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    comment: str | None
    nullable: bool


@dataclass
class TableSchema:
    full_name: str
    comment: str | None
    columns: list[ColumnInfo]


def get_table_schema(ws: WorkspaceClient, table_fqn: str) -> TableSchema:
    catalog, schema, name = table_fqn.split(".", 2)
    t = ws.tables.get(full_name=table_fqn)
    cols: list[ColumnInfo] = []
    for c in t.columns or []:
        cols.append(
            ColumnInfo(
                name=c.name or "",
                data_type=(c.type_name.value if c.type_name else (c.type_text or "")),
                comment=c.comment,
                nullable=bool(c.nullable),
            )
        )
    return TableSchema(full_name=table_fqn, comment=t.comment, columns=cols)
