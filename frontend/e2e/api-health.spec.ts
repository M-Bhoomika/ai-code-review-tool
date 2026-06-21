import { expect, test } from "@playwright/test";

import { API_BASE_URL } from "./helpers";

test.describe("API health", () => {
  test("main health endpoint responds", async ({ request }) => {
    const response = await request.get(`${API_BASE_URL}/health`);
    expect(response.ok()).toBeTruthy();

    const body = await response.json();
    expect(body).toMatchObject({
      status: "ok",
      service: "ai-code-review-api",
    });
  });

  test("reviews health endpoint responds", async ({ request }) => {
    const response = await request.get(`${API_BASE_URL}/reviews/health`);
    expect(response.ok()).toBeTruthy();
    expect(await response.json()).toEqual({ status: "ok" });
  });
});
