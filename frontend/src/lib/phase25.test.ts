import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { buildColumns } from "./recordColumns";
import { reasonCodeCopy } from "./frontierReasonCopy";
import { qualityStateInfo, isScopeNotConfirmedError } from "./qualityCopy";
import {
  scopeModeLabel,
  scopeModeInfo,
  requiresConfirmation,
  isUserConfirmed,
  SCOPE_MODE_ORDER,
} from "./scopeCopy";
import { ApiError } from "./api";
import type { FieldSpec } from "../types";

// ---------------------------------------------------------------------------
// scopeCopy
// ---------------------------------------------------------------------------

describe("scopeCopy — mode labels", () => {
  it("CURRENT_PAGE renders user label", () => {
    assert.equal(scopeModeLabel("CURRENT_PAGE"), "This page only");
  });

  it("PAGINATION renders user label", () => {
    assert.equal(scopeModeLabel("PAGINATION"), "Paginated list");
  });

  it("COLLECTION renders user label", () => {
    assert.equal(scopeModeLabel("COLLECTION"), "Related list pages");
  });

  it("DATASET renders user label", () => {
    assert.equal(scopeModeLabel("DATASET"), "Listing + detail pages");
  });

  it("FULL_SITE renders user label", () => {
    assert.equal(scopeModeLabel("FULL_SITE"), "Entire website");
  });

  it("raw enum names are never the label for known modes", () => {
    for (const mode of SCOPE_MODE_ORDER) {
      assert.notEqual(scopeModeLabel(mode), mode);
    }
  });

  it("unknown mode degrades gracefully", () => {
    assert.equal(scopeModeLabel("UNKNOWN_MODE"), "UNKNOWN_MODE");
  });
});

describe("scopeCopy — descriptions", () => {
  it("each known mode has a non-empty description", () => {
    for (const mode of SCOPE_MODE_ORDER) {
      assert.ok(scopeModeInfo(mode).description.length > 0);
    }
  });

  it("FULL_SITE has warnStrong = true", () => {
    assert.ok(scopeModeInfo("FULL_SITE").warnStrong);
  });

  it("CURRENT_PAGE has warnStrong = false", () => {
    assert.ok(!scopeModeInfo("CURRENT_PAGE").warnStrong);
  });
});

describe("scopeCopy — confirmation requirements", () => {
  it("CURRENT_PAGE does not require confirmation", () => {
    assert.ok(!requiresConfirmation("CURRENT_PAGE"));
  });

  it("PAGINATION requires confirmation", () => {
    assert.ok(requiresConfirmation("PAGINATION"));
  });

  it("COLLECTION requires confirmation", () => {
    assert.ok(requiresConfirmation("COLLECTION"));
  });

  it("DATASET requires confirmation", () => {
    assert.ok(requiresConfirmation("DATASET"));
  });

  it("FULL_SITE requires confirmation", () => {
    assert.ok(requiresConfirmation("FULL_SITE"));
  });
});

describe("scopeCopy — isUserConfirmed", () => {
  it("USER_CONFIRMED returns true", () => {
    assert.ok(isUserConfirmed("USER_CONFIRMED"));
  });

  it("AI_SUGGESTED returns false", () => {
    assert.ok(!isUserConfirmed("AI_SUGGESTED"));
  });

  it("SYSTEM_DEFAULTED returns false", () => {
    assert.ok(!isUserConfirmed("SYSTEM_DEFAULTED"));
  });

  it("undefined returns false", () => {
    assert.ok(!isUserConfirmed(undefined));
  });
});

// ---------------------------------------------------------------------------
// frontierReasonCopy
// ---------------------------------------------------------------------------

describe("reasonCodeCopy — known codes", () => {
  it("SEED_URL maps to user copy", () => {
    assert.equal(reasonCodeCopy("SEED_URL"), "Starting page");
  });

  it("EXCLUDED_DIFFERENT_ORIGIN maps to user copy", () => {
    assert.equal(reasonCodeCopy("EXCLUDED_DIFFERENT_ORIGIN"), "Different website");
  });

  it("PAGINATION_URL_PATTERN maps to user copy", () => {
    assert.equal(reasonCodeCopy("PAGINATION_URL_PATTERN"), "Looks like another page in this list");
  });

  it("COLLECTION_PATTERN_MATCH maps to user copy", () => {
    assert.equal(reasonCodeCopy("COLLECTION_PATTERN_MATCH"), "Related list page in this collection");
  });

  it("EXCLUDED_PAGE_LIMIT maps to user copy", () => {
    assert.equal(reasonCodeCopy("EXCLUDED_PAGE_LIMIT"), "Outside the safety limit");
  });
});

describe("reasonCodeCopy — unknown code fallback", () => {
  it("unknown code returns generic fallback", () => {
    assert.equal(reasonCodeCopy("SOME_FUTURE_CODE"), "Classified by crawl rules");
  });

  it("empty string returns fallback", () => {
    assert.equal(reasonCodeCopy(""), "Classified by crawl rules");
  });
});

// ---------------------------------------------------------------------------
// qualityCopy
// ---------------------------------------------------------------------------

describe("qualityStateInfo — known states", () => {
  it("good state", () => {
    const info = qualityStateInfo("good");
    assert.equal(info.tone, "success");
    assert.ok(info.label.length > 0);
  });

  it("needs_review state", () => {
    const info = qualityStateInfo("needs_review");
    assert.equal(info.tone, "warning");
    assert.ok(info.label.length > 0);
  });

  it("risky state", () => {
    const info = qualityStateInfo("risky");
    assert.equal(info.tone, "danger");
    assert.ok(info.label.length > 0);
  });

  it("unknown state", () => {
    const info = qualityStateInfo("unknown");
    assert.equal(info.tone, "neutral");
  });

  it("unrecognized value falls back to unknown", () => {
    const info = qualityStateInfo("totally_new_state");
    assert.equal(info.tone, "neutral");
  });
});

describe("isScopeNotConfirmedError", () => {
  it("detects SCOPE_NOT_CONFIRMED in ApiError.detail.detail.error_code", () => {
    const err = new ApiError(409, {
      detail: { error_code: "SCOPE_NOT_CONFIRMED", message: "scope not confirmed" }
    });
    assert.ok(isScopeNotConfirmedError(err));
  });

  it("returns false for other error codes", () => {
    const err = new ApiError(409, {
      detail: { error_code: "ACTIVE_JOB_LIMIT_REACHED" }
    });
    assert.ok(!isScopeNotConfirmedError(err));
  });

  it("returns false for non-ApiError", () => {
    assert.ok(!isScopeNotConfirmedError(new Error("generic")));
    assert.ok(!isScopeNotConfirmedError("string error"));
    assert.ok(!isScopeNotConfirmedError(null));
  });
});

// ---------------------------------------------------------------------------
// recordColumns
// ---------------------------------------------------------------------------

function makeField(name: string, selected = true): FieldSpec {
  return {
    name,
    label: name,
    user_label: null,
    selector: null,
    type: "string",
    selected,
    required: false,
    confidence: null,
    sample_values: [],
    warnings: [],
  };
}

describe("buildColumns", () => {
  it("returns spec field names in spec order", () => {
    const specFields = [makeField("price"), makeField("title"), makeField("url")];
    const cols = buildColumns(specFields, []);
    assert.deepEqual(cols, ["price", "title", "url"]);
  });

  it("excludes unselected fields from spec", () => {
    const specFields = [makeField("price", true), makeField("title", false)];
    const cols = buildColumns(specFields, []);
    assert.deepEqual(cols, ["price"]);
  });

  it("appends page columns not already in spec", () => {
    const specFields = [makeField("price")];
    const cols = buildColumns(specFields, ["price", "rating", "reviews"]);
    assert.deepEqual(cols, ["price", "rating", "reviews"]);
  });

  it("does not duplicate spec columns even if in pageColumns", () => {
    const specFields = [makeField("price"), makeField("title")];
    const cols = buildColumns(specFields, ["title", "extra"]);
    assert.deepEqual(cols, ["price", "title", "extra"]);
  });

  it("uses user_label when set", () => {
    const field: FieldSpec = {
      ...makeField("price_internal"),
      user_label: "Price",
    };
    const cols = buildColumns([field], []);
    assert.deepEqual(cols, ["Price"]);
  });

  it("handles null specFields gracefully", () => {
    const cols = buildColumns(null, ["col_a", "col_b"]);
    assert.deepEqual(cols, ["col_a", "col_b"]);
  });

  it("handles undefined specFields gracefully", () => {
    const cols = buildColumns(undefined, ["col_a"]);
    assert.deepEqual(cols, ["col_a"]);
  });

  it("handles empty both", () => {
    assert.deepEqual(buildColumns([], []), []);
  });
});
