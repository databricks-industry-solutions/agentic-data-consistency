export type RunStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
export type ProposalStatus = "QUEUED" | "IN_REVIEW" | "APPROVED" | "DENIED";
export type Provenance = "profile" | "ontology" | "lineage" | "llm" | "hybrid";
export type InvariantType =
  | "PK_UNIQUE"
  | "FK_INTEGRITY"
  | "DOMAIN_ENUM"
  | "NUMERIC_RANGE"
  | "CROSS_SYSTEM_FK"
  | "CROSS_TABLE_CONSISTENCY";

export interface AgentStep {
  iteration: number;
  kind: string;
  tool?: string | null;
  arguments?: Record<string, unknown> | null;
  result?: unknown;
  timestamp: string;
}

export interface ProposalRun {
  id: string;
  target_catalog: string;
  target_schema: string;
  requested_by?: string | null;
  status: RunStatus;
  started_at: string;
  completed_at?: string | null;
  iterations: number;
  agent_steps: AgentStep[];
  ontology_artifact_path?: string | null;
  genie_space_used?: string | null;
  report_path?: string | null;
}

export interface ReportResponse {
  path: string;
  markdown: string;
  regenerated: boolean;
}

export interface RunSummary {
  id: string;
  target_catalog: string;
  target_schema: string;
  status: RunStatus;
  started_at: string;
  completed_at?: string | null;
  proposal_count: number;
  accepted_count: number;
}

export interface Proposal {
  id: string;
  run_id: string;
  invariant_type: InvariantType;
  target_table_fqn: string;
  target_columns: string[];
  partner_table_fqn?: string | null;
  partner_columns: string[];
  generated_sql: string;
  rationale: string;
  confidence: number;
  provenance: Provenance;
  current_violation_count: number;
  sample_violations: unknown[];
  status: ProposalStatus;
  reviewer?: string | null;
  reviewer_comment?: string | null;
  reviewed_at?: string | null;
  created_at: string;
  join_predicate?: string | null;
}

export interface DiffRow {
  row_id: unknown;
  target_value: string | null;
  partner_value: string | null;
}

export interface DiffResponse {
  supported: boolean;
  reason?: string;
  target_table?: string;
  target_column?: string;
  partner_table?: string;
  partner_column?: string;
  join_predicate?: string;
  row_id_column?: string;
  sql?: string;
  rows: DiffRow[];
}

export interface RunRequest {
  catalog: string;
  schema: string;
  table_filter?: string[] | null;
  run_async?: boolean;
  dedup_already_accepted?: boolean | null;
  confidence_floor?: number | null;
}

export interface DecisionRequest {
  decision: "APPROVED" | "DENIED";
  comment?: string | null;
  alert_email_overrides?: string[] | null;
  alert_schedule_cron?: string | null;
  output_backends?: string[] | null;
}
