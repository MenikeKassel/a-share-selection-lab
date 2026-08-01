import type { LifecycleStatus, RunStatus } from "../types";

interface Props {
  status:
    | RunStatus
    | LifecycleStatus
    | "available"
    | "not-installed"
    | "formal"
    | "research";
  children?: React.ReactNode;
}

export function StatusBadge({ status, children }: Props) {
  return (
    <span className={`status-badge status-${status}`}>
      <span className="status-dot" aria-hidden="true" />
      {children ?? status}
    </span>
  );
}
