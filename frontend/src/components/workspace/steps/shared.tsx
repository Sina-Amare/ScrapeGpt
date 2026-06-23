import { FileText, Info } from "lucide-react";
import { motion } from "motion/react";
import { ChangeEvent, ReactNode, useMemo, useState } from "react";
import { buildColumns } from "../../../lib/recordColumns";
import type { FieldSpec } from "../../../types";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import { Input } from "../../ui/Input";
import { MarkdownPreviewDialog } from "../../ui/MarkdownPreviewDialog";
import { MarkdownView } from "../../ui/MarkdownView";
import { Select } from "../../ui/Select";

/** Section card wrapper used by every wizard step body. */
export function StepCard({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card-hover sheen relative rounded-xl border border-line bg-surface p-6 shadow-panel">
      {(title || action) && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-bold text-ink">{title}</h2>
            {description ? <p className="text-sm text-muted">{description}</p> : null}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function ConfidenceBar({ value }: { value: number | null }) {
  const pct = value == null ? 0 : Math.round(value * 100);
  return (
    <div className="flex items-center gap-3">
      <div className="relative h-2 flex-1 min-w-0 overflow-hidden rounded-full bg-line">
        <motion.div
          key={pct}
          className="absolute inset-y-0 left-0 rounded-full bg-teal"
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1], delay: 0.1 }}
        />
      </div>
      <span className="w-12 shrink-0 text-right text-sm font-bold text-ink">
        {value == null ? "-" : `${pct}%`}
      </span>
    </div>
  );
}

function asString(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

export function RecordsTable({
  rows,
  specFields,
  mode,
}: {
  rows: Record<string, unknown>[];
  specFields?: FieldSpec[] | null;
  mode?: "STRUCTURED" | "CONTENT" | string;
}) {
  const columns = useMemo(() => {
    const pageColumns = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
    return buildColumns(specFields, pageColumns).slice(0, 12);
  }, [rows, specFields]);
  const isContentMode = mode === "CONTENT" || columns.includes("content");
  if (!rows.length) {
    return (
      <p className="rounded-lg border border-line bg-porcelain p-6 text-center text-sm text-muted">
        No rows yet.
      </p>
    );
  }
  if (isContentMode) {
    return (
      <div className="grid gap-3">
        {rows.map((row, index) => (
          <ContentPreviewCard key={index} row={row} />
        ))}
      </div>
    );
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line bg-porcelain text-left text-xs font-bold uppercase tracking-widest text-muted">
            {columns.map((column) => (
              <th key={column} className="px-4 py-2.5">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line bg-surface">
          {rows.map((row, index) => (
            <tr key={index} className="hover:bg-teal-soft/30">
              {columns.map((column) => (
                <td
                  key={column}
                  className="max-w-xs truncate px-4 py-3 text-muted"
                  title={asString(row[column])}
                >
                  {asString(row[column]) || "-"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ContentPreviewCard({ row }: { row: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const content = asString(row.content);
  const sourceUrl = asString(row.source_url);

  return (
    <article className="rounded-lg border border-line bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">Content preview</p>
          {sourceUrl ? (
            <p className="mt-1 truncate text-xs text-muted" title={sourceUrl}>
              {sourceUrl}
            </p>
          ) : null}
        </div>
        <span className="rounded-full bg-porcelain px-2.5 py-1 text-xs font-semibold text-muted">
          {content.length.toLocaleString()} chars
        </span>
      </div>
      {content ? (
        <>
          <div className="relative max-h-72 overflow-hidden rounded-md border border-line p-4">
            <MarkdownView markdown={content} />
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-surface to-transparent" />
          </div>
          <Button variant="secondary" className="mt-3" onClick={() => setOpen(true)}>
            <FileText className="h-4 w-4" />
            Open .md preview
          </Button>
        </>
      ) : (
        <p className="text-sm text-muted">-</p>
      )}
      {open ? (
        <MarkdownPreviewDialog
          markdown={content}
          sourceUrl={sourceUrl || undefined}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </article>
  );
}

export function FieldEditor({
  fields,
  onChange,
  disabled,
}: {
  fields: FieldSpec[];
  onChange: (fields: FieldSpec[]) => void;
  disabled?: boolean;
}) {
  function updateField(index: number, patch: Partial<FieldSpec>) {
    onChange(fields.map((field, i) => (i === index ? { ...field, ...patch } : field)));
  }

  if (!fields.length) {
    return <Alert tone="info">No fields are available yet. Wait for analysis to finish.</Alert>;
  }

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-lg border border-line">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line bg-porcelain text-left text-xs font-bold uppercase tracking-widest text-muted">
              <th className="px-4 py-2.5">
                <span className="flex items-center gap-1">
                  Use
                  <span
                    title="Include this field in your output. Uncheck to skip it entirely."
                    className="cursor-help"
                  >
                    <Info className="h-3.5 w-3.5 text-muted/60" />
                  </span>
                </span>
              </th>
              <th className="px-4 py-2.5">Field name</th>
              <th className="px-4 py-2.5">
                <span className="flex items-center gap-1">
                  Type
                  <span
                    title="Auto-detected by AI — change only if the type is wrong."
                    className="cursor-help"
                  >
                    <Info className="h-3.5 w-3.5 text-muted/60" />
                  </span>
                </span>
              </th>
              <th className="px-4 py-2.5">
                <span className="flex items-center gap-1">
                  Required
                  <span
                    title="Discard any row where this field is empty. Only check for fields that must appear on every row."
                    className="cursor-help"
                  >
                    <Info className="h-3.5 w-3.5 text-muted/60" />
                  </span>
                </span>
              </th>
              <th className="px-4 py-2.5">Confidence</th>
              <th className="px-4 py-2.5">Samples</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line bg-surface">
            {fields.map((field, index) => (
              <tr key={`${field.name ?? ""}-${index}`} className="hover:bg-teal-soft/30">
                <td className="px-4 py-3">
                  <input
                    className="h-4 w-4 accent-teal"
                    type="checkbox"
                    checked={field.selected}
                    disabled={disabled}
                    onChange={(event: ChangeEvent<HTMLInputElement>) =>
                      updateField(index, { selected: event.target.checked })
                    }
                  />
                </td>
                <td className="min-w-56 px-4 py-3">
                  <Input
                    value={field.user_label ?? field.label ?? field.name ?? ""}
                    disabled={disabled}
                    onChange={(event) => updateField(index, { user_label: event.target.value })}
                  />
                </td>
                <td className="min-w-36 px-4 py-3">
                  <Select
                    value={field.type}
                    disabled={disabled}
                    title="Auto-detected by AI — change only if incorrect"
                    onChange={(event) => updateField(index, { type: event.target.value })}
                  >
                    <option value="string">Text</option>
                    <option value="number">Number</option>
                    <option value="url">URL</option>
                    <option value="date">Date</option>
                    <option value="boolean">Boolean</option>
                    <option value="image">Image</option>
                  </Select>
                </td>
                <td className="px-4 py-3">
                  <input
                    className="h-4 w-4 accent-teal"
                    type="checkbox"
                    checked={field.required}
                    disabled={disabled}
                    onChange={(event: ChangeEvent<HTMLInputElement>) =>
                      updateField(index, { required: event.target.checked })
                    }
                  />
                </td>
                <td className="whitespace-nowrap px-4 py-3 font-bold text-muted">
                  {field.confidence == null ? "-" : `${Math.round(field.confidence * 100)}%`}
                </td>
                <td className="max-w-xs truncate px-4 py-3 text-muted">
                  {(field.sample_values ?? []).slice(0, 2).join(", ") || "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">
        <strong>Tip:</strong> Check <strong>Use</strong> for every field you want in your output.
        Only mark <strong>Required</strong> for fields like title or ID that must appear on every
        row — rows missing a required field are dropped from the results.
      </p>
    </div>
  );
}
