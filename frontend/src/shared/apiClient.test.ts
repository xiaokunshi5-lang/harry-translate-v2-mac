import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  addSessionGlossaryTerm,
  clearSessionGlossaryTerms,
  getConfig,
  promoteGlossaryTerm,
  saveApiKey,
} from "./apiClient";

describe("apiClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads aliased config without exposing a plaintext key", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            settings: { active_provider: "qwen" },
            user_settings: { active_provider: "qwen" },
            key_mask: "sk-****1234",
            api_key_masked: "sk-****1234",
            needs_setup: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const config = await getConfig();
    expect(config.api_key_masked).toBe("sk-****1234");
    expect(JSON.stringify(config)).not.toContain("sk-real");
  });

  it("maps structured backend failures to ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: "unsupported_language_pair" }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(saveApiKey("sk-test-1234")).rejects.toMatchObject({
      status: 400,
      code: "unsupported_language_pair",
    });
  });

  it("injects session term, maps promote 500 error, and clears session", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: true,
            item: {
              source: "黄仁勋",
              target: "Jason Huang",
              category: "terms_zh_to_en",
              priority: 0,
              scope: "session",
              pack_id: "session",
              bucket: "terms_trans",
              lang_key: "zh->en",
            },
            session_items: [],
            pushed_channels: ["main"],
            elapsed_ms: 0.4,
            quota: {
              cloud_cap: 250,
              provider_limit: 1000,
              active_terms: 1,
              local_terms: 1,
              wire_bytes: 32,
              wire_budget_bytes: 4096,
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ error: "glossary_persist_failed", detail: "locked" }),
          { status: 500, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: true,
            cleared: 1,
            session_items: [],
            pushed_channels: ["main"],
            quota: {
              cloud_cap: 250,
              provider_limit: 1000,
              active_terms: 0,
              local_terms: 0,
              wire_bytes: 2,
              wire_budget_bytes: 4096,
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const added = await addSessionGlossaryTerm({
      source: "黄仁勋",
      target: "Jason Huang",
      bucket: "terms_trans",
      lang_key: "zh->en",
    });
    expect(added.pushed_channels).toEqual(["main"]);
    expect(added.item.bucket).toBe("terms_trans");

    await expect(
      promoteGlossaryTerm({
        source: "黄仁勋",
        bucket: "terms_trans",
        lang_key: "zh->en",
      }),
    ).rejects.toMatchObject({
      status: 500,
      code: "glossary_persist_failed",
    });

    const cleared = await clearSessionGlossaryTerms();
    expect(cleared.cleared).toBe(1);
    expect(ApiError).toBeDefined();
  });
});
