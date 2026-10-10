import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { approval, jsonResponse } from "@/test/fixtures";
import { ApprovalCard } from "./approval-card";

describe("ApprovalCard", () => {
  it("shows risk, permissions, simulated label and the diff", () => {
    render(<ApprovalCard approval={approval()} />);
    expect(screen.getByText("medium risk")).toBeInTheDocument();
    expect(screen.getByText("Tests were not executed in the sandbox")).toBeInTheDocument();
    expect(screen.getByText("contents:write on orchestra/* branch")).toBeInTheDocument();
    expect(screen.getByText("Simulated")).toBeInTheDocument();
    expect(screen.getByText("+x = 2")).toBeInTheDocument();
  });

  it("approves and reports the decision", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(approval({ status: "approved", decided_by: "admin" })));
    const onDecided = vi.fn();
    render(<ApprovalCard approval={approval()} onDecided={onDecided} />);
    await userEvent.type(screen.getByPlaceholderText(/decision note/i), "looks right");
    await userEvent.click(screen.getByRole("button", { name: /approve/i }));
    await waitFor(() => expect(onDecided).toHaveBeenCalledWith(expect.objectContaining({ status: "approved" })));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/orchestra/approvals/ap1/approve");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ reason: "looks right" });
  });

  it("requires a reason before rejecting", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    render(<ApprovalCard approval={approval()} />);
    await userEvent.click(screen.getByRole("button", { name: /reject/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/give a reason/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces server errors and keeps the buttons usable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ error: "forbidden", detail: "Requires approver role" }, 403));
    render(<ApprovalCard approval={approval()} />);
    await userEvent.click(screen.getByRole("button", { name: /approve/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Requires approver role");
    expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled();
  });

  it("hides decision controls once decided", () => {
    render(<ApprovalCard approval={approval({ status: "rejected", decided_by: "approver", decision_reason: "too broad" })} />);
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
    expect(screen.getByText(/rejected by approver/)).toBeInTheDocument();
    expect(screen.getByText("too broad")).toBeInTheDocument();
  });
});
