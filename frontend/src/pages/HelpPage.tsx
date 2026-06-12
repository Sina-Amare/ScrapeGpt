import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { PageHeader } from "../components/ui/PageHeader";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-line bg-surface shadow-panel">
      <button
        type="button"
        className="flex w-full items-center justify-between px-6 py-4 text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <h2 className="text-sm font-bold text-ink">{title}</h2>
        {open ? (
          <ChevronDown className="h-4 w-4 text-muted" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted" />
        )}
      </button>
      {open && (
        <div className="border-t border-line px-6 py-5 text-sm text-ink">
          {children}
        </div>
      )}
    </div>
  );
}

function Pipeline() {
  const steps = [
    { label: "1. URL", color: "bg-teal" },
    { label: "2. Fetch", color: "bg-teal" },
    { label: "3. AI Analysis", color: "bg-teal" },
    { label: "4. Field Setup", color: "bg-teal-soft border border-teal text-teal" },
    { label: "5. Extract", color: "bg-teal" },
    { label: "6. Export", color: "bg-teal" },
  ];
  return (
    <div className="flex flex-wrap items-center gap-2 py-2">
      {steps.map((step, i) => (
        <div key={step.label} className="flex items-center gap-2">
          <span
            className={`rounded-md px-3 py-1.5 text-xs font-bold text-white ${step.color}`}
          >
            {step.label}
          </span>
          {i < steps.length - 1 && (
            <span className="text-muted">→</span>
          )}
        </div>
      ))}
    </div>
  );
}

export function HelpPage() {
  return (
    <>
      <PageHeader title="Help & About" eyebrow="ScrapeGPT guide" />

      <div className="grid gap-4 max-w-3xl">
        {/* Pipeline overview */}
        <Section title="How ScrapeGPT works">
          <p className="mb-4 text-muted">
            ScrapeGPT automates web scraping in six steps:
          </p>
          <Pipeline />
          <ol className="mt-4 space-y-2 text-muted">
            <li><strong className="text-ink">1. URL</strong> — You paste the page you want to scrape.</li>
            <li><strong className="text-ink">2. Fetch</strong> — ScrapeGPT downloads the page (with optional browser rendering for JS-heavy sites).</li>
            <li><strong className="text-ink">3. AI Analysis</strong> — The LLM reads the HTML and identifies what structured data exists and how to extract it.</li>
            <li><strong className="text-ink">4. Field Setup</strong> — You review the detected fields, rename them, and choose which to include.</li>
            <li><strong className="text-ink">5. Extract</strong> — ScrapeGPT crawls the pages in the scope you selected and extracts data using CSS selectors.</li>
            <li><strong className="text-ink">6. Export</strong> — Download results as CSV, JSON, or a styled XLSX file.</li>
          </ol>
        </Section>

        {/* Crawl scopes */}
        <Section title="Crawl scopes explained">
          <p className="mb-3 text-muted">Choose how many pages ScrapeGPT will crawl.</p>
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-line text-left text-xs font-bold uppercase tracking-widest text-muted">
                <th className="py-2 pr-4">Mode</th>
                <th className="py-2 pr-4">What it crawls</th>
                <th className="py-2">Example</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              <tr>
                <td className="py-3 pr-4 font-semibold text-ink">This page only</td>
                <td className="py-3 pr-4 text-muted">The single URL you pasted</td>
                <td className="py-3 text-muted">One paper: arxiv.org/abs/2301.00001</td>
              </tr>
              <tr>
                <td className="py-3 pr-4 font-semibold text-ink">Paginated list</td>
                <td className="py-3 pr-4 text-muted">All pages of a list via Next/page links</td>
                <td className="py-3 text-muted">Search results: arxiv.org/search/… pages 1–40</td>
              </tr>
              <tr>
                <td className="py-3 pr-4 font-semibold text-ink">Listing + detail pages</td>
                <td className="py-3 pr-4 text-muted">List page + each linked detail page</td>
                <td className="py-3 text-muted">arxiv.org search → each /abs/ paper page</td>
              </tr>
              <tr>
                <td className="py-3 pr-4 font-semibold text-ink">Entire website</td>
                <td className="py-3 pr-4 text-muted">Every discoverable page on the domain</td>
                <td className="py-3 text-muted">All docs on docs.example.com</td>
              </tr>
            </tbody>
          </table>
          <p className="mt-3 text-xs text-muted">Use <strong>Page preview</strong> to verify which URLs will be crawled before committing to a full extraction run.</p>
        </Section>

        {/* USE vs REQUIRED */}
        <Section title="Fields: USE vs REQUIRED">
          <div className="space-y-4 text-muted">
            <div>
              <p className="font-semibold text-ink">USE checkbox</p>
              <p>Include this field in your output. If unchecked, the field is ignored entirely and won't appear in your export.</p>
            </div>
            <div>
              <p className="font-semibold text-ink">REQUIRED checkbox</p>
              <p>Discard any row where this field is empty. Use this only for fields like <em>title</em> or <em>ID</em> that every row must have — missing-required rows are silently dropped from results.</p>
            </div>
            <div className="rounded-md border border-line bg-porcelain px-4 py-3 text-xs">
              <strong>Example:</strong> If you scrape products with a <em>price</em> field and mark it REQUIRED, products with no listed price will be excluded. Useful for cleaning noisy data; risky if the field is sometimes legitimately absent.
            </div>
          </div>
        </Section>

        {/* Field types */}
        <Section title="Field types">
          <p className="mb-3 text-muted">Types are auto-detected by the AI. Only change if the detection is clearly wrong.</p>
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-line text-left text-xs font-bold uppercase tracking-widest text-muted">
                <th className="py-2 pr-4">Type</th>
                <th className="py-2">When to use</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line text-muted">
              <tr><td className="py-2.5 pr-4 font-semibold text-ink">Text</td><td className="py-2.5">Titles, descriptions, names — any free-form string</td></tr>
              <tr><td className="py-2.5 pr-4 font-semibold text-ink">Number</td><td className="py-2.5">Prices, ratings, counts — numeric values</td></tr>
              <tr><td className="py-2.5 pr-4 font-semibold text-ink">URL</td><td className="py-2.5">Links, image sources, canonical URLs</td></tr>
              <tr><td className="py-2.5 pr-4 font-semibold text-ink">Date</td><td className="py-2.5">Published dates, timestamps</td></tr>
              <tr><td className="py-2.5 pr-4 font-semibold text-ink">Boolean</td><td className="py-2.5">In stock / available / true/false flags</td></tr>
              <tr><td className="py-2.5 pr-4 font-semibold text-ink">Image</td><td className="py-2.5">Product images, thumbnails — extracts the src URL</td></tr>
            </tbody>
          </table>
        </Section>

        {/* Confidence score */}
        <Section title="Confidence score">
          <div className="space-y-2 text-muted">
            <p>The confidence score is the AI's certainty that the CSS selectors it generated will reliably extract the correct data.</p>
            <ul className="ml-4 list-disc space-y-1">
              <li><strong className="text-ink">80–100%</strong> — Good. Selectors are specific and stable.</li>
              <li><strong className="text-ink">60–79%</strong> — Needs review. May work but check the sample preview.</li>
              <li><strong className="text-ink">Below 60%</strong> — Risky. The page structure may be unusual or the AI was uncertain.</li>
            </ul>
            <p>A low confidence score doesn't mean extraction will fail — it means you should verify field values with <strong>Sample preview</strong> before running a full extraction.</p>
          </div>
        </Section>

        {/* Page preview vs Sample preview */}
        <Section title="Page preview vs Sample preview">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="rounded-lg border border-line bg-porcelain p-4">
              <p className="font-bold text-ink">Page preview</p>
              <p className="mt-1 text-muted">Shows the <em>list of URLs</em> ScrapeGPT will crawl based on your scope. Run this first to verify the right pages are included. Catches scope misconfiguration before you spend time extracting.</p>
            </div>
            <div className="rounded-lg border border-line bg-porcelain p-4">
              <p className="font-bold text-ink">Sample preview</p>
              <p className="mt-1 text-muted">Runs the extraction on <em>one page</em> and shows the actual field values. Run this to verify the fields look correct before the full run. Optional but recommended for complex pages.</p>
            </div>
          </div>
        </Section>

        {/* Extraction quality */}
        <Section title="Extraction quality panel">
          <div className="space-y-2 text-muted">
            <p>After extraction, ScrapeGPT computes a quality summary:</p>
            <ul className="ml-4 list-disc space-y-1">
              <li><strong className="text-ink">Good</strong> — All selected fields appeared on 70%+ of records.</li>
              <li><strong className="text-ink">Needs review</strong> — One or more fields had a low fill rate.</li>
              <li><strong className="text-ink">Risky</strong> — Many pages failed or field fill rates are very low.</li>
            </ul>
            <p>Warnings are shown per-field so you can identify which selector failed. If a required field has 0% coverage, the selector may have changed — re-analyze the project to regenerate it.</p>
          </div>
        </Section>

        {/* FAQ */}
        <Section title="FAQ">
          <div className="space-y-5 text-muted">
            <div>
              <p className="font-semibold text-ink">Why is my project stuck at "Analyzing"?</p>
              <p>The AI provider may be slow or rate-limited. ScrapeGPT enforces a 120-second timeout. If it stays stuck, check your provider's status page. Free-tier providers like OpenRouter sometimes queue requests for several minutes.</p>
            </div>
            <div>
              <p className="font-semibold text-ink">Why are rows missing from my results?</p>
              <p>Possible causes: (1) You marked a field as Required and some rows didn't have a value for it. (2) Some pages were blocked by bot protection — check the extraction progress for blocked pages. (3) The page limit was reached before all pages were crawled.</p>
            </div>
            <div>
              <p className="font-semibold text-ink">What is "Raw debug data"?</p>
              <p>It's the raw internal state of the project — the spec, analysis results, fetch metadata, and frontier preview. Useful for debugging unexpected behavior. Not needed for normal use.</p>
            </div>
            <div>
              <p className="font-semibold text-ink">My confidence score is low — should I re-analyze?</p>
              <p>Not necessarily. Run a Sample preview first — if the extracted values look correct, the selectors are working despite the low score. Only re-analyze if the values are wrong or missing.</p>
            </div>
            <div>
              <p className="font-semibold text-ink">Can I add my own API key?</p>
              <p>Yes — go to <strong>Providers</strong> in the sidebar. ScrapeGPT is a BYOK (Bring Your Own Key) platform. Add a provider config (OpenAI, Anthropic, OpenRouter, etc.) and it will be used for all AI analysis and extraction.</p>
            </div>
          </div>
        </Section>
      </div>
    </>
  );
}
