import { useId } from "react";

import { type Activity as ActivityData, activityBuckets } from "../activity.ts";
import type { Summary, TypeCount } from "../api/types.ts";
import type { TimeWindow } from "../time.ts";

export interface SummaryPanelProperties {
  summary: Summary | undefined;
  state: "pending" | "error" | "success";
  error: Error | undefined;
  refreshing: boolean;
  rangeText: string;
  window: TimeWindow;
}

const numbers = new Intl.NumberFormat("en-US");

function share(count: number, largest: number): string {
  return `${String(largest === 0 ? 0 : (count / largest) * 100)}%`;
}

function TypeBars({ entries, other }: { entries: TypeCount[]; other: number }) {
  const headingId = useId();
  const largest = Math.max(other, ...entries.map((entry) => entry.count));
  return (
    <>
      <h3 id={headingId}>Most common call types</h3>
      <ol className="type-bars" aria-labelledby={headingId}>
        {entries.map((entry) => (
          <li key={entry.Tencode_Description ?? ""}>
            <span className="type-name">{entry.Tencode_Description ?? "Type not published"}</span>
            <span className="bar-track" aria-hidden="true">
              <span className="bar" style={{ width: share(entry.count, largest) }} />
            </span>
            <span className="type-count">{numbers.format(entry.count)}</span>
          </li>
        ))}
        {other > 0 && (
          <li className="other">
            <span className="type-name">Other types</span>
            <span className="bar-track" aria-hidden="true">
              <span className="bar" style={{ width: share(other, largest) }} />
            </span>
            <span className="type-count">{numbers.format(other)}</span>
          </li>
        )}
      </ol>
    </>
  );
}

function Activity({ buckets, unit }: ActivityData) {
  const captionId = useId();
  const largest = Math.max(...buckets.map((bucket) => bucket.count));
  return (
    <figure className="activity" aria-labelledby={captionId}>
      <figcaption id={captionId}>{unit === "hour" ? "Calls per hour" : "Calls per day"}</figcaption>
      <div className="chart" aria-hidden="true">
        <span className="axis-max">{numbers.format(largest)}</span>
        <div className="columns">
          {buckets.map((bucket) => (
            <span
              key={bucket.key}
              className="column"
              data-tip={`${bucket.label}: ${numbers.format(bucket.count)}`}
            >
              <span className="bar" style={{ height: share(bucket.count, largest) }} />
            </span>
          ))}
        </div>
        <div className="axis-labels">
          <span>{buckets[0]?.label}</span>
          <span>{buckets.at(-1)?.label}</span>
        </div>
      </div>
      <details>
        <summary>Show as a table</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">{unit === "hour" ? "Hour" : "Day"}</th>
              <th scope="col">Calls</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map((bucket) => (
              <tr key={bucket.key}>
                <th scope="row">{bucket.label}</th>
                <td>{numbers.format(bucket.count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

function Contents({ summary, state, error, rangeText, window }: SummaryPanelProperties) {
  if (state === "pending") {
    return <p>Loading summary…</p>;
  }
  if (state === "error" || !summary) {
    return <p role="alert">The summary could not be loaded ({error?.message}).</p>;
  }
  if (summary.total === 0) {
    return <p>No published calls in {rangeText}.</p>;
  }
  const activity = activityBuckets(summary.hourly, window);
  return (
    <>
      <p className="hero">
        <span className="hero-number">{numbers.format(summary.total)}</span>{" "}
        <span>published calls in {rangeText}</span>
      </p>
      <p className="hint">
        {numbers.format(summary.withoutCoordinates)} have no map location and appear only in the
        list. Counts are published call records, not confirmed crimes.
      </p>
      <TypeBars entries={summary.types.entries} other={summary.types.otherCount} />
      <Activity {...activity} />
    </>
  );
}

export function SummaryPanel(properties: SummaryPanelProperties) {
  const headingId = useId();
  return (
    <section className="summary" aria-labelledby={headingId} aria-busy={properties.refreshing}>
      <h2 id={headingId}>Summary</h2>
      <Contents {...properties} />
    </section>
  );
}
