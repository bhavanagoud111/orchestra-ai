import { expect, test } from "@playwright/test";

test("bug investigation from request to approved draft pull request", async ({ page }) => {
  // 1. connect the sample repository
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Connect demo" }).click();
  await expect(page.getByRole("link", { name: /acme\/invoicekit/ })).toBeVisible();

  // 2. submit a bug investigation request
  await page.goto("/");
  await page.getByLabel("New engineering request").fill("Checkout fails for empty carts, see issue #12. Please investigate.");
  await page.getByRole("button", { name: "Run workflow" }).click();
  await expect(page).toHaveURL(/\/workflows\/[0-9a-f]{32}$/);
  await expect(page.getByText("Demo execution")).toBeVisible();

  // 3-7. agents run until the approval gate; nothing has been written yet
  const approval = page.getByRole("region").or(page.locator("[aria-label^='Approval:']")).first();
  await expect(page.getByText("Waiting for your approval")).toBeVisible({ timeout: 30_000 });
  await expect(approval).toContainText("Open pull request");
  await expect(approval).toContainText("if not cart.item_count:");

  const outputs = page.getByRole("tablist").last();
  await outputs.getByRole("tab", { name: "Context" }).click();
  await expect(page.getByRole("tabpanel")).toContainText("invoicekit/totals.py");
  await outputs.getByRole("tab", { name: /Plan/ }).click();
  await expect(page.getByRole("tabpanel")).toContainText("Diagnose root cause");
  await outputs.getByRole("tab", { name: "Diagnosis" }).click();
  await expect(page.getByRole("tabpanel")).toContainText("Unguarded division by cart.item_count");
  await outputs.getByRole("tab", { name: "Verification" }).click();
  await expect(page.getByRole("tabpanel")).toContainText("patch-applies");

  // 8. approve -> branch, commit, draft PR on the simulated host
  await page.getByPlaceholder(/decision note/i).fill("Verified the diff");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Completed", { exact: true }).first()).toBeVisible({ timeout: 30_000 });

  // 9. the timeline and report record the outcome
  await expect(page.getByText(/github\.open_draft_pull succeeded/)).toBeVisible();
  await expect(page.getByText(/^demo:\/\/github\/acme\/invoicekit\/pull\/\d+$/)).toBeVisible();
  await expect(page.getByText("approved by admin", { exact: true })).toBeVisible();

  await page.goto("/approvals");
  await expect(page.getByText("Nothing waiting for approval")).toBeVisible();
});
