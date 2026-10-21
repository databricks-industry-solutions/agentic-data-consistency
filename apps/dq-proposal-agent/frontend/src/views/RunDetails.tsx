import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "@/hooks/useApi";
import { Badge, statusBadgeVariant } from "@/components/Badge";
import { AgentTrace } from "@/components/AgentTrace";
import { ProposalCard } from "@/components/ProposalCard";
import { formatDate } from "@/lib/utils";
import type { Proposal, ProposalRun, ReportResponse } from "@/types";

export default function RunDetails() {
  const { id = "" } = useParams();
  const [run, setRun] = useState<ProposalRun | null>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("all");
  const [sortBy, setSortBy] = useState<"violations" | "confidence" | "invariant">("violations");
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportLoading, setReportLoading] = useState(false);

  async function loadReport(regenerate = false) {
    setReportLoading(true);
    try {
      const r = await api<ReportResponse>(`/api/runs/${id}/report`, {
        method: regenerate ? "POST" : "GET",
      });
      setReport(r);
      setReportOpen(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setReportLoading(false);
    }
  }

  async function load() {
    try {
      const [r, ps] = await Promise.all([
        api<ProposalRun>(`/api/runs/${id}`),
        api<Proposal[]>(`/api/runs/${id}/proposals`),
      ]);
      setRun(r);
      setProposals(ps);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    load();
  }, [id]);

  function onProposalChange(p: Proposal) {
    setProposals((arr) => arr.map((x) => (x.id === p.id ? p : x)));
  }

  if (error) return <div className="p-6 card card-body text-destructive">{error}</div>;
  if (!run) return <div className="p-6 text-muted-foreground">Loading…</div>;

  const filtered =
    filter === "all"
      ? proposals
      : proposals.filter((p) => p.invariant_type === filter || p.provenance === filter);

  const sorted = [...filtered].sort((a, b) => {
    if (sortBy === "violations") return b.current_violation_count - a.current_violation_count;
    if (sortBy === "confidence") return b.confidence - a.confidence;
    return a.invariant_type.localeCompare(b.invariant_type);
  });

  const groups: Record<string, Proposal[]> = {};
  for (const p of sorted) {
    const key = sortBy === "invariant" ? p.invariant_type : "Proposals";
    (groups[key] ||= []).push(p);
  }

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-4">
      <div>
        <Link to="/" className="text-sm text-blue-600 hover:underline">
          ← All runs
        </Link>
        <div className="flex items-start justify-between gap-4">
          <h1 className="text-2xl font-semibold">
            {run.target_catalog}.{run.target_schema}
          </h1>
          <div className="flex gap-2">
            {(run.status === "RUNNING" || run.status === "QUEUED") && (
              <button
                className="text-xs px-3 py-1.5 rounded bg-destructive text-destructive-foreground hover:opacity-90"
                onClick={async () => {
                  await api(`/api/runs/${id}/cancel`, { method: "POST" });
                  load();
                }}
              >
                Cancel run
              </button>
            )}
            {run.status === "COMPLETED" && (
              <>
                <button
                  className="text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
                  onClick={() => loadReport(false)}
                  disabled={reportLoading}
                >
                  {reportLoading ? "Loading…" : "View report"}
                </button>
                <a
                  className="text-xs px-3 py-1.5 rounded bg-muted hover:bg-muted/70"
                  href={`/api/runs/${id}/report.md`}
                  download
                >
                  Download .md
                </a>
              </>
            )}
          </div>
        </div>
        <div className="flex gap-2 items-center text-sm text-muted-foreground flex-wrap">
          <Badge variant={statusBadgeVariant(run.status)}>{run.status}</Badge>
          <span>· started {formatDate(run.started_at)}</span>
          {run.completed_at && <span>· completed {formatDate(run.completed_at)}</span>}
          {run.genie_space_used && <span>· Genie ✓</span>}
          {run.report_path && <span>· report ✓</span>}
        </div>
      </div>

      {proposals.length > 0 && (() => {
        const withViol = proposals.filter((p) => p.current_violation_count > 0).length;
        const totalViol = proposals.reduce((s, p) => s + p.current_violation_count, 0);
        const avgConf = proposals.reduce((s, p) => s + p.confidence, 0) / proposals.length;
        return (
          <div className="card card-body grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <div>
              <div className="text-xs text-muted-foreground">Total proposals</div>
              <div className="text-2xl font-semibold tabular-nums">{proposals.length}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">With live violations</div>
              <div className="text-2xl font-semibold tabular-nums text-orange-600">
                {withViol}
                <span className="text-sm text-muted-foreground font-normal"> / {proposals.length}</span>
              </div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Total violations</div>
              <div className="text-2xl font-semibold tabular-nums">{totalViol.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Avg confidence</div>
              <div className="text-2xl font-semibold tabular-nums">{avgConf.toFixed(2)}</div>
            </div>
          </div>
        );
      })()}

      {reportOpen && report && (
        <div className="card card-body space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold">Recommendation report</h2>
              <div className="text-xs text-muted-foreground">{report.path}</div>
            </div>
            <div className="flex gap-2">
              <button
                className="text-xs px-3 py-1.5 rounded bg-muted hover:bg-muted/70"
                onClick={() => loadReport(true)}
                disabled={reportLoading}
              >
                Regenerate
              </button>
              <button
                className="text-xs px-3 py-1.5 rounded bg-muted hover:bg-muted/70"
                onClick={() => window.print()}
              >
                Print / PDF
              </button>
              <button
                className="text-xs px-3 py-1.5 rounded bg-muted hover:bg-muted/70"
                onClick={() => setReportOpen(false)}
              >
                Close
              </button>
            </div>
          </div>
          <pre className="text-xs whitespace-pre-wrap bg-muted/40 p-4 rounded max-h-[60vh] overflow-auto font-mono">
            {report.markdown}
          </pre>
        </div>
      )}

      <div className="flex gap-2 items-center text-sm flex-wrap">
        <span className="text-muted-foreground">Sort:</span>
        <select
          className="text-xs border border-border rounded px-2 py-1 bg-background"
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
        >
          <option value="violations">violations (desc)</option>
          <option value="confidence">confidence (desc)</option>
          <option value="invariant">invariant type</option>
        </select>
        <span className="text-muted-foreground ml-3">Filter:</span>
        {[
          "all",
          "PK_UNIQUE",
          "FK_INTEGRITY",
          "DOMAIN_ENUM",
          "NUMERIC_RANGE",
          "CROSS_SYSTEM_FK",
          "CROSS_TABLE_CONSISTENCY",
        ].map((f) => (
          <button
            key={f}
            className={`text-xs px-2 py-1 rounded ${
              filter === f ? "bg-primary text-primary-foreground" : "bg-muted hover:bg-muted/70"
            }`}
            onClick={() => setFilter(f)}
          >
            {f}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-3">
          {Object.keys(groups).length === 0 && (
            <div className="card card-body text-muted-foreground">No proposals match the filter.</div>
          )}
          {Object.entries(groups).map(([k, ps]) => (
            <div key={k} className="space-y-2">
              <h2 className="text-sm font-medium text-muted-foreground">
                {k} <span className="text-muted-foreground">({ps.length})</span>
              </h2>
              {ps.map((p) => (
                <ProposalCard key={p.id} proposal={p} onChange={onProposalChange} />
              ))}
            </div>
          ))}
        </div>
        <div className="space-y-2">
          <h2 className="text-sm font-medium text-muted-foreground">Agent trace</h2>
          <AgentTrace steps={run.agent_steps} />
        </div>
      </div>
    </div>
  );
}
