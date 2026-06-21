import { expect, test } from "@playwright/test";

import { API_BASE_URL, buildGraphqlResponse } from "./helpers";

test.describe("Dashboard", () => {
  test("loads successfully", async ({ page }) => {
    await page.route(`${API_BASE_URL}/graphql`, async (route) => {
      const body = route.request().postDataJSON() as { query?: string };
      await route.fulfill({
        contentType: "application/json",
        json: buildGraphqlResponse(body.query ?? ""),
      });
    });

    await page.goto("/dashboard");

    await expect(
      page.getByRole("heading", { name: "Review Dashboard", level: 1 })
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Trigger a Review", level: 2 })
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Recent Reviews", level: 2 })
    ).toBeVisible();

    await expect(page.getByText("Total Reviews")).toBeVisible();
    await expect(page.getByText("Success Rate")).toBeVisible();
    await expect(page.getByRole("link", { name: "← Home" })).toBeVisible();
    await expect(page.getByText("Could not reach the API")).toHaveCount(0);
  });

  test("loads analytics from GraphQL", async ({ page }) => {
    await page.route(`${API_BASE_URL}/graphql`, async (route) => {
      const body = route.request().postDataJSON() as { query?: string };
      await route.fulfill({
        contentType: "application/json",
        json: buildGraphqlResponse(body.query ?? ""),
      });
    });

    await page.goto("/dashboard");

    await expect(page.getByText("Total Reviews")).toBeVisible();
    await expect(page.getByText("octocat/hello-world")).toBeVisible();
  });
});
