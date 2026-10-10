import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NAV, Sidebar, isActive } from "./app-shell";

describe("navigation", () => {
  it("lists every product area", () => {
    render(<Sidebar pathname="/" pendingApprovals={0} />);
    for (const item of NAV) expect(screen.getByRole("link", { name: new RegExp(item.label) })).toHaveAttribute("href", item.href);
  });

  it("marks the current section, including nested routes", () => {
    render(<Sidebar pathname="/workflows/abc123" pendingApprovals={0} />);
    expect(screen.getByRole("link", { name: /workflows/i })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /overview/i })).not.toHaveAttribute("aria-current");
  });

  it("shows the pending approvals count", () => {
    render(<Sidebar pathname="/" pendingApprovals={3} />);
    expect(screen.getByLabelText("3 pending approvals")).toBeInTheDocument();
  });

  it("renders without an active item while the pathname is unknown", () => {
    render(<Sidebar pathname={null} pendingApprovals={0} />);
    expect(screen.queryByRole("link", { current: "page" })).not.toBeInTheDocument();
  });

  it("matches routes precisely", () => {
    expect(isActive("/", "/")).toBe(true);
    expect(isActive("/agents", "/")).toBe(false);
    expect(isActive("/approvals", "/approvals")).toBe(true);
    expect(isActive("/approvalsx", "/approvals")).toBe(false);
  });
});
