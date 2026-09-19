import type { Connection } from "../api/events.ts";
import type { SourceStatus, StatusResult } from "../api/types.ts";
import { formatCallTime, formatRelative } from "../time.ts";

// Ingestion checks the source about twice a second; this long without a successful check means
// updates have stalled even if the worker has not reported a problem.
const STALE_AFTER_MS = 2 * 60_000;

type Tone = "ok" | "info" | "warning" | "critical";

interface Description {
  tone: Tone;
  headline: string;
  detail: string;
}

interface StatusBarProperties {
  status: StatusResult | undefined;
  error: Error | undefined;
  connection: Connection;
  now: number;
}

const ICONS: ReadonlyMap<Tone, string> = new Map([
  ["ok", "●"],
  ["info", "○"],
  ["warning", "▲"],
  ["critical", "■"],
]);

function describeSource(source: SourceStatus, now: number): Description {
  const retained = "Showing the calls already collected.";
  switch (source.state) {
    case "STARTING": {
      return {
        tone: "info",
        headline: "Starting",
        detail: "Connecting to Metro Nashville's published data.",
      };
    }
    case "BACKFILLING": {
      const percent = Math.floor((source.backfill?.fraction ?? 0) * 100);
      return {
        tone: "info",
        headline: "Loading history",
        detail: `${String(percent)}% collected. Counts may be incomplete until it finishes.`,
      };
    }
    case "DEGRADED": {
      const retry = source.retryAt ? ` Next attempt ${formatCallTime(source.retryAt)}.` : "";
      return {
        tone: "warning",
        headline: "Metro Nashville's data service isn't responding",
        detail: `${retained}${retry}`,
      };
    }
    case "SCHEMA_INCOMPATIBLE": {
      const problem = source.detail === null ? "" : ` Problem: ${source.detail}.`;
      return {
        tone: "critical",
        headline: "The source changed its format",
        detail: `Updates are paused until the change is reviewed.${problem} ${retained}`,
      };
    }
    case "LIVE": {
      const lastPoll = source.lastPollAt === null ? undefined : Date.parse(source.lastPollAt);
      if (lastPoll === undefined || now - lastPoll > STALE_AFTER_MS) {
        const when = lastPoll === undefined ? "has not happened yet" : `was ${formatRelative(lastPoll, now)}`;
        return {
          tone: "warning",
          headline: "Updates delayed",
          detail: `The last successful check ${when}. ${retained}`,
        };
      }
      return { tone: "ok", headline: "Live", detail: "Checking for newly published calls." };
    }
  }
}

function describe(
  status: StatusResult | undefined,
  error: Error | undefined,
  now: number,
): Description {
  if (error) {
    return {
      tone: "critical",
      headline: "The dashboard can't reach the panel's API",
      detail: "Retrying automatically.",
    };
  }
  if (!status) {
    return { tone: "info", headline: "Checking the data source", detail: "" };
  }
  const [source] = status.sourceStatus;
  return source
    ? describeSource(source, now)
    : {
        tone: "info",
        headline: "Waiting for the ingestion service",
        detail: "No calls have been collected yet.",
      };
}

function Moment({ value, now, relative }: { value: string | null; now: number; relative: boolean }) {
  return value === null ? (
    <>Not yet</>
  ) : (
    <time dateTime={value}>
      {relative ? formatRelative(Date.parse(value), now) : formatCallTime(value)}
    </time>
  );
}

const CONNECTION_TEXT: ReadonlyMap<Connection, string> = new Map([
  ["connecting", "Connecting to live updates…"],
  ["open", "Live updates on"],
  ["reconnecting", "Live updates interrupted; reconnecting…"],
]);

export function StatusBar({ status, error, connection, now }: StatusBarProperties) {
  const description = describe(status, error, now);
  const source = status?.sourceStatus[0];
  const backfill = source?.backfill;
  return (
    <header className="status-bar">
      <div className="masthead">
        <h1>Nashville police calls</h1>
        <p className="subtitle">
          Calls for service published by Metro Nashville Police. A call is a request for police
          response, not a confirmed crime, and may not be an emergency that is still active.
        </p>
      </div>
      <div className="source-status">
        <p
          className="status-message"
          data-tone={description.tone}
          role="status"
          aria-label="Data source status"
        >
          <span className="tone-icon" aria-hidden="true">
            {ICONS.get(description.tone)}
          </span>
          <strong>{description.headline}</strong> <span>{description.detail}</span>
        </p>
        {source && (
          <dl className="freshness" role="group" aria-label="Data freshness">
            <div>
              <dt>Last successful check</dt>
              <dd>
                <Moment value={source.lastPollAt} now={now} relative />
              </dd>
            </div>
            <div>
              <dt>Last new or changed call</dt>
              <dd>
                <Moment value={source.lastChangeAt} now={now} relative />
              </dd>
            </div>
            <div>
              <dt>Newest call received</dt>
              <dd>
                <Moment value={source.latestCallReceivedAt} now={now} relative={false} />
              </dd>
            </div>
            <div>
              <dt>Source last edited</dt>
              <dd>
                <Moment value={source.upstreamEditedAt} now={now} relative={false} />
              </dd>
            </div>
          </dl>
        )}
        {backfill && backfill.completedAt === null && (
          <progress
            className="backfill"
            aria-label="History loaded"
            value={backfill.fraction}
            max={1}
          />
        )}
        <p className="connection" data-connection={connection}>
          {CONNECTION_TEXT.get(connection)}
        </p>
      </div>
    </header>
  );
}
