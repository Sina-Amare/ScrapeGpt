import { animate } from "motion";
import { ReactNode, useEffect, useRef } from "react";

export function StatTile({
  label,
  value,
  icon
}: {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
}) {
  const numericValue = typeof value === "string" ? parseInt(value, 10) : NaN;
  const isNumeric = !isNaN(numericValue) && numericValue >= 0;
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!isNumeric || !ref.current) return;
    const node = ref.current;
    const controls = animate(0, numericValue, {
      duration: 0.7,
      ease: "easeOut",
      onUpdate: (v) => {
        if (node.isConnected) node.textContent = String(Math.round(v));
      },
    });
    return () => controls.stop();
  }, [numericValue, isNumeric]);

  return (
    <div className="card-hover sheen relative overflow-hidden rounded-xl border border-line bg-surface p-5 shadow-panel">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted">{label}</p>
        {icon ? (
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent/10 text-accent">
            {icon}
          </div>
        ) : null}
      </div>
      <div className="mt-3 text-3xl font-bold tracking-tight text-ink tabular-nums">
        {isNumeric ? <span ref={ref}>{numericValue}</span> : value}
      </div>
    </div>
  );
}
