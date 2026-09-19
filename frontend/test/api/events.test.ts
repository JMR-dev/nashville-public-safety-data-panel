import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { useDataVersion } from "../../src/api/events.ts";
import { FakeVersionSource, openFakeSource } from "../fake-version-source.ts";

function render() {
  const onVersion = vi.fn();
  const onReconnect = vi.fn();
  const hook = renderHook(() => useDataVersion(openFakeSource, { onVersion, onReconnect }));
  return { ...hook, onVersion, onReconnect };
}

beforeEach(() => {
  FakeVersionSource.reset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useDataVersion", () => {
  test("subscribes to /events and reports each published data version", () => {
    const { result, onVersion, onReconnect } = render();
    expect(result.current).toBe("connecting");
    const source = FakeVersionSource.latest();
    expect(source.url).toBe("/events");

    act(() => {
      source.open();
      source.version(4);
      source.version(5);
    });
    expect(result.current).toBe("open");
    expect(onVersion.mock.calls).toEqual([[4], [5]]);
    expect(onReconnect).not.toHaveBeenCalled();
  });

  test("refreshes once the browser reconnects after an interruption", () => {
    const { result, onReconnect } = render();
    const source = FakeVersionSource.latest();
    act(() => {
      source.open();
      source.fail();
    });
    expect(result.current).toBe("reconnecting");
    act(() => {
      source.open();
    });
    expect(result.current).toBe("open");
    expect(onReconnect).toHaveBeenCalledOnce();
  });

  test("opens a new stream when the server closes it, then refreshes", () => {
    vi.useFakeTimers();
    const { result, onReconnect } = render();
    const first = FakeVersionSource.latest();
    act(() => {
      first.open();
      first.fail({ fatal: true });
    });
    expect(result.current).toBe("reconnecting");
    act(() => {
      vi.advanceTimersByTime(4999);
    });
    expect(FakeVersionSource.instances).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    const second = FakeVersionSource.latest();
    expect(second).not.toBe(first);
    expect(first.readyState).toBe(2);
    act(() => {
      second.open();
    });
    expect(result.current).toBe("open");
    expect(onReconnect).toHaveBeenCalledOnce();
  });

  test("closes the stream and cancels a pending reopen when unmounted", () => {
    vi.useFakeTimers();
    const { unmount } = render();
    const source = FakeVersionSource.latest();
    act(() => {
      source.fail({ fatal: true });
    });
    unmount();
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(FakeVersionSource.instances).toHaveLength(1);
    expect(source.readyState).toBe(2);
  });
});
