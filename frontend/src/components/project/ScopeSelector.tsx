import { useEffect, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, Info } from "lucide-react";
import type { CrawlScope, CrawlScopeMode } from "../../types";
import {
  SCOPE_MODE_ORDER,
  isUserConfirmed,
  requiresConfirmation,
  scopeModeInfo,
} from "../../lib/scopeCopy";
import { Button } from "../ui/Button";

type Props = {
  crawlScope: CrawlScope | null | undefined;
  disabled?: boolean;
  onModeChange: (mode: CrawlScopeMode) => void;
  onConfirm: () => void;
  onPatternsChange?: (include: string[], exclude: string[]) => void;
  patternsSaving?: boolean;
};

function parsePatterns(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export function ScopeSelector({
  crawlScope,
  disabled,
  onModeChange,
  onConfirm,
  onPatternsChange,
  patternsSaving,
}: Props) {
  const currentMode: CrawlScopeMode = (crawlScope?.mode as CrawlScopeMode) ?? "CURRENT_PAGE";
  const currentStatus = crawlScope?.status;
  const confirmed = isUserConfirmed(currentStatus);
  const needsConfirm = requiresConfirmation(currentMode);
  const aiMode = crawlScope?.ai_recommendation?.recommended_mode;

  const savedInclude = (crawlScope?.include_patterns ?? []).join("\n");
  const savedExclude = (crawlScope?.exclude_patterns ?? []).join("\n");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [includeText, setIncludeText] = useState(savedInclude);
  const [excludeText, setExcludeText] = useState(savedExclude);

  // Re-sync the editor when the saved scope changes (e.g. after one-click
  // broaden writes derived include patterns).
  useEffect(() => {
    setIncludeText(savedInclude);
    setExcludeText(savedExclude);
  }, [savedInclude, savedExclude]);

  const patternsDirty = includeText !== savedInclude || excludeText !== savedExclude;

  return (
    <div className="grid gap-3">
      {SCOPE_MODE_ORDER.map((mode) => {
        const info = scopeModeInfo(mode);
        const isSelected = mode === currentMode;
        const isAiSuggested = mode === aiMode;

        return (
          <button
            key={mode}
            type="button"
            disabled={disabled}
            onClick={() => onModeChange(mode)}
            className={[
              "flex items-start gap-3 rounded-lg border px-4 py-3 text-left transition",
              isSelected
                ? "border-teal bg-teal-soft/40 text-ink"
                : "border-line bg-surface text-muted hover:border-teal/40 hover:bg-porcelain",
              info.warnStrong && isSelected ? "border-warning bg-warning/10" : "",
              disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            <span
              className={[
                "mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full border",
                isSelected ? "border-teal bg-teal text-white" : "border-line bg-surface",
              ].join(" ")}
            >
              {isSelected ? <span className="h-2 w-2 rounded-full bg-white" /> : null}
            </span>
            <span className="flex-1">
              <span className="flex items-center gap-2">
                <span className="text-sm font-semibold">{info.label}</span>
                {isAiSuggested ? (
                  <span className="rounded-full bg-teal-soft px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-teal">
                    Suggested
                  </span>
                ) : null}
                {info.warnStrong ? (
                  <span className="rounded-full bg-warning/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-warning-dark">
                    Broad
                  </span>
                ) : null}
              </span>
              <span className="mt-0.5 block text-xs text-muted">{info.description}</span>
              <span className="mt-0.5 block text-xs italic text-muted/70">{info.example}</span>
            </span>
          </button>
        );
      })}

      {needsConfirm ? (
        <div
          className={[
            "rounded-lg border p-4",
            currentMode === "FULL_SITE"
              ? "border-warning/60 bg-warning/10"
              : "border-line bg-porcelain",
          ].join(" ")}
        >
          {confirmed ? (
            <div className="flex items-center gap-2 text-sm font-semibold text-success">
              <CheckCircle2 className="h-4 w-4" />
              Scope confirmed: {scopeModeInfo(currentMode).label}
            </div>
          ) : (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-2 text-sm text-muted">
                <Info className="mt-0.5 h-4 w-4 flex-shrink-0 text-warning" />
                <span>
                  {currentMode === "FULL_SITE"
                    ? "Whole-site crawl requires explicit confirmation. This will explore the entire website."
                    : "Confirm the crawl scope before extraction can begin."}
                </span>
              </div>
              <Button
                variant={currentMode === "FULL_SITE" ? "danger" : "primary"}
                disabled={disabled}
                onClick={onConfirm}
              >
                {scopeModeInfo(currentMode).confirmLabel}
              </Button>
            </div>
          )}
        </div>
      ) : null}

      {onPatternsChange && currentMode !== "CURRENT_PAGE" ? (
        <div className="rounded-lg border border-line bg-porcelain">
          <button
            type="button"
            onClick={() => setAdvancedOpen((v) => !v)}
            className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-semibold text-ink"
          >
            {advancedOpen ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
            Advanced — include / exclude patterns
          </button>
          {advancedOpen ? (
            <div className="grid gap-4 border-t border-line p-4">
              <p className="text-xs text-muted">
                One glob per line, matched against the URL path (e.g.{" "}
                <code className="rounded bg-surface px-1">/food/*</code>). Include
                patterns pick which related pages to crawl; exclude patterns drop
                noise. Saving re-asks you to confirm the scope.
              </p>
              <label className="grid gap-1 text-xs font-semibold text-muted">
                Include patterns
                <textarea
                  value={includeText}
                  disabled={disabled || patternsSaving}
                  onChange={(e) => setIncludeText(e.target.value)}
                  rows={3}
                  spellCheck={false}
                  placeholder="/food/*"
                  className="rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink"
                />
              </label>
              <label className="grid gap-1 text-xs font-semibold text-muted">
                Exclude patterns
                <textarea
                  value={excludeText}
                  disabled={disabled || patternsSaving}
                  onChange={(e) => setExcludeText(e.target.value)}
                  rows={2}
                  spellCheck={false}
                  placeholder="/food/tag/*"
                  className="rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink"
                />
              </label>
              <div className="flex items-center justify-end">
                <Button
                  variant="secondary"
                  disabled={disabled || patternsSaving || !patternsDirty}
                  onClick={() =>
                    onPatternsChange(
                      parsePatterns(includeText),
                      parsePatterns(excludeText)
                    )
                  }
                >
                  {patternsSaving ? "Saving..." : "Save patterns"}
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
