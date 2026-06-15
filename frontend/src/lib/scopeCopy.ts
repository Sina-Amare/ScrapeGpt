import type { CrawlScopeMode } from "../types";

export type ScopeModeInfo = {
  label: string;
  description: string;
  example: string;
  confirmLabel: string;
  warnStrong: boolean;
};

const SCOPE_MODE_INFO: Record<CrawlScopeMode, ScopeModeInfo> = {
  CURRENT_PAGE: {
    label: "This page only",
    description: "Scrapes only the URL you pasted. Best for a single page or for testing your fields before a bigger run.",
    example: "e.g. arxiv.org/abs/2301.00001 — one paper",
    confirmLabel: "Use this page only",
    warnStrong: false,
  },
  PAGINATION: {
    label: "Paginated list",
    description: "Follows only Next / page 2 / page 3 links of this same list. Use when all results live on one list that spans numbered pages — not for separate category pages.",
    example: "e.g. arxiv.org/search/… pages 1–40",
    confirmLabel: "Confirm list pages",
    warnStrong: false,
  },
  COLLECTION: {
    label: "Related list pages",
    description: "Follows sibling / category list pages linked from this one (e.g. each food category), then scrapes each. Use when the data is split across many similar list pages rather than numbered pages.",
    example: "e.g. calories.info/food/meat, /food/fish, /food/fruit …",
    confirmLabel: "Confirm related pages",
    warnStrong: false,
  },
  DATASET: {
    label: "Listing + detail pages",
    description: "Follows links from this page to each item's own page and scrapes both. Use when each result has a detail URL with more data.",
    example: "e.g. arxiv.org search → each /abs/ paper page",
    confirmLabel: "Confirm dataset scope",
    warnStrong: false,
  },
  FULL_SITE: {
    label: "Entire website",
    description: "Crawls every discoverable page on the domain. Very slow — only use when you need everything on the site.",
    example: "e.g. all pages on docs.example.com",
    confirmLabel: "Confirm whole-site crawl",
    warnStrong: true,
  },
};

export function scopeModeInfo(mode: string): ScopeModeInfo {
  return (
    SCOPE_MODE_INFO[mode as CrawlScopeMode] ?? {
      label: mode,
      description: "",
      confirmLabel: "Confirm scope",
      warnStrong: false,
    }
  );
}

export function scopeModeLabel(mode: string): string {
  return scopeModeInfo(mode).label;
}

export function requiresConfirmation(mode: string): boolean {
  return mode !== "CURRENT_PAGE";
}

export function isUserConfirmed(status: string | undefined): boolean {
  return status === "USER_CONFIRMED";
}

export const SCOPE_MODE_ORDER: CrawlScopeMode[] = [
  "CURRENT_PAGE",
  "PAGINATION",
  "COLLECTION",
  "DATASET",
  "FULL_SITE",
];
