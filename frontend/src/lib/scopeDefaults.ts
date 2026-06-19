import type { CrawlScope, CrawlScopeMode } from "../types";

export function suggestedIncludePatterns(
  scope: CrawlScope | null | undefined,
  mode: CrawlScopeMode
): string[] {
  if (mode !== "COLLECTION" && mode !== "DATASET") return [];
  return (scope?.ai_recommendation?.suggested_include_patterns ?? [])
    .map((pattern) => pattern.trim())
    .filter(Boolean);
}

export function scopeWithSmartDefaults(
  scope: CrawlScope | null | undefined,
  mode: CrawlScopeMode
): Partial<CrawlScope> {
  const includePatterns = scope?.include_patterns ?? [];
  const suggested = suggestedIncludePatterns(scope, mode);
  const next: Partial<CrawlScope> = {
    ...(scope ?? {}),
    mode,
  };

  if (
    (mode === "COLLECTION" || mode === "DATASET") &&
    includePatterns.length === 0 &&
    suggested.length > 0
  ) {
    next.include_patterns = suggested;
  }

  if (mode === "COLLECTION") {
    const currentDepth = typeof scope?.max_depth === "number" ? scope.max_depth : null;
    next.max_depth = currentDepth && currentDepth > 0 ? currentDepth : 1;
  }

  return next;
}
