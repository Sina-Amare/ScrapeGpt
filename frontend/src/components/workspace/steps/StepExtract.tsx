import { Download } from "lucide-react";
import { toast } from "sonner";
import { api } from "../../../lib/api";
import type { ProjectResponse } from "../../../types";
import { PaginatedResultsTable } from "../../project/PaginatedResultsTable";
import { TrustSummaryPanel } from "../../project/TrustSummaryPanel";
import { Button } from "../../ui/Button";
import { Select } from "../../ui/Select";
import type { WorkspaceController } from "../useWorkspaceMutations";
import { StepCard } from "./shared";

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

function ProgressBar({
  value,
  indeterminate,
}: {
  value: number;
  indeterminate?: boolean;
}) {
  return (
    <div className="relative h-2.5 overflow-hidden rounded-full bg-line" aria-hidden="true">
      {indeterminate ? (
        <div
          data-no-transition
          className="absolute inset-y-0 left-0 w-1/3 rounded-full bg-teal will-change-transform"
          style={{ animation: "extract-progress-indeterminate 1.2s ease-in-out infinite" }}
        />
      ) : (
        <div
          className="h-full rounded-full bg-teal transition-[width] duration-500 ease-out"
          style={{ width: `${value}%` }}
        />
      )}
    </div>
  );
}

export function StepExtract({
  project,
  ws,
}: {
  project: ProjectResponse;
  ws: WorkspaceController;
}) {
  const {
    projectId,
    isExtracting,
    isCompleted,
    hasRecords,
    exportFormat,
    setExportFormat,
    isExporting,
    setIsExporting,
  } = ws;

  const progress = project.progress;
  const finishedPages =
    progress.crawl_pages_extracted + progress.crawl_pages_blocked + progress.crawl_pages_failed;
  const totalPages = progress.crawl_pages_total;
  const progressPercent = totalPages > 0 ? clampPercent((finishedPages / totalPages) * 100) : 0;
  const visibleProgress = isCompleted ? 100 : progressPercent;
  const progressLabel = isCompleted
    ? "Extraction finished"
    : totalPages > 0
    ? `${finishedPages} of ${totalPages} pages finished`
    : "Preparing extraction";
  const showIndeterminate = isExtracting && (totalPages === 0 || finishedPages === 0);

  return (
    <div className="grid gap-6">
      <StepCard
        title={isExtracting ? "Extracting…" : isCompleted ? "Extraction complete" : "Extraction"}
        description={
          isExtracting
            ? "ScrapeGPT is crawling and extracting. This page updates live; you can leave and come back."
            : "Progress and counts for this extraction run."
        }
      >
        <div className="mb-5 rounded-lg border border-line bg-porcelain p-4">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              {isExtracting ? (
                <span className="relative flex h-2.5 w-2.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-teal opacity-60" />
                  <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-teal" />
                </span>
              ) : null}
              <p className="text-sm font-semibold text-ink">{progressLabel}</p>
            </div>
            <span className="text-sm font-bold text-muted">{Math.round(visibleProgress)}%</span>
          </div>
          <ProgressBar value={visibleProgress} indeterminate={showIndeterminate} />
          {isExtracting ? (
            <p className="mt-2 text-xs text-muted">
              Updating every few seconds while ScrapeGPT fetches pages and writes records.
            </p>
          ) : null}
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <div className="rounded-lg border border-line bg-porcelain p-4">
            <p className="text-xs font-bold uppercase tracking-widest text-muted">Pages</p>
            <p className="mt-1 text-xl font-bold text-ink">{progress.crawl_pages_total}</p>
          </div>
          <div className="rounded-lg border border-line bg-porcelain p-4">
            <p className="text-xs font-bold uppercase tracking-widest text-muted">Records</p>
            <p className="mt-1 text-xl font-bold text-ink">{progress.extracted_records_total}</p>
          </div>
          <div className="rounded-lg border border-line bg-porcelain p-4">
            <p className="text-xs font-bold uppercase tracking-widest text-muted">Exports</p>
            <p className="mt-1 text-xl font-bold text-ink">{progress.exports_total}</p>
          </div>
        </div>
        <div className="mt-4 grid gap-3 text-sm sm:grid-cols-5">
          <span className="rounded-md border border-line bg-surface px-3 py-2 text-muted">
            Pending <strong className="text-ink">{progress.crawl_pages_pending}</strong>
          </span>
          <span className="rounded-md border border-line bg-surface px-3 py-2 text-muted">
            Fetching <strong className="text-ink">{progress.crawl_pages_fetching}</strong>
          </span>
          <span className="rounded-md border border-line bg-surface px-3 py-2 text-muted">
            Extracted <strong className="text-ink">{progress.crawl_pages_extracted}</strong>
          </span>
          <span className="rounded-md border border-line bg-surface px-3 py-2 text-muted">
            Blocked <strong className="text-ink">{progress.crawl_pages_blocked}</strong>
          </span>
          <span className="rounded-md border border-line bg-surface px-3 py-2 text-muted">
            Failed <strong className="text-ink">{progress.crawl_pages_failed}</strong>
          </span>
        </div>
      </StepCard>

      <StepCard title="Extraction quality" description="Trust signals for the extracted data.">
        <TrustSummaryPanel quality={project.extraction_quality} />
      </StepCard>

      <StepCard
        title="Results"
        description="Extracted records from this project."
        action={
          hasRecords ? (
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm text-muted">
                Export:
                <Select value={exportFormat} onChange={(e) => setExportFormat(e.target.value)}>
                  <option value="csv">CSV</option>
                  <option value="json">JSON</option>
                  <option value="xlsx">XLSX</option>
                </Select>
              </label>
              <Button
                variant="secondary"
                loading={isExporting}
                onClick={async () => {
                  setIsExporting(true);
                  try {
                    await api.exportProject(project.id, exportFormat as "csv" | "json" | "xlsx");
                    toast.success("Export downloaded");
                  } catch (err) {
                    toast.error(err instanceof Error ? err.message : "Export failed");
                  } finally {
                    setIsExporting(false);
                  }
                }}
              >
                <Download className="h-4 w-4" />
                Download
              </Button>
            </div>
          ) : null
        }
      >
        <PaginatedResultsTable
          projectId={projectId}
          specFields={project.spec?.fields}
          isCompleted={isCompleted}
          mode={project.extraction_mode}
        />
      </StepCard>
    </div>
  );
}
