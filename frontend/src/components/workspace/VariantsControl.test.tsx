import "../../test/setupDom";
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderWithProviders } from "../../test/render";
import { VariantsControl } from "./VariantsControl";
import type { InteractionProfile } from "../../types";

// A page whose ONLY variation is a display toggle (e.g. Metric/Imperial) — the
// shape detection produces. Its options can be stored as selected even while the
// profile is disabled (detection draft), which previously triggered a false
// "currently on … producing mismatched rows" warning.
function displayToggleProfile(enabled: boolean): InteractionProfile {
  return {
    enabled,
    merge_variants: false,
    max_variant_combinations: 12,
    groups: [
      {
        label: "Option",
        metadata_key: "unit_system",
        execution: "interactive",
        options: [
          { id: "metric", label: "Metric", selected: true, field_selectors: {}, recipe: [] },
          {
            id: "imperial",
            label: "Imperial",
            selected: true,
            field_selectors: {},
            recipe: [{ action: "click", by: "text", value: "Imperial" }],
          },
        ],
      },
    ],
  };
}

describe("VariantsControl display-toggle warning", () => {
  it("does not warn 'currently on' when the profile is disabled", () => {
    const { container } = renderWithProviders(
      <VariantsControl
        profile={displayToggleProfile(false)}
        onDetect={() => {}}
        onSave={() => {}}
      />
    );
    const text = container.textContent ?? "";
    assert.ok(!text.includes("currently on"), "must not claim the toggle is on");
    assert.ok(
      !text.includes("Turn off display toggles"),
      "must not offer to turn off a toggle that is not applied"
    );
    assert.ok(text.includes("off by default"), "explains the toggle is off");
  });

  it("warns 'currently on' only when the profile is enabled", () => {
    const { container } = renderWithProviders(
      <VariantsControl
        profile={displayToggleProfile(true)}
        onDetect={() => {}}
        onSave={() => {}}
      />
    );
    const text = container.textContent ?? "";
    assert.ok(text.includes("currently on"), "warns when actually applied");
    assert.ok(text.includes("Turn off display toggles"), "offers the turn-off action");
  });
});
