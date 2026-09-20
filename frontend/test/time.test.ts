import { describe, expect, test } from "vitest";

import {
  addDays,
  customWindow,
  formatCallTime,
  formatRelative,
  presetWindow,
  todayInChicago,
} from "../src/time.ts";

describe("formatCallTime", () => {
  test("shows Chicago wall time with its zone abbreviation", () => {
    expect(formatCallTime("2026-09-19T16:00:00+00:00")).toBe("Sep 19, 11:00 AM CDT");
  });

  test("switches to daylight time at 2 AM on March 8, 2026", () => {
    expect(formatCallTime("2026-03-08T07:59:00+00:00")).toBe("Mar 8, 1:59 AM CST");
    expect(formatCallTime("2026-03-08T08:00:00+00:00")).toBe("Mar 8, 3:00 AM CDT");
  });

  test("returns to standard time at 2 AM on November 1, 2026", () => {
    expect(formatCallTime("2026-11-01T06:59:00+00:00")).toBe("Nov 1, 1:59 AM CDT");
    expect(formatCallTime("2026-11-01T07:00:00+00:00")).toBe("Nov 1, 1:00 AM CST");
  });
});

describe("formatRelative", () => {
  const now = Date.parse("2026-09-19T12:00:00Z");

  test.each([
    [0, "now"],
    [5000, "5 seconds ago"],
    [90_000, "2 minutes ago"],
    [3 * 3_600_000, "3 hours ago"],
    [2 * 86_400_000, "2 days ago"],
  ])("describes %i ms ago as %s", (elapsed, expected) => {
    expect(formatRelative(now - elapsed, now)).toBe(expected);
  });

  test("treats small clock differences in the future as now", () => {
    expect(formatRelative(now + 800, now)).toBe("now");
  });
});

describe("presetWindow", () => {
  test("covers the preceding period with a few minutes of allowance for clock skew", () => {
    const now = Date.parse("2026-09-19T12:00:00Z");
    expect(presetWindow("24h", now)).toEqual({
      since: "2026-09-18T12:00:00.000Z",
      until: "2026-09-19T12:05:00.000Z",
    });
    expect(presetWindow("7d", now).since).toBe("2026-09-12T12:00:00.000Z");
    expect(presetWindow("30d", now).since).toBe("2026-08-20T12:00:00.000Z");
  });
});

describe("customWindow", () => {
  test("spans whole Chicago days from the first date through the last", () => {
    expect(customWindow("2026-09-01", "2026-09-19")).toEqual({
      since: "2026-09-01T05:00:00.000Z",
      until: "2026-09-20T05:00:00.000Z",
    });
  });

  test("uses each day's own offset across a daylight-saving change", () => {
    expect(customWindow("2026-03-01", "2026-03-10")).toEqual({
      since: "2026-03-01T06:00:00.000Z",
      until: "2026-03-11T05:00:00.000Z",
    });
  });
});

test("todayInChicago reads the calendar date in Chicago, not UTC", () => {
  expect(todayInChicago(Date.parse("2026-09-20T03:30:00Z"))).toBe("2026-09-19");
  expect(todayInChicago(Date.parse("2026-09-20T05:30:00Z"))).toBe("2026-09-20");
});

test("addDays moves a calendar date across month ends", () => {
  expect(addDays("2026-09-19", -6)).toBe("2026-09-13");
  expect(addDays("2026-09-30", 1)).toBe("2026-10-01");
});
