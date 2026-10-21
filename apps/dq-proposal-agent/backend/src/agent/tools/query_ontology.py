"""query_ontology: optional reader for uploaded mapping JSONs.

For v1 we accept a directory of `uc-mapping-<table>.<concept>.json` files (the
shape Ontos uses for Telco BSS) and surface column→property mappings + joins
as a high-confidence signal for the agent.

============================================================================
DEFERRED — see docs/ROADMAP.md (Ontology integration).
The OntologyIndex class is wired through AgentRunner today but is never
populated: there is no UI to upload mapping JSONs and no API route accepts
them. The runner falls through to profile/lineage/heuristics signals when
the index is empty, which is the only mode exercised today.
============================================================================
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ColumnMapping:
    table_fqn: str
    column: str
    property_iri: str
    notes: str | None = None


@dataclass
class JoinHint:
    from_table: str
    to_table: str
    join_condition: str
    relationship: str | None = None


@dataclass
class OntologyIndex:
    column_mappings: list[ColumnMapping] = field(default_factory=list)
    joins: list[JoinHint] = field(default_factory=list)
    primary_keys: dict[str, list[str]] = field(default_factory=dict)

    def by_property(self) -> dict[str, list[ColumnMapping]]:
        out: dict[str, list[ColumnMapping]] = defaultdict(list)
        for m in self.column_mappings:
            out[m.property_iri].append(m)
        return dict(out)

    def joins_for(self, table: str) -> list[JoinHint]:
        return [j for j in self.joins if j.from_table == table or j.to_table == table]


def load_mapping_dir(directory: Path) -> OntologyIndex:
    idx = OntologyIndex()
    if not directory.exists():
        return idx
    for f in sorted(directory.glob("uc-mapping-*.json")):
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        table = data.get("uc_table")
        if not table:
            continue
        if data.get("primary_key_columns"):
            idx.primary_keys[table] = list(data["primary_key_columns"])
        for cm in data.get("column_mappings", []) or []:
            iri = cm.get("property")
            col = cm.get("column")
            if not (iri and col):
                continue
            idx.column_mappings.append(
                ColumnMapping(
                    table_fqn=table,
                    column=col,
                    property_iri=iri,
                    notes=cm.get("notes"),
                )
            )
        for j in data.get("joins", []) or []:
            to_table = j.get("to_table")
            cond = j.get("join_condition")
            if not (to_table and cond):
                continue
            idx.joins.append(
                JoinHint(
                    from_table=table,
                    to_table=to_table,
                    join_condition=cond,
                    relationship=j.get("relationship"),
                )
            )
    return idx
