import { Eye, SkipForward } from "lucide-react";
import type { FrontierPreviewResponse } from "../../types";
import { reasonCodeCopy } from "../../lib/frontierReasonCopy";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";

type Props = {
  preview: FrontierPreviewResponse | null | undefined;
  loading?: boolean;
  error?: string | null;
  stale?: boolean;
  disabled?: boolean;
  onGenerate: () => void;
};

function UrlRow({ url, reasonCode, reason, linkText, depth }: {
  url: string;
  reasonCode: string;
  reason: string;
  linkText: string | null;
  depth: number;
}) {
  const userCopy = reasonCodeCopy(reasonCode);
  return (
    <tr className="hover:bg-teal-soft/20">
      <td className="max-w-xs truncate px-4 py-2.5 text-sm text-ink" title={url}>
        {url}
      </td>
      <td className="px-4 py-2.5 text-sm text-muted">{userCopy}</td>
      {linkText ? (
        <td className="px-4 py-2.5 text-xs text-muted italic">{linkText}</td>
      ) : (
        <td className="px-4 py-2.5 text-xs text-muted">-</td>
      )}
      <td className="px-4 py-2.5 text-xs text-muted">{depth}</td>
      <td className="hidden px-4 py-2.5 text-xs text-muted/60 sm:table-cell"
          title={reason}>{reasonCode}</td>
    </tr>
  );
}

export function FrontierPreviewPanel({ preview, loading, error, stale, disabled, onGenerate }: Props) {
  const included = preview?.included_urls ?? [];
  const excluded = preview?.excluded_urls ?? [];
  const warnings: Record<string, unknown>[] = preview?.warnings ?? [];
  const qualitySummary = preview?.quality_summary ?? {};
  const includedCount = (qualitySummary.included_count as number | undefined) ?? included.length;
  const excludedCount = (qualitySummary.excluded_count as number | undefined) ?? excluded.length;
  const estimatedPages = preview?.estimated_page_count ?? null;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">
          See what pages ScrapeGPT will visit before extraction begins.
        </p>
        <Button onClick={onGenerate} disabled={disabled || loading} variant="secondary">
          <Eye className="h-4 w-4" />
          {loading
            ? "Generating preview..."
            : preview
              ? "Regenerate page preview"
              : "Generate page preview"}
        </Button>
      </div>

      {stale ? (
        <Alert tone="info">
          Scope changed. Regenerate page preview before extracting.
        </Alert>
      ) : null}

      {error ? <Alert tone="danger">{error}</Alert> : null}

      {!preview && !loading ? (
        <div className="rounded-lg border border-dashed border-line bg-porcelain p-8 text-center text-sm text-muted">
          Generate a page preview to see what will be crawled.
        </div>
      ) : null}

      {preview ? (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-lg border border-line bg-porcelain p-3 text-center">
              <p className="text-xs font-bold uppercase tracking-wide text-muted">Will visit</p>
              <p className="mt-1 text-xl font-bold text-ink">{includedCount}</p>
            </div>
            <div className="rounded-lg border border-line bg-porcelain p-3 text-center">
              <p className="text-xs font-bold uppercase tracking-wide text-muted">Skipped</p>
              <p className="mt-1 text-xl font-bold text-ink">{excludedCount}</p>
            </div>
            <div className="rounded-lg border border-line bg-porcelain p-3 text-center">
              <p className="text-xs font-bold uppercase tracking-wide text-muted">Safety limit</p>
              <p className="mt-1 text-xl font-bold text-ink">
                {estimatedPages != null ? estimatedPages : "-"}
              </p>
              <p className="text-[10px] text-muted/60">max pages setting</p>
            </div>
            <div className="rounded-lg border border-line bg-porcelain p-3 text-center">
              <p className="text-xs font-bold uppercase tracking-wide text-muted">Warnings</p>
              <p className="mt-1 text-xl font-bold text-ink">{warnings.length}</p>
            </div>
          </div>

          {warnings.length ? (
            <div className="grid gap-2">
              {warnings.map((w, i) => (
                <Alert key={i} tone="info">
                  {String((w as { message?: unknown }).message ?? JSON.stringify(w))}
                </Alert>
              ))}
            </div>
          ) : null}

          <div className="grid gap-4">
            {included.length ? (
              <div>
                <h4 className="mb-2 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-muted">
                  <Eye className="h-3.5 w-3.5" />
                  Will visit ({includedCount})
                  {includedCount > included.length ? (
                    <span className="font-normal normal-case tracking-normal">
                      - showing sample
                    </span>
                  ) : null}
                </h4>
                <div className="overflow-x-auto rounded-lg border border-line">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-line bg-porcelain text-left text-xs font-bold uppercase tracking-wide text-muted">
                        <th className="px-4 py-2">URL</th>
                        <th className="px-4 py-2">Reason</th>
                        <th className="px-4 py-2">Link text</th>
                        <th className="px-4 py-2">Depth</th>
                        <th className="hidden px-4 py-2 sm:table-cell">Code</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line bg-surface">
                      {included.map((d, i) => (
                        <UrlRow
                          key={i}
                          url={d.url}
                          reasonCode={d.reason_code}
                          reason={d.reason}
                          linkText={d.link_text}
                          depth={d.depth}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}

            {excluded.length ? (
              <div>
                <h4 className="mb-2 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-muted">
                  <SkipForward className="h-3.5 w-3.5" />
                  Skipped ({excludedCount})
                  {excludedCount > excluded.length ? (
                    <span className="font-normal normal-case tracking-normal">
                      - showing sample
                    </span>
                  ) : null}
                </h4>
                <div className="overflow-x-auto rounded-lg border border-line">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-line bg-porcelain text-left text-xs font-bold uppercase tracking-wide text-muted">
                        <th className="px-4 py-2">URL</th>
                        <th className="px-4 py-2">Reason</th>
                        <th className="px-4 py-2">Link text</th>
                        <th className="px-4 py-2">Depth</th>
                        <th className="hidden px-4 py-2 sm:table-cell">Code</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line bg-surface">
                      {excluded.map((d, i) => (
                        <UrlRow
                          key={i}
                          url={d.url}
                          reasonCode={d.reason_code}
                          reason={d.reason}
                          linkText={d.link_text}
                          depth={d.depth}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
