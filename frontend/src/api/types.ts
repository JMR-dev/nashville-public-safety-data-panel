// Response shapes for the operations in documents.ts, written from docs/schema.graphql.
// Upstream record fields keep their published names; every one of them may be null.

export type SourceState = "STARTING" | "BACKFILLING" | "LIVE" | "DEGRADED" | "SCHEMA_INCOMPATIBLE";

export interface CallFilter {
  since: string;
  until: string;
  ZONE_: string[];
  Sector: string[];
  Tencode_Description: string[];
  Disposition_Description: string[];
}

export interface AnchorResult {
  dataVersion: number;
  serverTime: string;
}

export interface FeedCall {
  id: string;
  receivedAt: string | null;
  hasCoordinates: boolean;
  sourcePresent: boolean;
  record: {
    OBJECTID: number;
    Tencode_Description: string | null;
    Disposition_Description: string | null;
    Block: string | null;
    Street_Name: string | null;
    ZONE_: string | null;
    Sector: string | null;
  };
}

export interface FeedPage {
  asOfVersion: number;
  pageInfo: { endCursor: string | null; hasNextPage: boolean };
  nodes: FeedCall[];
}

export interface FeedResult {
  calls: FeedPage;
}

export interface NewCallsResult {
  newCallCount: number;
}

export interface CallRecord {
  OBJECTID: number;
  Event_Number: string | null;
  Complaint_Number: number | null;
  Tencode: number | null;
  Tencode_Description: string | null;
  Tencode_Suffix: string | null;
  Tencode_Suffix_Description: string | null;
  Disposition_Code: string | null;
  Disposition_Description: string | null;
  Block: string | null;
  Street_Name: string | null;
  Unit_Dispatched: string | null;
  Shift: string | null;
  Sector: string | null;
  Mapped_Location: string | null;
  POINT_X: number | null;
  POINT_Y: number | null;
  ZONE_: string | null;
  Latitude: number | null;
  Longitude: number | null;
  RPA: string | null;
  // Epoch milliseconds, as published.
  Call_Received: number | null;
}

export interface CallDetail {
  id: string;
  generation: number;
  receivedAt: string | null;
  hasCoordinates: boolean;
  sourcePresent: boolean;
  removedAt: string | null;
  firstSeenAt: string;
  lastChangedAt: string;
  // ArcGIS attributes are scalars.
  additionalAttributes: Record<string, string | number | boolean | null>;
  record: CallRecord;
}

export interface CallDetailResult {
  call: CallDetail | null;
}

export interface MapCall {
  id: string;
  receivedAt: string | null;
  record: {
    Latitude: number | null;
    Longitude: number | null;
    Tencode_Description: string | null;
    Block: string | null;
    Street_Name: string | null;
  };
}

export interface MapCallsResult {
  mapCalls: {
    matching: number;
    withCoordinates: number;
    truncated: boolean;
    calls: MapCall[];
  };
}

export interface TypeCount {
  Tencode_Description: string | null;
  count: number;
}

export interface HourCount {
  start: string;
  count: number;
}

export interface Summary {
  total: number;
  withCoordinates: number;
  withoutCoordinates: number;
  types: { otherCount: number; entries: TypeCount[] };
  hourly: HourCount[];
}

export interface SummaryResult {
  summary: Summary;
}

export interface FilterValues {
  ZONE_: string[];
  Sector: string[];
  Tencode_Description: string[];
  Disposition_Description: string[];
}

export interface FilterValuesResult {
  filterValues: FilterValues;
}

export interface SourceStatus {
  source: string;
  state: SourceState;
  detail: string | null;
  lastPollAt: string | null;
  lastChangeAt: string | null;
  latestCallReceivedAt: string | null;
  upstreamEditedAt: string | null;
  degradedSince: string | null;
  retryAt: string | null;
  backfill: {
    fraction: number;
    rangesTotal: number;
    rangesCompleted: number;
    completedAt: string | null;
  } | null;
}

export interface StatusResult {
  serverTime: string;
  sourceStatus: SourceStatus[];
}
