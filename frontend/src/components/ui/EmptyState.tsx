import { ReactNode } from "react";

/**
 * Centered dashed-border placeholder for "nothing here yet" states. Consolidates
 * the ad-hoc dashed boxes that were hand-written per panel (no rows / generate a
 * preview / no variants).
 */
export function EmptyState({
  icon,
  title,
  hint,
  action,
  className = "",
}: {
  icon?: ReactNode;
  title: string;
  hint?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`flex flex-col items-center gap-2 rounded-lg border border-dashed border-line bg-porcelain p-8 text-center ${className}`}
    >
      {icon ? <div className="text-muted/70">{icon}</div> : null}
      <p className="text-sm font-semibold text-ink">{title}</p>
      {hint ? <p className="max-w-md text-sm text-muted">{hint}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
