import { describe, expect, it, vi } from "vitest";
import { jsonResponse } from "@/test/fixtures";
import { ApiError, api, eventStreamUrl } from "./client";

describe("api client", () => {
  it("calls the same-origin proxy with query parameters and no credentials", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ items: [], total: 0, limit: 5, offset: 0 }));
    await api.workflows.list({ status: "running", limit: 5, repository_id: undefined });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/orchestra/workflows?status=running&limit=5");
    expect(new Headers(init?.headers).get("authorization")).toBeNull();
  });

  it("sends JSON bodies for writes", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ id: "ap1" }));
    await api.approvals.reject("ap1", "not this way");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/orchestra/approvals/ap1/reject");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ reason: "not this way" });
  });

  it("raises ApiError with the server's detail and request id", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ error: "conflict", detail: "Approval is already approved", request_id: "req-1" }, 409),
    );
    const err = await api.approvals.approve("ap1").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "conflict", requestId: "req-1", message: "Approval is already approved" });
  });

  it("summarizes validation errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ error: "validation_error", detail: [{ loc: ["body", "request"], msg: "too short" }] }, 422),
    );
    await expect(api.workflows.create({ request: "x", repository_id: "r" })).rejects.toThrow("request: too short");
  });

  it("reports network failures distinctly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api.health()).rejects.toMatchObject({ status: 0, message: "Cannot reach the Orchestra API" });
  });

  it("builds stream URLs with a resume cursor", () => {
    expect(eventStreamUrl("wf 1", 7)).toBe("/api/orchestra/workflows/wf%201/events?stream=true&after=7");
  });
});
