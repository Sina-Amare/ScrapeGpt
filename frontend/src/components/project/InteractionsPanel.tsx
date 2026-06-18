import { useEffect, useMemo, useState } from "react";
import { Sparkles, MousePointerClick, Layers } from "lucide-react";
import type {
  InteractionGroup,
  InteractionOption,
  InteractionProfile,
} from "../../types";
import {
  MAX_INTERACTION_COMBOS as MAX_COMBOS,
  countCombinations,
  normalizeInteractionProfile,
} from "../../lib/interactionProfile";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";

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
  const [draft, setDraft] = useState<InteractionProfile>(() =>
    normalizeInteractionProfile(profile)
  );

  // Re-sync when the saved profile changes (e.g. after Detect).
  useEffect(() => {
    setDraft(normalizeInteractionProfile(profile));
  }, [profile]);

  const combos = useMemo(
    () => countCombinations(draft.groups),
    [draft.groups]
  );
  const overCap = combos > MAX_COMBOS;
  const hasGroups = draft.groups.length > 0;
  // A selected option needs a browser whenever it carries a click/select recipe —
  // true for "interactive" groups and for "mixed" groups (static columns + a
  // browser toggle on the same axis).
  const interactiveSelected = draft.groups.some(
    (g) =>
      (g.execution === "interactive" || g.execution === "mixed") &&
      g.options.some((o) => o.selected && o.recipe.length > 0)
  );

  function patchGroup(gi: number, next: Partial<InteractionGroup>) {
    setDraft((d) => ({
      ...d,
      groups: d.groups.map((g, i) => (i === gi ? { ...g, ...next } : g)),
    }));
  }

  function patchOption(gi: number, oi: number, next: Partial<InteractionOption>) {
    setDraft((d) => ({
      ...d,
      groups: d.groups.map((g, i) =>
        i === gi
          ? {
              ...g,
              options: g.options.map((o, j) =>
                j === oi ? { ...o, ...next } : o
              ),
            }
          : g
      ),
    }));
  }

  function toggleOption(gi: number, oi: number) {
    patchOption(gi, oi, { selected: !draft.groups[gi].options[oi].selected });
  }

  function setOptionSelector(
    gi: number,
    oi: number,
    fieldName: string,
    selector: string
  ) {
    const current = draft.groups[gi].options[oi].field_selectors ?? {};
    patchOption(gi, oi, {
      field_selectors: { ...current, [fieldName]: selector },
    });
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
                  {group.execution === "interactive" || group.execution === "mixed" ? (
                    <MousePointerClick className="h-3 w-3" />
                  ) : (
                    <Layers className="h-3 w-3" />
                  )}
                  {group.execution === "mixed"
                    ? "columns + browser"
                    : group.execution}
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
              {group.execution === "deterministic" ? (
                <div className="grid gap-2">
                  {group.options.map((option, oi) => (
                    <div key={oi} className="rounded-md border border-line/70 bg-porcelain/50 p-2">
                      <div className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={option.selected}
                          disabled={disabled}
                          onChange={() => toggleOption(gi, oi)}
                        />
                        <input
                          type="text"
                          value={option.label}
                          disabled={disabled}
                          onChange={(e) => patchOption(gi, oi, { label: e.target.value })}
                          className="rounded border border-line bg-surface px-2 py-1 text-sm font-semibold text-ink"
                          aria-label="Variant label"
                        />
                        <span className="text-[10px] text-muted/70">(no browser)</span>
                      </div>
                      <div className="mt-2 grid gap-1 pl-6">
                        {Object.entries(option.field_selectors ?? {}).map(
                          ([fieldName, selector]) => (
                            <div key={fieldName} className="flex items-center gap-2 text-xs">
                              <span className="w-32 shrink-0 truncate text-muted" title={fieldName}>
                                {fieldName}
                              </span>
                              <input
                                type="text"
                                value={selector}
                                disabled={disabled}
                                onChange={(e) =>
                                  setOptionSelector(gi, oi, fieldName, e.target.value)
                                }
                                className="flex-1 rounded border border-line bg-surface px-2 py-1 font-mono text-xs text-ink"
                                placeholder="CSS selector for this variant's column"
                              />
                            </div>
                          )
                        )}
                        {Object.keys(option.field_selectors ?? {}).length === 0 ? (
                          <span className="text-[10px] text-muted/60">
                            No per-field selectors — this variant reads the base
                            field selectors.
                          </span>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
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
              )}
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
              Some selected variants render in a browser. If no browser backend
              is available, those values fall back to the page's static data and
              a warning is shown — extraction won't fail.
            </Alert>
          ) : null}
        </>
      )}
    </div>
  );
}
