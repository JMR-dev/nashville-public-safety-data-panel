import { render, screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import type { Connection } from "../../src/api/events.ts";
import type { SourceStatus, StatusResult } from "../../src/api/types.ts";
import { StatusBar } from "../../src/components/status-bar.tsx";
import { iso, liveStatus, T0 } from "../fixtures.ts";

interface Options {
  connection?: Connection;
  error?: Error;
}

function renderStatus(sources: SourceStatus[] | undefined, options: Options = {}) {
  const { connection = "open", error } = options;
  const status: StatusResult | undefined = sources && { serverTime: iso(T0), sourceStatus: sources };
  return render(<StatusBar status={status} error={error} connection={connection} now={T0} />);
}

function message(): HTMLElement {
  return screen.getByRole("status", { name: "Data source status" });
}

describe("StatusBar", () => {
  test("separates successful checks, actual changes, the newest call, and upstream edits", () => {
    renderStatus([liveStatus()]);
    expect(screen.getByRole("heading", { name: "Nashville police calls" })).toBeVisible();
    expect(message()).toHaveTextContent("Live");
    const facts = screen.getByRole("group", { name: "Data freshness" });
    expect(within(facts).getByText("Last successful check").nextSibling).toHaveTextContent(
      "2 seconds ago",
    );
    expect(within(facts).getByText("Last new or changed call").nextSibling).toHaveTextContent(
      "5 minutes ago",
    );
    expect(within(facts).getByText("Newest call received").nextSibling).toHaveTextContent(
      "Sep 19, 11:53 AM CDT",
    );
    expect(within(facts).getByText("Source last edited").nextSibling).toHaveTextContent(
      "Sep 19, 11:54 AM CDT",
    );
  });

  test("says so when nothing has been observed yet", () => {
    renderStatus([
      liveStatus({
        lastPollAt: null,
        lastChangeAt: null,
        latestCallReceivedAt: null,
        upstreamEditedAt: null,
      }),
    ]);
    expect(screen.getAllByText("Not yet")).toHaveLength(4);
  });

  test.each([
    [{ lastPollAt: iso(T0 - 3 * 60_000) }, "Updates delayed", "3 minutes ago"],
    [
      { state: "DEGRADED", retryAt: iso(T0 + 60_000), detail: "HTTP 503" },
      "isn't responding",
      "12:01 PM CDT",
    ],
    [{ state: "SCHEMA_INCOMPATIBLE", detail: "ZONE_ is missing" }, "changed its format", "ZONE_"],
    [{ state: "STARTING" }, "Starting", "Connecting to Metro Nashville"],
    [
      {
        state: "BACKFILLING",
        backfill: { fraction: 0.45, rangesTotal: 4, rangesCompleted: 1, completedAt: null },
      },
      "Loading history",
      "45%",
    ],
  ] as const)("describes %o", (overrides, headline, detail) => {
    renderStatus([liveStatus(overrides)]);
    expect(message()).toHaveTextContent(headline);
    expect(message()).toHaveTextContent(detail);
  });

  test.each([
    [{ state: "BACKFILLING", backfill: null }, "0% collected"],
    [{ state: "DEGRADED", retryAt: null }, "Showing the calls already collected."],
    [{ state: "SCHEMA_INCOMPATIBLE", detail: null }, "reviewed. Showing"],
  ] as const)("reads sensibly when optional status fields are absent: %o", (overrides, text) => {
    renderStatus([liveStatus(overrides)]);
    expect(message()).toHaveTextContent(text);
    expect(message()).not.toHaveTextContent("Next attempt");
    expect(message()).not.toHaveTextContent("null");
  });

  test("shows history loading progress as a progress bar", () => {
    renderStatus([
      liveStatus({
        state: "BACKFILLING",
        backfill: { fraction: 0.45, rangesTotal: 4, rangesCompleted: 1, completedAt: null },
      }),
    ]);
    expect(screen.getByRole("progressbar", { name: "History loaded" })).toHaveAttribute(
      "value",
      "0.45",
    );
  });

  test("explains the states before and without source status", () => {
    const { unmount } = renderStatus(undefined);
    expect(message()).toHaveTextContent("Checking the data source");
    unmount();
    renderStatus([]);
    expect(message()).toHaveTextContent("Waiting for the ingestion service");
  });

  test("reports when the dashboard cannot reach its API", () => {
    renderStatus(undefined, { error: new Error("The API responded with HTTP 502") });
    expect(message()).toHaveTextContent("can't reach the panel's API");
  });

  test.each([
    ["connecting", "Connecting to live updates"],
    ["open", "Live updates on"],
    ["reconnecting", "Live updates interrupted"],
  ] as const)("describes a %s browser connection", (connection, text) => {
    renderStatus([liveStatus()], { connection });
    expect(screen.getByText(text, { exact: false })).toBeVisible();
  });
});
