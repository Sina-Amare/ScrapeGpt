import { useQuery } from "@tanstack/react-query";
import { Check, Globe2, RefreshCw, Send, X } from "lucide-react";
import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { TaskResultPanel } from "../components/ui/TaskResultPanel";
import { ApiError, api } from "../lib/api";
import { shouldPollTask, stateTone } from "../lib/taskPolling";
import { TaskResponse, TaskState } from "../types";

// ---------------------------------------------------------------------------
// Pipeline progress bar
// ---------------------------------------------------------------------------

const STAGES: { state: TaskState; label: string }[] = [
  { state: "PERMISSION_GRANTED", label: "Queued" },
  { state: "SCRAPING", label: "Scraping" },
  { state: "SCRAPED", label: "Scraped" },
  { state: "LLM_PROCESSING", label: "Analyzing" },
  { state: "COMPLETED", label: "Done" },
];

const STATE_ORDER = STAGES.map((s) => s.state);

function stageIndex(state: TaskState): number {
  return STATE_ORDER.indexOf(state);
}

function PipelineProgress({ state }: { state: TaskState }) {
  const isFailed = state === "FAILED";
  const currentIdx = isFailed ? -1 : stageIndex(state);

  if (isFailed) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm font-semibold text-danger">
        <X className="h-4 w-4 shrink-0" />
        Pipeline failed — see error below
      </div>
    );
  }

  return (
    <div className="flex items-center">
      {STAGES.map(({ state: s, label }, idx) => {
        const isDone = idx < currentIdx;
        const isActive = idx === currentIdx;
        const isCompleted = s === "COMPLETED" && isDone;

        return (
          <div key={s} className="flex flex-1 items-center">
            <div className="flex flex-col items-center gap-1">
              <div
                className={[
                  "flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold transition-all",
                  isDone
                    ? "bg-primary text-onprimary"
                    : isActive
                    ? "bg-teal/15 text-teal ring-2 ring-teal animate-pulse"
                    : "bg-porcelain text-muted",
                ].join(" ")}
              >
                {isDone || isCompleted ? (
                  <Check className="h-3.5 w-3.5" />
                ) : (
                  <span>{idx + 1}</span>
                )}
              </div>
              <span
                className={[
                  "text-xs whitespace-nowrap",
                  isDone || isActive ? "font-semibold text-ink" : "text-muted",
                ].join(" ")}
              >
                {label}
              </span>
            </div>
            {idx < STAGES.length - 1 ? (
              <div
                className={[
                  "mb-5 h-0.5 flex-1 mx-1 transition-all",
                  idx < currentIdx ? "bg-teal" : "bg-line",
                ].join(" ")}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Error message extraction
// ---------------------------------------------------------------------------

function scrapeError(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return "Active scrape limit reached. Finish or cancel an existing task first.";
  }
  if (error instanceof ApiError && error.status === 429) {
    return "Scrape rate limit reached. Wait a moment and try again.";
  }
  return error instanceof Error ? error.message : "Could not start scrape task";
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function NewScrapePage() {
  const [url, setUrl] = useState("");
  const [startedTask, setStartedTask] = useState<TaskResponse | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [failureCount, setFailureCount] = useState(0);

  const taskStatus = useQuery({
    queryKey: ["task", startedTask?.task_id],
    enabled: Boolean(startedTask?.task_id),
    queryFn: async () => {
      if (!startedTask) return null;
      try {
        const response = await api.getTask(startedTask.task_id);
        setFailureCount(0);
        return response;
      } catch (err) {
        setFailureCount((c) => c + 1);
        throw err;
      }
    },
    refetchInterval: (query) =>
      shouldPollTask(query.state.data ?? startedTask, failureCount) ? 2000 : false,
    retry: false
  });

  const task = taskStatus.data ?? startedTask;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitError(null);
    setSubmitting(true);
    try {
      const response = await api.startScrape(url);
      setStartedTask(response);
      setFailureCount(0);
    } catch (err) {
      setSubmitError(scrapeError(err));
    } finally {
      setSubmitting(false);
    }
  }

  const isCompleted = task?.state === "COMPLETED";
  const isFailed = task?.state === "FAILED";

  return (
    <>
      <PageHeader title="New Scrape" eyebrow="Extraction pipeline" />

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Left: URL form */}
        <section className="min-w-0 w-full overflow-hidden rounded-xl border border-line bg-surface p-6 shadow-panel lg:col-span-1">
          <div className="mb-5 flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-xl bg-teal-soft text-teal">
              <Globe2 className="h-5 w-5" />
            </div>
            <div>
              <h2 className="font-bold text-ink">Submit URL</h2>
              <p className="text-sm text-muted">Runs the full scrape → AI pipeline.</p>
            </div>
          </div>

          <form className="grid gap-4 min-w-0 w-full" onSubmit={onSubmit}>
            {submitError ? <Alert tone="danger">{submitError}</Alert> : null}
            <Field label="URL">
              <Input
                type="url"
                placeholder="https://example.com"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                required
              />
            </Field>
            <Button type="submit" disabled={submitting}>
              <Send className="h-4 w-4" />
              {submitting ? "Starting..." : "Start scrape"}
            </Button>
          </form>

          {!task ? null : (
            <div className="mt-5 rounded-xl border border-line bg-porcelain p-4 text-sm">
              <p className="mb-1 text-xs font-bold uppercase tracking-widest text-muted">
                Pipeline
              </p>
              <div className="mt-3">
                <PipelineProgress state={task.state} />
              </div>
            </div>
          )}
        </section>

        {/* Right: Live task status */}
        <section className="min-w-0 rounded-xl border border-line bg-surface p-6 shadow-panel lg:col-span-2">
          <div className="mb-5 flex items-center justify-between gap-3">
            <div>
              <h2 className="font-bold text-ink">Task status</h2>
              <p className="text-sm text-muted">
                {task && !isCompleted && !isFailed
                  ? "Polling every 2 seconds."
                  : "Polling stopped."}
              </p>
            </div>
            {task ? (
              <Button variant="secondary" onClick={() => taskStatus.refetch()}>
                <RefreshCw className="h-4 w-4" />
                Refresh
              </Button>
            ) : null}
          </div>

          {!task ? (
            <div className="rounded-xl border border-dashed border-line bg-porcelain p-10 text-center text-sm text-muted">
              Submit a URL to watch the pipeline run.
            </div>
          ) : failureCount >= 3 ? (
            <Alert tone="danger">
              Polling paused after repeated failures. Use Refresh to try again.
            </Alert>
          ) : (
            <div className="grid gap-5">
              {/* Header row */}
              <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                <div className="min-w-0">
                  <h3 className="text-lg font-bold text-ink">
                    Task #{task.task_id}
                  </h3>
                  <p className="mt-0.5 break-all text-sm text-muted">{task.url}</p>
                </div>
                <div className="shrink-0">
                  <Badge tone={stateTone(task.state)}>{task.state}</Badge>
                </div>
              </div>

              {/* Error */}
              {task.error ? <Alert tone="danger">{task.error}</Alert> : null}

              {/* Content length bar — shown once scraping is done */}
              {task.content_length ? (
                <div className="rounded-lg border border-line bg-porcelain px-4 py-3">
                  <p className="text-xs font-semibold text-muted">
                    Scraped{" "}
                    <span className="text-ink">
                      {task.content_length.toLocaleString()}
                    </span>{" "}
                    characters of page text
                  </p>
                </div>
              ) : null}

              {/* Result panel (structured) or pending hint */}
              {isCompleted && task.result ? (
                <div className="rounded-xl border border-line bg-porcelain p-5">
                  <p className="mb-4 text-xs font-bold uppercase tracking-widest text-muted">
                    AI Analysis
                  </p>
                  <TaskResultPanel
                    result={task.result}
                    contentLength={task.content_length}
                  />
                </div>
              ) : !isFailed ? (
                <div className="rounded-xl border border-dashed border-line bg-porcelain p-6 text-center text-sm text-muted">
                  {task.state === "PERMISSION_GRANTED"
                    ? "Task queued — pipeline starting..."
                    : task.state === "SCRAPING"
                    ? "Fetching and parsing the URL..."
                    : task.state === "SCRAPED"
                    ? "Page scraped — sending to AI provider..."
                    : task.state === "LLM_PROCESSING"
                    ? "AI is analyzing the content..."
                    : "Processing..."}
                </div>
              ) : null}

              <Link
                className="text-sm font-semibold text-teal hover:text-teal-dark"
                to="/dashboard"
              >
                View on dashboard →
              </Link>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
