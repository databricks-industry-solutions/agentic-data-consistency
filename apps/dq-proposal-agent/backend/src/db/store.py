"""UC Delta read/write for app state.

All writes go through the SQL warehouse. We don't use SQLAlchemy — these are
batch-or-rare writes against three Delta tables; raw SQL via the Statement
Execution API is simpler and avoids a separate Postgres dependency.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..clients.sql_warehouse import SqlClient, sql_literal
from .schema import (
    Acceptance,
    AgentStep,
    InvariantType,
    Proposal,
    ProposalRun,
    ProposalStatus,
    Provenance,
    RunStatus,
    RunSummary,
)

DDL_PATH = Path(__file__).parent / "ddl.sql"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


_esc = sql_literal


def _ts(d: datetime | None) -> str:
    if d is None:
        return "NULL"
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return f"TIMESTAMP '{d.strftime('%Y-%m-%d %H:%M:%S.%f%z')}'"


class Store:
    def __init__(self, sql: SqlClient, settings: Settings | None = None) -> None:
        self.sql = sql
        self.settings = settings or get_settings()

    @property
    def ns(self) -> str:
        return self.settings.app_namespace

    # ---------------- DDL ---------------- #

    def ensure_schema(self) -> None:
        """Create catalog / schema / tables if missing."""
        ddl_text = DDL_PATH.read_text().format(
            catalog=self.settings.app_catalog,
            schema=self.settings.app_schema,
        )
        # Strip line-leading `--` comments before splitting on `;`.
        cleaned_lines = []
        for line in ddl_text.splitlines():
            stripped = line.split("--", 1)[0].rstrip() if "--" in line else line
            if stripped.strip():
                cleaned_lines.append(stripped)
        body = "\n".join(cleaned_lines)
        for stmt in [s.strip() for s in body.split(";") if s.strip()]:
            try:
                self.sql.execute(stmt)
            except Exception as e:  # noqa: BLE001
                # Tolerate idempotent ALTER TABLE ADD COLUMNS that re-run after
                # the column already exists. Everything else re-raises.
                msg = str(e).lower()
                if (
                    stmt.upper().startswith("ALTER TABLE")
                    and (
                        "already exists" in msg
                        or "field_already_exists" in msg
                        or "duplicate column" in msg
                    )
                ):
                    continue
                raise

    # ---------------- proposal_run ---------------- #

    def insert_run(self, run: ProposalRun) -> None:
        sql = f"""
INSERT INTO {self.ns}.proposal_run
  (id, target_catalog, target_schema, requested_by, status, started_at, completed_at,
   iterations, agent_steps_json, ontology_artifact_path, genie_space_used,
   report_path, cancel_requested)
VALUES
  ({_esc(run.id)}, {_esc(run.target_catalog)}, {_esc(run.target_schema)},
   {_esc(run.requested_by)}, {_esc(run.status.value)}, {_ts(run.started_at)},
   {_ts(run.completed_at)}, {run.iterations or 0},
   {_esc(_json([s.model_dump(mode='json') for s in run.agent_steps]))},
   {_esc(run.ontology_artifact_path)}, {_esc(run.genie_space_used)}, {_esc(run.report_path)},
   {'true' if run.cancel_requested else 'false'})
"""
        self.sql.execute(sql)

    def update_run(self, run: ProposalRun) -> None:
        sql = f"""
UPDATE {self.ns}.proposal_run SET
  status = {_esc(run.status.value)},
  completed_at = {_ts(run.completed_at)},
  iterations = {run.iterations or 0},
  agent_steps_json = {_esc(_json([s.model_dump(mode='json') for s in run.agent_steps]))},
  genie_space_used = {_esc(run.genie_space_used)},
  report_path = {_esc(run.report_path)}
WHERE id = {_esc(run.id)}
"""
        self.sql.execute(sql)

    def get_run(self, run_id: str) -> ProposalRun | None:
        rows = self.sql.query(f"SELECT * FROM {self.ns}.proposal_run WHERE id = {_esc(run_id)}")
        if not rows:
            return None
        r = rows[0]
        steps = [AgentStep.model_validate(s) for s in json.loads(r["agent_steps_json"] or "[]")]
        return ProposalRun(
            id=r["id"],
            target_catalog=r["target_catalog"],
            target_schema=r["target_schema"],
            requested_by=r.get("requested_by"),
            status=RunStatus(r["status"]),
            started_at=r["started_at"],
            completed_at=r.get("completed_at"),
            iterations=r.get("iterations") or 0,
            agent_steps=steps,
            ontology_artifact_path=r.get("ontology_artifact_path"),
            genie_space_used=r.get("genie_space_used"),
            report_path=r.get("report_path"),
            cancel_requested=bool(r.get("cancel_requested") or False),
        )

    def request_cancel(self, run_id: str) -> None:
        self.sql.execute(
            f"UPDATE {self.ns}.proposal_run SET cancel_requested = true "
            f"WHERE id = {_esc(run_id)}"
        )

    def is_cancel_requested(self, run_id: str) -> bool:
        rows = self.sql.query(
            f"SELECT cancel_requested FROM {self.ns}.proposal_run "
            f"WHERE id = {_esc(run_id)}"
        )
        if not rows:
            return False
        val = rows[0].get("cancel_requested")
        # Statement Execution returns booleans as strings in older client paths;
        # treat anything that isn't an explicit truthy True as False.
        if isinstance(val, str):
            return val.strip().lower() == "true"
        return bool(val)

    def mark_stale_runs_as_failed(self) -> int:
        """Flip any RUNNING/QUEUED rows to FAILED — they were orphaned by an
        app restart (the BackgroundTasks worker died with the process and never
        got to write its final status). Called at app boot.

        Returns the number of rows updated."""
        # Count first so we can log meaningfully.
        rows = self.sql.query(
            f"SELECT COUNT(*) AS n FROM {self.ns}.proposal_run "
            f"WHERE status IN ('RUNNING','QUEUED')"
        )
        n = int(rows[0].get("n") or 0) if rows else 0
        if n == 0:
            return 0
        self.sql.execute(f"""
UPDATE {self.ns}.proposal_run
SET status = 'FAILED',
    completed_at = CURRENT_TIMESTAMP()
WHERE status IN ('RUNNING','QUEUED')
""")
        return n

    def clear_history(self) -> dict[str, int]:
        """Wipe state tables. Returns rows removed per table (best-effort)."""
        out: dict[str, int] = {}
        for t in ("acceptance", "dq_finding", "proposal", "proposal_run"):
            try:
                # Capture pre-count so the UI can show what was wiped.
                rows = self.sql.query(f"SELECT COUNT(*) AS n FROM {self.ns}.{t}")
                pre = int(rows[0].get("n") or 0) if rows else 0
                self.sql.execute(f"DELETE FROM {self.ns}.{t}")
                out[t] = pre
            except Exception as e:  # noqa: BLE001
                out[t] = -1
                print(f"[clear_history] {t} failed: {e!r}")
        return out

    def list_runs(self, limit: int = 100) -> list[RunSummary]:
        sql = f"""
SELECT
  r.id, r.target_catalog, r.target_schema, r.status, r.started_at, r.completed_at,
  COUNT(p.id) AS proposal_count,
  SUM(CASE WHEN p.status = 'APPROVED' THEN 1 ELSE 0 END) AS accepted_count
FROM {self.ns}.proposal_run r
LEFT JOIN {self.ns}.proposal p ON p.run_id = r.id
GROUP BY r.id, r.target_catalog, r.target_schema, r.status, r.started_at, r.completed_at
ORDER BY r.started_at DESC
LIMIT {int(limit)}
"""
        rows = self.sql.query(sql)
        return [
            RunSummary(
                id=r["id"],
                target_catalog=r["target_catalog"],
                target_schema=r["target_schema"],
                status=RunStatus(r["status"]),
                started_at=r["started_at"],
                completed_at=r.get("completed_at"),
                proposal_count=int(r.get("proposal_count") or 0),
                accepted_count=int(r.get("accepted_count") or 0),
            )
            for r in rows
        ]

    # ---------------- proposal ---------------- #

    def insert_proposals(self, proposals: list[Proposal]) -> None:
        if not proposals:
            return
        rows: list[str] = []
        for p in proposals:
            rows.append(
                "("
                + ", ".join(
                    [
                        _esc(p.id),
                        _esc(p.run_id),
                        _esc(p.invariant_type.value),
                        _esc(p.target_table_fqn),
                        _esc(_json(p.target_columns)),
                        _esc(p.partner_table_fqn),
                        _esc(_json(p.partner_columns)),
                        _esc(p.generated_sql),
                        _esc(p.rationale),
                        str(float(p.confidence)),
                        _esc(p.provenance.value),
                        str(int(p.current_violation_count)),
                        _esc(_json(p.sample_violations)),
                        _esc(p.status.value),
                        _esc(p.reviewer),
                        _esc(p.reviewer_comment),
                        _ts(p.reviewed_at),
                        _ts(p.created_at),
                        _esc(p.join_predicate),
                    ]
                )
                + ")"
            )
        sql = f"""
INSERT INTO {self.ns}.proposal
  (id, run_id, invariant_type, target_table_fqn, target_columns_json, partner_table_fqn,
   partner_columns_json, generated_sql, rationale, confidence, provenance,
   current_violation_count, sample_violations_json, status, reviewer, reviewer_comment,
   reviewed_at, created_at, join_predicate)
VALUES {', '.join(rows)}
"""
        self.sql.execute(sql)

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        rows = self.sql.query(
            f"SELECT * FROM {self.ns}.proposal WHERE id = {_esc(proposal_id)}"
        )
        return self._row_to_proposal(rows[0]) if rows else None

    def list_proposals(self, run_id: str) -> list[Proposal]:
        rows = self.sql.query(
            f"SELECT * FROM {self.ns}.proposal WHERE run_id = {_esc(run_id)} "
            f"ORDER BY confidence DESC, invariant_type ASC"
        )
        return [self._row_to_proposal(r) for r in rows]

    def _row_to_proposal(self, r: dict) -> Proposal:
        return Proposal(
            id=r["id"],
            run_id=r["run_id"],
            invariant_type=InvariantType(r["invariant_type"]),
            target_table_fqn=r["target_table_fqn"],
            target_columns=json.loads(r.get("target_columns_json") or "[]"),
            partner_table_fqn=r.get("partner_table_fqn"),
            partner_columns=json.loads(r.get("partner_columns_json") or "[]"),
            generated_sql=r["generated_sql"],
            rationale=r.get("rationale") or "",
            confidence=float(r.get("confidence") or 0.0),
            provenance=Provenance(r.get("provenance") or "llm"),
            current_violation_count=int(r.get("current_violation_count") or 0),
            sample_violations=json.loads(r.get("sample_violations_json") or "[]"),
            status=ProposalStatus(r["status"]),
            reviewer=r.get("reviewer"),
            reviewer_comment=r.get("reviewer_comment"),
            reviewed_at=r.get("reviewed_at"),
            created_at=r["created_at"],
            join_predicate=r.get("join_predicate"),
        )

    def update_proposal_decision(
        self,
        proposal_id: str,
        status: ProposalStatus,
        reviewer: str | None,
        comment: str | None,
    ) -> None:
        sql = f"""
UPDATE {self.ns}.proposal SET
  status = {_esc(status.value)},
  reviewer = {_esc(reviewer)},
  reviewer_comment = {_esc(comment)},
  reviewed_at = {_ts(_now())}
WHERE id = {_esc(proposal_id)}
"""
        self.sql.execute(sql)

    def already_accepted_signature(self, invariant: str, table: str, columns: list[str]) -> bool:
        """Idempotency check: has an equivalent proposal already been accepted?"""
        cols_json = _esc(_json(sorted(columns)))
        sql = f"""
SELECT 1 FROM {self.ns}.proposal p
JOIN {self.ns}.acceptance a ON a.proposal_id = p.id
WHERE p.invariant_type = {_esc(invariant)}
  AND p.target_table_fqn = {_esc(table)}
  AND p.target_columns_json = {cols_json}
LIMIT 1
"""
        return bool(self.sql.query(sql))

    # ---------------- findings ---------------- #

    def insert_finding(self, *, proposal_id: str, alert_id: str | None,
                       invariant_type: str, target_table_fqn: str,
                       target_columns: list[str], violations: int,
                       sample_ids: list[Any], source: str,
                       evaluated_at: datetime | None = None) -> str:
        finding_id = new_id()
        ts = evaluated_at or _now()
        self.sql.execute(f"""
INSERT INTO {self.ns}.dq_finding
  (id, proposal_id, alert_id, invariant_type, target_table_fqn,
   target_columns_json, evaluated_at, violations, sample_ids_json, source)
VALUES
  ({_esc(finding_id)}, {_esc(proposal_id)}, {_esc(alert_id)},
   {_esc(invariant_type)}, {_esc(target_table_fqn)},
   {_esc(_json(sorted(target_columns)))}, {_ts(ts)},
   {int(violations)}, {_esc(_json(sample_ids))}, {_esc(source)})
""")
        return finding_id

    def approved_proposals(self) -> list[dict[str, Any]]:
        """Returns approved proposals with their alert_id (if any)."""
        return self.sql.query(f"""
SELECT p.id, p.invariant_type, p.target_table_fqn, p.target_columns_json,
       p.generated_sql, a.dbsql_alert_id AS alert_id
FROM {self.ns}.proposal p
LEFT JOIN {self.ns}.acceptance a ON a.proposal_id = p.id
WHERE p.status = 'APPROVED'
""")

    # ---------------- acceptance ---------------- #

    def insert_acceptance(self, acc: Acceptance) -> None:
        sql = f"""
INSERT INTO {self.ns}.acceptance
  (id, proposal_id, dbsql_alert_id, dbsql_query_id, dqx_rule_path,
   email_subscribers_json, schedule_cron, created_at)
VALUES
  ({_esc(acc.id)}, {_esc(acc.proposal_id)}, {_esc(acc.dbsql_alert_id)},
   {_esc(acc.dbsql_query_id)}, {_esc(acc.dqx_rule_path)},
   {_esc(_json(acc.email_subscribers))}, {_esc(acc.schedule_cron)},
   {_ts(acc.created_at)})
"""
        self.sql.execute(sql)


def new_id() -> str:
    return str(uuid.uuid4())
