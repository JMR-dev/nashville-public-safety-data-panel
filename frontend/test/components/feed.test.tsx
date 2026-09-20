import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import type { FeedCall } from "../../src/api/types.ts";
import { Feed, type FeedProperties } from "../../src/components/feed.tsx";
import { feedCall } from "../fixtures.ts";

function renderFeed(overrides: Partial<FeedProperties> = {}) {
  const properties: FeedProperties = {
    calls: [feedCall(1), feedCall(2)],
    state: "success",
    error: undefined,
    refreshing: false,
    hasMore: false,
    loadingMore: false,
    newCalls: 0,
    paused: false,
    selectedId: undefined,
    rangeText: "the last 24 hours",
    backfilling: false,
    onLoadMore: vi.fn(),
    onShowNew: vi.fn(),
    onTogglePause: vi.fn(),
    onSelect: vi.fn(),
    onRetry: vi.fn(),
    onWiden: vi.fn(),
    newestCallAt: undefined,
    canWiden: false,
    ...overrides,
  };
  render(<Feed {...properties} />);
  return properties;
}

function rows(): HTMLElement[] {
  return within(screen.getByRole("list", { name: "Calls, newest first" })).getAllByRole("button");
}

describe("Feed", () => {
  test("lists each call's type, time, approximate location, area, and disposition", () => {
    renderFeed();
    const [first] = rows();
    expect(first).toHaveTextContent("ALARM - BURGLAR");
    expect(first).toHaveTextContent("Sep 19, 11:59 AM CDT");
    expect(first).toHaveTextContent("100 block of BROADWAY");
    expect(first).toHaveTextContent("Zone 15 · Sector C1");
    expect(first).toHaveTextContent("ASSISTED CITIZEN");
  });

  test("marks calls without a map location or no longer published", () => {
    const hidden: FeedCall = { ...feedCall(3), hasCoordinates: false, sourcePresent: false };
    renderFeed({ calls: [hidden] });
    expect(rows()[0]).toHaveTextContent("No map location");
    expect(rows()[0]).toHaveTextContent("No longer published");
  });

  test("describes missing upstream values instead of leaving gaps", () => {
    const sparse = feedCall(4, {
      Tencode_Description: null,
      Disposition_Description: null,
      Block: null,
      Street_Name: null,
      ZONE_: null,
      Sector: null,
    });
    const street = feedCall(5, { Block: null, ZONE_: null });
    const undated: FeedCall = { ...feedCall(6), receivedAt: null };
    renderFeed({ calls: [sparse, street, undated] });
    const [first, second, third] = rows();
    expect(first).toHaveTextContent("Call type not published");
    expect(first).toHaveTextContent("Location not published");
    expect(first).toHaveTextContent("No disposition yet");
    expect(first).not.toHaveTextContent("Zone");
    expect(second).toHaveTextContent("BROADWAY");
    expect(second).toHaveTextContent("Sector C1");
    expect(second).not.toHaveTextContent("block of");
    expect(third).toHaveTextContent("Time not published");
  });

  test("selecting a call reports it and marks it as selected", async () => {
    const user = userEvent.setup();
    const properties = renderFeed({ selectedId: "1:2" });
    expect(rows()[1]).toHaveAttribute("aria-pressed", "true");
    expect(rows()[0]).toHaveAttribute("aria-pressed", "false");
    await user.click(rows()[0]!);
    expect(properties.onSelect).toHaveBeenCalledWith("1:1");
  });

  test("announces new arrivals without inserting them", async () => {
    const user = userEvent.setup();
    const properties = renderFeed({ newCalls: 3 });
    expect(rows()).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Show 3 new calls" }));
    expect(properties.onShowNew).toHaveBeenCalledOnce();
  });

  test("uses the singular for one arrival and shows nothing for none", () => {
    renderFeed({ newCalls: 1 });
    expect(screen.getByRole("button", { name: "Show 1 new call" })).toBeVisible();
  });

  test("pauses and resumes displayed updates", async () => {
    const user = userEvent.setup();
    const properties = renderFeed();
    const pause = screen.getByRole("button", { name: "Pause updates" });
    expect(pause).toHaveAttribute("aria-pressed", "false");
    await user.click(pause);
    expect(properties.onTogglePause).toHaveBeenCalledOnce();
  });

  test("explains a paused feed", () => {
    renderFeed({ paused: true });
    expect(screen.getByRole("button", { name: "Resume updates" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText(/Updates paused/)).toBeVisible();
  });

  test("loads more calls on request", async () => {
    const user = userEvent.setup();
    const properties = renderFeed({ hasMore: true });
    await user.click(screen.getByRole("button", { name: "Load more calls" }));
    expect(properties.onLoadMore).toHaveBeenCalledOnce();
  });

  test("disables loading more while a page is on its way", () => {
    renderFeed({ hasMore: true, loadingMore: true });
    expect(screen.getByRole("button", { name: "Loading more calls…" })).toBeDisabled();
  });

  test("keeps the list visible and marked busy while refreshing", () => {
    renderFeed({ refreshing: true });
    expect(screen.getByRole("list", { name: "Calls, newest first" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
  });

  test("shows initial loading", () => {
    renderFeed({ state: "pending", calls: [] });
    expect(screen.getByText("Loading calls…")).toBeVisible();
  });

  test("offers a retry when calls cannot be loaded", async () => {
    const user = userEvent.setup();
    const properties = renderFeed({ state: "error", calls: [], error: new Error("HTTP 502") });
    expect(screen.getByRole("alert")).toHaveTextContent("Calls could not be loaded (HTTP 502).");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(properties.onRetry).toHaveBeenCalledOnce();
  });

  test("says when no calls match the filters", () => {
    renderFeed({ calls: [] });
    expect(screen.getByText("No calls match these filters in the last 24 hours.")).toBeVisible();
  });

  test("shows a published ten-code as a code", () => {
    renderFeed({ calls: [feedCall(9, { Tencode_Description: "43" })] });
    expect(rows()[0]).toHaveTextContent("Code 43");
  });

  test("points at the newest published call when the period holds none", async () => {
    const user = userEvent.setup();
    const properties = renderFeed({
      calls: [],
      newestCallAt: "2026-09-18T04:58:00+00:00",
      canWiden: true,
    });
    expect(
      screen.getByText("The newest call the source has published is Sep 17, 11:58 PM CDT."),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Show the last 7 days" }));
    expect(properties.onWiden).toHaveBeenCalledOnce();
  });

  test("offers no wider period when the reader already chose one", () => {
    renderFeed({ calls: [], newestCallAt: "2026-09-18T04:58:00+00:00", canWiden: false });
    expect(screen.getByText(/newest call the source has published/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Show the last 7 days" })).not.toBeInTheDocument();
  });

  test("notes that history is still loading when nothing matches yet", () => {
    renderFeed({ calls: [], backfilling: true });
    expect(screen.getByText(/History is still loading/)).toBeVisible();
  });
});
