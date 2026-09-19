import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { Map as LeafletMap } from "leaflet";
import { afterAll, beforeAll, describe, expect, test, vi } from "vitest";

import { CallMap, type CallMapProperties, markerLabel } from "../../src/components/call-map.tsx";
import { FakeResizeObserver } from "../fake-resize-observer.ts";
import { mapCall } from "../fixtures.ts";

// jsdom performs no layout; give elements the size a browser would so Leaflet has a viewport.
const layout = ["clientWidth", "clientHeight"] as const;
beforeAll(() => {
  for (const property of layout) {
    Object.defineProperty(HTMLElement.prototype, property, { configurable: true, value: 600 });
  }
});
afterAll(() => {
  for (const property of layout) {
    Reflect.deleteProperty(HTMLElement.prototype, property);
  }
});

function renderMap(overrides: Partial<CallMapProperties> = {}) {
  const properties: CallMapProperties = {
    result: {
      matching: 5,
      withCoordinates: 3,
      truncated: false,
      calls: [mapCall(1, 36.16, -86.78), mapCall(2, 36.3, -86.6), mapCall(3, 36, -86.9)],
    },
    state: "success",
    error: undefined,
    selectedId: undefined,
    onSelect: vi.fn(),
    ...overrides,
  };
  const view = render(<CallMap {...properties} />);
  return { ...view, properties };
}

describe("CallMap", () => {
  test("places a marker per located call and credits the tiles", async () => {
    const { container } = renderMap();
    await waitFor(() => {
      expect(container.querySelectorAll(".call-marker")).toHaveLength(3);
    });
    expect(screen.getByText(/Showing 3 calls with a location/)).toBeVisible();
    expect(screen.getByText(/2 without a published location appear only in the list/)).toBeVisible();
    expect(screen.getByRole("link", { name: "OpenStreetMap" })).toBeInTheDocument();
  });

  test("selects a call from its marker by pointer or keyboard", async () => {
    const { properties } = renderMap();
    const marker = await screen.findByTitle(
      "ALARM - BURGLAR, 100 block of BROADWAY, Sep 19, 11:58 AM CDT",
    );
    fireEvent.click(marker);
    expect(properties.onSelect).toHaveBeenLastCalledWith("1:2");
    fireEvent.keyDown(marker, { key: "Enter" });
    fireEvent.keyDown(marker, { key: " " });
    fireEvent.keyDown(marker, { key: "a" });
    expect(properties.onSelect).toHaveBeenCalledTimes(3);
  });

  test("highlights the selected call", async () => {
    const { container } = renderMap({ selectedId: "1:3" });
    await waitFor(() => {
      expect(container.querySelectorAll(".call-marker.selected")).toHaveLength(1);
    });
    expect(container.querySelector(".call-marker.selected")).toHaveAttribute(
      "title",
      "ALARM - BURGLAR, 100 block of BROADWAY, Sep 19, 11:57 AM CDT",
    );
  });

  test("distinguishes a bounded set of markers from every located call", () => {
    renderMap({
      result: { matching: 4000, withCoordinates: 4000, truncated: true, calls: [mapCall(1)] },
    });
    expect(
      screen.getByText("Showing the 1 most recent of 4,000 calls with a location.", {
        exact: false,
      }),
    ).toBeVisible();
    expect(screen.queryByText(/appear only in the list/)).not.toBeInTheDocument();
  });

  test("keeps working without tiles", async () => {
    const { container } = renderMap();
    const tile = await waitFor(() => {
      const found = container.querySelector("img.leaflet-tile");
      expect(found).not.toBeNull();
      return found!;
    });
    fireEvent.error(tile);
    expect(await screen.findByText(/Map tiles could not be loaded/)).toBeVisible();
  });

  test("shows loading and failures without affecting the list", () => {
    const { unmount } = renderMap({ result: undefined, state: "pending" });
    expect(screen.getByText("Loading map locations…")).toBeVisible();
    unmount();
    renderMap({ result: undefined, state: "error", error: new Error("HTTP 502") });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Map locations could not be loaded (HTTP 502). The list is unaffected.",
    );
  });

  test("labels markers whose details were not published", () => {
    const sparse = mapCall(4);
    sparse.receivedAt = null;
    sparse.record.Tencode_Description = null;
    expect(markerLabel(sparse)).toBe(
      "Call type not published, 100 block of BROADWAY, time not published",
    );
  });

  test("re-measures the map when its container changes size, such as when it is shown", () => {
    const invalidate = vi.spyOn(LeafletMap.prototype, "invalidateSize");
    const { container, unmount } = renderMap();
    const map = container.querySelector(".leaflet-container")!;
    FakeResizeObserver.resize(map);
    expect(invalidate).toHaveBeenCalled();
    unmount();
    invalidate.mockClear();
    FakeResizeObserver.resize(map);
    expect(invalidate).not.toHaveBeenCalled();
  });
});
