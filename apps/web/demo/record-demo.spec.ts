import { expect, test, type Page } from "@playwright/test";

/** Shows a caption bar at the bottom of the page (re-added after each navigation). */
async function caption(page: Page, step: string, text: string, holdMs = 2600) {
  await page.evaluate(
    ([s, t]) => {
      document.getElementById("demo-caption")?.remove();
      const el = document.createElement("div");
      el.id = "demo-caption";
      el.innerHTML = `<span style="color:#7aa2f7;font-weight:600;margin-right:10px">${s}</span>${t}`;
      Object.assign(el.style, {
        position: "fixed", left: "50%", bottom: "28px", transform: "translateX(-50%)", zIndex: "9999",
        background: "rgba(11,13,16,0.94)", color: "#e6e8eb", border: "1px solid #2f3641", borderRadius: "10px",
        padding: "12px 20px", font: "500 17px/1.4 ui-sans-serif, system-ui, sans-serif", maxWidth: "1100px",
        boxShadow: "0 8px 30px rgba(0,0,0,.45)", pointerEvents: "none",
      });
      document.body.appendChild(el);
    },
    [step, text],
  );
  await page.waitForTimeout(holdMs);
}

async function smoothScroll(page: Page, y: number, ms = 1200) {
  await page.evaluate(([top, dur]) => {
    const start = window.scrollY;
    const t0 = performance.now();
    return new Promise<void>((done) => {
      const tick = (now: number) => {
        const p = Math.min(1, (now - t0) / dur);
        window.scrollTo(0, start + (top - start) * (1 - Math.pow(1 - p, 3)));
        if (p < 1) requestAnimationFrame(tick);
        else done();
      };
      requestAnimationFrame(tick);
    });
  }, [y, ms] as const);
}

test("Orchestra AI walkthrough", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("All systems normal")).toBeVisible();
  await caption(page, "Orchestra AI", "A multi-agent control plane for engineering work. This run uses demo mode: no API keys, simulated GitHub.", 4000);

  // Connect the sample repository
  await page.goto("/repositories");
  await caption(page, "1 · Connect", "Connect a repository. The demo repo is a small Python project with a real bug.");
  await page.getByRole("button", { name: "Connect demo" }).click();
  await expect(page.getByRole("link", { name: /acme\/invoicekit/ })).toBeVisible();
  await page.waitForTimeout(1200);

  // Submit a request
  await page.goto("/");
  await caption(page, "2 · Request", "Describe the problem in plain language. Here: an issue about checkout crashing on empty carts.");
  await page.getByLabel("New engineering request").pressSequentially("Checkout fails for empty carts, see issue #12. Please investigate.", { delay: 28 });
  await page.waitForTimeout(600);
  await page.getByRole("button", { name: "Run workflow" }).click();
  await expect(page).toHaveURL(/\/workflows\/[0-9a-f]{32}$/);

  // Watch the agents
  await caption(page, "3 · Agents", "The LangGraph workflow routes the request through specialised agents. The graph lights up as each stage finishes.", 3000);
  await expect(page.getByText("Waiting for your approval")).toBeVisible({ timeout: 60_000 });
  await caption(page, "4 · Approval gate", "Nothing is written to GitHub yet. The workflow is checkpointed and paused for a human decision.", 3400);

  // Inspect the agents' structured outputs
  await smoothScroll(page, 1100);
  const tabs = page.getByRole("tablist").last();
  await tabs.getByRole("tab", { name: "Context" }).click();
  await caption(page, "5 · Retrieval", "Repository Intelligence ranked the relevant files from the stack trace, without loading the whole repo.");
  await tabs.getByRole("tab", { name: /Plan/ }).click();
  await caption(page, "6 · Plan", "Planning produced a task graph with dependencies, acceptance criteria and required permissions.");
  await tabs.getByRole("tab", { name: "Diagnosis" }).click();
  await caption(page, "7 · Diagnosis", "Debugging traced the crash to an unguarded division and proposed a minimal, exact edit.");
  await tabs.getByRole("tab", { name: "Verification" }).click();
  await caption(page, "8 · Verification", "Verification applied the patch in isolation and ran the test suite in a no-network Docker sandbox.", 3200);

  // Review the diff and approve
  await page.locator("[aria-label^='Approval:']").first().scrollIntoViewIfNeeded();
  await smoothScroll(page, Math.max(0, (await page.evaluate(() => window.scrollY)) - 40), 400);
  await caption(page, "9 · Review", "The approval shows the risk level, the permissions requested and the exact diff.", 3200);
  await page.getByPlaceholder(/decision note/i).pressSequentially("Verified the diff and test results", { delay: 25 });
  await page.getByRole("button", { name: "Approve" }).click();
  await caption(page, "Approved", "The workflow resumes from its checkpoint and the Automation agent runs the approved writes.", 1800);
  await expect(page.getByText("Completed", { exact: true }).first()).toBeVisible({ timeout: 60_000 });

  // Outcome
  await smoothScroll(page, 0, 900);
  await caption(page, "10 · Done", "After approval: an orchestra/* branch, a commit and a draft pull request (simulated in demo mode).", 3000);
  await smoothScroll(page, 760);
  await page.getByRole("tablist").last().getByRole("tab", { name: "Report" }).click();
  await caption(page, "Audit trail", "Every agent decision, tool call and approval is recorded in the timeline and the final report.", 4000);

  await page.goto("/agents");
  await caption(page, "Agents", "Eight agents, each with a fixed tool allowlist enforced by the server.", 3200);
  await page.goto("/workflows");
  await caption(page, "Workflows", "Every request runs through one LangGraph graph with bounded loops and a human approval gate.", 3800);
});
