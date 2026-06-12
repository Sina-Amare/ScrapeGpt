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
    <div className="rounded-md border border-line bg-surface p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-muted">{label}</p>
        {icon ? <div className="text-teal">{icon}</div> : null}
      </div>
      <div className="mt-2 text-xl font-bold text-ink">
        {isNumeric ? <span ref={ref}>{numericValue}</span> : value}
      </div>
    </div>
  );
}
