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
        />
      </StepCard>
    </div>
  );
}
