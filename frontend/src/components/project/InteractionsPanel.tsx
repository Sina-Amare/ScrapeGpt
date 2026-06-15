import { useEffect, useMemo, useState } from "react";
import { Sparkles, MousePointerClick, Layers } from "lucide-react";
import type {
  InteractionGroup,
  InteractionProfile,
} from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";

type Props = {
  profile: InteractionProfile | null | undefined;
  disabled?: boolean;
  detecting?: boolean;
  saving?: boolean;
  detectError?: string | null;
  saveError?: string | null;
  onDetect: () => void;
  onSave: (profile: InteractionProfile) => void;
};

const MAX_COMBOS = 12;

function emptyProfile(): InteractionProfile {
  return {
    enabled: false,
    merge_variants: false,
    max_variant_combinations: MAX_COMBOS,
    groups: [],
  };
}

function countCombinations(groups: InteractionGroup[]): number {
  const active = groups.filter((g) => g.options.some((o) => o.selected));
  if (!active.length) return 0;
  return active.reduce(
    (acc, g) => acc * g.options.filter((o) => o.selected).length,
    1
  );
}

export function InteractionsPanel({
  profile,
  disabled,
  detecting,
  saving,
  detectError,
  saveError,
  onDetect,
  onSave,
}: Props) {
  const [draft, setDraft] = useState<InteractionProfile>(
    profile ?? emptyProfile()
  );

  // Re-sync when the saved profile changes (e.g. after Detect).
  useEffect(() => {
    setDraft(profile ?? emptyProfile());
  }, [profile]);

  const combos = useMemo(
    () => countCombinations(draft.groups),
    [draft.groups]
  );
  const overCap = combos > MAX_COMBOS;
  const hasGroups = draft.groups.length > 0;
  const interactiveSelected = draft.groups.some(
    (g) =>
      g.execution === "interactive" &&
      g.options.some((o) => o.selected && o.recipe.length > 0)
  );

  function patchGroup(gi: number, next: Partial<InteractionGroup>) {
    setDraft((d) => ({
      ...d,
      groups: d.groups.map((g, i) => (i === gi ? { ...g, ...next } : g)),
    }));
  }

  function toggleOption(gi: number, oi: number) {
    setDraft((d) => ({
      ...d,
      groups: d.groups.map((g, i) =>
        i === gi
          ? {
              ...g,
              options: g.options.map((o, j) =>
                j === oi ? { ...o, selected: !o.selected } : o
              ),
            }
          : g
      ),
    }));
  }

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">
          Some pages show the same data in variants (e.g. per 100 g vs per
          serving, metric vs imperial). Detect them to extract each variant as
          its own labelled rows.
        </p>
        <Button onClick={onDetect} disabled={disabled || detecting} variant="secondary">
          <Sparkles className="h-4 w-4" />
          {detecting ? "Detecting..." : hasGroups ? "Re-detect variants" : "Detect variants"}
        </Button>
      </div>

      {detectError ? <Alert tone="danger">{detectError}</Alert> : null}
      {saveError ? <Alert tone="danger">{saveError}</Alert> : null}

      {!hasGroups ? (
        <div className="rounded-lg border border-dashed border-line bg-porcelain p-6 text-center text-sm text-muted">
          No page variants configured. Run detection to find toggles on the page,
          or leave this off for normal single-variant extraction.
        </div>
      ) : (
        <>
          <label className="flex items-center gap-2 text-sm font-semibold text-ink">
            <input
              type="checkbox"
              checked={draft.enabled}
              disabled={disabled}
              onChange={(e) => setDraft((d) => ({ ...d, enabled: e.target.checked }))}
            />
            Extract every selected variant combination
          </label>

          <label className="flex items-center gap-2 text-sm text-muted">
            <input
              type="checkbox"
              checked={!!draft.merge_variants}
              disabled={disabled || !draft.enabled}
              onChange={(e) =>
                setDraft((d) => ({ ...d, merge_variants: e.target.checked }))
              }
            />
            Merge variants into one row per item (a column per variant, e.g.
            "Calories (per 100 g)")
          </label>

          {draft.groups.map((group, gi) => (
            <div key={gi} className="rounded-lg border border-line bg-surface p-4">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1 rounded-full bg-porcelain px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-muted">
                  {group.execution === "interactive" ? (
                    <MousePointerClick className="h-3 w-3" />
                  ) : (
                    <Layers className="h-3 w-3" />
                  )}
                  {group.execution}
                </span>
                <span className="text-sm font-semibold text-ink">{group.label}</span>
              </div>
              <label className="mb-2 grid gap-1 text-xs font-semibold text-muted sm:max-w-xs">
                Column name in export
                <input
                  type="text"
                  value={group.metadata_key}
                  disabled={disabled}
                  onChange={(e) => patchGroup(gi, { metadata_key: e.target.value })}
                  className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink"
                />
              </label>
              <div className="flex flex-wrap gap-3">
                {group.options.map((option, oi) => (
                  <label key={oi} className="flex items-center gap-1.5 text-sm text-ink">
                    <input
                      type="checkbox"
                      checked={option.selected}
                      disabled={disabled}
                      onChange={() => toggleOption(gi, oi)}
                    />
                    {option.label}
                    {option.recipe.length === 0 ? (
                      <span className="text-[10px] text-muted/70">(no browser)</span>
                    ) : null}
                  </label>
                ))}
              </div>
            </div>
          ))}

          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-sm text-muted">
              {combos} variant combination{combos === 1 ? "" : "s"} per page
            </span>
            <Button
              variant="primary"
              disabled={disabled || saving || overCap}
              onClick={() => onSave(draft)}
            >
              {saving ? "Saving..." : "Save variants"}
            </Button>
          </div>

          {overCap ? (
            <Alert tone="danger">
              {combos} combinations exceed the limit of {MAX_COMBOS}. Deselect
              some options before saving.
            </Alert>
          ) : null}
          {draft.enabled && interactiveSelected ? (
            <Alert tone="info">
              Interactive variants need a browser render backend. If none is
              available, extraction will stop with INTERACTION_BROWSER_REQUIRED.
            </Alert>
          ) : null}
        </>
      )}
    </div>
  );
}
