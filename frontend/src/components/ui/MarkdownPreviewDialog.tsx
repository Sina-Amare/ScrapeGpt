import { Check, Code2, Copy, Download, Eye, FileText, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "./Button";
import { MarkdownView } from "./MarkdownView";

type Tab = "rendered" | "source";

/**
 * Full-width modal that previews extracted content exactly as the downloaded
 * `.md` file will render. Toggles between the rendered view and the raw Markdown
 * source, with copy-to-clipboard and an optional direct `.md` download.
 */
export function MarkdownPreviewDialog({
  markdown,
  sourceUrl,
  title = "Markdown preview",
  onClose,
  onDownload,
}: {
  markdown: string;
  sourceUrl?: string;
  title?: string;
  onClose: () => void;
  onDownload?: () => void | Promise<void>;
}) {
  const [tab, setTab] = useState<Tab>("rendered");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
      toast.success("Markdown copied");
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy to clipboard");
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-ink/35 p-4 sm:p-6"
      onClick={onClose}
    >
      <section
        className="flex h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-panel"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="flex min-w-0 items-start gap-2.5">
            <FileText className="mt-0.5 h-5 w-5 shrink-0 text-teal" />
            <div className="min-w-0">
              <h2 className="text-base font-semibold text-ink">{title}</h2>
              {sourceUrl ? (
                <a
                  href={sourceUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-0.5 block truncate text-xs text-muted hover:text-teal"
                  title={sourceUrl}
                >
                  {sourceUrl}
                </a>
              ) : (
                <p className="mt-0.5 text-xs text-muted">
                  This is how your <code className="font-mono">.md</code> file will look.
                </p>
              )}
            </div>
          </div>
          <button
            type="button"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted transition hover:bg-porcelain hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-teal focus-visible:ring-offset-2"
            onClick={onClose}
            aria-label="Close preview"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line bg-porcelain px-5 py-2.5">
          <div className="inline-flex rounded-md border border-line bg-surface p-0.5">
            <TabButton active={tab === "rendered"} onClick={() => setTab("rendered")} icon={<Eye className="h-3.5 w-3.5" />}>
              Rendered
            </TabButton>
            <TabButton active={tab === "source"} onClick={() => setTab("source")} icon={<Code2 className="h-3.5 w-3.5" />}>
              Source
            </TabButton>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" className="h-9 px-3" onClick={copy}>
              {copied ? <Check className="h-4 w-4 text-teal" /> : <Copy className="h-4 w-4" />}
              {copied ? "Copied" : "Copy"}
            </Button>
            {onDownload ? (
              <Button variant="secondary" className="h-9 px-3" onClick={() => void onDownload()}>
                <Download className="h-4 w-4" />
                Download .md
              </Button>
            ) : null}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto bg-surface p-6 min-h-0">
          {tab === "rendered" ? (
            <MarkdownView markdown={markdown} />
          ) : (
            <pre className="whitespace-pre-wrap break-words font-mono text-[0.82rem] leading-6 text-ink">
              {markdown}
            </pre>
          )}
        </div>
      </section>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        "inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-semibold transition",
        active ? "bg-teal text-white" : "text-muted hover:text-ink",
      ].join(" ")}
    >
      {icon}
      {children}
    </button>
  );
}
