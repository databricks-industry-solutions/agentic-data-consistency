import type { AgentStep } from "@/types";

export function AgentTrace({ steps }: { steps: AgentStep[] }) {
  if (steps.length === 0) {
    return <p className="text-sm text-muted-foreground">No agent steps recorded.</p>;
  }
  return (
    <div className="space-y-2 text-xs">
      {steps.map((s) => (
        <div key={s.iteration} className="card">
          <div className="card-body">
            <div className="flex justify-between items-baseline">
              <span className="font-mono font-medium">
                #{s.iteration} {s.tool ?? s.kind}
              </span>
              <span className="text-muted-foreground">{new Date(s.timestamp).toLocaleTimeString()}</span>
            </div>
            {s.arguments && Object.keys(s.arguments).length > 0 && (
              <div className="text-muted-foreground mt-1 truncate">
                args: <code>{JSON.stringify(s.arguments).slice(0, 200)}</code>
              </div>
            )}
            {s.result !== undefined && s.result !== null && (
              <div className="text-muted-foreground mt-1 truncate">
                result: <code>{JSON.stringify(s.result).slice(0, 200)}</code>
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
