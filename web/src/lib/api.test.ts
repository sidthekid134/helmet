import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("API client", () => {
  it("fails explicitly when NEXT_PUBLIC_API_URL is absent", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");

    await expect(api.players()).rejects.toMatchObject({
      name: "ApiError",
      status: 0,
    });
  });

  it("uses the configured API URL and preserves empty data", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.helmet.test/");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ data: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.players()).resolves.toEqual({ data: [] });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.helmet.test/v1/players",
      expect.objectContaining({
        headers: expect.objectContaining({ Accept: "application/json" }),
      }),
    );
  });

  it("propagates API failure details", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.helmet.test");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ message: "League unavailable" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(api.lineup()).rejects.toMatchObject({
      message: "League unavailable",
      status: 503,
    });
  });
});
