import { ReactNode } from "react";

type BadgeTone = "success" | "warning" | "danger" | "neutral" | "accent";

const toneClasses: Record<BadgeTone, string> = {
  success: "border-green-200 bg-green-50 text-success dark:border-green-800/40 dark:bg-green-900/20 dark:text-green-400",
  warning: "border-amber-200 bg-amber-50 text-warning dark:border-amber-800/40 dark:bg-amber-900/20 dark:text-amber-400",
  danger:  "border-red-200 bg-red-50 text-danger dark:border-red-800/40 dark:bg-red-900/20 dark:text-red-400",
  neutral: "border-line bg-porcelain text-muted",
  accent:  "border-teal/25 bg-teal/[0.08] text-teal",
};

export function Badge({
  children,
  tone = "neutral"
}: {
  children: ReactNode;
  tone?: BadgeTone;
}) {
  return (
    <span
      className={`inline-flex min-h-6 items-center rounded-md border px-2 py-0.5 text-xs font-semibold ${toneClasses[tone]}`}
    >
      {children}
    </span>
  );
}
