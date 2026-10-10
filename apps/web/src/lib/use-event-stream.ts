"use client";

import { useEffect, useRef, useState } from "react";
import { api, eventStreamUrl } from "./api/client";
import type { WorkflowEvent } from "./api/types";

export type StreamState = "connecting" | "live" | "polling" | "ended" | "error";

/**
 * Loads a workflow's event history, then follows it over Server-Sent Events.
 * Events are de-duplicated by sequence number. If the stream fails repeatedly
 * the hook falls back to polling the JSON endpoint.
 */
export function useEventStream(workflowId: string, enabled = true) {
  // State is tagged with the workflow it belongs to, so switching workflows never shows stale events.
  const [stream, setStream] = useState<{ id: string; events: WorkflowEvent[]; state: StreamState }>({
    id: workflowId,
    events: [],
    state: "connecting",
  });
  const lastSeq = useRef(0);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let source: EventSource | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let failures = 0;
    lastSeq.current = 0;
    const setState = (state: StreamState) =>
      setStream((prev) => (prev.id === workflowId ? { ...prev, state } : { id: workflowId, events: [], state }));

    const append = (incoming: WorkflowEvent[]) => {
      const fresh = incoming.filter((e) => e.seq > lastSeq.current).sort((a, b) => a.seq - b.seq);
      if (fresh.length === 0) return;
      lastSeq.current = fresh[fresh.length - 1].seq;
      setStream((prev) =>
        prev.id === workflowId ? { ...prev, events: [...prev.events, ...fresh] } : { id: workflowId, events: fresh, state: prev.state },
      );
    };

    const poll = async () => {
      try {
        append(await api.workflows.events(workflowId, lastSeq.current));
      } catch {
        if (!cancelled) setState("error");
      }
    };

    const startPolling = () => {
      setState("polling");
      void poll();
      pollTimer = setInterval(poll, 3000);
    };

    const connect = () => {
      if (cancelled) return;
      source = new EventSource(eventStreamUrl(workflowId, lastSeq.current));
      source.onopen = () => {
        failures = 0;
        setState("live");
      };
      source.addEventListener("workflow_event", (msg) => {
        try {
          append([JSON.parse((msg as MessageEvent<string>).data) as WorkflowEvent]);
        } catch {
          /* ignore malformed frame */
        }
      });
      source.addEventListener("end", () => {
        source?.close();
        setState("ended");
      });
      source.onerror = () => {
        source?.close();
        failures += 1;
        if (cancelled) return;
        if (failures >= 3) startPolling();
        else setTimeout(connect, 1000 * failures);
      };
    };

    (async () => {
      await poll(); // history first, so the stream only needs to send what's new
      if (!cancelled) {
        if (typeof EventSource === "undefined") startPolling();
        else connect();
      }
    })();

    return () => {
      cancelled = true;
      source?.close();
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [workflowId, enabled]);

  const current = stream.id === workflowId ? stream : { events: [], state: "connecting" as StreamState };
  return { events: current.events, state: current.state };
}
