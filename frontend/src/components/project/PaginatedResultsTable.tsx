import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, FileText } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { MarkdownPreviewDialog } from "../ui/MarkdownPreviewDialog";
import { MarkdownView } from "../ui/MarkdownView";
import { Select } from "../ui/Select";
import { Skeleton } from "../ui/Skeleton";
import { api } from "../../lib/api";
import { buildColumns } from "../../lib/recordColumns";
import type { FieldSpec, ProjectRecord } from "../../types";

function asString(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

type Props = {
  projectId: number;
  specFields: FieldSpec[] | null | undefined;
  isCompleted: boolean;
  mode?: "STRUCTURED" | "CONTENT" | string;
};

function ContentCard({
  record,
  projectId,
}: {
  record: ProjectRecord;
  projectId: number;
}) {
  const data = record.normalized_data ?? record.raw_data;
  const content = asString(data.content);
  const sourceUrl = asString(data.source_url);
  const [open, setOpen] = useState(false);

  async function downloadMd() {
    try {
      await api.exportProject(projectId, "md");
      toast.success("Markdown downloaded");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Download failed");
    }
  }

  return (
    <article className="rounded-lg border border-line bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold text-ink">
            <FileText className="h-4 w-4 text-teal" />
            Content page
          </div>
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
          <div className="relative max-h-80 overflow-hidden rounded-md border border-line p-4">
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
          onDownload={downloadMd}
        />
      ) : null}
    </article>
  );
}

export function PaginatedResultsTable({ projectId, specFields, isCompleted, mode }: Props) {
  const [skip, setSkip] = useState(0);
  const [limit, setLimit] = useState(100);

  const recordsQuery = useQuery({
    queryKey: ["project-records-page", projectId, skip, limit],
    queryFn: () => api.getProjectRecordsPage(projectId, { skip, limit }),
    enabled: isCompleted,
    retry: false,
  });

  const data = recordsQuery.data;
  const total = data?.total ?? 0;
  const hasMore = data?.has_more ?? false;
  const items = data?.items ?? [];
  const columns = buildColumns(specFields, data?.columns ?? []);
  const isContentMode = mode === "CONTENT" || columns.includes("content");

  const pageStart = total > 0 ? skip + 1 : 0;
  const pageEnd = Math.min(skip + limit, total);

  if (!isCompleted) {
    return (
      <p className="rounded-lg border border-dashed border-line bg-porcelain p-6 text-center text-sm text-muted">
        Results will appear after extraction.
      </p>
    );
  }

  if (recordsQuery.isLoading) {
    return (
      <div className="grid gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }

  if (recordsQuery.error) {
    return <Alert tone="danger">Could not load records page.</Alert>;
  }

  if (total === 0) {
    return (
      <p className="rounded-lg border border-dashed border-line bg-porcelain p-6 text-center text-sm text-muted">
        Extraction finished, but no records were found.
      </p>
    );
  }

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <span className="text-muted">
          Showing <strong className="text-ink">{pageStart}-{pageEnd}</strong> of{" "}
          <strong className="text-ink">{total}</strong> records
        </span>
        <div className="flex items-center gap-2">
          <span className="text-muted">Page size:</span>
          <Select
            value={String(limit)}
            onChange={(e) => {
              setLimit(Number(e.target.value));
              setSkip(0);
            }}
          >
            <option value="50">50</option>
            <option value="100">100</option>
            <option value="250">250</option>
            <option value="500">500</option>
          </Select>
        </div>
      </div>

      {isContentMode ? (
        <div className="grid gap-3">
          {items.map((record) => (
            <ContentCard key={record.id} record={record} projectId={projectId} />
          ))}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line bg-porcelain text-left text-xs font-bold uppercase tracking-widest text-muted">
                {columns.map((col) => (
                  <th key={col} className="px-4 py-2.5">{col}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line bg-surface">
              {items.map((record) => (
                <tr key={record.id} className="hover:bg-teal-soft/30">
                  {columns.map((col) => {
                    const val = (record.normalized_data ?? record.raw_data)[col];
                    const str = asString(val);
                    return (
                      <td
                        key={col}
                        className="max-w-xs truncate px-4 py-3 text-muted"
                        title={str || undefined}
                      >
                        {str || "-"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex items-center justify-between gap-3">
        <Button
          variant="secondary"
          disabled={skip === 0}
          onClick={() => setSkip(Math.max(0, skip - limit))}
        >
          <ChevronLeft className="h-4 w-4" />
          Previous
        </Button>
        <span className="text-sm text-muted">
          {Math.floor(skip / limit) + 1} / {Math.ceil(total / limit)}
        </span>
        <Button
          variant="secondary"
          disabled={!hasMore}
          onClick={() => setSkip(skip + limit)}
        >
          Next
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
