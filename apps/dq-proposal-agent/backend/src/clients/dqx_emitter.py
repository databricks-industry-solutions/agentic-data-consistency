"""DqxEmitter: optional secondary backend on accept. Writes a DQX rule entry
to a YAML file on a configured workspace path.

DQX is the Databricks Labs declarative DQ rule framework. Format reference:
  https://github.com/databrickslabs/dqx

============================================================================
DEFERRED — see docs/ROADMAP.md (DQX rule emission).
The DecisionRequest API model has an `output_backends` field that's parsed
but never honoured. routes/decisions.py does not instantiate or call this
emitter. Today the only backend wired on accept is the v2 DBSQL Alert.
============================================================================
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass

import yaml
from databricks.sdk import WorkspaceClient

from ..db.schema import InvariantType, Proposal


@dataclass
class DqxRuleWritten:
    workspace_path: str
    rule_name: str


class DqxEmitter:
    def __init__(self, ws: WorkspaceClient, base_path: str) -> None:
        self.ws = ws
        self.base_path = base_path.rstrip("/")  # e.g. /Workspace/Shared/dqx-rules

    def write_rule_yaml(self, proposal: Proposal) -> DqxRuleWritten:
        rule = self._proposal_to_rule(proposal)
        # File partitioned per (catalog).(schema)
        parts = proposal.target_table_fqn.split(".")
        cat, sch = (parts[0], parts[1]) if len(parts) >= 2 else (parts[0], "default")
        path = f"{self.base_path}/{cat}.{sch}.yaml"
        existing = self._read_or_empty(path)
        existing.setdefault("rules", [])
        existing["rules"].append(rule)
        self._write(path, yaml.safe_dump(existing, sort_keys=False))
        return DqxRuleWritten(workspace_path=path, rule_name=rule["name"])

    def _proposal_to_rule(self, p: Proposal) -> dict:
        """Map a proposal to a DQX-shaped check entry.

        The DQX schema is:
          name, criticality, check (function + arguments), filter (optional)
        For SQL-driven checks, DQX supports a `sql_expression` check. We use
        that consistently — the SQL is the proposal's `generated_sql`.
        """
        name = (
            f"{p.invariant_type.value.lower()}_"
            f"{p.target_table_fqn.replace('.', '_')}_"
            f"{'_'.join(p.target_columns)}"
        )
        criticality = "error" if p.invariant_type in {
            InvariantType.PK_UNIQUE, InvariantType.FK_INTEGRITY,
            InvariantType.NUMERIC_RANGE,
        } else "warn"
        return {
            "name": name,
            "criticality": criticality,
            "check": {
                "function": "sql_expression",
                "arguments": {"expression": p.generated_sql, "expected_violations": 0},
            },
            "metadata": {
                "invariant_type": p.invariant_type.value,
                "target_table": p.target_table_fqn,
                "target_columns": p.target_columns,
                "rationale": p.rationale,
                "confidence": p.confidence,
                "provenance": p.provenance.value,
                "source_proposal_id": p.id,
            },
        }

    def _read_or_empty(self, path: str) -> dict:
        try:
            buf = io.BytesIO()
            self.ws.workspace.download(path).into(buf)
            buf.seek(0)
            data = yaml.safe_load(buf.read().decode("utf-8")) or {}
            if not isinstance(data, dict):
                return {}
            return data
        except Exception:
            return {}

    def _write(self, path: str, content: str) -> None:
        from databricks.sdk.service.workspace import ImportFormat
        self.ws.workspace.import_(
            path=path,
            content=content.encode("utf-8"),
            format=ImportFormat.AUTO,
            overwrite=True,
        )
