import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { useNow } from "../src/use-now.ts";

afterEach(() => {
  vi.useRealTimers();
});

test("useNow advances with the clock and stops when unmounted", () => {
  vi.useFakeTimers({ now: Date.parse("2026-09-19T17:00:00Z") });
  const { result, unmount } = renderHook(() => useNow(1000));
  expect(result.current).toBe(Date.parse("2026-09-19T17:00:00Z"));
  act(() => {
    vi.advanceTimersByTime(3000);
  });
  expect(result.current).toBe(Date.parse("2026-09-19T17:00:03Z"));
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});
