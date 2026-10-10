import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "./route";

const ctx = (path: string[]) => ({ params: Promise.resolve({ path }) }) as never;

describe("API proxy route", () => {
  beforeEach(() => {
    process.env.ORCHESTRA_API_TOKEN = "server-side-secret-token";
    process.env.ORCHESTRA_API_URL = "http://api.internal:8000";
  });
  afterEach(() => {
    delete process.env.ORCHESTRA_API_TOKEN;
  });

  it("attaches the server-side token and forwards the path and query", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("[]", { headers: { "content-type": "application/json", "set-cookie": "x=1" } }));
    const res = await GET(new NextRequest("http://web.local/api/orchestra/workflows?limit=5", { headers: { authorization: "Bearer browser-supplied" } }), ctx(["workflows"]));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://api.internal:8000/api/workflows?limit=5");
    expect(new Headers(init?.headers).get("authorization")).toBe("Bearer server-side-secret-token");
    expect(res.status).toBe(200);
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("rejects cross-origin writes", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const req = new NextRequest("http://web.local/api/orchestra/approvals/a/approve", {
      method: "POST",
      headers: { origin: "http://evil.example", host: "web.local" },
      body: "{}",
    });
    const res = await POST(req, ctx(["approvals", "a", "approve"]));
    expect(res.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects path traversal", async () => {
    const res = await GET(new NextRequest("http://web.local/api/orchestra/x"), ctx(["..", "admin"]));
    expect(res.status).toBe(400);
  });

  it("fails closed without a configured token", async () => {
    delete process.env.ORCHESTRA_API_TOKEN;
    const res = await GET(new NextRequest("http://web.local/api/orchestra/health"), ctx(["health"]));
    expect(res.status).toBe(500);
  });

  it("returns 502 when the API is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("connect ECONNREFUSED"));
    const res = await GET(new NextRequest("http://web.local/api/orchestra/health"), ctx(["health"]));
    expect(res.status).toBe(502);
  });
});
