import { ChevronDown, ChevronRight, Layers, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import {
  countCombinations,
  normalizeInteractionProfile,
} from "../../lib/interactionProfile";
import { variantCopy, variantsUseBrowser } from "../../lib/variantCopy";
import type { InteractionProfile } from "../../types";
import { InteractionsPanel } from "../project/InteractionsPanel";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";

type Props = {
  profile: Partial<InteractionProfile> | null | undefined;
  disabled?: boolean;
  detecting?: boolean;
  saving?: boolean;
  detectError?: string | null;
  saveError?: string | null;
  onDetect: () => void;
  onSave: (profile: InteractionProfile) => void;
};

/**
 * Plain-language front for page variants. The common case needs zero edits — the
 * backend already pre-selects the safe defaults and we trust them. Users see human
 * choices ("Capture both per-100g and per-serving values"), never the underlying
 * execution kinds. The full low-level editor (CSS selectors, merge, combination
 * cap, export column names) lives untouched behind "Advanced (for developers)".
 */
export function VariantsControl({
  profile,
  disabled,
  detecting,
  saving,
  detectError,
  saveError,
  onDetect,
  onSave,
}: Props) {
  const normalized = useMemo(() => normalizeInteractionProfile(profile), [profile]);
  const groups = normalized.groups;
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const combos = countCombinations(groups);
  const usesBrowser = variantsUseBrowser(groups);

  // Data axes (static columns / url params) are real, separable data and safe to
  // capture. A purely *interactive* group is a DISPLAY toggle (e.g.
  // Metric/Imperial) that re-renders the whole page — combining it with a static
  // column axis misaligns the cells and multiplies rows into garbage (the
  // "per 100 g × Imperial" problem). Keep those out of the simple chips; they
  // stay in Advanced, off by default (the backend also auto-deselects them).
  const dataGroups = groups
    .map((group, index) => ({ group, index }))
    .filter(({ group }) => group.execution !== "interactive");
  const hasDisplayToggles = groups.some((g) => g.execution === "interactive");
  const displayToggleSelected = groups.some(
    (g) => g.execution === "interactive" && g.options.some((o) => o.selected)
  );

  function toggleOption(gi: number, oi: number) {
    const nextGroups = groups.map((g, i) =>
      i === gi
        ? { ...g, options: g.options.map((o, j) => (j === oi ? { ...o, selected: !o.selected } : o)) }
        : g
    );
    const enabled = nextGroups.some((g) => g.options.some((o) => o.selected));
    onSave({ ...normalized, enabled, groups: nextGroups });
  }

  function clearDisplayToggles() {
    const nextGroups = groups.map((g) =>
      g.execution === "interactive"
        ? { ...g, options: g.options.map((o) => ({ ...o, selected: false })) }
        : g
    );
    const enabled = nextGroups.some((g) => g.options.some((o) => o.selected));
    onSave({ ...normalized, enabled, groups: nextGroups });
  }

  if (groups.length === 0) {
    return (
      <div className="grid gap-3">
        {detectError ? <Alert tone="danger">{detectError}</Alert> : null}
        <EmptyState
          icon={<Layers className="h-6 w-6" />}
          title="No page variations detected"
          hint="Most pages don't need this. If this page shows the same items more than one way (e.g. per 100 g vs per serving, metric vs imperial), detect them to capture each as its own labelled rows."
          action={
            <Button variant="secondary" onClick={onDetect} disabled={disabled || detecting}>
              <Sparkles className="h-4 w-4" />
              {detecting ? "Detecting…" : "Detect variations"}
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted">
        Each selected version becomes its own labelled rows. The defaults below already work; adjust
        only if you want to.
      </p>

      {detectError ? <Alert tone="danger">{detectError}</Alert> : null}
      {saveError ? <Alert tone="danger">{saveError}</Alert> : null}

      {dataGroups.length > 0 ? (
        <div className="grid gap-3">
          {dataGroups.map(({ group, index: gi }) => {
            const copy = variantCopy(group);
            return (
              <div key={gi} className="rounded-lg border border-line bg-surface p-4">
                <p className="text-sm font-semibold text-ink">{copy.title}</p>
                {copy.help ? <p className="mt-0.5 text-xs text-muted">{copy.help}</p> : null}
                <div className="mt-3 flex flex-wrap gap-2">
                  {group.options.map((option, oi) => (
                    <button
                      key={oi}
                      type="button"
                      disabled={disabled || saving}
                      onClick={() => toggleOption(gi, oi)}
                      className={[
                        "rounded-full border px-3 py-1.5 text-sm font-medium transition",
                        option.selected
                          ? "border-teal bg-teal-soft/50 text-ink"
                          : "border-line bg-surface text-muted hover:border-teal/40",
                        disabled || saving ? "cursor-not-allowed opacity-60" : "cursor-pointer",
                      ].join(" ")}
                      aria-pressed={option.selected}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-sm text-muted">
          This page's only variations are display toggles — they're optional and stay off by default
          (see Advanced).
        </p>
      )}

      {hasDisplayToggles ? (
        <Alert tone="warning">
          <div className="flex flex-col gap-2">
            <span>
              This page also has a <strong>display toggle</strong> (e.g. Metric/Imperial). It re-renders
              the whole page rather than adding new data, so it multiplies your rows and usually doesn't
              line up with the columns above —{" "}
              {displayToggleSelected ? (
                <>
                  and it's <strong>currently on</strong>, which is likely producing mismatched rows.
                </>
              ) : (
                <>
                  it's left <strong>off</strong> by default. Turn it on under <strong>Advanced</strong>{" "}
                  only if you specifically want every unit combination.
                </>
              )}
            </span>
            {displayToggleSelected ? (
              <div>
                <Button
                  variant="secondary"
                  disabled={disabled || saving}
                  onClick={clearDisplayToggles}
                >
                  {saving ? "Saving…" : "Turn off display toggles"}
                </Button>
              </div>
            ) : null}
          </div>
        </Alert>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" onClick={onDetect} disabled={disabled || detecting}>
          <Sparkles className="h-4 w-4" />
          {detecting ? "Detecting…" : "Re-detect"}
        </Button>
        <span className="text-sm text-muted">
          {combos > 0
            ? `${combos} version${combos === 1 ? "" : "s"} of each item`
            : "No versions selected — extracts items once"}
        </span>
      </div>

      {usesBrowser ? (
        <Alert tone="info">
          Some versions are read by opening the page in a browser. If that isn't available,
          ScrapeGPT falls back to the page's default values and notes it — extraction won't fail.
        </Alert>
      ) : null}

      <div className="rounded-lg border border-line bg-porcelain">
        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-semibold text-ink"
        >
          {advancedOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          Advanced (for developers)
        </button>
        {advancedOpen ? (
          <div className="border-t border-line p-4">
            <p className="mb-3 text-xs text-muted">
              Per-variant CSS selectors, the merge-into-columns option, export column names, and the
              combination limit. Changes here save with the panel's own button.
            </p>
            <InteractionsPanel
              profile={profile}
              disabled={disabled}
              detecting={detecting}
              saving={saving}
              detectError={null}
              saveError={null}
              onDetect={onDetect}
              onSave={onSave}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
