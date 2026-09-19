import { screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { FakeApi } from "./fake-api.ts";
import { FakeVersionSource } from "./fake-version-source.ts";
import { iso, liveStatus, T0 } from "./fixtures.ts";

test("mounts the monitor and subscribes the browser to live updates", async () => {
  FakeVersionSource.reset();
  vi.stubGlobal("EventSource", FakeVersionSource);
  new FakeApi()
    .on("Anchor", () => ({ dataVersion: 1, serverTime: iso(T0) }))
    .on("Status", () => ({ serverTime: iso(T0), sourceStatus: [liveStatus()] }));
  document.body.innerHTML = '<div id="root"></div>';

  await import("../src/main.tsx");

  expect(await screen.findByRole("heading", { name: "Nashville police calls" })).toBeVisible();
  await waitFor(() => {
    expect(FakeVersionSource.latest().url).toBe("/events");
  });
});
