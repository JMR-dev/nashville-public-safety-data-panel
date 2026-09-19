import type { CallFilter } from "./api/types.ts";
import { customWindow, presetWindow, type RangePreset, type TimeWindow } from "./time.ts";

export type DateRange =
  | { kind: "preset"; preset: RangePreset }
  | { kind: "custom"; first: string; last: string };

// An empty string means "all values".
export interface Filters {
  range: DateRange;
  zone: string;
  sector: string;
  type: string;
  disposition: string;
}

export const DEFAULT_FILTERS: Filters = {
  range: { kind: "preset", preset: "24h" },
  zone: "",
  sector: "",
  type: "",
  disposition: "",
};

// The API's limit on how long a date range may be.
const MAX_RANGE_MS = 92 * 86_400_000;

const dayFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "short",
  day: "numeric",
});
const dayWithYearFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "short",
  day: "numeric",
  year: "numeric",
});

export function windowFor(range: DateRange, now: number): TimeWindow {
  return range.kind === "preset"
    ? presetWindow(range.preset, now)
    : customWindow(range.first, range.last);
}

function selected(value: string): string[] {
  return value === "" ? [] : [value];
}

export function callFilter(filters: Filters, window: TimeWindow): CallFilter {
  return {
    ...window,
    ZONE_: selected(filters.zone),
    Sector: selected(filters.sector),
    Tencode_Description: selected(filters.type),
    Disposition_Description: selected(filters.disposition),
  };
}

// Metro Nashville publishes Tencode_Description as the ten-code itself ("43"), not a name for it,
// and publishes no table of names, so a numeric value is shown as the code it is.
export function describeCallType(value: string | null): string {
  if (value === null) {
    return "Call type not published";
  }
  return /^\d+$/.test(value) ? `Code ${value}` : value;
}

export function describeRange(range: DateRange): string {
  if (range.kind === "custom") {
    const first = dayFormat.format(Date.parse(range.first));
    return `${first} through ${dayWithYearFormat.format(Date.parse(range.last))}`;
  }
  switch (range.preset) {
    case "24h": {
      return "the last 24 hours";
    }
    case "7d": {
      return "the last 7 days";
    }
    case "30d": {
      return "the last 30 days";
    }
  }
}

export function isDefault(filters: Filters): boolean {
  return (
    filters.range.kind === "preset" &&
    filters.range.preset === "24h" &&
    filters.zone === "" &&
    filters.sector === "" &&
    filters.type === "" &&
    filters.disposition === ""
  );
}

// Why a custom date range cannot be used, or undefined when it can.
export function rangeProblem(first: string, last: string, today: string): string | undefined {
  if (first === "" || last === "") {
    return "Choose both dates.";
  }
  if (first > last) {
    return "The start date must be on or before the end date.";
  }
  if (last > today) {
    return "The end date cannot be after today.";
  }
  const window = customWindow(first, last);
  return Date.parse(window.until) - Date.parse(window.since) > MAX_RANGE_MS
    ? "Choose a range of 92 days or fewer."
    : undefined;
}
