import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test } from "vitest";

import type { CallFilter } from "../../src/api/types.ts";
import { Monitor } from "../../src/components/monitor.tsx";
import { FakeApi } from "../fake-api.ts";
import { FakeVersionSource, openFakeSource } from "../fake-version-source.ts";
import {
  callDetail,
  FILTER_VALUES,
  feedCall,
  HOUR,
  iso,
  liveStatus,
  mapCall,
  summary,
  T0,
} from "../fixtures.ts";
import { renderWithClient } from "../render.tsx";

interface Backend {
  api: FakeApi;
  version: number;
  newCalls: number;
}

function fakeBackend(): Backend {
  const backend: Backend = { api: new FakeApi(), version: 7, newCalls: 0 };
  backend.api
    .on("Anchor", () => ({ dataVersion: backend.version, serverTime: iso(T0) }))
    .on("Status", () => ({ serverTime: iso(T0), sourceStatus: [liveStatus()] }))
    .on("Feed", ({ after }) => ({
      calls: {
        asOfVersion: backend.version,
        pageInfo: {
          endCursor: after === undefined ? "page-2" : "page-3",
          hasNextPage: after === undefined,
        },
        nodes: after === undefined ? [feedCall(1), feedCall(2)] : [feedCall(3)],
      },
    }))
    .on("NewCalls", () => ({ newCallCount: backend.newCalls }))
    .on("Summary", () => ({ summary: summary() }))
    .on("MapCalls", () => ({
      mapCalls: { matching: 2, withCoordinates: 1, truncated: false, calls: [mapCall(1)] },
    }))
    .on("FilterValues", () => ({ filterValues: FILTER_VALUES }))
    .on("CallDetail", ({ id }) => ({ call: { ...callDetail(1), id } }));
  return backend;
}

beforeEach(() => {
  FakeVersionSource.reset();
});

function feedRows(): HTMLElement[] {
  return within(screen.getByRole("list", { name: "Calls, newest first" })).queryAllByRole("button");
}

async function renderMonitor() {
  const view = renderWithClient(<Monitor openEvents={openFakeSource} />);
  await waitFor(() => {
    expect(feedRows()).toHaveLength(2);
  });
  return view;
}

function publish(backend: Backend, next: number): void {
  backend.version = next;
  act(() => {
    FakeVersionSource.latest().version(next);
  });
}

describe("Monitor", () => {
  test("pins the feed to the data version and server time it was loaded at", async () => {
    const backend = fakeBackend();
    const { api } = backend;
    await renderMonitor();
    const [feed] = api.calls("Feed");
    expect(feed).toMatchObject({
      asOfVersion: 7,
      first: 50,
      filter: {
        since: iso(T0 - 24 * HOUR),
        until: iso(T0 + 5 * 60_000),
        ZONE_: [],
        Sector: [],
        Tencode_Description: [],
        Disposition_Description: [],
      },
    });
    expect(api.calls("NewCalls")[0]).toMatchObject({ sinceVersion: 7 });
    expect(await screen.findByText("1,284")).toBeVisible();
    expect(screen.getByRole("status", { name: "Data source status" })).toHaveTextContent("Live");
    expect(FakeVersionSource.latest().url).toBe("/events");
  });

  test("loads further pages at the same pinned version", async () => {
    const backend = fakeBackend();
    const { api } = backend;
    const user = userEvent.setup();
    await renderMonitor();
    await user.click(screen.getByRole("button", { name: "Load more calls" }));
    await waitFor(() => {
      expect(feedRows()).toHaveLength(3);
    });
    expect(api.calls("Feed")[1]).toMatchObject({ after: "page-2", asOfVersion: 7 });
    expect(screen.queryByRole("button", { name: "Load more calls" })).not.toBeInTheDocument();
  });

  test("counts arrivals and updates in place without moving the list", async () => {
    const backend = fakeBackend();
    const { api } = backend;
    await renderMonitor();
    const summaries = api.calls("Summary").length;
    backend.newCalls = 2;
    publish(backend, 8);
    expect(await screen.findByRole("button", { name: "Show 2 new calls" })).toBeVisible();
    await waitFor(() => {
      expect(api.calls("Summary").length).toBeGreaterThan(summaries);
    });
    expect(api.calls("Feed").at(-1)).toMatchObject({ asOfVersion: 7 });
    expect(feedRows()).toHaveLength(2);
    const latest = api.calls("NewCalls").at(-1) as { filter: CallFilter; sinceVersion: number };
    expect(latest.sinceVersion).toBe(7);
    expect(Date.parse(latest.filter.until)).toBeGreaterThanOrEqual(T0 + 5 * 60_000);
  });

  test("offers a wider period when the day holds no calls", async () => {
    // Metro Nashville publishes in batches, so the last day can be empty while the source is fine.
    const backend = fakeBackend();
    const { api } = backend;
    const user = userEvent.setup();
    backend.api
      .on("Status", () => ({
        serverTime: iso(T0),
        sourceStatus: [liveStatus({ latestCallReceivedAt: iso(T0 - 36 * HOUR) })],
      }))
      .on("Feed", () => ({
        calls: {
          asOfVersion: backend.version,
          pageInfo: { endCursor: null, hasNextPage: false },
          nodes: [],
        },
      }));
    renderWithClient(<Monitor openEvents={openFakeSource} />);
    expect(
      await screen.findByText("The newest call the source has published is Sep 18, 12:00 AM CDT."),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Show the last 7 days" }));

    await waitFor(() => {
      const { filter } = api.calls("Feed").at(-1) as { filter: CallFilter };
      expect(filter.since).toBe(iso(T0 - 7 * 24 * HOUR));
    });
    expect(screen.getByLabelText("Date range")).toHaveValue("7d");
    // The reader is already looking at the wider period, so the offer is gone.
    expect(screen.queryByRole("button", { name: "Show the last 7 days" })).not.toBeInTheDocument();
  });

  test("says history is still loading before the source has published anything", async () => {
    const backend = fakeBackend();
    backend.api
      .on("Status", () => ({
        serverTime: iso(T0),
        sourceStatus: [
          liveStatus({
            state: "BACKFILLING",
            latestCallReceivedAt: null,
            backfill: { fraction: 0.2, rangesTotal: 5, rangesCompleted: 1, completedAt: null },
          }),
        ],
      }))
      .on("Feed", () => ({
        calls: {
          asOfVersion: backend.version,
          pageInfo: { endCursor: null, hasNextPage: false },
          nodes: [],
        },
      }));
    renderWithClient(<Monitor openEvents={openFakeSource} />);
    expect(
      await screen.findByText(/History is still loading, so older calls may appear later/),
    ).toBeVisible();
    expect(screen.queryByText(/newest call the source has published/)).not.toBeInTheDocument();
  });

  test("ignores a notification for the version already shown", async () => {
    const backend = fakeBackend();
    const { api } = backend;
    await renderMonitor();
    const requests = api.requests.length;
    publish(backend, 7);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.requests.length).toBe(requests);
  });

  test("shows new calls by reloading the feed at the newest version", async () => {
    const backend = fakeBackend();
    const { api } = backend;
    const user = userEvent.setup();
    await renderMonitor();
    backend.newCalls = 1;
    publish(backend, 9);
    const list = screen.getByRole("list", { name: "Calls, newest first" });
    list.scrollTop = 400;
    await user.click(await screen.findByRole("button", { name: "Show 1 new call" }));
    expect(list.scrollTop).toBe(0);
    await waitFor(() => {
      expect(api.calls("Feed").at(-1)).toMatchObject({ asOfVersion: 9 });
    });
    expect(api.calls("Anchor")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "Calls" })).toHaveFocus();
  });

  test("keeps the list, map, and summary still while paused", async () => {
    const backend = fakeBackend();
    const { api } = backend;
    const user = userEvent.setup();
    await renderMonitor();
    await user.click(screen.getByRole("button", { name: "Pause updates" }));
    const shown = { feed: api.calls("Feed").length, summary: api.calls("Summary").length };
    backend.newCalls = 3;
    publish(backend, 10);
    expect(await screen.findByRole("button", { name: "Show 3 new calls" })).toBeVisible();
    expect(api.calls("Feed")).toHaveLength(shown.feed);
    expect(api.calls("Summary")).toHaveLength(shown.summary);

    await user.click(screen.getByRole("button", { name: "Resume updates" }));
    await waitFor(() => {
      expect(api.calls("Summary").length).toBeGreaterThan(shown.summary);
    });
  });

  test("refreshes after live updates reconnect", async () => {
    const backend = fakeBackend();
    const { api } = backend;
    await renderMonitor();
    const statuses = api.calls("Status").length;
    act(() => {
      FakeVersionSource.latest().fail();
    });
    expect(screen.getByText(/Live updates interrupted/)).toBeVisible();
    act(() => {
      FakeVersionSource.latest().open();
    });
    await waitFor(() => {
      expect(api.calls("Status").length).toBeGreaterThan(statuses);
    });
  });

  test("reloads everything for new filters", async () => {
    const backend = fakeBackend();
    const { api } = backend;
    const user = userEvent.setup();
    await renderMonitor();
    await user.selectOptions(screen.getByLabelText("Zone"), "23");
    await waitFor(() => {
      expect(api.calls("Feed").at(-1)).toMatchObject({ filter: { ZONE_: ["23"] } });
    });
    expect(api.calls("Anchor")).toHaveLength(2);
    expect(api.calls("Summary").at(-1)).toMatchObject({ filter: { ZONE_: ["23"] } });
    expect(api.calls("MapCalls").at(-1)).toMatchObject({ filter: { ZONE_: ["23"] } });
  });

  test("opens and closes the details of a selected call", async () => {
    const backend = fakeBackend();
    const { api } = backend;
    const user = userEvent.setup();
    await renderMonitor();
    await user.click(feedRows()[0]!);
    const details = await screen.findByRole("complementary", { name: "ALARM - BURGLAR" });
    expect(api.calls("CallDetail")).toEqual([{ id: "1:1" }]);
    await user.click(within(details).getByRole("button", { name: "Close call details" }));
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  test("switches between the list and the map on small screens", async () => {
    fakeBackend();
    const user = userEvent.setup();
    const { container } = await renderMonitor();
    const layout = container.querySelector("[data-view]");
    expect(layout).toHaveAttribute("data-view", "list");
    await user.click(screen.getByRole("button", { name: "Map" }));
    expect(layout).toHaveAttribute("data-view", "map");
    expect(screen.getByRole("button", { name: "Map" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "List" }));
    expect(layout).toHaveAttribute("data-view", "list");
  });

  test("offers a retry when the dashboard cannot start", async () => {
    const backend = fakeBackend();
    const { api } = backend;
    const user = userEvent.setup();
    api.on("Anchor", () => {
      throw new Error("Unexpected error.");
    });
    renderWithClient(<Monitor openEvents={openFakeSource} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The calls could not be loaded (Unexpected error.).",
    );
    api.on("Anchor", () => ({ dataVersion: backend.version, serverTime: iso(T0) }));
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => {
      expect(feedRows()).toHaveLength(2);
    });
  });

  test("retries the feed after it fails to load", async () => {
    const backend = fakeBackend();
    const { api } = backend;
    const user = userEvent.setup();
    const feed = { respond: false };
    api.on("Feed", () => {
      if (!feed.respond) {
        throw new Error("database is locked");
      }
      return {
        calls: {
          asOfVersion: 7,
          pageInfo: { endCursor: "end", hasNextPage: false },
          nodes: [feedCall(1)],
        },
      };
    });
    renderWithClient(<Monitor openEvents={openFakeSource} />);
    expect(await screen.findByText("Calls could not be loaded (database is locked).")).toBeVisible();
    feed.respond = true;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => {
      expect(feedRows()).toHaveLength(1);
    });
  });
});
