import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/hooks/useApi";
import { Badge, statusBadgeVariant } from "@/components/Badge";
import { NewRunDialog } from "@/components/NewRunDialog";
import { formatDate } from "@/lib/utils";
import type { ProposalRun, RunSummary } from "@/types";

export default function RunsList() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showDialog, setShowDialog] = useState(false);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      setRuns(await api<RunSummary[]>("/api/runs"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    refresh();
  }, []);

  function onCreated(_run: ProposalRun) {
    refresh();
  }

  async function cancelRun(id: string) {
    try {
      await api<{ status: string }>(`/api/runs/${id}/cancel`, { method: "POST" });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function clearHistory() {
    if (
      !confirm(
        "Clear ALL run history?\n\nThis deletes every row from proposal_run, proposal, " +
          "acceptance, and dq_finding, and removes the markdown report files from the " +
          "reports volume. DBSQL Alerts created from prior approvals are NOT deleted."
      )
    ) {
      return;
    }
    try {
      const r = await api<{ tables_wiped: Record<string, number>; report_files_deleted: number }>(
        "/api/admin/clear-history",
        {
          method: "POST",
          body: JSON.stringify({ confirm: true }),
        }
      );
      alert(
        `Cleared.\n\n` +
          Object.entries(r.tables_wiped)
            .map(([t, n]) => `  ${t}: ${n}`)
            .join("\n") +
          `\n  report files deleted: ${r.report_files_deleted}`
      );
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-4">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-semibold">Data Quality Proposal Runs</h1>
          <p className="text-sm text-muted-foreground">
            Click <em>New run</em> to scan a UC schema. Each run produces proposed checks for human review.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            className="text-xs px-3 py-1.5 rounded bg-muted hover:bg-muted/70"
            onClick={clearHistory}
            disabled={runs.length === 0}
            title="Wipe all runs, proposals, and report files"
          >
            Clear history
          </button>
          <button className="btn btn-primary" onClick={() => setShowDialog(true)}>
            + New run
          </button>
        </div>
      </div>

      {error && (
        <div className="card card-body text-destructive">{error}</div>
      )}
      {loading && <div className="text-muted-foreground">Loading…</div>}
      {!loading && runs.length === 0 && (
        <div className="card card-body text-muted-foreground">
          No runs yet. Click <em>New run</em> to start one.
        </div>
      )}
      {runs.length > 0 && (
        <div className="card">
          <table className="w-full text-sm">
            <thead className="border-b border-border">
              <tr className="text-left text-muted-foreground">
                <th className="px-4 py-2">Schema</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Proposals</th>
                <th className="px-4 py-2">Accepted</th>
                <th className="px-4 py-2">Started</th>
                <th className="px-4 py-2">Completed</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                  <td className="px-4 py-2">
                    <Link to={`/runs/${r.id}`} className="text-blue-600 hover:underline">
                      {r.target_catalog}.{r.target_schema}
                    </Link>
                  </td>
                  <td className="px-4 py-2">
                    <Badge variant={statusBadgeVariant(r.status)}>{r.status}</Badge>
                  </td>
                  <td className="px-4 py-2 tabular-nums">{r.proposal_count}</td>
                  <td className="px-4 py-2 tabular-nums">{r.accepted_count}</td>
                  <td className="px-4 py-2 text-muted-foreground">{formatDate(r.started_at)}</td>
                  <td className="px-4 py-2 text-muted-foreground">{formatDate(r.completed_at)}</td>
                  <td className="px-4 py-2">
                    {(r.status === "RUNNING" || r.status === "QUEUED") && (
                      <button
                        className="text-xs px-2 py-1 rounded bg-muted hover:bg-muted/70"
                        onClick={() => cancelRun(r.id)}
                      >
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <NewRunDialog open={showDialog} onClose={() => setShowDialog(false)} onCreated={onCreated} />
    </div>
  );
}
