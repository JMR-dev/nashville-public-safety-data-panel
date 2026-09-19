import { expect, test } from "@playwright/test";

import { feedRows, FIXTURE_CALLS, openDashboard, publishCall, retractPublishedCalls } from "./support.ts";

test.beforeEach(async ({ page, request }) => {
  await retractPublishedCalls(request);
  await openDashboard(page);
});

test.afterEach(async ({ request }) => {
  await retractPublishedCalls(request);
});

test("offers calls published while the reader is looking, without moving the list", async ({
  page,
  request,
}) => {
  await publishCall(request);

  const offer = page.getByRole("button", { name: "Show 1 new call" });
  await expect(offer).toBeVisible();
  // The list is pinned to the version it was loaded at, so nothing shifts under the reader.
  await expect(feedRows(page)).toHaveCount(FIXTURE_CALLS);

  await offer.click();

  await expect(feedRows(page)).toHaveCount(FIXTURE_CALLS + 1);
  await expect(feedRows(page).first()).toContainText("SHOTS FIRED");
  await expect(offer).toHaveCount(0);
});

test("keeps counting new calls while updates are paused", async ({ page, request }) => {
  await page.getByRole("button", { name: "Pause updates" }).click();
  await expect(
    page.getByText("Updates paused. The list, map, and summary stay as they are"),
  ).toBeVisible();

  await publishCall(request);
  await publishCall(request);

  await expect(page.getByRole("button", { name: "Show 2 new calls" })).toBeVisible();
  await expect(feedRows(page)).toHaveCount(FIXTURE_CALLS);

  await page.getByRole("button", { name: "Resume updates" }).click();

  await expect(page.getByRole("button", { name: "Pause updates" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Show 2 new calls" })).toBeVisible();
});
