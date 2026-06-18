import type { InteractionGroup } from "../types";

export type VariantCopy = { title: string; help?: string };

function humanizeKey(key: string): string {
  const cleaned = (key || "").replace(/[_-]+/g, " ").trim();
  if (!cleaned) return "Page variation";
  return cleaned.replace(/\b\w/g, (c) => c.toUpperCase());
}

function labelsInclude(group: InteractionGroup, ...needles: string[]): boolean {
  const hay = group.options.map((o) => (o.label || "").toLowerCase());
  return needles.every((n) => hay.some((l) => l.includes(n)));
}

/**
 * Plain, user-facing copy for a detected variant group. The execution kind
 * (deterministic / interactive / mixed / url_param) NEVER surfaces here — it is
 * an implementation detail. Keyed on the stable metadata_key first, then on
 * option-label heuristics, with a clean humanized fallback.
 */
export function variantCopy(group: InteractionGroup): VariantCopy {
  const key = (group.metadata_key || "").toLowerCase();
  if (key === "serving_basis" || labelsInclude(group, "per 100", "serving")) {
    return {
      title: "Capture both per-100g and per-serving values",
      help: "Each item gets a labelled row for every serving basis you keep.",
    };
  }
  if (key === "unit_system" || labelsInclude(group, "metric", "imperial")) {
    return {
      title: "Capture metric and imperial units",
      help: "Each item gets a labelled row for every unit system you keep.",
    };
  }
  if (key === "currency") {
    return {
      title: "Currencies to capture",
      help: "Each item gets a labelled row for every currency you keep.",
    };
  }
  return {
    title: humanizeKey(group.metadata_key || group.label),
    help: "Capture each selected variation as its own labelled rows.",
  };
}

/**
 * Whether any selected option needs a browser to render — used only to show a
 * soft, non-technical note ("read by opening the page in a browser"). Mirrors the
 * `interactiveSelected` rule in InteractionsPanel.
 */
export function variantsUseBrowser(groups: InteractionGroup[]): boolean {
  return groups.some(
    (g) =>
      (g.execution === "interactive" || g.execution === "mixed") &&
      g.options.some((o) => o.selected && (o.recipe?.length ?? 0) > 0)
  );
}
