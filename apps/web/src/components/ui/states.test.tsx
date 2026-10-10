import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { ErrorState, LoadingRows } from "./primitives";
import { StatusBadge } from "./status";

describe("loading and error states", () => {
  it("announces loading", () => {
    render(<LoadingRows label="Loading approvals" />);
    expect(screen.getByRole("status", { name: "Loading approvals" })).toBeInTheDocument();
  });

  it("shows the error message, request id and retry", async () => {
    const retry = vi.fn();
    render(<ErrorState error={new ApiError(502, "upstream_error", "GitHub unreachable", "req-9")} onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("GitHub unreachable");
    expect(screen.getByText("request req-9")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it.each([
    ["waiting_for_approval", "Waiting for approval"],
    ["completed", "Completed"],
    ["failed", "Failed"],
    ["cancelled", "Cancelled"],
  ])("labels status %s", (status, label) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});
