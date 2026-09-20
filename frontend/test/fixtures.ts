import type {
  CallDetail,
  FeedCall,
  FilterValues,
  MapCall,
  SourceStatus,
  Summary,
} from "../src/api/types.ts";

export const T0 = Date.parse("2026-09-19T17:00:00Z");
export const HOUR = 3_600_000;

export function iso(epoch: number): string {
  return new Date(epoch).toISOString();
}

export function feedCall(oid: number, overrides: Partial<FeedCall["record"]> = {}): FeedCall {
  return {
    id: `1:${String(oid)}`,
    receivedAt: iso(T0 - oid * 60_000),
    hasCoordinates: true,
    sourcePresent: true,
    record: {
      OBJECTID: oid,
      Tencode_Description: "ALARM - BURGLAR",
      Disposition_Description: "ASSISTED CITIZEN",
      Block: "100",
      Street_Name: "BROADWAY",
      ZONE_: "15",
      Sector: "C1",
      ...overrides,
    },
  };
}

export function callDetail(oid: number, overrides: Partial<CallDetail> = {}): CallDetail {
  return {
    id: `1:${String(oid)}`,
    generation: 1,
    receivedAt: iso(T0 - oid * 60_000),
    hasCoordinates: true,
    sourcePresent: true,
    removedAt: null,
    firstSeenAt: iso(T0),
    lastChangedAt: iso(T0),
    additionalAttributes: {},
    record: {
      OBJECTID: oid,
      Event_Number: `PD2026${String(oid).padStart(8, "0")}`,
      Complaint_Number: null,
      Tencode: 70,
      Tencode_Description: "ALARM - BURGLAR",
      Tencode_Suffix: null,
      Tencode_Suffix_Description: null,
      Disposition_Code: "4",
      Disposition_Description: "ASSISTED CITIZEN",
      Block: "100",
      Street_Name: "BROADWAY",
      Unit_Dispatched: "121B",
      Shift: "B",
      Sector: "C1",
      Mapped_Location: null,
      POINT_X: -9_667_000.5,
      POINT_Y: 4_323_000.25,
      ZONE_: "15",
      Latitude: 36.16,
      Longitude: -86.78,
      RPA: "2201",
      Call_Received: T0 - oid * 60_000,
    },
    ...overrides,
  };
}

export function mapCall(oid: number, latitude = 36.16, longitude = -86.78): MapCall {
  return {
    id: `1:${String(oid)}`,
    receivedAt: iso(T0 - oid * 60_000),
    record: {
      Latitude: latitude,
      Longitude: longitude,
      Tencode_Description: "ALARM - BURGLAR",
      Block: "100",
      Street_Name: "BROADWAY",
    },
  };
}

export const FILTER_VALUES: FilterValues = {
  ZONE_: ["15", "23"],
  Sector: ["C1", "N2"],
  Tencode_Description: ["ALARM - BURGLAR", "TRAFFIC VIOLATION"],
  Disposition_Description: ["ASSISTED CITIZEN", "REPORT TAKEN"],
};

export function summary(overrides: Partial<Summary> = {}): Summary {
  return {
    total: 1284,
    withCoordinates: 1200,
    withoutCoordinates: 84,
    types: {
      otherCount: 400,
      entries: [
        { Tencode_Description: "ALARM - BURGLAR", count: 500 },
        { Tencode_Description: "TRAFFIC VIOLATION", count: 384 },
      ],
    },
    hourly: [
      { start: iso(T0 - 2 * HOUR), count: 40 },
      { start: iso(T0 - HOUR), count: 60 },
    ],
    ...overrides,
  };
}

export function liveStatus(overrides: Partial<SourceStatus> = {}): SourceStatus {
  return {
    source: "mnpd-calls",
    state: "LIVE",
    detail: null,
    lastPollAt: iso(T0 - 2000),
    lastChangeAt: iso(T0 - 5 * 60_000),
    latestCallReceivedAt: iso(T0 - 7 * 60_000),
    upstreamEditedAt: iso(T0 - 6 * 60_000),
    degradedSince: null,
    retryAt: null,
    backfill: { fraction: 1, rangesTotal: 4, rangesCompleted: 4, completedAt: iso(T0 - HOUR) },
    ...overrides,
  };
}
