import { expect, test } from "@playwright/test";

import { API_BASE_URL, buildGraphqlResponse, sampleReview } from "./helpers";

test.describe("Review listing", () => {
  test("renders review analytics table", async ({ page }) => {
    await page.route(`${API_BASE_URL}/graphql`, async (route) => {
      const body = route.request().postDataJSON() as { query?: string };
      await route.fulfill({
        contentType: "application/json",
        json: buildGraphqlResponse(body.query ?? ""),
      });
    });

    await page.goto("/dashboard");

    const row = page.getByRole("row").filter({
      hasText: sampleReview.repositoryName,
    });
    await expect(row).toBeVisible();
    await expect(row.getByRole("cell", { name: "42", exact: true })).toBeVisible();
    await expect(row.getByText("completed", { exact: true })).toBeVisible();
    await expect(row.getByRole("cell", { name: "1", exact: true })).toBeVisible();
    await expect(row.getByRole("cell", { name: "75", exact: true })).toBeVisible();
  });

  test("shows empty state when no reviews exist", async ({ page }) => {
    await page.route(`${API_BASE_URL}/graphql`, async (route) => {
      const body = route.request().postDataJSON() as { query?: string };
      const query = body.query ?? "";
      if (query.includes("reviewStats")) {
        await route.fulfill({
          json: {
            data: {
              reviewStats: {
                totalReviews: 0,
                totalComments: 0,
                completedReviews: 0,
                pendingReviews: 0,
                failedReviews: 0,
                averageRiskScore: null,
                averageProcessingTimeMs: null,
              },
            },
          },
        });
        return;
      }
      await route.fulfill({ json: { data: { reviews: [] } } });
    });

    await page.goto("/dashboard");

    await expect(
      page.getByRole("heading", { name: "Recent Reviews", level: 2 })
    ).toBeVisible();
    await expect(page.getByText("No review analytics yet.")).toBeVisible();
  });
});
