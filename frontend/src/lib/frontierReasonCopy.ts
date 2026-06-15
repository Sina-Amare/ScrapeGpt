const REASON_CODE_COPY: Record<string, string> = {
  SEED_URL: "Starting page",
  CURRENT_PAGE_SCOPE: "Skipped because scope is set to this page only",
  PAGINATION_URL_PATTERN: "Looks like another page in this list",
  PAGINATION_QUERY_PARAM: "Looks like another page in this list",
  PAGINATION_SELECTOR_MATCH: "Matches the detected page navigation",
  PAGINATION_PATTERN_MATCH: "Looks like another page in this list",
  DATASET_PATTERN_MATCH: "Matches this dataset's URL pattern",
  COLLECTION_PATTERN_MATCH: "Related list page in this collection",
  DETAIL_LINK_SELECTOR_MATCH: "Looks like a detail page for this dataset",
  FULL_SITE_SAME_ORIGIN: "Same website and whole-site mode is selected",
  EXCLUDED_DIFFERENT_ORIGIN: "Different website",
  EXCLUDED_SCOPE_MODE: "Outside the selected scope",
  EXCLUDED_PATTERN: "Excluded by the selected scope",
  EXCLUDED_NAVIGATION: "Navigation or non-data page",
  EXCLUDED_PAGE_LIMIT: "Outside the safety limit",
  EXCLUDED_DEPTH_LIMIT: "Too many clicks away for this scope",
  EXCLUDED_INVALID_URL: "Invalid or unsafe URL",
};

const FALLBACK_COPY = "Classified by crawl rules";

export function reasonCodeCopy(code: string): string {
  return REASON_CODE_COPY[code] ?? FALLBACK_COPY;
}
