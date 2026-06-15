import type { InteractionGroup, InteractionProfile } from "../types";

export const MAX_INTERACTION_COMBOS = 12;

/**
 * Normalize a possibly-partial/empty/null interaction_profile into a complete
 * object. The backend stores the column default as `{}` (and legacy specs may
 * send null), so the UI must never assume `groups`/`enabled` exist — reading
 * `.groups` off `{}` is `undefined` and crashes the render.
 */
export function normalizeInteractionProfile(
  profile: Partial<InteractionProfile> | null | undefined
): InteractionProfile {
  return {
    enabled: !!profile?.enabled,
    merge_variants: !!profile?.merge_variants,
    max_variant_combinations:
      profile?.max_variant_combinations ?? MAX_INTERACTION_COMBOS,
    groups: Array.isArray(profile?.groups) ? profile!.groups : [],
  };
}

/** Number of variant combinations = product of selected options per active group. */
export function countCombinations(groups: InteractionGroup[]): number {
  const active = (groups ?? []).filter((g) =>
    (g.options ?? []).some((o) => o.selected)
  );
  if (!active.length) return 0;
  return active.reduce(
    (acc, g) => acc * (g.options ?? []).filter((o) => o.selected).length,
    1
  );
}
