import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { App, createQueryClient } from "../src/app.tsx";
import { FakeApi } from "./fake-api.ts";
import { FakeVersionSource, openFakeSource } from "./fake-version-source.ts";

test("the default client relies on live notifications instead of refetching stale data", () => {
  const defaults = createQueryClient().getDefaultOptions().queries;
  expect(defaults?.staleTime).toBe(Infinity);
  expect(defaults?.refetchOnWindowFocus).toBe(false);
});

test("App renders the monitor with the client it is given", async () => {
  FakeVersionSource.reset();
  new FakeApi();
  const client = createQueryClient();
  render(<App openEvents={openFakeSource} client={client} />);
  expect(await screen.findByRole("heading", { name: "Nashville police calls" })).toBeVisible();
  expect(client.getQueryCache().find({ queryKey: ["status"] })).toBeDefined();
});
