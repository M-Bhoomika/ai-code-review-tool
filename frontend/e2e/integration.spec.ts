import { expect, test } from "@playwright/test";

import { INTEGRATION_API_BASE_URL } from "./helpers";

const REPOSITORY = "octocat/hello";
const PULL_NUMBER = "42";

test.describe("Integration (live stack)", () => {
  test("dashboard displays analytics from a real review run", async ({ page }) => {
    test.setTimeout(180_000);

    await page.goto("/dashboard");

    await expect(
      page.getByRole("heading", { name: "Review Dashboard", level: 1 })
    ).toBeVisible();
    await expect(page.getByTestId("review-queue-error")).toHaveCount(0);

    const submitButton = page.getByRole("button", { name: "Start Review" });
    await expect(submitButton).toBeEnabled();

    const queueResponse = page.waitForResponse(
      (response) =>
        response.url().startsWith(`${INTEGRATION_API_BASE_URL}/reviews/jobs`) &&
        response.request().method() === "POST",
      { timeout: 30_000 }
    );

    await page.getByLabel("Repository").fill(REPOSITORY);
    await page.getByLabel("Pull request number").fill(PULL_NUMBER);
    await submitButton.click();

    const response = await queueResponse;
    expect(response.status(), await response.text()).toBe(202);

    const successMessage = page.getByTestId("review-queue-success");
    await expect(successMessage).toBeVisible({ timeout: 10_000 });
    await expect(successMessage).toHaveText(
      `Review queued for ${REPOSITORY} #${PULL_NUMBER}.`
    );
    await expect(page.getByTestId("review-queue-error")).toHaveCount(0);

    const row = page
      .locator("tbody tr")
      .filter({ hasText: REPOSITORY })
      .filter({ hasText: PULL_NUMBER })
      .first();
    await expect(row).toBeVisible({ timeout: 120_000 });
    await expect(row.getByText("completed", { exact: true })).toBeVisible({
      timeout: 120_000,
    });

    const commentCell = row.getByRole("cell").nth(3);
    await expect(commentCell).not.toHaveText("0");

    await expect(page.getByText("Total Reviews").locator("..")).toContainText(
      /[1-9]/
    );
    await expect(page.getByText("Total Comments").locator("..")).toContainText(
      /[1-9]/
    );
  });
});
