import { AlertCircle, AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { ReactNode } from "react";

type AlertTone = "info" | "success" | "danger" | "warning";

const toneClasses: Record<AlertTone, string> = {
  info:    "border-line bg-porcelain text-ink dark:border-line dark:bg-porcelain dark:text-ink",
  success: "border-green-300/50 bg-green-500/[0.08] text-success dark:border-green-500/30 dark:text-green-400",
  danger:  "border-red-300/50 bg-red-500/[0.08] text-danger dark:border-red-500/30 dark:text-red-400",
  warning: "border-amber-300/50 bg-amber-500/[0.08] text-warning dark:border-amber-500/30 dark:text-amber-400",
};

const icons = {
  info: Info,
  success: CheckCircle2,
  danger: AlertCircle,
  warning: AlertTriangle
};

export function Alert({
  tone = "info",
  children
}: {
  tone?: AlertTone;
  children: ReactNode;
}) {
  const Icon = icons[tone];
  return (
    <div className={`flex gap-3 rounded-md border p-3 text-sm ${toneClasses[tone]}`}>
      <Icon className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" />
      <div>{children}</div>
    </div>
  );
}
