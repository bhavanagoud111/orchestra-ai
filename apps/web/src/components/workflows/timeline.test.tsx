import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { event } from "@/test/fixtures";
import { Timeline } from "./timeline";
import { nodeStatesFromEvents } from "./workflow-graph";

const events = [
  event(1, "stage_started", "debug started"),
  event(2, "tool_call", "debugging → repo.read_file"),
  event(3, "agent_output", "Debugging produced DebugReport", { simulated: true, payload: { reasoning: "demo-heuristic" } }),
  event(4, "security_warning", "Possible prompt injection in issue #15", { payload: { findings: [] } }),
  event(5, "stage_completed", "debug completed", { duration_ms: 1234 }),
];

describe("Timeline", () => {
  it("renders events in order with simulated labels", () => {
    render(<Timeline events={events} state="live" />);
    const items = within(screen.getByRole("list", { name: /execution timeline/i })).getAllByRole("listitem");
    expect(items).toHaveLength(5);
    expect(items[2]).toHaveTextContent("Demo reasoning");
    expect(items[4]).toHaveTextContent("1.2s");
    expect(screen.getByText("Live")).toBeInTheDocument();
  });

  it("filters by category", async () => {
    render(<Timeline events={events} />);
    await userEvent.click(screen.getByRole("button", { name: /problems/i }));
    const items = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent("prompt injection");
    await userEvent.click(screen.getByRole("button", { name: /tool calls/i }));
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(1);
  });

  it("expands payload details on demand", async () => {
    render(<Timeline events={events} />);
    const [details] = screen.getAllByRole("button", { name: "Details" });
    await userEvent.click(details);
    expect(screen.getByText(/demo-heuristic/)).toBeInTheDocument();
  });

  it("shows a waiting state before any events arrive", () => {
    render(<Timeline events={[]} state="connecting" />);
    expect(screen.getByText(/waiting for the first event/i)).toBeInTheDocument();
  });
});

describe("nodeStatesFromEvents", () => {
  it("derives active, done, waiting and failed nodes", () => {
    const states = nodeStatesFromEvents(
      [
        { type: "stage_started", node: "classify" },
        { type: "stage_completed", node: "classify" },
        { type: "stage_started", node: "approval_gate" },
        { type: "approval_requested", node: "approval_gate" },
      ],
      "waiting_for_approval",
    );
    expect(states).toMatchObject({ __start__: "done", classify: "done", approval_gate: "waiting" });
    const failed = nodeStatesFromEvents([{ type: "stage_started", node: "plan" }], "failed");
    expect(failed.plan).toBe("failed");
    const done = nodeStatesFromEvents([{ type: "stage_started", node: "report" }], "completed");
    expect(done).toMatchObject({ report: "done", __end__: "done" });
  });
});
