import { cn } from "@/lib/utils";

type Variant = "default" | "success" | "warning" | "destructive" | "info";

export function Badge({
  variant = "default",
  className,
  children,
}: {
  variant?: Variant;
  className?: string;
  children: React.ReactNode;
}) {
  const cls = {
    default: "badge badge-default",
    success: "badge badge-success",
    warning: "badge badge-warning",
    destructive: "badge badge-destructive",
    info: "badge badge-info",
  }[variant];
  return <span className={cn(cls, className)}>{children}</span>;
}

export function statusBadgeVariant(status: string): Variant {
  switch (status) {
    case "COMPLETED":
    case "APPROVED":
      return "success";
    case "FAILED":
    case "DENIED":
      return "destructive";
    case "RUNNING":
    case "QUEUED":
    case "IN_REVIEW":
      return "info";
    default:
      return "default";
  }
}

export function provenanceVariant(p: string): Variant {
  if (p === "ontology" || p === "hybrid") return "success";
  if (p === "lineage") return "info";
  if (p === "llm") return "warning";
  return "default";
}
