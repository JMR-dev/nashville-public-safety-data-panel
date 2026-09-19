import { useCallback, useId } from "react";

import { useCallDetail } from "../api/queries.ts";
import type { CallDetail } from "../api/types.ts";
import { describeCallType } from "../filters.ts";
import { formatCallTime } from "../time.ts";
import { describeArea, describePlace } from "./feed.tsx";

interface CallDetailsProperties {
  id: string;
  onClose: () => void;
}

type Scalar = string | number | boolean | null;

function formatValue(name: string, value: Scalar): string {
  if (value === null) {
    return "Not published";
  }
  return name === "Call_Received" && typeof value === "number"
    ? `${String(value)} (${formatCallTime(new Date(value).toISOString())})`
    : String(value);
}

function Fields({ label, values }: { label: string; values: [string, Scalar][] }) {
  return (
    <dl className="record-fields" role="group" aria-label={label}>
      {values.map(([name, value]) => (
        <div key={name}>
          <dt>
            <code>{name}</code>
          </dt>
          <dd>{formatValue(name, value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Details({ call }: { call: CallDetail }) {
  const { record } = call;
  const extra = Object.entries(call.additionalAttributes);
  const area = describeArea(record.ZONE_, record.Sector);
  return (
    <>
      <p className="detail-lede">
        {call.receivedAt !== null && (
          <>
            <time dateTime={call.receivedAt}>{formatCallTime(call.receivedAt)}</time>
            {" · "}
          </>
        )}
        <span>{`${describePlace(record.Block, record.Street_Name)} (approximate)`}</span>
      </p>
      {!call.sourcePresent && call.removedAt !== null && (
        <p className="notice">
          {`No longer in Metro Nashville's published data as of ${formatCallTime(call.removedAt)}.`}
        </p>
      )}
      <dl className="detail-summary">
        <div>
          <dt>Disposition</dt>
          <dd>{record.Disposition_Description ?? "No disposition yet"}</dd>
        </div>
        <div>
          <dt>Area</dt>
          <dd>{area === "" ? "Not published" : area}</dd>
        </div>
        <div>
          <dt>Map location</dt>
          <dd>
            {call.hasCoordinates
              ? "Approximate location published by the source."
              : "The source did not publish a usable location."}
          </dd>
        </div>
      </dl>
      <h3>Published record</h3>
      <p className="hint">Field names and values exactly as Metro Nashville publishes them.</p>
      <Fields label="Published record" values={Object.entries(record)} />
      {extra.length > 0 && (
        <>
          <h3>Additional published attributes</h3>
          <Fields label="Additional published attributes" values={extra} />
        </>
      )}
      <h3>Collection</h3>
      <dl className="detail-summary">
        <div>
          <dt>First collected</dt>
          <dd>{formatCallTime(call.firstSeenAt)}</dd>
        </div>
        <div>
          <dt>Last changed</dt>
          <dd>{formatCallTime(call.lastChangedAt)}</dd>
        </div>
        <div>
          <dt>Source generation</dt>
          <dd>{call.generation}</dd>
        </div>
      </dl>
    </>
  );
}

export function CallDetails({ id, onClose }: CallDetailsProperties) {
  const headingId = useId();
  const query = useCallDetail(id);
  // Take focus when opened. The parent remounts the panel (keyed by id) for each call.
  const focusHeading = useCallback((node: HTMLHeadingElement | null) => {
    node?.focus();
  }, []);
  const call = query.data;
  const title = call ? describeCallType(call.record.Tencode_Description) : "Call details";

  return (
    <aside
      className="call-details"
      aria-labelledby={headingId}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          onClose();
        }
      }}
    >
      <div className="details-header">
        <h2 id={headingId} ref={focusHeading} tabIndex={-1}>
          {title}
        </h2>
        <button type="button" className="quiet" aria-label="Close call details" onClick={onClose}>
          ×
        </button>
      </div>
      {query.isPending && <p>Loading call details…</p>}
      {query.isError && <p role="alert">Call details could not be loaded ({query.error.message}).</p>}
      {call === null && <p>This call is no longer available.</p>}
      {call && <Details call={call} />}
    </aside>
  );
}
