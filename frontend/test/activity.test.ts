import { describe, expect, test } from "vitest";

import { activityBuckets } from "../src/activity.ts";

const HOUR = 3_600_000;

describe("activityBuckets", () => {
  test("fills every hour of a short range, including hours without calls", () => {
    const buckets = activityBuckets(
      [
        { start: "2026-09-19T15:00:00.000Z", count: 4 },
        { start: "2026-09-19T17:00:00.000Z", count: 9 },
      ],
      { since: "2026-09-19T14:30:00.000Z", until: "2026-09-19T17:30:00.000Z" },
    );
    expect(buckets.unit).toBe("hour");
    expect(buckets.buckets).toEqual([
      { key: "2026-09-19T14:00:00.000Z", label: "Sep 19, 9 AM", count: 0 },
      { key: "2026-09-19T15:00:00.000Z", label: "Sep 19, 10 AM", count: 4 },
      { key: "2026-09-19T16:00:00.000Z", label: "Sep 19, 11 AM", count: 0 },
      { key: "2026-09-19T17:00:00.000Z", label: "Sep 19, 12 PM", count: 9 },
    ]);
  });

  test("groups longer ranges into Chicago calendar days", () => {
    const since = Date.parse("2026-09-15T05:00:00.000Z");
    const buckets = activityBuckets(
      [
        // 11 PM on Sep 15 in Chicago is already Sep 16 in UTC.
        { start: new Date(since + 23 * HOUR).toISOString(), count: 2 },
        { start: new Date(since + 24 * HOUR).toISOString(), count: 3 },
        { start: new Date(since + 25 * HOUR).toISOString(), count: 1 },
      ],
      { since: new Date(since).toISOString(), until: new Date(since + 4 * 24 * HOUR).toISOString() },
    );
    expect(buckets.unit).toBe("day");
    expect(buckets.buckets).toEqual([
      { key: "2026-09-15", label: "Sep 15", count: 2 },
      { key: "2026-09-16", label: "Sep 16", count: 4 },
      { key: "2026-09-17", label: "Sep 17", count: 0 },
      { key: "2026-09-18", label: "Sep 18", count: 0 },
    ]);
  });
});
