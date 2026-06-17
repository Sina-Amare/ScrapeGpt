import "../test/setupDom";
import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it } from "node:test";
import { ApiError, api, apiErrorCode, apiRequest, setAccessToken } from "./api";

type FetchCall = [RequestInfo | URL, RequestInit | undefined];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

describe("api client", () => {
  const originalFetch = globalThis.fetch;
  let calls: FetchCall[] = [];

  beforeEach(() => {
    window.localStorage.clear();
    setAccessToken(null);
    calls = [];
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("login sends form-url-encoded payload", async () => {
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return jsonResponse({ access_token: "a", refresh_token: "r", token_type: "bearer" });
    };

    await api.login("user@example.com", "password123");

    const [, init] = calls[0];
    const headers = new Headers(init?.headers);
    assert.equal(calls[0][0], "/api/v1/auth/login");
    assert.equal(headers.get("Content-Type"), "application/x-www-form-urlencoded");
    assert.equal(String(init?.body), "username=user%40example.com&password=password123");
  });

  it("refresh retry happens once on 401", async () => {
    window.localStorage.setItem("scrapegpt_refresh_token", "refresh-token");
    setAccessToken("old-access");
    const responses = [
      jsonResponse({ detail: "expired" }, 401),
      jsonResponse({
          access_token: "new-access",
          refresh_token: "new-refresh",
          token_type: "bearer"
        }),
      jsonResponse([])
    ];
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return responses.shift() ?? jsonResponse([]);
    };

    const providers = await api.listProviders();

    assert.deepEqual(providers, []);
    assert.equal(calls.length, 3);
    assert.equal(calls[1][0], "/api/v1/auth/refresh");
    assert.equal(new Headers(calls[2][1]?.headers).get("Authorization"), "Bearer new-access");
    assert.equal(window.localStorage.getItem("scrapegpt_refresh_token"), "new-refresh");
  });

  it("throws 429 details as readable errors", async () => {
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return jsonResponse({ detail: "slow down" }, 429);
    };

    await assert.rejects(apiRequest("/anything"), /slow down/);
  });

  it("surfaces scrape active-limit conflicts", async () => {
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return jsonResponse(
        {
          detail: {
            message: "Active scraping task limit reached",
            error_type: "TOO_MANY_ACTIVE_TASKS"
          }
        },
        409
      );
    };

    await assert.rejects(api.startScrape("https://example.com"), /Active scraping task/);
  });

  it("surfaces provider write conflicts", async () => {
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return jsonResponse({ detail: "Provider configuration conflict" }, 409);
    };

    await assert.rejects(
      api.createProvider({
        name: "OpenAI",
        provider: "openai",
        model: "gpt-4o-mini",
        api_key: "secret"
      }),
      /Provider configuration conflict/
    );
  });

  it("reveals provider keys with password-confirmed POST", async () => {
    globalThis.fetch = async (input, init) => {
      calls.push([input, init]);
      return jsonResponse({ api_key: "revealed-secret" });
    };

    const result = await api.revealProviderKey(7, { password: "correct-password" });

    assert.equal(result.api_key, "revealed-secret");
    assert.equal(calls[0][0], "/api/v1/providers/7/reveal-key");
    assert.equal(calls[0][1]?.method, "POST");
    assert.equal(String(calls[0][1]?.body), '{"password":"correct-password"}');
  });

  it("apiErrorCode pulls error_code from a structured detail body", () => {
    const err = new ApiError(400, {
      detail: { message: "The browser closed unexpectedly", error_code: "BROWSER_DRIVER_CRASHED" }
    });
    assert.equal(apiErrorCode(err), "BROWSER_DRIVER_CRASHED");
  });

  it("apiErrorCode returns null for a plain string detail or non-ApiError", () => {
    assert.equal(apiErrorCode(new ApiError(400, { detail: "Preview failed" })), null);
    assert.equal(apiErrorCode(new Error("nope")), null);
  });
});
