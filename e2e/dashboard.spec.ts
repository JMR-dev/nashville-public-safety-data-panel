import { expect, test } from "@playwright/test";

import { feedRows, openDashboard, retractPublishedCalls } from "./support.ts";

test.beforeEach(async ({ page, request }) => {
  await retractPublishedCalls(request);
  await openDashboard(page);
});

test("lists the calls the source published, newest first", async ({ page }) => {
  const rows = feedRows(page);
  // Metro Nashville publishes the ten-code itself as the call type, so it reads as a code.
  await expect(rows.first()).toContainText("Code 3");
  await expect(rows.nth(1)).toContainText("ALARM - BURGLAR");
  await expect(rows.nth(1)).toContainText("100 block of BROADWAY");
  await expect(rows.nth(1)).toContainText("Zone 15 · Sector C1");

  const times = await rows.locator("time").evaluateAll((nodes) =>
    nodes.map((node) => Date.parse((node as HTMLTimeElement).dateTime)),
  );
  expect(times).toEqual(times.toSorted((first, second) => second - first));

  const summary = page.getByRole("region", { name: "Summary" });
  await expect(summary).toContainText("4 published calls in the last 24 hours");
  await expect(summary).toContainText("2 have no map location and appear only in the list.");
  await expect(summary).toContainText("not confirmed crimes");
});

test("reports how ingestion is doing", async ({ page }) => {
  const status = page.getByRole("status", { name: "Data source status" });
  await expect(status).toContainText("Loading history");
  await expect(status).toContainText("83% collected.");
  await expect(status).toContainText("Counts may be incomplete until it finishes.");
});

test("narrows the list and the figures to a chosen zone", async ({ page }) => {
  await page.getByLabel("Zone").selectOption("15");

  await expect(feedRows(page)).toHaveCount(2);
  await expect(page.getByRole("region", { name: "Summary" })).toContainText(
    "2 published calls in the last 24 hours",
  );
  // The filter menus offer only what the source published in the period.
  await expect(page.getByRole("combobox", { name: "Call type" })).toContainText("ALARM - BURGLAR");
});

test("opens a call and shows the record as published", async ({ page }) => {
  await feedRows(page).first().click();

  const details = page.getByRole("complementary");
  await expect(details.getByRole("heading", { name: "Code 3" })).toBeFocused();
  const record = details.getByRole("group", { name: "Published record" });
  await expect(record).toContainText("ZONE_");
  await expect(record).toContainText("Event_Number");
  await expect(record).toContainText("PD202600000005");
  // An attribute the schema does not know about is kept rather than dropped.
  await expect(details.getByRole("group", { name: "Additional published attributes" })).toContainText(
    "Priority",
  );

  await page.keyboard.press("Escape");
  await expect(details).toHaveCount(0);
});

test("maps the calls that have a published location", async ({ page }) => {
  // A wide window shows the list and the map together.
  const map = page.getByRole("region", { name: "Map" });
  await expect(map).toContainText("Showing 2 calls with a location.");
  await expect(map).toContainText("2 without a published location appear only in the list.");
  await expect(map).toContainText("Locations are approximate.");
  // Both located fixture calls share a coordinate, so they arrive as one cluster of two.
  const markers = page.locator(".leaflet-marker-icon");
  await expect(markers).toHaveCount(1);
  await expect(markers.first()).toContainText("2");
});

test("switches between the list and the map on a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const feed = page.getByRole("region", { name: "Calls" });
  const map = page.getByRole("region", { name: "Map" });
  await expect(feed).toBeVisible();
  await expect(map).toBeHidden();

  await page.getByRole("group", { name: "View" }).getByRole("button", { name: "Map" }).click();

  await expect(map).toBeVisible();
  await expect(feed).toBeHidden();
});

test("says when nothing matches and offers a longer period", async ({ page }) => {
  await page.getByLabel("Zone").selectOption("23");
  await page.getByLabel("Disposition").selectOption("ASSISTED CITIZEN");

  await expect(page.getByText("No calls match these filters in the last 24 hours.")).toBeVisible();
  await expect(
    page.getByText("The newest call the source has published is Sep 19, 6:00 AM CDT."),
  ).toBeVisible();

  await page.getByRole("button", { name: "Show the last 7 days" }).click();

  await expect(page.getByLabel("Date range")).toHaveValue("7d");
  await expect(page.getByRole("button", { name: "Show the last 7 days" })).toHaveCount(0);
});
