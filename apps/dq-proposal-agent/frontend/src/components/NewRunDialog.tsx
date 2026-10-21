import { useEffect, useState } from "react";
import { api } from "@/hooks/useApi";
import type { ProposalRun, RunRequest } from "@/types";

export function NewRunDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (run: ProposalRun) => void;
}) {
  const [catalog, setCatalog] = useState("");
  const [schema, setSchema] = useState("");
  const [catalogs, setCatalogs] = useState<string[]>([]);
  const [schemas, setSchemas] = useState<string[]>([]);
  const [tables, setTables] = useState<string[]>([]);
  const [selectedTables, setSelectedTables] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dedupAccepted, setDedupAccepted] = useState(false);
  const [confidenceFloor, setConfidenceFloor] = useState(0.7);

  useEffect(() => {
    if (!open) return;
    api<string[]>("/api/catalogs")
      .then((cs) => {
        setCatalogs(cs);
        if (cs.includes("telco_bss")) setCatalog("telco_bss");
      })
      .catch((e) => setError(e.message));
  }, [open]);

  useEffect(() => {
    if (!catalog) return;
    api<string[]>(`/api/catalogs/${encodeURIComponent(catalog)}/schemas`)
      .then((ss) => {
        setSchemas(ss);
        if (ss.includes("silver")) setSchema("silver");
      })
      .catch((e) => setError(e.message));
  }, [catalog]);

  useEffect(() => {
    if (!catalog || !schema) {
      setTables([]);
      setSelectedTables(new Set());
      return;
    }
    api<string[]>(
      `/api/catalogs/${encodeURIComponent(catalog)}/schemas/${encodeURIComponent(schema)}/tables`,
    )
      .then((ts) => {
        setTables(ts);
        setSelectedTables(new Set(ts)); // default = all selected
      })
      .catch((e) => setError(e.message));
  }, [catalog, schema]);

  function toggleTable(t: string) {
    setSelectedTables((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t); else next.add(t);
      return next;
    });
  }

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const allSelected = selectedTables.size === tables.length;
      const body: RunRequest = {
        catalog,
        schema,
        table_filter: allSelected ? null : Array.from(selectedTables),
        dedup_already_accepted: dedupAccepted,
        confidence_floor: confidenceFloor,
      };
      const run = await api<ProposalRun>("/api/runs", {
        method: "POST",
        body: JSON.stringify(body),
      });
      onCreated(run);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) return null;
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="card w-[480px]">
        <div className="card-header flex justify-between items-center">
          <h2 className="font-semibold">New run</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>
        <div className="card-body space-y-3">
          <div>
            <label className="text-sm font-medium block mb-1">Catalog</label>
            <select
              className="w-full border border-border rounded px-2 py-1.5 bg-background"
              value={catalog}
              onChange={(e) => setCatalog(e.target.value)}
            >
              <option value="">(select)</option>
              {catalogs.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-sm font-medium block mb-1">Schema</label>
            <select
              className="w-full border border-border rounded px-2 py-1.5 bg-background"
              value={schema}
              onChange={(e) => setSchema(e.target.value)}
              disabled={!catalog}
            >
              <option value="">(select)</option>
              {schemas.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
          {schema && tables.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-sm font-medium">
                  Tables <span className="text-muted-foreground font-normal">({selectedTables.size}/{tables.length})</span>
                </label>
                <div className="flex gap-2 text-xs">
                  <button type="button" className="text-blue-600 hover:underline"
                          onClick={() => setSelectedTables(new Set(tables))}>All</button>
                  <button type="button" className="text-blue-600 hover:underline"
                          onClick={() => setSelectedTables(new Set())}>None</button>
                </div>
              </div>
              <div className="max-h-48 overflow-y-auto border border-border rounded p-2 space-y-1 bg-background">
                {tables.map((t) => (
                  <label key={t} className="flex items-center gap-2 text-sm cursor-pointer hover:bg-muted/40 px-1 rounded">
                    <input
                      type="checkbox"
                      checked={selectedTables.has(t)}
                      onChange={() => toggleTable(t)}
                    />
                    <span>{t}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          <div>
            <label className="text-sm font-medium block mb-1">
              Confidence floor <span className="text-muted-foreground font-normal">({confidenceFloor.toFixed(2)})</span>
            </label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={confidenceFloor}
              onChange={(e) => setConfidenceFloor(parseFloat(e.target.value))}
              className="w-full"
            />
            <div className="text-xs text-muted-foreground flex justify-between">
              <span>0.00 — emit everything</span>
              <span>1.00 — only the most confident</span>
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={dedupAccepted}
              onChange={(e) => setDedupAccepted(e.target.checked)}
            />
            <span>Skip checks that are already approved as DBSQL Alerts (production mode)</span>
          </label>
          {error && (
            <div className="text-sm text-destructive">{error}</div>
          )}
          <p className="text-xs text-muted-foreground">
            By default the agent scans every table you select and emits a stable proposal set on each run.
            Larger schemas take longer; you can leave this page while it runs.
          </p>
        </div>
        <div className="card-header flex justify-end gap-2 border-t border-b-0">
          <button className="btn btn-outline" onClick={onClose}>Cancel</button>
          <button
            className="btn btn-primary"
            disabled={!catalog || !schema || submitting || selectedTables.size === 0}
            onClick={submit}
          >
            {submitting ? "Running…" : "Start run"}
          </button>
        </div>
      </div>
    </div>
  );
}
