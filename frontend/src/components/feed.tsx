import { useId, useRef } from "react";

import type { FeedCall } from "../api/types.ts";
import { formatCallTime } from "../time.ts";

export interface FeedProperties {
  calls: FeedCall[];
  state: "pending" | "error" | "success";
  error: Error | undefined;
  refreshing: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  newCalls: number;
  paused: boolean;
  selectedId: string | undefined;
  rangeText: string;
  backfilling: boolean;
  onLoadMore: () => void;
  onShowNew: () => void;
  onTogglePause: () => void;
  onSelect: (id: string) => void;
  onRetry: () => void;
}

// Upstream publishes a block number and street name; neither is an exact address.
export function describePlace(block: string | null, street: string | null): string {
  if (street === null) {
    return "Location not published";
  }
  return block === null ? street : `${block} block of ${street}`;
}

export function describeArea(zone: string | null, sector: string | null): string {
  const zoneText = zone === null ? "" : `Zone ${zone}`;
  const sectorText = sector === null ? "" : `Sector ${sector}`;
  return [zoneText, sectorText].filter((part) => part !== "").join(" · ");
}

function CallRow({
  call,
  selected,
  onSelect,
}: {
  call: FeedCall;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const { record } = call;
  const area = describeArea(record.ZONE_, record.Sector);
  return (
    <li>
      <button
        type="button"
        className="call-row"
        aria-pressed={selected}
        onClick={() => {
          onSelect(call.id);
        }}
      >
        <span className="call-type">{record.Tencode_Description ?? "Call type not published"}</span>
        {call.receivedAt === null ? (
          <span className="call-time">Time not published</span>
        ) : (
          <time className="call-time" dateTime={call.receivedAt}>
            {formatCallTime(call.receivedAt)}
          </time>
        )}
        <span className="call-place">{describePlace(record.Block, record.Street_Name)}</span>
        {area !== "" && <span className="call-area">{area}</span>}
        <span className="call-disposition">
          {record.Disposition_Description ?? "No disposition yet"}
        </span>
        {(!call.hasCoordinates || !call.sourcePresent) && (
          <span className="tags">
            {!call.hasCoordinates && <span className="tag">No map location</span>}
            {!call.sourcePresent && <span className="tag">No longer published</span>}
          </span>
        )}
      </button>
    </li>
  );
}

function Contents(properties: FeedProperties) {
  if (properties.state === "pending") {
    return <p className="feed-note">Loading calls…</p>;
  }
  if (properties.state === "error") {
    return (
      <div className="feed-note" role="alert">
        <p>Calls could not be loaded ({properties.error?.message}).</p>
        <button type="button" onClick={properties.onRetry}>
          Try again
        </button>
      </div>
    );
  }
  return properties.calls.length === 0 ? (
    <p className="feed-note">
      No calls match these filters in {properties.rangeText}.
      {properties.backfilling && " History is still loading, so older calls may appear later."}
    </p>
  ) : undefined;
}

export function Feed(properties: FeedProperties) {
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const list = useRef<HTMLOListElement>(null);
  const { calls, newCalls, paused } = properties;

  // Showing new calls replaces the list, so start the reader at its top.
  function showNew(): void {
    properties.onShowNew();
    // Both elements render whenever this button does.
    list.current!.scrollTop = 0;
    heading.current!.focus();
  }

  return (
    <section className="feed" aria-labelledby={headingId}>
      <div className="feed-toolbar">
        <h2 id={headingId} ref={heading} tabIndex={-1}>
          Calls
        </h2>
        <button
          type="button"
          className="quiet"
          aria-pressed={paused}
          onClick={properties.onTogglePause}
        >
          {paused ? "Resume updates" : "Pause updates"}
        </button>
      </div>
      <div className="arrivals" aria-live="polite">
        {paused && (
          <p className="feed-note">
            Updates paused. The list, map, and summary stay as they are; new calls are still
            counted.
          </p>
        )}
        {newCalls > 0 && (
          <button type="button" className="show-new" onClick={showNew}>
            Show {newCalls} new {newCalls === 1 ? "call" : "calls"}
          </button>
        )}
      </div>
      <Contents {...properties} />
      <ol
        className="feed-list"
        ref={list}
        aria-label="Calls, newest first"
        aria-busy={properties.refreshing}
      >
        {calls.map((call) => (
          <CallRow
            key={call.id}
            call={call}
            selected={call.id === properties.selectedId}
            onSelect={properties.onSelect}
          />
        ))}
      </ol>
      {properties.hasMore && (
        <button
          type="button"
          className="load-more"
          disabled={properties.loadingMore}
          onClick={properties.onLoadMore}
        >
          {properties.loadingMore ? "Loading more calls…" : "Load more calls"}
        </button>
      )}
    </section>
  );
}
