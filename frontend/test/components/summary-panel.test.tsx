import { render, screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { SummaryPanel, type SummaryPanelProperties } from "../../src/components/summary-panel.tsx";
import { HOUR, iso, summary, T0 } from "../fixtures.ts";

const WINDOW = { since: iso(T0 - 3 * HOUR), until: iso(T0) };

function renderPanel(overrides: Partial<SummaryPanelProperties> = {}) {
  return render(
    <SummaryPanel
      summary={summary()}
      state="success"
      error={undefined}
      refreshing={false}
      rangeText="the last 24 hours"
      window={WINDOW}
      {...overrides}
    />,
  );
}

describe("SummaryPanel", () => {
  test("leads with the published-call count and how many lack a location", () => {
    renderPanel();
    expect(screen.getByText("1,284")).toBeVisible();
    expect(screen.getByText("published calls in the last 24 hours")).toBeVisible();
    expect(screen.getByText(/84 have no map location/)).toBeVisible();
    expect(screen.getByText(/not confirmed crimes/)).toBeVisible();
  });

  test("ranks the most common call types and folds the rest into other", () => {
    renderPanel();
    const types = within(screen.getByRole("list", { name: "Most common call types" }));
    const items = types.getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      "ALARM - BURGLAR500",
      "TRAFFIC VIOLATION384",
      "Other types400",
    ]);
  });

  test("names types the source did not publish and omits an empty other row", () => {
    renderPanel({
      summary: summary({
        types: {
          otherCount: 0,
          entries: [
            { Tencode_Description: null, count: 3 },
            { Tencode_Description: "43", count: 2 },
          ],
        },
      }),
    });
    const types = within(screen.getByRole("list", { name: "Most common call types" }));
    expect(types.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Call type not published3",
      "Code 432",
    ]);
  });

  test("charts activity over the window with a table of the same values", () => {
    renderPanel();
    const chart = screen.getByRole("figure", { name: "Calls per hour" });
    const table = within(chart).getByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      "Sep 19, 9 AM0",
      "Sep 19, 10 AM40",
      "Sep 19, 11 AM60",
    ]);
    expect(within(chart).getByText("60", { selector: ".axis-max" })).toBeInTheDocument();
  });

  test("labels daily activity for long ranges", () => {
    renderPanel({ window: { since: iso(T0 - 10 * 24 * HOUR), until: iso(T0) } });
    expect(screen.getByRole("figure", { name: "Calls per day" })).toBeInTheDocument();
  });

  test("keeps bars well-formed when earlier figures cover a different window", () => {
    // While a new range loads, the previous summary stays on screen for the new window.
    renderPanel({ window: { since: iso(T0 - 30 * HOUR), until: iso(T0 - 28 * HOUR) } });
    const chart = screen.getByRole("figure", { name: "Calls per hour" });
    const heights = [...chart.querySelectorAll<HTMLElement>(":scope .column .bar")].map(
      (bar) => bar.style.height,
    );
    expect(heights).toEqual(["0%", "0%"]);
  });

  test("says when nothing was published in the period", () => {
    renderPanel({
      summary: summary({
        total: 0,
        withCoordinates: 0,
        withoutCoordinates: 0,
        types: { otherCount: 0, entries: [] },
        hourly: [],
      }),
    });
    expect(screen.getByText("No published calls in the last 24 hours.")).toBeVisible();
    expect(screen.queryByRole("figure")).not.toBeInTheDocument();
  });

  test("holds the previous figures while refreshing", () => {
    renderPanel({ refreshing: true });
    expect(screen.getByRole("region", { name: "Summary" })).toHaveAttribute("aria-busy", "true");
  });

  test("shows loading and errors", () => {
    const { unmount } = renderPanel({ summary: undefined, state: "pending" });
    expect(screen.getByText("Loading summary…")).toBeVisible();
    unmount();
    renderPanel({ summary: undefined, state: "error", error: new Error("HTTP 502") });
    expect(screen.getByRole("alert")).toHaveTextContent("The summary could not be loaded (HTTP 502).");
  });
});
