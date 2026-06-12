// Central mapping of backend error_codes to user-facing guidance, so failure
// states tell the user what happened and how to fix it instead of showing a
// raw message. Keep messages short and actionable.

export type ErrorHelp = {
  title: string;
  guidance: string;
};

const HELP: Record<string, ErrorHelp> = {
  ANALYSIS_FAILED: {
    title: "AI analysis failed",
    guidance:
      "The AI provider couldn't analyze this page. Try a different provider or model below, or check that the provider's API key is valid."
  },
  NO_PROVIDER_CONFIGURED: {
    title: "No AI provider configured",
    guidance: "Add an AI provider in Settings → Providers, then retry."
  },
  BOT_PROTECTION_BLOCKED: {
    title: "Blocked by bot protection",
    guidance:
      "ScrapeGPT couldn't get past this site's bot protection automatically. Add a browser session for the domain below, then retry."
  },
  ALL_PAGES_FAILED: {
    title: "All pages failed",
    guidance:
      "Every page failed to fetch or was blocked. Try Browser rendering, narrow the crawl scope, or double-check the URL."
  },
  NO_RECORDS_EXTRACTED: {
    title: "No records extracted",
    guidance:
      "Pages were fetched but the selectors matched nothing. Re-check your fields in the Fields step and run Preview before extracting."
  }
};

export function errorHelp(code: string | null | undefined): ErrorHelp {
  if (code && HELP[code]) return HELP[code];
  return {
    title: "Something went wrong",
    guidance:
      "The project failed. Retry below, or adjust the provider/settings and try again."
  };
}

/**
 * A provider swap only helps when analysis hasn't succeeded yet — that's the
 * only stage where retry re-runs the AI call. Once analysis is done, retry
 * resumes from field setup and never re-calls the provider.
 */
export function canRetryWithProvider(hasAnalysis: boolean): boolean {
  return !hasAnalysis;
}
