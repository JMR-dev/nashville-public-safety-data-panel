import { describe, expect, test } from "vitest";

import {
  callFilter,
  describeCallType,
  DEFAULT_FILTERS,
  describeRange,
  isDefault,
  rangeProblem,
  windowFor,
} from "../src/filters.ts";

const NOW = Date.parse("2026-09-19T12:00:00Z");

describe("windowFor", () => {
  test("follows the clock for presets and fixed days for custom ranges", () => {
    expect(windowFor({ kind: "preset", preset: "24h" }, NOW).since).toBe(
      "2026-09-18T12:00:00.000Z",
    );
    expect(windowFor({ kind: "custom", first: "2026-09-01", last: "2026-09-02" }, NOW)).toEqual({
      since: "2026-09-01T05:00:00.000Z",
      until: "2026-09-03T05:00:00.000Z",
    });
  });
});

test("callFilter sends each chosen value under its upstream field name", () => {
  const window = { since: "a", until: "b" };
  expect(callFilter(DEFAULT_FILTERS, window)).toEqual({
    since: "a",
    until: "b",
    ZONE_: [],
    Sector: [],
    Tencode_Description: [],
    Disposition_Description: [],
  });
  expect(
    callFilter(
      { ...DEFAULT_FILTERS, zone: "15", sector: "C1", type: "ALARM", disposition: "REPORT" },
      window,
    ),
  ).toMatchObject({
    ZONE_: ["15"],
    Sector: ["C1"],
    Tencode_Description: ["ALARM"],
    Disposition_Description: ["REPORT"],
  });
});

test("describeRange names the period in words", () => {
  expect(describeRange({ kind: "preset", preset: "24h" })).toBe("the last 24 hours");
  expect(describeRange({ kind: "preset", preset: "7d" })).toBe("the last 7 days");
  expect(describeRange({ kind: "preset", preset: "30d" })).toBe("the last 30 days");
  expect(describeRange({ kind: "custom", first: "2026-09-01", last: "2026-09-19" })).toBe(
    "Sep 1 through Sep 19, 2026",
  );
});

test("isDefault recognizes untouched filters", () => {
  expect(isDefault(DEFAULT_FILTERS)).toBe(true);
  expect(isDefault({ ...DEFAULT_FILTERS, zone: "15" })).toBe(false);
  expect(isDefault({ ...DEFAULT_FILTERS, range: { kind: "preset", preset: "7d" } })).toBe(false);
});

describe("rangeProblem", () => {
  test.each([
    ["2026-09-01", "2026-09-19", undefined],
    ["", "2026-09-19", "Choose both dates."],
    ["2026-09-20", "2026-09-19", "The start date must be on or before the end date."],
    ["2026-09-18", "2026-09-20", "The end date cannot be after today."],
    ["2026-06-01", "2026-09-19", "Choose a range of 92 days or fewer."],
  ])("from %s through %s", (first, last, problem) => {
    expect(rangeProblem(first, last, "2026-09-19")).toBe(problem);
  });
});

describe("describeCallType", () => {
  test("labels the ten-codes the source publishes as codes", () => {
    // Metro Nashville publishes Tencode_Description as the numeric code itself.
    expect(describeCallType("43")).toBe("Code 43");
    expect(describeCallType("3")).toBe("Code 3");
  });

  test("shows published text unchanged and says when there is none", () => {
    expect(describeCallType("ALARM - BURGLAR")).toBe("ALARM - BURGLAR");
    expect(describeCallType("10-50")).toBe("10-50");
    expect(describeCallType(null)).toBe("Call type not published");
  });
});
