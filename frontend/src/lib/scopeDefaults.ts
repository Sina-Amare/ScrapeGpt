import type { CrawlScope, CrawlScopeMode } from "../types";

// Display-only helper: surfaces the AI's suggested include patterns so the user
// can see which globs a related-page scope will apply *before* saving. The
// patterns themselves are seeded authoritatively by the backend
// (`normalize_crawl_scope`) on save — the frontend must not seed them itself, or
// the two implementations drift. See StepScope's "will apply" hint.
export function suggestedIncludePatterns(
  scope: CrawlScope | null | undefined,
  mode: CrawlScopeMode
): string[] {
  if (mode !== "COLLECTION" && mode !== "DATASET") return [];
  return (scope?.ai_recommendation?.suggested_include_patterns ?? [])
    .map((pattern) => pattern.trim())
    .filter(Boolean);
}
