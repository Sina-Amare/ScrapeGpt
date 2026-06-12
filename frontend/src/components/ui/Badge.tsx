import { ReactNode } from "react";

type BadgeTone = "success" | "warning" | "danger" | "neutral" | "accent";

const toneClasses: Record<BadgeTone, string> = {
  success: "border-green-200 bg-green-50 text-success",
  warning: "border-amber-200 bg-amber-50 text-warning",
  danger: "border-red-200 bg-red-50 text-danger",
  neutral: "border-line bg-porcelain text-muted",
  accent: "border-indigo-200 bg-indigo-50 text-teal"
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
