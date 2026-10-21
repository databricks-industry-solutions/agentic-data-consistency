"""Build a human-readable markdown report of a proposal run.

Pure function — given a ProposalRun + its proposals, produce a markdown string.
The report is the artifact a non-technical reviewer can read offline.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from ..db.schema import Proposal, ProposalRun, ProposalStatus


_INVARIANT_BLURB = {
    "PK_UNIQUE": "Primary-key column must be unique.",
    "FK_INTEGRITY": "Foreign-key value must exist in the referenced table.",
    "DOMAIN_ENUM": "Column value must belong to an allowed set.",
    "NUMERIC_RANGE": "Numeric column must fall within an allowed range.",
    "CROSS_TABLE_CONSISTENCY": "Two columns describing the same entity must agree.",
    "CROSS_SYSTEM_FK": "Foreign-key across systems loaded from independent sources.",
}


def _fmt_pct(n: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{(n / total) * 100:.0f}%"


def _duration(run: ProposalRun) -> str:
    if not run.completed_at:
        return "—"
    delta = run.completed_at - run.started_at
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60}s"


def _recommendation(p: Proposal) -> str:
    if p.confidence >= 0.9 and p.current_violation_count > 0:
        return "**Approve** — high confidence, real violations on live data."
    if p.confidence >= 0.9:
        return "**Approve** — high-confidence invariant, no current violations (acts as a guardrail)."
    if p.current_violation_count > 0:
        return "**Review** — violations present but confidence is moderate; check the SQL before approving."
    return "**Defer** — low confidence and no current violations; revisit if data evolves."


def build_markdown(
    run: ProposalRun,
    proposals: list[Proposal],
    *,
    confidence_floor: float = 0.0,
) -> str:
    # Split into shown vs hidden. Summary section reports against the full set;
    # per-table breakdown and SQL appendix only include the shown set.
    if confidence_floor > 0:
        shown = [p for p in proposals if p.confidence >= confidence_floor]
        hidden = [p for p in proposals if p.confidence < confidence_floor]
    else:
        shown = list(proposals)
        hidden = []

    by_invariant: dict[str, list[Proposal]] = defaultdict(list)
    by_table: dict[str, list[Proposal]] = defaultdict(list)
    status_counts: Counter[str] = Counter()

    for p in proposals:
        by_invariant[p.invariant_type.value].append(p)
        by_table[p.target_table_fqn].append(p)
        status_counts[p.status.value] += 1

    total = len(proposals)
    with_violations = sum(1 for p in proposals if p.current_violation_count > 0)
    total_violations = sum(p.current_violation_count for p in proposals)
    approved = status_counts.get(ProposalStatus.APPROVED.value, 0)
    denied = status_counts.get(ProposalStatus.DENIED.value, 0)
    pending = total - approved - denied

    lines: list[str] = []
    lines.append(f"# Data Quality Recommendations — `{run.target_catalog}.{run.target_schema}`")
    lines.append("")
    lines.append(
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
        f"by dq-proposal-agent (run `{run.id}`)._"
    )
    lines.append("")

    # ---- 1. Executive summary ----
    lines.append("## 1. Executive summary")
    lines.append("")
    lines.append(f"- **Target schema:** `{run.target_catalog}.{run.target_schema}`")
    lines.append(f"- **Run status:** {run.status.value}  ·  **Runtime:** {_duration(run)}")
    lines.append(f"- **Proposals emitted:** {total}")
    lines.append(
        f"- **Decisions:** {approved} approved · {denied} denied · {pending} pending review"
    )
    lines.append(
        f"- **Live violations:** {total_violations:,} across {with_violations} of {total} checks "
        f"({_fmt_pct(with_violations, total)})"
    )
    if confidence_floor > 0:
        lines.append(
            f"- **Confidence floor applied:** {confidence_floor:.2f} — "
            f"{len(hidden)} proposal(s) hidden from the per-table breakdown and SQL appendix."
        )
    if run.genie_space_used:
        lines.append(f"- **Genie space used for SQL generation:** `{run.genie_space_used}`")
    lines.append("")

    lines.append("### Top 5 highest-violation findings")
    lines.append("")
    top = sorted(proposals, key=lambda p: p.current_violation_count, reverse=True)[:5]
    if not top or top[0].current_violation_count == 0:
        lines.append("_No active violations on live data._")
    else:
        lines.append("| # | Invariant | Target | Violations | Confidence | Provenance |")
        lines.append("|---|---|---|---:|---:|---|")
        for i, p in enumerate(top, 1):
            lines.append(
                f"| {i} | `{p.invariant_type.value}` | `{p.target_table_fqn}` "
                f"({', '.join(p.target_columns) or '—'}) "
                f"| {p.current_violation_count:,} | {p.confidence:.2f} "
                f"| {p.provenance.value} |"
            )
    lines.append("")

    lines.append("### Coverage by invariant")
    lines.append("")
    lines.append("| Invariant | Count | With violations |")
    lines.append("|---|---:|---:|")
    for inv, plist in sorted(by_invariant.items()):
        viol = sum(1 for p in plist if p.current_violation_count > 0)
        lines.append(f"| `{inv}` | {len(plist)} | {viol} |")
    lines.append("")

    # ---- 2. Per-table breakdown (floor-filtered) ----
    shown_by_table: dict[str, list[Proposal]] = defaultdict(list)
    for p in shown:
        shown_by_table[p.target_table_fqn].append(p)
    lines.append("## 2. Per-table breakdown")
    lines.append("")
    if not shown_by_table:
        lines.append(
            f"_All {total} proposal(s) fell below the {confidence_floor:.2f} confidence floor._"
        )
        lines.append("")
    for table in sorted(shown_by_table.keys()):
        table_proposals = sorted(
            shown_by_table[table],
            key=lambda p: (-p.current_violation_count, -p.confidence),
        )
        lines.append(f"### `{table}`")
        lines.append("")
        lines.append("| Invariant | Columns | Confidence | Provenance | Violations | Status | Recommendation |")
        lines.append("|---|---|---:|---|---:|---|---|")
        for p in table_proposals:
            cols = ", ".join(p.target_columns) or "—"
            if p.partner_table_fqn:
                cols += f" → `{p.partner_table_fqn}`"
                if p.partner_columns:
                    cols += f" ({', '.join(p.partner_columns)})"
            lines.append(
                f"| `{p.invariant_type.value}` | {cols} | {p.confidence:.2f} "
                f"| {p.provenance.value} | {p.current_violation_count:,} "
                f"| {p.status.value} | {_recommendation(p)} |"
            )
        lines.append("")
        # Rationale per proposal
        for p in table_proposals:
            if p.rationale:
                lines.append(f"- **{p.invariant_type.value} on {', '.join(p.target_columns) or '—'}** — {p.rationale}")
        lines.append("")

    # ---- 3. SQL appendix ----
    lines.append("## 3. SQL appendix")
    lines.append("")
    lines.append(
        "Every proposal's SQL has already been dry-run against live data and conforms to "
        "the shape contract `(violations BIGINT, sample_ids ARRAY<STRING>)`."
    )
    lines.append("")
    for p in shown:
        header = (
            f"### `{p.invariant_type.value}` on `{p.target_table_fqn}`"
            f"{(' — ' + ', '.join(p.target_columns)) if p.target_columns else ''}"
        )
        lines.append(header)
        lines.append("")
        lines.append(
            f"- Confidence **{p.confidence:.2f}**, provenance `{p.provenance.value}`, "
            f"violations **{p.current_violation_count:,}**, status `{p.status.value}`."
        )
        if p.sample_violations:
            preview = ", ".join(str(s) for s in p.sample_violations[:5])
            more = f" … (+{len(p.sample_violations) - 5} more)" if len(p.sample_violations) > 5 else ""
            lines.append(f"- Sample offending IDs: `{preview}`{more}")
        lines.append("")
        lines.append("```sql")
        lines.append(p.generated_sql.strip())
        lines.append("```")
        lines.append("")

    # ---- 4. Methodology footer ----
    lines.append("## 4. Methodology")
    lines.append("")
    lines.append(
        "The agent runs a deterministic discovery pipeline — it does **not** free-form a "
        "tool-call loop. For each `(invariant × target)` candidate it (1) lists tables and "
        "constraints in the target schema, (2) profiles columns, (3) consults column lineage, "
        "(4) asks Genie to author the validation SQL using a per-invariant prompt template, "
        "(5) dry-runs the candidate SQL against live data and verifies its column shape, "
        "and (6) persists the validated SQL as a Proposal. Hand-authored fallback templates "
        "kick in when Genie isn't reachable."
    )
    lines.append("")
    lines.append("**Invariant types covered:**")
    lines.append("")
    for inv, blurb in _INVARIANT_BLURB.items():
        lines.append(f"- `{inv}` — {blurb}")
    lines.append("")
    lines.append(
        f"Full agent trace (one OTEL span per tool call) is available in MLflow under "
        f"`dq_run_id = '{run.id}'`."
    )
    lines.append("")
    return "\n".join(lines)
