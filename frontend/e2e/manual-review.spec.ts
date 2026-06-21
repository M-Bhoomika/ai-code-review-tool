import { expect, test } from "@playwright/test";

import {
  API_BASE_URL,
  buildGraphqlResponse,
  sampleJob,
  sampleReview,
} from "./helpers";

test.describe("Manual review workflow", () => {
  test("shows validation error for invalid repository format", async ({
    page,
  }) => {
    await page.goto("/dashboard");

    await page.getByLabel("Repository").fill("invalid-repo");
    await page.getByLabel("Pull request number").fill("12");
    await page.getByRole("button", { name: "Start Review" }).click();

    await expect(
      page.getByText("Repository must be in 'owner/name' format.")
    ).toBeVisible();
  });

  test("queues a review and refreshes analytics", async ({ page }) => {
    let graphqlRequested = false;

    await page.route(`${API_BASE_URL}/reviews/jobs`, async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 202,
          json: sampleJob,
        });
        return;
      }
      await route.continue();
    });

    await page.route(`${API_BASE_URL}/graphql`, async (route) => {
      graphqlRequested = true;
      const body = route.request().postDataJSON() as { query?: string };
      await route.fulfill({
        contentType: "application/json",
        json: buildGraphqlResponse(body.query ?? ""),
      });
    });

    await page.goto("/dashboard");

    await page.getByLabel("Repository").fill("octocat/demo");
    await page.getByLabel("Pull request number").fill("99");
    await page.getByRole("button", { name: "Start Review" }).click();

    await expect(
      page.getByText("Review queued for octocat/demo #99.")
    ).toBeVisible();

    const row = page.getByRole("row").filter({
      hasText: sampleReview.repositoryName,
    });
    await expect(row).toBeVisible();
    await expect(row.getByText("completed", { exact: true })).toBeVisible();
    expect(graphqlRequested).toBeTruthy();
  });
});
