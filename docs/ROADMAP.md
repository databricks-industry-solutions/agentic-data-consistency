# Roadmap

Features that have design hooks in the codebase but are intentionally **deferred** to a later milestone. Each section explains what's there today, what's missing, and roughly where to start when you pick it up.

---

## Ontology integration

**What's in the repo:**
- `backend/src/agent/tools/query_ontology.py` — `OntologyIndex` loader for `uc-mapping-*.json` files in the shape Ontos uses for Telco BSS.
- `agent/runner.py` constructs an empty `OntologyIndex` per run and consults `self.ontology.primary_keys` in `_candidates_pk`. With an empty index this branch is a no-op.
- `db/schema.py` has an `ontology_artifact_path` field on `ProposalRun`. Never populated today.

**What's missing:**
1. No UI to upload mapping JSONs.
2. No API route accepts them (the plan calls for `POST /api/runs/{id}/ontology` accepting multipart upload).
3. `OntologyIndex` only reads PK and join hints. FK ground-truth, CHECK lists, and SKOS code-list references from the mappings aren't surfaced.
4. `query_ontology` isn't currently called from any `_candidates_*` method beyond the PK check.

**Where to start:**
- Add `routes/ontology_upload.py` mirroring the structure of `routes/runs.py`. Accept a zip of mapping JSONs and persist them to a workspace path indexed by `run_id`.
- Thread an `OntologyIndex` populated from that path into `AgentRunner.run()` (it's already a constructor parameter).
- Extend `_candidates_fk` and `_candidates_cross_table_consistency` to read `OntologyIndex.fks` and `OntologyIndex.column_iris` when present and set `provenance=Provenance.ONTOLOGY` / confidence ≥ 0.95.

---

## DQX rule emission

**What's in the repo:**
- `backend/src/clients/dqx_emitter.py` — `DqxEmitter.write_rule_yaml(proposal)` that appends a DQX-format check to a YAML file at a configured workspace path.
- `DecisionRequest.output_backends` (in `db/schema.py`) accepts `["dbsql_alert", "dqx"]`.
- `settings.dqx_enabled` and `settings.dqx_rules_path` env vars exist.

**What's missing:**
- `routes/decisions.py` never imports or invokes the emitter. `output_backends` is parsed and silently discarded; only the v2 DBSQL Alert is ever created on accept.

**Where to start:**
- In `decisions.py` after the alert is created, branch on `body.output_backends` (defaulting from `settings.default_output_backends`). When `"dqx"` is requested, build a `DqxEmitter(workspace_files_client, settings.dqx_rules_path)` and call `write_rule_yaml(proposal)`. Persist the resulting path into `Acceptance.dqx_rule_path`.
- Add a settings UI toggle that flips `DQ_DQX_ENABLED`.

---

## Lakehouse Monitor profile re-use ✅ DONE

`profile_column` now consults the table's UC Quality Monitor (`*_profile_metrics`) before falling back to a sampled `SELECT`. Behaviour:

- SDK lookup via `ws.quality_monitors.get(table_name=…)` returns the `profile_metrics_table_name`. Result cached per-table for the lifetime of the app process.
- Reads the most recent snapshot row for the column, filtered by `slice_key IS NULL` and `window.end >= NOW() - INTERVAL DQ_MONITOR_FRESHNESS_HOURS` (default 168 h).
- Maps `count`/`num_nulls`/`distinct_count`/`min`/`max`/`frequent_items` → `ColumnProfile`, marked `source='monitor'`.
- Silently falls through to sampled SELECT when no monitor exists, the window is stale, or the SDK returns the `__db_internal_permission_denied` sentinel (caller lacks `SELECT` on `*_monitoring`).

**Requires:** the app SP must have `USE SCHEMA, SELECT` on the monitor's output schema (typically `<source_schema>_monitoring`). Without this grant, the SDK returns the sentinel name and the agent silently re-samples — measurable as `monitor=0, sample=N` in trace counts.

**Follow-up still open:**
- Use `system.data_quality_monitoring.table_results` as a workspace-wide secondary source when the per-monitor schema isn't directly grantable.
- Configurable freshness window via `DQ_MONITOR_FRESHNESS_HOURS` env var (currently hard-coded constant).

---

## Concurrent materialise ✅ DONE

The materialise loop runs in a `ThreadPoolExecutor` sized by `genie_concurrent_cap` (default 4). Span context is propagated to worker threads via OTEL `attach()`/`detach()` so MLflow trace parenthood survives the thread hop. Cancellation is honoured inside each worker via `_raise_if_cancelled`.

Observed speedup on the 4-table demo run: materialise phase dropped from ~8.5 min to ~5 min (effective ~2× rather than 4× — the residual ceiling is the SQL warehouse statement queue and Genie API back-pressure, not the runner code).

---

## Table-lineage fallback ✅ DONE

`query_lineage.py` exposes both `query_column_lineage` and `query_table_lineage`. When `system.access.column_lineage` returns 0 rows for a table (common when the silver layer is loaded from a Volume read with no explicit column projection), the runner falls back to `system.access.table_lineage` and stores per-table upstream roots in `AgentRunner._table_lineage_roots`. `_root_catalog` consults this fallback before defaulting to the table's own catalog.

**Limitation surfaced in practice:** when all upstreams share the same UC catalog (e.g. a single source volume), `_root_catalog` resolves every table to the same root and `_is_cross_system` returns False. To distinguish source systems from a single shared catalog we'd need either:
- A smarter heuristic that drops to upstream schema / volume path / filename (e.g. parse `crm.customer` vs `billing.account` out of the file identifier), or
- An ontology mapping that tags each silver table with its source system.

---

## Other deferred items (smaller)

- **`databricks.yml` Asset Bundle.** Currently deploys via `databricks sync` + `databricks workspace import-dir` (for `static/`, which is gitignored) + `databricks apps deploy`. A bundle would make CI-driven deploys + per-target overrides clean.
- **Batch `query_lineage`.** Currently one query per table against `system.access.column_lineage` (5–10 s each). A single query with `WHERE target_table_name IN (...)` would 10× the lineage phase.
- **Async run via Workflow** for runs spanning > ~50 tables. The app's `BackgroundTasks` model breaks down past a few hundred candidates; UC state already supports being driven from a Databricks Job instead.
- **In-run `profile_column` cache.** Even after Monitor reuse, the same column may be requested twice across candidate generators. A `(table, column) → ColumnProfile` memo on `AgentRunner` would close that gap.
- **Real PDF endpoint.** Today the markdown report renders in-app and can be Print-to-PDF'd from the browser. A `GET .../report.pdf` endpoint via WeasyPrint or similar is on the backlog.
- **Smarter cross-system detection.** See "Table-lineage fallback" above — the `_root_catalog` heuristic is too coarse when all upstream tables share a catalog.

---

## OBO write-API limitation (not deferred — known platform limitation)

When a route handler runs against a Databricks workspace API that mutates state (e.g., `POST /api/2.0/alerts`), the Databricks SDK silently prefers the auto-injected `DATABRICKS_CLIENT_ID`/`SECRET` env vars over an explicit bearer token, so the call lands as the app's service principal regardless of OBO. `routes/decisions.py` works around this by explicitly using SP auth + placing the resulting alert under the SP's home folder, with the requesting user kept as the email subscriber and as `Proposal.reviewer`.

If a future SDK release respects `auth_type='pat'` strictly, the SP/OBO split can be removed and approved alerts will be owned by the user who accepted them.
