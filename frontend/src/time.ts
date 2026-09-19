// Times are stored and exchanged in UTC and shown in Nashville's time zone.

export const TIME_ZONE = "America/Chicago";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
// Upstream timestamps can run slightly ahead of this server's clock.
const SKEW_ALLOWANCE = 5 * MINUTE;

export type RangePreset = "24h" | "7d" | "30d";

export interface TimeWindow {
  since: string;
  until: string;
}

function presetLength(preset: RangePreset): number {
  switch (preset) {
    case "24h": {
      return DAY;
    }
    case "7d": {
      return 7 * DAY;
    }
    case "30d": {
      return 30 * DAY;
    }
  }
}

const callTimeFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: TIME_ZONE,
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

// ISO-like "YYYY-MM-DD HH:MM" wall time in Chicago.
const wallFormat = new Intl.DateTimeFormat("sv-SE", {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const dateFormat = new Intl.DateTimeFormat("en-CA", {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const relativeFormat = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

// ICU separates the time from AM/PM with a narrow no-break space; keep plain spaces.
function plain(text: string): string {
  return text.replaceAll(" ", " ");
}

export function formatCallTime(iso: string): string {
  return plain(callTimeFormat.format(new Date(iso)));
}

export function formatRelative(then: number, now: number): string {
  const elapsed = Math.max(0, now - then);
  if (elapsed < MINUTE) {
    return relativeFormat.format(-Math.floor(elapsed / 1000), "second");
  }
  if (elapsed < HOUR) {
    return relativeFormat.format(-Math.round(elapsed / MINUTE), "minute");
  }
  return elapsed < DAY
    ? relativeFormat.format(-Math.round(elapsed / HOUR), "hour")
    : relativeFormat.format(-Math.round(elapsed / DAY), "day");
}

export function presetWindow(preset: RangePreset, now: number): TimeWindow {
  return {
    since: new Date(now - presetLength(preset)).toISOString(),
    until: new Date(now + SKEW_ALLOWANCE).toISOString(),
  };
}

// Milliseconds between UTC and Chicago wall time at an instant, e.g. -5 hours during CDT.
function chicagoOffset(instant: number): number {
  const wall = Date.parse(`${wallFormat.format(instant).replace(" ", "T")}:00Z`);
  return wall - Math.floor(instant / MINUTE) * MINUTE;
}

// The UTC instant of midnight in Chicago on a calendar date (YYYY-MM-DD).
function chicagoMidnight(date: string): number {
  const utcMidnight = Date.parse(`${date}T00:00:00Z`);
  // Daylight-saving changes happen at 2 AM, so the offset a few hours in is midnight's offset.
  return utcMidnight - chicagoOffset(utcMidnight + 6 * HOUR);
}

// A calendar date (YYYY-MM-DD) moved by whole days.
export function addDays(date: string, days: number): string {
  return new Date(Date.parse(`${date}T00:00:00Z`) + days * DAY).toISOString().slice(0, 10);
}

// Whole Chicago calendar days from ``first`` through ``last`` inclusive.
export function customWindow(first: string, last: string): TimeWindow {
  return {
    since: new Date(chicagoMidnight(first)).toISOString(),
    until: new Date(chicagoMidnight(addDays(last, 1))).toISOString(),
  };
}

export function todayInChicago(now: number): string {
  return dateFormat.format(now);
}
