"""AgentRunner — the discovery + proposal loop.

This is intentionally NOT a free-form LLM tool-calling loop. The flow is:
  1. List tables in target schema.
  2. For each table, fetch schema + declared constraints + (optional) lineage + (optional) ontology.
  3. Profile each column to bucket candidates per InvariantType.
  4. For each candidate (invariant × table[s] × cols):
       a. Build the per-invariant Genie prompt (or fallback template if no Genie).
       b. Ask Genie → get candidate SQL.
       c. dry_run_check the SQL. On shape failure: retry with explicit shape hint, then fall back to template.
       d. If validated: persist Proposal with confidence + provenance.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from opentelemetry import context as otel_context

import mlflow
from databricks.sdk import WorkspaceClient

from ..clients.genie_mcp import GenieClient
from ..clients.sql_warehouse import SqlClient
from ..clients.workspace import list_tables
from ..config import Settings
from ..db.schema import (
    AgentStep,
    InvariantType,
    Proposal,
    ProposalRun,
    ProposalStatus,
    Provenance,
    RunStatus,
)
from ..db.store import Store, new_id
from . import genie_prompts, invariant_catalog, report as report_builder
from ..clients import report_writer
from .tools.ask_genie import ask_genie
from .tools.dry_run_check import DryRunResult, dry_run_check
from .tools.get_table_schema import TableSchema, get_table_schema
from .tools.profile_column import ColumnProfile, profile_column
from .tools.query_constraints import Constraint, query_constraints
from .tools.query_lineage import (
    LineageEntry,
    TableLineageEntry,
    query_column_lineage,
    query_table_lineage,
)
from .tools.query_ontology import OntologyIndex


_PK_NAME_PATTERN = re.compile(r"(_id$|^id$|_pk$|_key$)", re.IGNORECASE)
_AMOUNT_NAME_PATTERN = re.compile(r"(amount|price|qty|fee|balance|total|charge)", re.IGNORECASE)


class _RunCancelled(Exception):
    """Raised by _checkpoint when the run row has cancel_requested=true."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize(name: str) -> str:
    return re.sub(r"_(cd|code|name|dt|nm|date)$", "", name.lower())


_GENERIC_PK_SUFFIXES = {"id", "pk", "key"}


def _suffix_lookup_pk(
    col_name_lower: str,
    pk_index: dict[str, list[tuple[str, str]]],
    *,
    exclude_fqn: str,
) -> tuple[str, str] | None:
    """Find a parent PK by stripping leading underscore-separated prefixes.

    Example: `master_party_id` → tries `party_id` → matches `party.party_id`.
    Skips trivially-generic suffixes (`id`, `pk`, `key`) to avoid false matches.
    Returns the first non-self parent in the index.
    """
    parts = col_name_lower.split("_")
    for i in range(1, len(parts)):
        suffix = "_".join(parts[i:])
        if suffix in _GENERIC_PK_SUFFIXES:
            continue
        if not _PK_NAME_PATTERN.search(suffix):
            continue
        for parent_fqn, parent_col in pk_index.get(suffix, []):
            if parent_fqn != exclude_fqn:
                return (parent_fqn, parent_col)
    return None


@dataclass
class _Candidate:
    invariant: InvariantType
    target_table: str
    target_columns: list[str]
    partner_table: str | None = None
    partner_columns: list[str] | None = None
    rationale: str = ""
    base_confidence: float = 0.6
    provenance: Provenance = Provenance.PROFILE
    prompt: str = ""
    fallback_sql: str = ""
    # SQL fragment for the join (cross-table-consistency / cross-system-FK only).
    # Persisted on the resulting Proposal so the /diff endpoint can rebuild
    # a per-invariant diff query without re-running candidate inference.
    join_predicate: str | None = None


class AgentRunner:
    def __init__(
        self,
        *,
        ws: WorkspaceClient,
        sql: SqlClient,
        genie: GenieClient,
        store: Store,
        settings: Settings,
        ontology: OntologyIndex | None = None,
    ) -> None:
        self.ws = ws
        self.sql = sql
        self.genie = genie
        self.store = store
        self.settings = settings
        self.ontology = ontology or OntologyIndex()
        self._steps: list[AgentStep] = []
        self._iteration = 0
        self._genie_calls = 0
        self._genie_conversation_id: str | None = None
        self._current_run_id: str | None = None
        self._last_cancel_check_at: float = 0.0
        # Protects shared mutable runner state under the materialise ThreadPool.
        self._step_lock = threading.Lock()
        # Table-level lineage fallback: table_fqn -> list of upstream identifiers.
        # Populated by _build_lineage_index when column_lineage comes back empty.
        self._table_lineage_roots: dict[str, list[str]] = {}
        # Trace-correlation context — copied onto every child span so
        # `WHERE attributes:dq_run_id = 'X'` filters spans for one run.
        self._span_context: dict[str, str] = {}

    # ---------------- public ---------------- #

    @mlflow.trace(span_type="AGENT", name="dq_proposal_run")
    def run(self, run: ProposalRun, table_filter: list[str] | None = None,
            dedup_already_accepted: bool | None = None,
            confidence_floor: float | None = None) -> tuple[ProposalRun, list[Proposal]]:
        self._dedup_already_accepted = (
            dedup_already_accepted
            if dedup_already_accepted is not None
            else self.settings.dedup_already_accepted
        )
        self._confidence_floor = (
            confidence_floor
            if confidence_floor is not None
            else self.settings.confidence_floor
        )
        self._current_run_id = run.id
        self._last_cancel_check_at = 0.0
        self._span_context = {
            "dq_run_id": run.id,
            "dq_target_catalog": run.target_catalog,
            "dq_target_schema": run.target_schema,
            "dq_requested_by": run.requested_by or "",
        }
        try:
            mlflow.update_current_trace(
                request_preview=json.dumps({
                    "target": f"{run.target_catalog}.{run.target_schema}",
                    "table_filter": table_filter,
                }),
                tags=self._span_context,
            )
        except Exception:  # noqa: BLE001
            pass
        run.status = RunStatus.RUNNING
        run.genie_space_used = self.settings.genie_space_id or None
        self.store.update_run(run)
        print(f"[run {run.id}] starting; target={run.target_catalog}.{run.target_schema} filter={table_filter}", flush=True)

        proposals: list[Proposal] = []
        try:
            return self._run_pipeline(
                run, table_filter, self._dedup_already_accepted, self._confidence_floor,
            )
        except _RunCancelled:
            print(f"[run {run.id}] cancelled by user", flush=True)
            run.status = RunStatus.FAILED
            run.completed_at = _now()
            run.iterations = self._iteration
            run.agent_steps = list(self._steps)
            try:
                self.store.update_run(run)
            except Exception as e:  # noqa: BLE001
                print(f"[run {run.id}] final-update after cancel failed: {e!r}", flush=True)
            return run, proposals

    def _run_pipeline(
        self, run: ProposalRun, table_filter: list[str] | None,
        dedup_already_accepted: bool = False,
        confidence_floor: float = 0.0,
    ) -> tuple[ProposalRun, list[Proposal]]:
        tables = self._step("list_tables", {"catalog": run.target_catalog, "schema": run.target_schema},
                            lambda: list_tables(self.ws, run.target_catalog, run.target_schema))
        if table_filter:
            allowed = {t.lower() for t in table_filter}
            tables = [t for t in tables if t["name"].lower() in allowed or t["full_name"].lower() in allowed]
        print(f"[run {run.id}] tables: {[t['name'] for t in tables]}", flush=True)
        self._checkpoint(run)

        all_schemas: dict[str, TableSchema] = {}
        all_constraints = self._step(
            "query_constraints",
            {"catalog": run.target_catalog, "schema": run.target_schema},
            lambda: query_constraints(self.sql, run.target_catalog, run.target_schema),
        )
        constraints_by_table: dict[str, list[Constraint]] = {}
        for c in all_constraints:
            constraints_by_table.setdefault(c.table_name, []).append(c)

        for t in tables:
            sch = self._step(
                "get_table_schema",
                {"table": t["full_name"]},
                lambda fn=t["full_name"]: get_table_schema(self.ws, fn),
            )
            all_schemas[t["full_name"]] = sch
        print(f"[run {run.id}] fetched {len(all_schemas)} schemas", flush=True)
        self._checkpoint(run)

        lineage_index = self._build_lineage_index(all_schemas)
        print(f"[run {run.id}] lineage entries: {sum(len(v) for v in lineage_index.values())} across {len(lineage_index)} columns", flush=True)
        self._checkpoint(run)

        candidates: list[_Candidate] = []
        candidates += list(self._candidates_pk(all_schemas, constraints_by_table))
        print(f"[run {run.id}] PK candidates: {len(candidates)}", flush=True)
        self._checkpoint(run)
        candidates += list(self._candidates_fk(all_schemas, constraints_by_table, lineage_index))
        print(f"[run {run.id}] +FK total candidates: {len(candidates)}", flush=True)
        self._checkpoint(run)
        candidates += list(self._candidates_domain_enum(all_schemas))
        print(f"[run {run.id}] +domain_enum total candidates: {len(candidates)}", flush=True)
        self._checkpoint(run)
        candidates += list(self._candidates_numeric_range(all_schemas))
        print(f"[run {run.id}] +numeric_range total candidates: {len(candidates)}", flush=True)
        self._checkpoint(run)
        candidates += list(self._candidates_cross_table_consistency(all_schemas, constraints_by_table, lineage_index))
        print(f"[run {run.id}] +cross_table_consistency total candidates: {len(candidates)}", flush=True)
        self._checkpoint(run)

        # De-dup against already-accepted (idempotency). Off by default so
        # repeated scans produce stable proposal sets — enable via
        # Settings.dedup_already_accepted or per-run override.
        if dedup_already_accepted:
            before = len(candidates)
            candidates = [
                c for c in candidates
                if not self.store.already_accepted_signature(
                    c.invariant.value, c.target_table, c.target_columns,
                )
            ]
            print(f"[run {run.id}] dedup dropped {before - len(candidates)} already-accepted candidates", flush=True)

        # Confidence floor: skip the expensive Genie + dry-run for candidates
        # whose base_confidence is below the threshold.
        if confidence_floor > 0:
            before = len(candidates)
            candidates = [c for c in candidates if c.base_confidence >= confidence_floor]
            print(f"[run {run.id}] floor {confidence_floor:.2f} dropped {before - len(candidates)} low-confidence candidates", flush=True)

        proposals: list[Proposal] = []
        total = len(candidates)
        max_workers = max(1, int(self.settings.genie_concurrent_cap or 1))
        print(
            f"[run {run.id}] materialising {total} candidates with {max_workers}-way parallelism",
            flush=True,
        )

        # Capture the active OTEL/MLflow context so child TOOL spans created in
        # worker threads stay attached to the run's AGENT trace.
        parent_ctx = otel_context.get_current()

        def _worker(cand: _Candidate) -> Proposal | None:
            token = otel_context.attach(parent_ctx)
            try:
                return self._materialise(cand, run.id)
            finally:
                otel_context.detach(token)

        with ThreadPoolExecutor(max_workers=max_workers,
                                thread_name_prefix="dq-mat") as pool:
            futures = {pool.submit(_worker, c): c for c in candidates}
            completed = 0
            cancel_propagating: _RunCancelled | None = None
            for fut in as_completed(futures):
                cand = futures[fut]
                completed += 1
                try:
                    p = fut.result()
                except _RunCancelled as e:
                    # Stop dispatching new work; let in-flight workers finish
                    # so spans close cleanly, then propagate.
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    cancel_propagating = e
                    continue
                except Exception as e:  # noqa: BLE001
                    print(
                        f"[run {run.id}] materialise {completed}/{total} failed "
                        f"({cand.invariant.value} on {cand.target_table}): {e!r}",
                        flush=True,
                    )
                    continue
                print(
                    f"[run {run.id}] materialise {completed}/{total}: "
                    f"{cand.invariant.value} on {cand.target_table}.{cand.target_columns}",
                    flush=True,
                )
                if p is not None:
                    proposals.append(p)
                if completed % 5 == 0:
                    self._checkpoint(run)
        if cancel_propagating is not None:
            raise cancel_propagating

        if proposals:
            self.store.insert_proposals(proposals)
        print(f"[run {run.id}] persisted {len(proposals)} proposals", flush=True)

        run.status = RunStatus.COMPLETED
        run.completed_at = _now()
        run.iterations = self._iteration
        run.agent_steps = list(self._steps)

        # Generate + persist a markdown recommendation report. Soft-fail: a
        # broken volume must not poison the run.
        try:
            report_writer.ensure_volume(
                self.sql, self.settings.app_catalog, self.settings.app_schema,
            )
            md = report_builder.build_markdown(
                run, proposals, confidence_floor=confidence_floor,
            )
            run.report_path = report_writer.write_markdown(
                self.ws,
                app_catalog=self.settings.app_catalog,
                app_schema=self.settings.app_schema,
                run_id=run.id,
                markdown=md,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[run {run.id}] report generation skipped: {e!r}", flush=True)

        self.store.update_run(run)
        return run, proposals

    # ---------------- candidate generators ---------------- #

    def _candidates_pk(
        self, schemas: dict[str, TableSchema], constraints: dict[str, list[Constraint]]
    ) -> Iterable[_Candidate]:
        for fqn, sch in schemas.items():
            short = fqn.split(".")[-1]
            declared = {c.constraint_name for c in constraints.get(short, []) if c.constraint_type == "PRIMARY KEY"}
            for col in sch.columns:
                if not _PK_NAME_PATTERN.search(col.name):
                    continue
                # Use ontology mapping JSONs first
                ont_pk = self.ontology.primary_keys.get(fqn) or self.ontology.primary_keys.get(short)
                base_conf = 0.95
                provenance = Provenance.PROFILE
                rationale_bits = [f"Column `{col.name}` matches PK naming convention."]
                if ont_pk and col.name in ont_pk:
                    base_conf = 0.97
                    provenance = Provenance.ONTOLOGY
                    rationale_bits.append(f"Ontology mapping declares this PK.")
                if declared:
                    base_conf = max(base_conf, 0.99)
                    rationale_bits.append(f"UC already declares PK on this table.")

                # Profile to confirm distinctness ≥ 0.99 and rows >= 100
                prof: ColumnProfile = self._step(
                    "profile_column",
                    {"table": fqn, "column": col.name},
                    lambda fn=fqn, c=col.name: profile_column(self.sql, fn, c, ws=self.ws),
                )
                if prof.row_count < 100:
                    continue
                non_null = max(prof.row_count - prof.null_count, 1)
                distinctness = prof.distinct_count / non_null if non_null else 0
                if distinctness < 0.99 and not declared and not (ont_pk and col.name in ont_pk):
                    continue

                yield _Candidate(
                    invariant=InvariantType.PK_UNIQUE,
                    target_table=fqn,
                    target_columns=[col.name],
                    rationale=" ".join(rationale_bits)
                              + f" Distinctness ≈ {distinctness:.4f} on {prof.row_count} rows.",
                    base_confidence=base_conf,
                    provenance=provenance,
                    prompt=genie_prompts.pk_unique(fqn, col.name),
                    fallback_sql=invariant_catalog.pk_unique_sql(fqn, col.name),
                )

    def _candidates_fk(
        self,
        schemas: dict[str, TableSchema],
        constraints: dict[str, list[Constraint]],
        lineage_index: dict[tuple[str, str], list[tuple[str, str]]],
    ) -> Iterable[_Candidate]:
        # Build a name→tables index of likely PK columns (filtered by name pattern).
        pk_index: dict[str, list[tuple[str, str]]] = {}  # pk_col_name (lower) → [(table_fqn, col_name)]
        for fqn, sch in schemas.items():
            for col in sch.columns:
                if _PK_NAME_PATTERN.search(col.name):
                    pk_index.setdefault(col.name.lower(), []).append((fqn, col.name))

        # Set of (child_fqn, child_col) pairs we've already emitted, to dedup name- vs lineage-based.
        emitted: set[tuple[str, str]] = set()

        for fqn, sch in schemas.items():
            for col in sch.columns:
                if not _PK_NAME_PATTERN.search(col.name):
                    continue
                same = pk_index.get(col.name.lower(), [])
                parents = [(p, c) for (p, c) in same if p != fqn]
                stem_match: tuple[str, str] | None = None
                if not parents:
                    # Try a stem-suffix lookup: e.g. `master_party_id` → look up `party_id`.
                    stem_match = _suffix_lookup_pk(col.name.lower(), pk_index, exclude_fqn=fqn)
                    if not stem_match:
                        continue
                    parents = [stem_match]

                # Strongest signal first: lineage. If this column traces upstream to a
                # column on a known parent table, that parent wins regardless of name.
                lineage_parents = [
                    (up_t, up_c) for (up_t, up_c) in lineage_index.get((fqn, col.name), [])
                    if up_t in schemas and any(p == up_t and c == up_c for (p, c) in parents)
                ]
                provenance = Provenance.PROFILE
                base_confidence = 0.85
                rationale: str

                if lineage_parents:
                    parent_fqn, parent_col = lineage_parents[0]
                    provenance = Provenance.LINEAGE
                    base_confidence = 0.97
                    rationale = (
                        f"`{col.name}` is derived from `{parent_fqn}.{parent_col}` per UC "
                        f"column lineage; this is the parent."
                    )
                elif stem_match:
                    parent_fqn, parent_col = stem_match
                    base_confidence = 0.80
                    rationale = (
                        f"`{col.name}` ends in `{parent_col}` (stem match); "
                        f"parent `{parent_fqn}.{parent_col}`."
                    )
                else:
                    # Fall back to the name-stem heuristic.
                    stem = col.name.lower().rsplit("_id", 1)[0]
                    chosen = None
                    for parent_fqn, parent_col in parents:
                        parent_short = parent_fqn.split(".")[-1].lower()
                        if parent_short == stem or parent_short in stem or stem in parent_short:
                            chosen = (parent_fqn, parent_col)
                            break
                    if chosen is None:
                        chosen = parents[0]
                    parent_fqn, parent_col = chosen
                    rationale = (
                        f"`{col.name}` looks like a FK; parent `{parent_fqn}.{parent_col}` "
                        f"matches naming convention."
                    )

                # Decide cross-system vs same-system based on lineage roots.
                cross_system = self._is_cross_system(fqn, parent_fqn, lineage_index)
                if cross_system:
                    invariant = InvariantType.CROSS_SYSTEM_FK
                    rationale += (
                        f" {fqn} and {parent_fqn} appear to originate from different "
                        f"source systems (distinct lineage roots), so this is a cross-system FK check."
                    )
                    prompt = genie_prompts.cross_system_fk(fqn, col.name, parent_fqn, parent_col)
                    fallback = invariant_catalog.cross_system_fk_sql(fqn, col.name, parent_fqn, parent_col)
                else:
                    invariant = InvariantType.FK_INTEGRITY
                    prompt = genie_prompts.fk_integrity(fqn, col.name, parent_fqn, parent_col)
                    fallback = invariant_catalog.fk_integrity_sql(fqn, col.name, parent_fqn, parent_col)

                if (fqn, col.name) in emitted:
                    continue
                emitted.add((fqn, col.name))

                yield _Candidate(
                    invariant=invariant,
                    target_table=fqn,
                    target_columns=[col.name],
                    partner_table=parent_fqn,
                    partner_columns=[parent_col],
                    rationale=rationale,
                    base_confidence=base_confidence,
                    provenance=provenance,
                    prompt=prompt,
                    fallback_sql=fallback,
                )

        # Lineage-only discovery: any column whose lineage points to a PK column on
        # another table, even if the column name didn't trip the FK heuristic above.
        for (child_fqn, child_col), upstreams in lineage_index.items():
            if child_fqn not in schemas:
                continue
            if (child_fqn, child_col) in emitted:
                continue
            for up_t, up_c in upstreams:
                if up_t == child_fqn or up_t not in schemas:
                    continue
                if not _PK_NAME_PATTERN.search(up_c):
                    continue
                cross_system = self._is_cross_system(child_fqn, up_t, lineage_index)
                if cross_system:
                    invariant = InvariantType.CROSS_SYSTEM_FK
                    prompt = genie_prompts.cross_system_fk(child_fqn, child_col, up_t, up_c)
                    fallback = invariant_catalog.cross_system_fk_sql(child_fqn, child_col, up_t, up_c)
                else:
                    invariant = InvariantType.FK_INTEGRITY
                    prompt = genie_prompts.fk_integrity(child_fqn, child_col, up_t, up_c)
                    fallback = invariant_catalog.fk_integrity_sql(child_fqn, child_col, up_t, up_c)
                emitted.add((child_fqn, child_col))
                yield _Candidate(
                    invariant=invariant,
                    target_table=child_fqn,
                    target_columns=[child_col],
                    partner_table=up_t,
                    partner_columns=[up_c],
                    rationale=(
                        f"`{child_col}` is derived from `{up_t}.{up_c}` per UC column lineage; "
                        f"name heuristic missed this FK relationship."
                    ),
                    base_confidence=0.95,
                    provenance=Provenance.LINEAGE,
                    prompt=prompt,
                    fallback_sql=fallback,
                )
                break  # one parent per child column

    def _candidates_domain_enum(self, schemas: dict[str, TableSchema]) -> Iterable[_Candidate]:
        for fqn, sch in schemas.items():
            for col in sch.columns:
                if "string" not in col.data_type.lower():
                    continue
                if "_id" in col.name.lower() or "_key" in col.name.lower():
                    continue
                prof = self._step(
                    "profile_column",
                    {"table": fqn, "column": col.name},
                    lambda fn=fqn, c=col.name: profile_column(self.sql, fn, c, ws=self.ws),
                )
                if prof.row_count < 50 or prof.distinct_count == 0 or prof.distinct_count > 20:
                    continue
                allowed = [v for (v, _c) in prof.top_k][:20]
                if not allowed:
                    continue
                yield _Candidate(
                    invariant=InvariantType.DOMAIN_ENUM,
                    target_table=fqn,
                    target_columns=[col.name],
                    rationale=f"`{col.name}` has {prof.distinct_count} distinct values; observed enum: {allowed}.",
                    base_confidence=0.75,
                    provenance=Provenance.PROFILE,
                    prompt=genie_prompts.domain_enum(fqn, col.name, allowed),
                    fallback_sql=invariant_catalog.domain_enum_sql(fqn, col.name, allowed),
                )

    def _candidates_numeric_range(self, schemas: dict[str, TableSchema]) -> Iterable[_Candidate]:
        for fqn, sch in schemas.items():
            for col in sch.columns:
                if not any(t in col.data_type.lower() for t in ["int", "decimal", "double", "float", "bigint"]):
                    continue
                if not _AMOUNT_NAME_PATTERN.search(col.name):
                    continue
                yield _Candidate(
                    invariant=InvariantType.NUMERIC_RANGE,
                    target_table=fqn,
                    target_columns=[col.name],
                    rationale=f"`{col.name}` looks like a non-negative amount/balance; checking for negatives.",
                    base_confidence=0.85,
                    provenance=Provenance.PROFILE,
                    prompt=genie_prompts.numeric_range(fqn, col.name, min_inclusive=0),
                    fallback_sql=invariant_catalog.numeric_range_sql(fqn, col.name, min_inclusive=0),
                )

    def _candidates_cross_table_consistency(
        self,
        schemas: dict[str, TableSchema],
        constraints: dict[str, list[Constraint]],
        lineage_index: dict[tuple[str, str], list[tuple[str, str]]] | None = None,
    ) -> Iterable[_Candidate]:
        # Strategy: group columns by normalised name across tables. Same name in 2+
        # tables that are joinable (share a PK or FK link) → consistency candidate.
        norm_index: dict[str, list[tuple[str, str]]] = {}  # normalised_name → [(table, col)]
        for fqn, sch in schemas.items():
            for col in sch.columns:
                if "_id" in col.name.lower() or "_key" in col.name.lower():
                    continue
                norm_index.setdefault(_normalize(col.name), []).append((fqn, col.name))

        # Reusable join detection: try ontology first; else look for shared id columns between the pair.
        for norm_name, occurrences in norm_index.items():
            if len(occurrences) < 2:
                continue
            for i in range(len(occurrences)):
                for j in range(i + 1, len(occurrences)):
                    (table_a, col_a), (table_b, col_b) = occurrences[i], occurrences[j]
                    join_pred, key = self._infer_join(table_a, table_b, schemas)
                    if not join_pred:
                        continue
                    rationale = (
                        f"Columns `{table_a}.{col_a}` and `{table_b}.{col_b}` share the same "
                        f"normalised name and tables are joinable via {join_pred}."
                    )
                    # 0.72 baseline — same-normalised-name + joinable is a solid
                    # heuristic; previous 0.65 was below the default 0.70 floor
                    # and silently killed high-value findings like nationality.
                    base_conf = 0.72
                    provenance = Provenance.PROFILE
                    # Cross-system boost: if lineage tells us the two tables
                    # come from different source systems, that's a real sync-gap
                    # signal and worth a higher prior.
                    if lineage_index and self._is_cross_system(table_a, table_b, lineage_index):
                        base_conf = 0.85
                        provenance = Provenance.LINEAGE
                        rationale += " Source tables come from different upstream systems (cross-system consistency)."
                    # Ontology boost (strongest)
                    iri_match = self._same_property_iri(table_a, col_a, table_b, col_b)
                    if iri_match:
                        base_conf = 0.95
                        provenance = Provenance.ONTOLOGY
                        rationale += f" Both columns map to the same ontology property `{iri_match}`."
                    yield _Candidate(
                        invariant=InvariantType.CROSS_TABLE_CONSISTENCY,
                        target_table=table_a,
                        target_columns=[col_a],
                        partner_table=table_b,
                        partner_columns=[col_b],
                        rationale=rationale,
                        base_confidence=base_conf,
                        provenance=provenance,
                        prompt=genie_prompts.cross_table_consistency(
                            table_a, col_a, table_b, col_b, join_pred, key
                        ),
                        fallback_sql=invariant_catalog.cross_table_consistency_sql(
                            table_a, col_a, table_b, col_b, join_pred, key
                        ),
                        join_predicate=join_pred,
                    )

    def _infer_join(self, t_a: str, t_b: str, schemas: dict[str, TableSchema]) -> tuple[str, str]:
        # 1) Ontology join hint
        for j in self.ontology.joins:
            if {j.from_table, j.to_table} == {t_a, t_b} and j.join_condition:
                # Pick a key from the condition (first column referenced in t_a)
                short_a = t_a.split(".")[-1]
                # Try to extract the LHS column from "x.col = y.col" using the table's short name
                pattern = re.compile(rf"{re.escape(short_a)}\.(\w+)")
                m = pattern.search(j.join_condition)
                key = m.group(1) if m else ""
                return j.join_condition, key
        # 2) Shared *_id column with same name in both tables
        cols_a = {c.name.lower(): c.name for c in schemas[t_a].columns}
        cols_b = {c.name.lower(): c.name for c in schemas[t_b].columns}
        shared_ids = [name for name in cols_a if name in cols_b and (name.endswith("_id") or name == "id")]
        if shared_ids:
            key = cols_a[shared_ids[0]]
            return f"a.{key} = b.{key}", key
        # 3) party_id / master_party_id pattern
        if "master_party_id" in cols_a and "party_id" in cols_b:
            return "a.master_party_id = b.party_id", "master_party_id"
        if "party_id" in cols_a and "master_party_id" in cols_b:
            return "a.party_id = b.master_party_id", "party_id"
        return "", ""

    def _same_property_iri(self, t_a: str, c_a: str, t_b: str, c_b: str) -> str | None:
        a_iri = next((m.property_iri for m in self.ontology.column_mappings
                      if m.table_fqn == t_a and m.column == c_a), None)
        b_iri = next((m.property_iri for m in self.ontology.column_mappings
                      if m.table_fqn == t_b and m.column == c_b), None)
        if a_iri and a_iri == b_iri:
            return a_iri
        return None

    # ---------------- lineage helpers ---------------- #

    def _build_lineage_index(
        self, schemas: dict[str, TableSchema]
    ) -> dict[tuple[str, str], list[tuple[str, str]]]:
        """Pre-fetch column-level lineage for every table in scope.

        Returns {(table_fqn, column) -> [(upstream_table_fqn, upstream_column), ...]}.
        Empty when lineage isn't enabled or the user lacks SELECT on system.access —
        callers degrade to name-based heuristics.
        """
        out: dict[tuple[str, str], list[tuple[str, str]]] = {}
        # Reset table-level fallback for this run.
        self._table_lineage_roots = {}
        for fqn in schemas:
            entries: list[LineageEntry] = self._step(
                "query_lineage",
                {"table": fqn},
                lambda f=fqn: query_column_lineage(self.sql, f),
            )
            for e in entries:
                out.setdefault((fqn, e.target_column), []).append((e.upstream_table, e.upstream_column))
            # If column-lineage came up empty for this table, fall back to
            # system.access.table_lineage so cross-system detection still works.
            if not any(t == fqn for (t, _c) in out):
                tbl_entries: list[TableLineageEntry] = self._step(
                    "query_table_lineage",
                    {"table": fqn},
                    lambda f=fqn: query_table_lineage(self.sql, f),
                )
                if tbl_entries:
                    self._table_lineage_roots[fqn] = [e.upstream_full_name for e in tbl_entries]
        return out

    def _is_cross_system(
        self,
        child_fqn: str,
        parent_fqn: str,
        lineage_index: dict[tuple[str, str], list[tuple[str, str]]],
    ) -> bool:
        """A pair is cross-system when their lineage roots live in different catalogs.

        We use the immediate upstream catalog as a proxy for "source system": if
        any column in the table has a recorded upstream, the upstream's catalog is
        treated as the system root. With no lineage, falls back to the table's own
        catalog (so two same-catalog tables → same system, never cross-system).
        """
        return self._root_catalog(child_fqn, lineage_index) != self._root_catalog(parent_fqn, lineage_index)

    def _root_catalog(
        self,
        table_fqn: str,
        lineage_index: dict[tuple[str, str], list[tuple[str, str]]],
    ) -> str:
        # 1. Column-lineage: catalog of the first recorded upstream column.
        for (t, _c), upstreams in lineage_index.items():
            if t != table_fqn or not upstreams:
                continue
            up_t, _ = upstreams[0]
            return up_t.split(".", 1)[0]
        # 2. Table-lineage fallback (system.access.table_lineage). For volume
        #    reads we still get an upstream identifier; treat the first dotted
        #    segment as the root system.
        tbl_roots = self._table_lineage_roots.get(table_fqn) or []
        if tbl_roots:
            return tbl_roots[0].split(".", 1)[0]
        # 3. Last resort: the table's own catalog.
        return table_fqn.split(".", 1)[0]

    # ---------------- materialisation ---------------- #

    def _materialise(self, cand: _Candidate, run_id: str) -> Proposal | None:
        sql_text, sql_source = self._get_sql(cand)
        if not sql_text:
            return None
        result: DryRunResult = self._step(
            "dry_run_check",
            {"invariant": cand.invariant.value, "target": cand.target_table, "source": sql_source},
            lambda s=sql_text: dry_run_check(self.sql, s),
        )
        if not result.ok:
            # Retry with explicit shape hint via Genie if we used Genie.
            if sql_source == "genie" and self.genie.available:
                hint = (cand.prompt + "\n\nIMPORTANT: SELECT must return EXACTLY the columns "
                                       "`violations BIGINT` and `sample_ids ARRAY<STRING>`. No others.")
                retry_sql, retry_source = self._call_genie(hint, retry=True)
                if retry_sql:
                    retry_res: DryRunResult = self._step(
                        "dry_run_check",
                        {"retry": True, "target": cand.target_table},
                        lambda s=retry_sql: dry_run_check(self.sql, s),
                    )
                    if retry_res.ok:
                        sql_text, sql_source, result = retry_sql, retry_source, retry_res
            if not result.ok:
                # Fall back to template
                tmpl_res: DryRunResult = self._step(
                    "dry_run_check",
                    {"fallback": True, "target": cand.target_table},
                    lambda s=cand.fallback_sql: dry_run_check(self.sql, s),
                )
                if tmpl_res.ok:
                    sql_text, sql_source, result = cand.fallback_sql, "template_fallback", tmpl_res
                else:
                    return None
        rationale = cand.rationale
        provenance = cand.provenance
        if sql_source == "genie":
            provenance = Provenance.LLM if provenance == Provenance.PROFILE else provenance
        confidence = cand.base_confidence

        return Proposal(
            id=new_id(),
            run_id=run_id,
            invariant_type=cand.invariant,
            target_table_fqn=cand.target_table,
            target_columns=cand.target_columns,
            partner_table_fqn=cand.partner_table,
            partner_columns=cand.partner_columns or [],
            generated_sql=sql_text,
            rationale=rationale,
            confidence=confidence,
            provenance=provenance,
            current_violation_count=result.violations,
            sample_violations=result.sample_ids or [],
            status=ProposalStatus.IN_REVIEW,
            created_at=_now(),
            join_predicate=cand.join_predicate,
        )

    def _get_sql(self, cand: _Candidate) -> tuple[str, str]:
        if self.genie.available and self._genie_calls < self.settings.genie_call_cap:
            sql, source = self._call_genie(cand.prompt, retry=False)
            if sql:
                return sql, source
        return cand.fallback_sql, "template"

    def _call_genie(self, prompt: str, *, retry: bool) -> tuple[str, str]:
        self._genie_calls += 1
        result = self._step(
            "ask_genie",
            {"retry": retry, "prompt_preview": prompt[:200]},
            lambda: ask_genie(self.genie, prompt, conversation_id=self._genie_conversation_id),
        )
        if result.conversation_id:
            self._genie_conversation_id = result.conversation_id
        if result.available and result.generated_sql:
            return result.generated_sql, "genie"
        return "", "genie_failed"

    # ---------------- step recording ---------------- #

    def _checkpoint(self, run: ProposalRun) -> None:
        """Persist progress; raise _RunCancelled if the user requested cancel."""
        run.iterations = self._iteration
        run.agent_steps = list(self._steps)
        try:
            self.store.update_run(run)
        except Exception as e:  # noqa: BLE001
            print(f"[run {run.id}] checkpoint failed: {e!r}", flush=True)
        self._raise_if_cancelled(force=True)

    def _raise_if_cancelled(self, *, force: bool = False) -> None:
        """Throttled cancel-check. Called from every _step so long inner loops
        (lineage per table, materialise per candidate) become cancel points.
        Skips the SQL roundtrip if it ran in the last 2 seconds unless force=True."""
        if not self._current_run_id:
            return
        now = time.monotonic()
        if not force and now - self._last_cancel_check_at < 2.0:
            return
        self._last_cancel_check_at = now
        try:
            if self.store.is_cancel_requested(self._current_run_id):
                raise _RunCancelled(f"run {self._current_run_id} cancelled by user")
        except _RunCancelled:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[run {self._current_run_id}] cancel-check failed: {e!r}", flush=True)

    def _step(self, tool: str, args: dict, fn):
        # Iteration counter must increment atomically under the materialise
        # ThreadPool. Capture the local value so subsequent span / step rows
        # are stable regardless of concurrent increments.
        with self._step_lock:
            self._iteration += 1
            iteration = self._iteration
        # Each agent step is a TOOL span in the trace. Args go on as inputs;
        # serialised result on as outputs. Errors get raised as span status.
        with mlflow.start_span(name=tool, span_type="TOOL") as span:
            try:
                # Copy run-level identifiers onto each child span so
                # SELECT * WHERE attributes:dq_run_id = '...' filters cleanly.
                for k, v in self._span_context.items():
                    span.set_attribute(k, v)
                span.set_attribute("dq_iteration", iteration)
                # If the args carry a target table FQN, surface it as its own
                # attribute (most-asked filter dimension).
                target = args.get("table") or args.get("target_table") or args.get("target")
                if target:
                    span.set_attribute("dq_target_table", str(target))
            except Exception:  # noqa: BLE001
                pass
            try:
                span.set_inputs(args)
            except Exception:  # noqa: BLE001
                pass
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001
                with self._step_lock:
                    self._steps.append(AgentStep(
                        iteration=iteration, kind="error", tool=tool,
                        arguments=args, result=str(e), timestamp=_now(),
                    ))
                try:
                    span.set_outputs({"error": str(e)})
                except Exception:
                    pass
                raise
            serialised = _serialise(res)
            with self._step_lock:
                self._steps.append(AgentStep(
                    iteration=iteration, kind="tool_result", tool=tool,
                    arguments=args, result=serialised, timestamp=_now(),
                ))
            try:
                span.set_outputs(serialised)
            except Exception:  # noqa: BLE001
                pass
        # Cancel-check after every tool call so long inner loops (per-table
        # lineage, per-candidate materialise) become responsive cancel points.
        self._raise_if_cancelled()
        return res


def _serialise(value):
    """Best-effort serialisation of tool results for AgentStep persistence."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_serialise(v) for v in value][:50]
    if isinstance(value, dict):
        return {k: _serialise(v) for k, v in list(value.items())[:50]}
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict
        return _serialise(asdict(value))
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)
