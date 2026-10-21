"""query_constraints: read declared PK/FK/CHECK from information_schema."""
from __future__ import annotations

from dataclasses import dataclass

from ...clients.sql_warehouse import SqlClient, sql_literal


@dataclass
class Constraint:
    table_name: str
    constraint_name: str
    constraint_type: str  # PRIMARY KEY | FOREIGN KEY | CHECK | UNIQUE | NOT NULL


def query_constraints(sql: SqlClient, catalog: str, schema: str, table: str | None = None) -> list[Constraint]:
    where = f"constraint_schema = {sql_literal(schema)}"
    if table:
        where += f" AND table_name = {sql_literal(table)}"
    rows = sql.query(
        f"SELECT table_name, constraint_name, constraint_type "
        f"FROM {catalog}.information_schema.table_constraints "
        f"WHERE {where} ORDER BY table_name, constraint_type"
    )
    return [
        Constraint(
            table_name=r["table_name"],
            constraint_name=r["constraint_name"],
            constraint_type=r["constraint_type"],
        )
        for r in rows
    ]
