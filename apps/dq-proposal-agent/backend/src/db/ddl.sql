-- Three Delta tables backing the dq-proposal-agent app state.
-- All statements are idempotent; safe to run on every startup.

CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}
  COMMENT 'App tables for dq-proposal-agent: proposal_run, proposal, acceptance.';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.proposal_run (
  id                       STRING NOT NULL,
  target_catalog           STRING NOT NULL,
  target_schema            STRING NOT NULL,
  requested_by             STRING,
  status                   STRING NOT NULL,
  started_at               TIMESTAMP NOT NULL,
  completed_at             TIMESTAMP,
  iterations               INT,
  agent_steps_json         STRING,
  ontology_artifact_path   STRING,
  genie_space_used         STRING,
  report_path              STRING,
  cancel_requested         BOOLEAN
)
USING DELTA
CLUSTER BY (status, started_at)
COMMENT 'One row per agent invocation.';

ALTER TABLE {catalog}.{schema}.proposal_run ADD COLUMNS (report_path STRING);
ALTER TABLE {catalog}.{schema}.proposal_run ADD COLUMNS (cancel_requested BOOLEAN);

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.proposal (
  id                        STRING NOT NULL,
  run_id                    STRING NOT NULL,
  invariant_type            STRING NOT NULL,
  target_table_fqn          STRING NOT NULL,
  target_columns_json       STRING,
  partner_table_fqn         STRING,
  partner_columns_json      STRING,
  generated_sql             STRING NOT NULL,
  rationale                 STRING,
  confidence                DOUBLE,
  provenance                STRING,
  current_violation_count   BIGINT,
  sample_violations_json    STRING,
  status                    STRING NOT NULL,
  reviewer                  STRING,
  reviewer_comment          STRING,
  reviewed_at               TIMESTAMP,
  created_at                TIMESTAMP NOT NULL,
  join_predicate            STRING
)
USING DELTA
CLUSTER BY (run_id, status)
COMMENT 'One row per agent-proposed data-quality check.';

ALTER TABLE {catalog}.{schema}.proposal ADD COLUMNS (join_predicate STRING);

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.acceptance (
  id                       STRING NOT NULL,
  proposal_id              STRING NOT NULL,
  dbsql_alert_id           STRING,
  dbsql_query_id           STRING,
  dqx_rule_path            STRING,
  email_subscribers_json   STRING,
  schedule_cron            STRING,
  created_at               TIMESTAMP NOT NULL
)
USING DELTA
CLUSTER BY (proposal_id)
COMMENT 'Linkage from accepted proposal to created alert / DQX rule.';

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.dq_finding (
  id                       STRING NOT NULL,
  proposal_id              STRING NOT NULL,
  alert_id                 STRING,
  invariant_type           STRING NOT NULL,
  target_table_fqn         STRING NOT NULL,
  target_columns_json      STRING,
  evaluated_at             TIMESTAMP NOT NULL,
  violations               BIGINT NOT NULL,
  sample_ids_json          STRING,
  source                   STRING NOT NULL,
  resolved                 BOOLEAN
)
USING DELTA
CLUSTER BY (proposal_id)
COMMENT 'One row per evaluation of an approved check (agent refresh or alert run).';
