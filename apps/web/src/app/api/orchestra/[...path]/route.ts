/**
 * Same-origin proxy to the Orchestra API.
 *
 * The API token lives only in this server process (ORCHESTRA_API_TOKEN) and is
 * attached here; the browser never sees it. Responses are streamed through
 * unchanged so Server-Sent Events work end to end.
 */
import type { NextRequest } from "next/server";

const FORWARD_REQUEST_HEADERS = ["accept", "content-type", "last-event-id", "x-request-id"];
const FORWARD_RESPONSE_HEADERS = ["content-type", "cache-control", "x-request-id", "retry-after"];
const SEGMENT = /^[A-Za-z0-9_.-]+$/;

async function proxy(request: NextRequest, ctx: RouteContext<"/api/orchestra/[...path]">): Promise<Response> {
  const { path } = await ctx.params;
  if (!path.every((segment) => SEGMENT.test(segment) && segment !== ".." && segment !== ".")) {
    return Response.json({ error: "bad_request", detail: "Invalid path" }, { status: 400 });
  }

  // Reject cross-site writes: browsers always send Origin on cross-origin POST/PUT.
  if (request.method !== "GET") {
    const origin = request.headers.get("origin");
    if (origin && new URL(origin).host !== request.headers.get("host")) {
      return Response.json({ error: "forbidden", detail: "Cross-origin request rejected" }, { status: 403 });
    }
  }

  const token = process.env.ORCHESTRA_API_TOKEN;
  if (!token) {
    return Response.json(
      { error: "misconfigured", detail: "ORCHESTRA_API_TOKEN is not set on the web server" },
      { status: 500 },
    );
  }

  const headers = new Headers({ Authorization: `Bearer ${token}` });
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const apiUrl = process.env.ORCHESTRA_API_URL ?? "http://127.0.0.1:8000";
  const target = new URL(`/api/${path.join("/")}${request.nextUrl.search}`, apiUrl);
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" ? undefined : await request.text(),
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return Response.json(
      { error: "upstream_unavailable", detail: `Cannot reach the Orchestra API at ${apiUrl}` },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  if (responseHeaders.get("content-type")?.startsWith("text/event-stream")) {
    responseHeaders.set("X-Accel-Buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export { proxy as GET, proxy as POST, proxy as PUT };
