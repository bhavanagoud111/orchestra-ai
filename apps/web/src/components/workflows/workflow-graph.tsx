"use client";

import "@xyflow/react/dist/style.css";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  applyNodeChanges,
  type NodeChange,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import clsx from "clsx";
import { useCallback, useMemo, useState } from "react";
import type { GraphTopology } from "@/lib/api/types";
import { humanize } from "@/lib/format";

export type NodeState = "idle" | "active" | "done" | "failed" | "waiting" | "on-route";

const COL = 175;
const ROW = 140;

// Two bands: intake pipeline on top, specialists and execution below.
const POSITIONS: Record<string, [number, number]> = {
  __start__: [0, 0],
  classify: [1, 0],
  retrieve_context: [2, 0],
  plan: [3, 0],
  dispatch: [4, 0],
  debug: [0.5, 1],
  review: [1.5, 1],
  verify: [2.5, 1],
  propose_actions: [3.5, 1],
  approval_gate: [4.5, 1],
  execute_actions: [5.5, 1],
  report: [6.5, 1],
  __end__: [7.5, 1],
};

const AGENT_LABEL: Record<string, string> = {
  orchestrator: "Orchestrator",
  repository_intelligence: "Repository Intelligence",
  planning: "Planning",
  debugging: "Debugging",
  code_review: "Code Review",
  verification: "Verification",
  automation: "Automation",
  reporting: "Reporting",
};

type StageData = { label: string; agent: string | null; state: NodeState; terminal: boolean };

function StageNode({ data }: NodeProps<Node<StageData>>) {
  return (
    <div
      className={clsx(
        "rounded-md border bg-panel px-3 py-2 text-left shadow-sm transition-colors",
        data.terminal ? "w-24 text-center" : "w-36",
        data.state === "idle" && "border-border",
        data.state === "on-route" && "border-accent/60 bg-accent-soft",
        data.state === "done" && "border-ok/50",
        data.state === "active" && "border-info ring-2 ring-info/30",
        data.state === "waiting" && "border-warn ring-2 ring-warn/25",
        data.state === "failed" && "border-err ring-2 ring-err/25",
      )}
      data-state={data.state}
    >
      <Handle type="target" position={Position.Left} className="!size-1.5 !border-0 !bg-border-strong" />
      <div className="flex items-center gap-1.5">
        {!data.terminal && (
          <span
            aria-hidden
            className={clsx(
              "size-1.5 shrink-0 rounded-full",
              data.state === "done" && "bg-ok",
              data.state === "active" && "bg-info pulse-dot",
              data.state === "waiting" && "bg-warn",
              data.state === "failed" && "bg-err",
              (data.state === "idle" || data.state === "on-route") && "bg-faint",
            )}
          />
        )}
        <span className="truncate text-[12px] font-medium text-text">{data.label}</span>
      </div>
      {data.agent && <div className="mt-0.5 truncate text-[10px] text-muted">{AGENT_LABEL[data.agent] ?? data.agent}</div>}
      <Handle type="source" position={Position.Right} className="!size-1.5 !border-0 !bg-border-strong" />
    </div>
  );
}

const nodeTypes = { stage: StageNode };

function buildNodes(topology: GraphTopology, states: Record<string, NodeState>, route?: string[]): Node<StageData>[] {
  const onRoute = new Set(route ?? []);
  let fallbackCol = 0;
  return topology.nodes.map((n) => {
    const [x, y] = POSITIONS[n.id] ?? [fallbackCol++, 2];
    const terminal = n.id === "__start__" || n.id === "__end__";
    const state = states[n.id] ?? (onRoute.has(n.id) ? "on-route" : "idle");
    return {
      id: n.id,
      type: "stage",
      position: { x: x * COL, y: y * ROW },
      data: { label: terminal ? (n.id === "__start__" ? "Start" : "End") : humanize(n.id), agent: n.agent, state, terminal },
      draggable: false,
      connectable: false,
    };
  });
}

function buildEdges(topology: GraphTopology, states: Record<string, NodeState>, route?: string[]): Edge[] {
  const onRoute = new Set(route ?? []);
  const visited = (id: string) => ["done", "active", "waiting", "failed"].includes(states[id] ?? "") || onRoute.has(id);
  return topology.edges.map((e) => {
    const isBack = e.source === "verify" && e.target === "debug";
    const lit = visited(e.source) && visited(e.target);
    return {
      id: `${e.source}->${e.target}`,
      source: e.source,
      target: e.target,
      type: "smoothstep",
      animated: states[e.target] === "active",
      label: isBack ? "revise" : undefined,
      labelStyle: { fill: "var(--muted)", fontSize: 10 },
      labelBgStyle: { fill: "var(--panel)" },
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: lit ? "var(--accent)" : "var(--border-strong)" },
      style: {
        stroke: lit ? "var(--accent)" : "var(--border-strong)",
        strokeWidth: lit ? 1.6 : 1,
        strokeDasharray: e.conditional ? "4 4" : undefined,
      },
    };
  });
}

export function WorkflowGraph({
  topology,
  states = {},
  route,
  height = 340,
  interactive = true,
}: {
  topology: GraphTopology;
  states?: Record<string, NodeState>;
  route?: string[];
  height?: number;
  interactive?: boolean;
}) {
  // Nodes live in React Flow-managed state so measured sizes survive updates. Rebuilding node
  // objects on every status change would drop those measurements and leave nodes hidden.
  const nodeData = useMemo(() => buildNodes(topology, states, route), [topology, states, route]);
  const [nodes, setNodes] = useState(nodeData);
  const [synced, setSynced] = useState(nodeData);
  if (synced !== nodeData) {
    // Adjust state during render when inputs change (no effect, no extra paint).
    setSynced(nodeData);
    setNodes((prev) => {
      const byId = new Map(nodeData.map((n) => [n.id, n]));
      const sameIds = prev.length === nodeData.length && prev.every((n) => byId.has(n.id));
      return sameIds ? prev.map((n) => ({ ...n, data: byId.get(n.id)!.data })) : nodeData;
    });
  }
  const onNodesChange = useCallback((changes: NodeChange<Node<StageData>>[]) => setNodes((ns) => applyNodeChanges(changes, ns)), []);
  const edges = useMemo(() => buildEdges(topology, states, route), [topology, states, route]);

  return (
    <div style={{ height }} className="w-full" aria-label="Workflow graph" role="figure">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        minZoom={0.3}
        maxZoom={1.5}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag={interactive}
        zoomOnScroll={false}
        zoomOnPinch={interactive}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} color="var(--border)" />
        {interactive && <Controls showInteractive={false} position="bottom-right" />}
      </ReactFlow>
    </div>
  );
}

/** Derive per-node execution state from a workflow's event timeline. */
export function nodeStatesFromEvents(
  events: { type: string; node: string | null }[],
  workflowStatus: string,
): Record<string, NodeState> {
  const states: Record<string, NodeState> = {};
  for (const e of events) {
    if (!e.node) continue;
    if (e.type === "stage_started") states[e.node] = "active";
    else if (e.type === "stage_completed") states[e.node] = "done";
    else if (e.type === "stage_failed") states[e.node] = "failed";
    else if (e.type === "approval_requested") states[e.node] = "waiting";
  }
  if (workflowStatus === "waiting_for_approval" && states.approval_gate) states.approval_gate = "waiting";
  if (["completed", "failed", "cancelled"].includes(workflowStatus)) {
    for (const [node, s] of Object.entries(states)) if (s === "active" || s === "waiting") states[node] = workflowStatus === "completed" ? "done" : "failed";
    if (workflowStatus === "completed") {
      states.__start__ = "done";
      states.__end__ = "done";
    }
  } else if (Object.keys(states).length) {
    states.__start__ = "done";
  }
  return states;
}
