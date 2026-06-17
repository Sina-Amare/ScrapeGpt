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
  },
  BROWSER_DRIVER_CRASHED: {
    title: "The browser closed unexpectedly",
    guidance:
      "The page needed a browser to render and it crashed mid-load. This is usually temporary — just retry. If it keeps happening, try Browser rendering off or a simpler crawl scope."
  },
  INTERACTION_BROWSER_REQUIRED: {
    title: "A browser is required for the selected variants",
    guidance:
      "The interactive page variants you turned on need a browser backend that isn't installed. Turn the variants off, or install a browser backend (Playwright/Camoufox), then retry."
  },
  BROWSER_UNAVAILABLE: {
    title: "No browser backend available",
    guidance:
      "This page needs a browser to render, but no browser backend is installed. Install Playwright or Camoufox on the server, then retry."
  },
  FETCH_TIMEOUT: {
    title: "The page timed out",
    guidance:
      "The site took too long to respond. Retry — if it persists, the site may be slow or blocking automated requests."
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
