import { PageHeader } from "../components/ui/PageHeader";

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-surface shadow-panel">
      <div className="border-b border-line px-6 py-4">
        <h2 className="text-sm font-bold text-ink">{title}</h2>
      </div>
      <div className="px-6 py-5 text-sm text-muted">{children}</div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4 py-2.5 border-b border-line last:border-0">
      <span className="w-36 flex-shrink-0 font-semibold text-ink">{label}</span>
      <span className="text-muted">{children}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pipeline diagram
// ---------------------------------------------------------------------------

function Pipeline() {
  const steps = [
    { n: 1, label: "URL" },
    { n: 2, label: "Fetch" },
    { n: 3, label: "AI Analysis" },
    { n: 4, label: "Field Setup", current: true },
    { n: 5, label: "Extract" },
    { n: 6, label: "Export" },
  ];
  return (
    <div className="flex flex-wrap items-center gap-2 py-1">
      {steps.map((step, i) => (
        <div key={step.n} className="flex items-center gap-2">
          <span
            className={`rounded-md px-3 py-1.5 text-xs font-bold ${
              step.current
                ? "bg-primary text-onprimary ring-2 ring-accent/50 ring-offset-2 ring-offset-surface"
                : "bg-porcelain text-muted"
            }`}
          >
            {step.n}. {step.label}
          </span>
          {i < steps.length - 1 && <span className="text-line">→</span>}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function HelpPage() {
  return (
    <>
      <PageHeader title="Help" eyebrow="Reference" />

      <div className="grid gap-4 max-w-3xl">

        {/* Pipeline */}
        <SectionCard title="How it works">
          <Pipeline />
          <ol className="mt-4 space-y-1.5 text-muted">
            <li><strong className="text-ink">1. URL</strong> — Paste the page you want to scrape.</li>
            <li><strong className="text-ink">2. Fetch</strong> — Page is downloaded; browser rendering available for JS-heavy sites.</li>
            <li><strong className="text-ink">3. AI Analysis</strong> — LLM identifies extractable fields and generates CSS selectors.</li>
            <li><strong className="text-ink">4. Field Setup</strong> — Review fields, rename, choose which to include. ScrapeGPT waits here.</li>
            <li><strong className="text-ink">5. Extract</strong> — Pages in your selected scope are crawled and data extracted.</li>
            <li><strong className="text-ink">6. Export</strong> — Download results as CSV, JSON, or XLSX.</li>
          </ol>
        </SectionCard>

        {/* Crawl scopes */}
        <SectionCard title="Crawl scopes">
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-line text-left text-xs font-bold uppercase tracking-widest text-muted">
                  <th className="pb-2 pr-6">Mode</th>
                  <th className="pb-2 pr-6">What it crawls</th>
                  <th className="pb-2">Example</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                <tr>
                  <td className="py-2.5 pr-6 font-semibold text-ink whitespace-nowrap">This page only</td>
                  <td className="py-2.5 pr-6">Single URL</td>
                  <td className="py-2.5 text-muted/70">arxiv.org/abs/2301.00001</td>
                </tr>
                <tr>
                  <td className="py-2.5 pr-6 font-semibold text-ink whitespace-nowrap">Paginated list</td>
                  <td className="py-2.5 pr-6">All pages via Next/page links</td>
                  <td className="py-2.5 text-muted/70">Search results pages 1–40</td>
                </tr>
                <tr>
                  <td className="py-2.5 pr-6 font-semibold text-ink whitespace-nowrap">Listing + detail</td>
                  <td className="py-2.5 pr-6">List + each linked detail page</td>
                  <td className="py-2.5 text-muted/70">Search → each /abs/ page</td>
                </tr>
                <tr>
                  <td className="py-2.5 pr-6 font-semibold text-ink whitespace-nowrap">Entire site</td>
                  <td className="py-2.5 pr-6">Every discoverable page on domain</td>
                  <td className="py-2.5 text-muted/70">All docs.example.com pages</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs text-muted/60">Use <strong className="text-muted">Crawl preview</strong> to verify scope before extraction.</p>
        </SectionCard>

        {/* Fields reference */}
        <SectionCard title="Fields">
          <div className="mb-4 flex gap-6 rounded-md bg-porcelain px-4 py-3 text-xs">
            <div>
              <span className="font-bold text-ink">USE</span>
              <span className="ml-2">Include in output. Uncheck to skip entirely.</span>
            </div>
            <div>
              <span className="font-bold text-ink">REQUIRED</span>
              <span className="ml-2">Drop any row missing this field.</span>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-line text-left text-xs font-bold uppercase tracking-widest text-muted">
                  <th className="pb-2 pr-6">Type</th>
                  <th className="pb-2">Use for</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {[
                  ["Text",    "Titles, descriptions, free-form strings"],
                  ["Number",  "Prices, ratings, counts"],
                  ["URL",     "Links, image sources, canonical URLs"],
                  ["Date",    "Published dates, timestamps"],
                  ["Boolean", "In stock / available / true-false flags"],
                  ["Image",   "Product images — extracts the src URL"],
                ].map(([type, desc]) => (
                  <tr key={type}>
                    <td className="py-2 pr-6 font-semibold text-ink whitespace-nowrap">{type}</td>
                    <td className="py-2 text-muted">{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>

        {/* Confidence + quality */}
        <SectionCard title="Confidence & quality">
          <div className="space-y-0">
            <Row label="80–100%">Selectors are specific and stable. Good to go.</Row>
            <Row label="60–79%">Verify with Sample preview before running full extraction.</Row>
            <Row label="Below 60%">Page structure may be unusual. Check sample values.</Row>
            <Row label="Good">All selected fields appeared on 70%+ of records.</Row>
            <Row label="Needs review">One or more fields had a low fill rate.</Row>
            <Row label="Risky">Many pages failed or field fill rates are very low.</Row>
          </div>
        </SectionCard>

        {/* FAQ */}
        <SectionCard title="FAQ">
          <div className="space-y-4">
            {[
              {
                q: "Project stuck at Analyzing?",
                a: "The AI provider may be slow or rate-limited. ScrapeGPT enforces a 120s timeout. Free-tier providers (OpenRouter) sometimes queue for several minutes."
              },
              {
                q: "Rows missing from results?",
                a: "A field may be marked Required with no value on some rows — they're silently dropped. Check the extraction progress for blocked/failed pages."
              },
              {
                q: "Some pages failed — can I retry just those?",
                a: "Not selectively. Use Retry to reopen the project, then run extraction again."
              },
              {
                q: "Confidence is low — should I re-analyze?",
                a: "Run Sample preview first. If extracted values look correct, the selectors are working. Only re-analyze if values are wrong or missing."
              },
              {
                q: "How do I add my own API key?",
                a: "Go to Providers in the sidebar. ScrapeGPT is BYOK — add an OpenAI, Anthropic, or OpenRouter config and it will be used for all AI operations."
              },
              {
                q: "What is the Raw debug data section?",
                a: "The raw internal project state: spec, analysis, fetch metadata, crawl preview. For debugging unexpected behavior only."
              }
            ].map(({ q, a }) => (
              <div key={q}>
                <p className="font-semibold text-ink">{q}</p>
                <p className="mt-0.5 text-muted">{a}</p>
              </div>
            ))}
          </div>
        </SectionCard>

      </div>
    </>
  );
}
