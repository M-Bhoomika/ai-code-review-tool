import { expect, test } from "@playwright/test";

const REPOSITORY = "octocat/hello";
const PULL_NUMBER = "42";

test.describe("Integration (live stack)", () => {
  test("dashboard displays analytics from a real review run", async ({ page }) => {
    test.setTimeout(180_000);

    await page.goto("/dashboard");

    await page.getByLabel("Repository").fill(REPOSITORY);
    await page.getByLabel("Pull request number").fill(PULL_NUMBER);
    await page.getByRole("button", { name: "Start Review" }).click();

    await expect(
      page.getByText(`Review queued for ${REPOSITORY} #${PULL_NUMBER}.`)
    ).toBeVisible({ timeout: 15_000 });

    const row = page
      .getByRole("row", {
        name: new RegExp(`${REPOSITORY}\\s+${PULL_NUMBER}`),
      })
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
