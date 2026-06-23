import { ReactNode } from "react";

export function Table({
  headings,
  children
}: {
  headings: string[];
  children: ReactNode;
}) {
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface shadow-panel">
      <table className="w-full min-w-[720px] border-collapse text-left text-sm">
        <thead className="bg-porcelain/60 text-xs uppercase tracking-wider text-muted">
          <tr>
            {headings.map((heading) => (
              <th key={heading} className="px-4 py-3 font-semibold">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">{children}</tbody>
      </table>
    </div>
  );
}
