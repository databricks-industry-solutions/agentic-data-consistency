"""Pydantic models for app state (mirror the Delta tables in ddl.sql)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class InvariantType(str, Enum):
    PK_UNIQUE = "PK_UNIQUE"
    FK_INTEGRITY = "FK_INTEGRITY"
    DOMAIN_ENUM = "DOMAIN_ENUM"
    NUMERIC_RANGE = "NUMERIC_RANGE"
    CROSS_SYSTEM_FK = "CROSS_SYSTEM_FK"
    CROSS_TABLE_CONSISTENCY = "CROSS_TABLE_CONSISTENCY"


class Provenance(str, Enum):
    PROFILE = "profile"
    ONTOLOGY = "ontology"
    LINEAGE = "lineage"
    LLM = "llm"
    HYBRID = "hybrid"


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ProposalStatus(str, Enum):
    QUEUED = "QUEUED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class AgentStep(BaseModel):
    iteration: int
    kind: str  # tool_call | tool_result | message | error
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    result: Any | None = None
    timestamp: datetime


class ProposalRun(BaseModel):
    id: str
    target_catalog: str
    target_schema: str
    requested_by: str | None = None
    status: RunStatus
    started_at: datetime
    completed_at: datetime | None = None
    iterations: int = 0
    agent_steps: list[AgentStep] = Field(default_factory=list)
    # DEFERRED: ontology integration (see docs/ROADMAP.md). Always None today.
    ontology_artifact_path: str | None = None
    genie_space_used: str | None = None
    report_path: str | None = None
    cancel_requested: bool = False


class Proposal(BaseModel):
    id: str
    run_id: str
    invariant_type: InvariantType
    target_table_fqn: str
    target_columns: list[str] = Field(default_factory=list)
    partner_table_fqn: str | None = None
    partner_columns: list[str] = Field(default_factory=list)
    generated_sql: str
    rationale: str = ""
    confidence: float = 0.0
    provenance: Provenance = Provenance.LLM
    current_violation_count: int = 0
    sample_violations: list[Any] = Field(default_factory=list)
    status: ProposalStatus = ProposalStatus.IN_REVIEW
    reviewer: str | None = None
    reviewer_comment: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    # SQL fragment for the join used by CROSS_TABLE_CONSISTENCY / CROSS_SYSTEM_FK
    # proposals, e.g. "a.master_party_id = b.master_party_id". Used by the
    # /diff endpoint to reconstruct an invariant-specific diff query.
    join_predicate: str | None = None


class Acceptance(BaseModel):
    id: str
    proposal_id: str
    dbsql_alert_id: str | None = None
    dbsql_query_id: str | None = None
    # DEFERRED: DQX rule emission (see docs/ROADMAP.md). Always None today.
    dqx_rule_path: str | None = None
    email_subscribers: list[str] = Field(default_factory=list)
    schedule_cron: str | None = None
    created_at: datetime


class RunRequest(BaseModel):
    catalog: str
    target_schema: str = Field(..., alias="schema")
    table_filter: list[str] | None = None
    run_async: bool = True
    # Per-run override of Settings.dedup_already_accepted. None = use settings.
    dedup_already_accepted: bool | None = None
    # Per-run override of Settings.confidence_floor. None = use settings.
    confidence_floor: float | None = None

    model_config = {"populate_by_name": True}


class DecisionRequest(BaseModel):
    decision: ProposalStatus  # APPROVED or DENIED
    comment: str | None = None
    alert_email_overrides: list[str] | None = None
    alert_schedule_cron: str | None = None
    # DEFERRED: DQX backend (see docs/ROADMAP.md). routes/decisions.py ignores
    # this field; only the v2 DBSQL Alert is ever created.
    output_backends: list[str] | None = None


class RunSummary(BaseModel):
    """Compact summary returned by GET /runs."""
    id: str
    target_catalog: str
    target_schema: str
    status: RunStatus
    started_at: datetime
    completed_at: datetime | None
    proposal_count: int
    accepted_count: int
