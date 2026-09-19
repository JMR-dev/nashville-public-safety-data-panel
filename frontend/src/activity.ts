import type { HourCount } from "./api/types.ts";
import { addDays, TIME_ZONE, type TimeWindow, todayInChicago } from "./time.ts";

const HOUR = 3_600_000;
// Ranges up to this long are shown by the hour; longer ones by Chicago calendar day.
const HOURLY_LIMIT = 3 * 24 * HOUR;

export interface Bucket {
  key: string;
  label: string;
  count: number;
}

export interface Activity {
  unit: "hour" | "day";
  buckets: Bucket[];
}

const hourLabel = new Intl.DateTimeFormat("en-US", {
  timeZone: TIME_ZONE,
  month: "short",
  day: "numeric",
  hour: "numeric",
});

const dayLabel = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "short",
  day: "numeric",
});

function hours(counts: ReadonlyMap<number, number>, since: number, until: number): Bucket[] {
  const buckets: Bucket[] = [];
  for (let start = Math.floor(since / HOUR) * HOUR; start < until; start += HOUR) {
    buckets.push({
      key: new Date(start).toISOString(),
      label: hourLabel.format(start).replaceAll(" ", " "),
      count: counts.get(start) ?? 0,
    });
  }
  return buckets;
}

function days(counts: ReadonlyMap<number, number>, since: number, until: number): Bucket[] {
  const totals = new Map<string, number>();
  for (const [start, count] of counts) {
    const day = todayInChicago(start);
    totals.set(day, (totals.get(day) ?? 0) + count);
  }
  const buckets: Bucket[] = [];
  const last = todayInChicago(until - 1);
  for (let day = todayInChicago(since); day <= last; day = addDays(day, 1)) {
    buckets.push({
      key: day,
      label: dayLabel.format(Date.parse(day)),
      count: totals.get(day) ?? 0,
    });
  }
  return buckets;
}

// Every period in the window, including empty ones, so gaps in activity stay visible.
export function activityBuckets(hourly: readonly HourCount[], window: TimeWindow): Activity {
  const counts = new Map(hourly.map((entry) => [Date.parse(entry.start), entry.count]));
  const since = Date.parse(window.since);
  const until = Date.parse(window.until);
  return until - since <= HOURLY_LIMIT
    ? { unit: "hour", buckets: hours(counts, since, until) }
    : { unit: "day", buckets: days(counts, since, until) };
}
