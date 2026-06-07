import "../test/setupDom";
import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import { fireEvent } from "@testing-library/react";
import { DashboardPage } from "./DashboardPage";
import { renderWithProviders } from "../test/render";

type FetchCall = [RequestInfo | URL, RequestInit | undefined];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

describe("DashboardPage", () => {
  const originalFetch = globalThis.fetch;
  let calls: FetchCall[] = [];

  beforeEach(() => {
    window.localStorage.clear();
    calls = [];
    globalThis.fetch = originalFetch;
  });

  it("renders task history and allows viewing details", async () => {
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      const path = String(input);
      // Analysis jobs (primary section) — path includes query string e.g. /jobs?limit=20
      if (path.includes("/jobs") && !path.includes("/cancel") && !init?.method) {
        return jsonResponse([]);
      }
      if (path.endsWith("/scrape/tasks/current")) {
        return jsonResponse({ detail: "No active task" }, 404);
      }
      if (path.endsWith("/scrape/tasks/1") && !init?.method) {
        return jsonResponse({
          task_id: 1,
          state: "COMPLETED",
          url: "https://example.com",
          created_at: new Date().toISOString(),
          content_length: 500,
          result: {
            summary: "This is a mocked summary",
            key_points: ["Mock point 1", "Mock point 2"],
            data_type: "article",
            word_count: 50
          }
        });
      }
      if (path.endsWith("/scrape/tasks") && !init?.method) {
        return jsonResponse([
          {
            task_id: 1,
            state: "COMPLETED",
            url: "https://example.com",
            created_at: new Date().toISOString()
          }
        ]);
      }
      return jsonResponse({});
    };

    const view = renderWithProviders(<DashboardPage />);

    // Expand the legacy scrape collapsible section
    const legacyToggle = await view.findByText("Legacy scrape");
    fireEvent.click(legacyToggle);

    assert.ok(await view.findByText("https://example.com"));

    // Find and click the View Details button (1st action button)
    const viewButton = view.getByTitle("View Details");
    assert.ok(viewButton);
    fireEvent.click(viewButton);

    // Verify task detail modal loaded
    assert.ok(await view.findByText("Task #1 Details"));
    assert.ok(await view.findByText("This is a mocked summary"));
    assert.ok(await view.findByText("Mock point 1"));
  });

  it("allows deleting a completed task", async () => {
    let deleted = false;
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      const path = String(input);
      // Analysis jobs (primary section) — path includes query string e.g. /jobs?limit=20
      if (path.includes("/jobs") && !path.includes("/cancel") && !init?.method) {
        return jsonResponse([]);
      }
      if (path.endsWith("/scrape/tasks/current")) {
        return jsonResponse({ detail: "No active task" }, 404);
      }
      if (path.endsWith("/scrape/tasks/1") && init?.method === "DELETE") {
        deleted = true;
        return new Response(null, { status: 204 });
      }
      if (path.endsWith("/scrape/tasks") && !init?.method) {
        if (deleted) return jsonResponse([]);
        return jsonResponse([
          {
            task_id: 1,
            state: "COMPLETED",
            url: "https://example.com",
            created_at: new Date().toISOString()
          }
        ]);
      }
      return jsonResponse({});
    };

    const view = renderWithProviders(<DashboardPage />);

    // Expand the legacy scrape collapsible section
    const legacyToggle = await view.findByText("Legacy scrape");
    fireEvent.click(legacyToggle);

    assert.ok(await view.findByText("https://example.com"));

    // Find and click the Delete Task button (2nd action button)
    const deleteButton = view.getByTitle("Delete Task");
    assert.ok(deleteButton);
    fireEvent.click(deleteButton);

    // Verify confirmation modal renders
    assert.ok(await view.findByText(/Are you sure you want to delete Scrape Task/));

    // Confirm deletion
    const confirmButton = view
      .getAllByRole("button")
      .find((b) => b.textContent?.trim() === "Delete task");
    assert.ok(confirmButton);
    fireEvent.click(confirmButton);

    // Verify that the task is deleted and the list refetches/updates to empty state
    assert.ok(await view.findByText("No scrape tasks yet."));
    assert.equal(deleted, true);
  });
});
