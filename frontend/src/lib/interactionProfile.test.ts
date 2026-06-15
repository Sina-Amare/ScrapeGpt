import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  MAX_INTERACTION_COMBOS,
  countCombinations,
  normalizeInteractionProfile,
} from "./interactionProfile";

// ---------------------------------------------------------------------------
// normalizeInteractionProfile — must tolerate the backend's `{}` column default,
// null (legacy), and partial objects without crashing (regression: a project
// page went blank because `{}` had no `groups` and `.filter` threw).
// ---------------------------------------------------------------------------

describe("normalizeInteractionProfile", () => {
  it("fills defaults for the empty-object backend default", () => {
    const p = normalizeInteractionProfile({} as never);
    assert.equal(p.enabled, false);
    assert.equal(p.merge_variants, false);
    assert.equal(p.max_variant_combinations, MAX_INTERACTION_COMBOS);
    assert.deepEqual(p.groups, []);
  });

  it("handles null and undefined", () => {
    for (const input of [null, undefined]) {
      const p = normalizeInteractionProfile(input);
      assert.equal(p.enabled, false);
      assert.deepEqual(p.groups, []);
    }
  });

  it("preserves a real profile", () => {
    const p = normalizeInteractionProfile({
      enabled: true,
      merge_variants: true,
      max_variant_combinations: 8,
      groups: [
        {
          label: "Unit",
          metadata_key: "unit_system",
          execution: "interactive",
          options: [
            { id: "m", label: "Metric", selected: true, field_selectors: {}, recipe: [] },
          ],
        },
      ],
    });
    assert.equal(p.enabled, true);
    assert.equal(p.merge_variants, true);
    assert.equal(p.max_variant_combinations, 8);
    assert.equal(p.groups.length, 1);
  });
});

describe("countCombinations", () => {
  it("returns 0 for no groups / no selection", () => {
    assert.equal(countCombinations([]), 0);
    assert.equal(
      countCombinations([
        {
          label: "x",
          metadata_key: "x",
          execution: "interactive",
          options: [{ id: "a", label: "A", selected: false, field_selectors: {}, recipe: [] }],
        },
      ]),
      0
    );
  });

  it("multiplies selected options across active groups", () => {
    const groups = [
      {
        label: "g1",
        metadata_key: "g1",
        execution: "deterministic" as const,
        options: [
          { id: "a", label: "A", selected: true, field_selectors: {}, recipe: [] },
          { id: "b", label: "B", selected: true, field_selectors: {}, recipe: [] },
        ],
      },
      {
        label: "g2",
        metadata_key: "g2",
        execution: "interactive" as const,
        options: [
          { id: "c", label: "C", selected: true, field_selectors: {}, recipe: [] },
          { id: "d", label: "D", selected: false, field_selectors: {}, recipe: [] },
        ],
      },
    ];
    assert.equal(countCombinations(groups), 2); // 2 x 1
  });
});
