import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";

// The control service of the fixture harness (backend/tests/fixture_api.py). It commits calls
// into the same database the API serves, which is how a flow sees an update arrive.
const CONTROL = "http://127.0.0.1:8097";

// The calls the fixture dataset holds in the dashboard's opening period.
export const FIXTURE_CALLS = 4;

export function feedRows(page: Page): Locator {
  return page.getByRole("list", { name: "Calls, newest first" }).getByRole("button");
}

export async function publishCall(request: APIRequestContext): Promise<void> {
  const response = await request.post(`${CONTROL}/publish-call`);
  expect(response.ok()).toBe(true);
}

/**
 * Take back anything a flow published, so the next one starts from the fixture dataset.
 */
export async function retractPublishedCalls(request: APIRequestContext): Promise<void> {
  const response = await request.post(`${CONTROL}/retract-calls`);
  expect(response.ok()).toBe(true);
}

export async function openDashboard(page: Page): Promise<void> {
  await page.goto("/");
  await expect(feedRows(page)).toHaveCount(FIXTURE_CALLS);
}
