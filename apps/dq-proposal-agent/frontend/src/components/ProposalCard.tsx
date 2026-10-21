import { useState } from "react";
import { api } from "@/hooks/useApi";
import { Badge, provenanceVariant, statusBadgeVariant } from "@/components/Badge";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import type { DiffResponse, Proposal } from "@/types";

const INVARIANT_LABEL: Record<string, string> = {
  PK_UNIQUE: "PK uniqueness",
  FK_INTEGRITY: "FK integrity",
  DOMAIN_ENUM: "Domain enum",
  NUMERIC_RANGE: "Numeric range",
  CROSS_SYSTEM_FK: "Cross-system FK",
  CROSS_TABLE_CONSISTENCY: "Cross-table consistency",
};

export function ProposalCard({
  proposal,
  onChange,
}: {
  proposal: Proposal;
  onChange: (p: Proposal) => void;
}) {
  const [showSql, setShowSql] = useState(false);
  const [showSamples, setShowSamples] = useState(false);
  const [comment, setComment] = useState("");
  const [schedule, setSchedule] = useState("0 0 6 * * ?");  // daily 06:00 UTC
  const [busy, setBusy] = useState(false);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [showDiff, setShowDiff] = useState(false);

  const supportsDiff = proposal.invariant_type === "CROSS_TABLE_CONSISTENCY";

  async function loadDiff() {
    if (diff) {
      setShowDiff((x) => !x);
      return;
    }
    setDiffLoading(true);
    try {
      const r = await api<DiffResponse>(`/api/proposals/${proposal.id}/diff?limit=20`);
      setDiff(r);
      setShowDiff(true);
    } catch (e) {
      alert(`Diff failed: ${(e as Error).message}`);
    } finally {
      setDiffLoading(false);
    }
  }

  async function decide(decision: "APPROVED" | "DENIED") {
    setBusy(true);
    try {
      const body: Record<string, unknown> = { decision, comment: comment || null };
      if (decision === "APPROVED") body.alert_schedule_cron = schedule;
      const updated = await api<Proposal>(`/api/proposals/${proposal.id}/decision`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      onChange(updated);
    } catch (e) {
      alert(`Failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  // Quartz cron presets for the schedule picker. The v2 Alerts API uses
  // Quartz syntax (6 or 7 fields, with `?` to skip day-of-week when day-of-month
  // is set, and vice-versa).
  const SCHEDULE_PRESETS = [
    { label: "Every 15 minutes",   cron: "0 0/15 * * * ?" },
    { label: "Hourly",             cron: "0 0 * * * ?" },
    { label: "Daily 06:00 UTC",    cron: "0 0 6 * * ?" },
    { label: "Daily 18:00 UTC",    cron: "0 0 18 * * ?" },
    { label: "Weekly Mon 08:00",   cron: "0 0 8 ? * MON" },
  ];

  const isOpen = proposal.status === "IN_REVIEW" || proposal.status === "QUEUED";

  return (
    <div className="card">
      <div className="card-header flex items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge variant="info">{INVARIANT_LABEL[proposal.invariant_type]}</Badge>
            <Badge variant={provenanceVariant(proposal.provenance)}>{proposal.provenance}</Badge>
            <Badge variant={statusBadgeVariant(proposal.status)}>{proposal.status}</Badge>
          </div>
          <div className="font-medium">
            {proposal.target_table_fqn}
            {proposal.target_columns.length > 0 && (
              <span className="text-muted-foreground"> ({proposal.target_columns.join(", ")})</span>
            )}
          </div>
          {proposal.partner_table_fqn && (
            <div className="text-sm text-muted-foreground">
              ↔ {proposal.partner_table_fqn}
              {proposal.partner_columns.length > 0 && ` (${proposal.partner_columns.join(", ")})`}
            </div>
          )}
        </div>
        <div className="text-right space-y-1">
          <ConfidenceBar value={proposal.confidence} />
          <div className="text-xs text-muted-foreground">
            {proposal.current_violation_count} violation
            {proposal.current_violation_count === 1 ? "" : "s"} now
          </div>
        </div>
      </div>
      <div className="card-body space-y-3">
        {proposal.rationale && (
          <p className="text-sm text-muted-foreground">{proposal.rationale}</p>
        )}
        <div className="flex gap-2 text-xs">
          <button className="text-blue-600 hover:underline" onClick={() => setShowSql((x) => !x)}>
            {showSql ? "Hide SQL" : "Show SQL"}
          </button>
          {proposal.sample_violations.length > 0 && (
            <button className="text-blue-600 hover:underline" onClick={() => setShowSamples((x) => !x)}>
              {showSamples ? "Hide" : "Show"} {proposal.sample_violations.length} sample
              {proposal.sample_violations.length === 1 ? "" : "s"}
            </button>
          )}
          {supportsDiff && (
            <button
              className="text-blue-600 hover:underline disabled:opacity-50"
              onClick={loadDiff}
              disabled={diffLoading}
            >
              {diffLoading ? "Loading…" : showDiff && diff ? "Hide diff" : "Show diff"}
            </button>
          )}
        </div>
        {showSql && (
          <pre className="bg-muted text-xs p-3 rounded overflow-x-auto whitespace-pre-wrap">
            {proposal.generated_sql}
          </pre>
        )}
        {showSamples && (
          <div className="bg-muted text-xs p-3 rounded">
            <div className="font-medium mb-1">Offending IDs:</div>
            <div className="font-mono text-muted-foreground">
              {(proposal.sample_violations as unknown[]).map((v, i) => (
                <span key={i} className="inline-block mr-2">{String(v)}</span>
              ))}
            </div>
          </div>
        )}
        {showDiff && diff && (
          <div className="bg-muted text-xs p-3 rounded space-y-2">
            {diff.supported ? (
              diff.rows.length === 0 ? (
                <div className="text-muted-foreground">No disagreeing rows returned (data may have changed since the run).</div>
              ) : (
                <>
                  <div className="text-muted-foreground">
                    First {diff.rows.length} disagreeing row(s). Left = <code>{diff.target_table}.{diff.target_column}</code>, right = <code>{diff.partner_table}.{diff.partner_column}</code>. Joined on <code>{diff.join_predicate}</code>.
                  </div>
                  <table className="w-full text-xs font-mono">
                    <thead className="border-b border-border text-muted-foreground">
                      <tr>
                        <th className="text-left px-2 py-1">{diff.row_id_column}</th>
                        <th className="text-left px-2 py-1">{diff.target_column} (left)</th>
                        <th className="text-left px-2 py-1">{diff.partner_column} (right)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {diff.rows.map((r, i) => (
                        <tr key={i} className="border-b border-border last:border-0">
                          <td className="px-2 py-1">{String(r.row_id ?? "")}</td>
                          <td className="px-2 py-1">{r.target_value === null ? <span className="italic text-muted-foreground">NULL</span> : r.target_value}</td>
                          <td className="px-2 py-1">{r.partner_value === null ? <span className="italic text-muted-foreground">NULL</span> : r.partner_value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )
            ) : (
              <div className="text-muted-foreground">Diff unavailable: {diff.reason}</div>
            )}
          </div>
        )}
        {isOpen && (
          <div className="space-y-2 pt-2 border-t border-border">
            <input
              type="text"
              placeholder="Optional comment"
              className="w-full border border-border rounded px-2 py-1.5 text-sm"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              disabled={busy}
            />
            <div className="flex items-center gap-2 text-xs">
              <label className="text-muted-foreground">Schedule:</label>
              <select
                className="border border-border rounded px-2 py-1 text-xs"
                value={SCHEDULE_PRESETS.find((p) => p.cron === schedule) ? schedule : ""}
                onChange={(e) => e.target.value && setSchedule(e.target.value)}
                disabled={busy}
              >
                {SCHEDULE_PRESETS.map((p) => (
                  <option key={p.cron} value={p.cron}>{p.label}</option>
                ))}
                <option value="">Custom…</option>
              </select>
              <input
                type="text"
                className="flex-1 border border-border rounded px-2 py-1 text-xs font-mono"
                value={schedule}
                onChange={(e) => setSchedule(e.target.value)}
                disabled={busy}
                placeholder="Quartz cron"
              />
            </div>
            <div className="flex gap-2">
              <button className="btn btn-success" onClick={() => decide("APPROVED")} disabled={busy}>
                Accept → create alert
              </button>
              <button className="btn btn-destructive" onClick={() => decide("DENIED")} disabled={busy}>
                Deny
              </button>
            </div>
          </div>
        )}
        {!isOpen && proposal.reviewer_comment && (
          <p className="text-xs text-muted-foreground">
            Reviewer note: <em>{proposal.reviewer_comment}</em>
          </p>
        )}
      </div>
    </div>
  );
}
