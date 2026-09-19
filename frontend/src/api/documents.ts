// Operations the dashboard sends. A test validates each against docs/schema.graphql.

const FEED_FIELDS = `
  id
  receivedAt
  hasCoordinates
  sourcePresent
  record {
    OBJECTID
    Tencode_Description
    Disposition_Description
    Block
    Street_Name
    ZONE_
    Sector
  }
`;

export const ANCHOR = `
query Anchor {
  dataVersion
  serverTime
}
`;

export const FEED = `
query Feed($filter: CallFilter!, $first: Int!, $after: String, $asOfVersion: Int!) {
  calls(filter: $filter, first: $first, after: $after, asOfVersion: $asOfVersion) {
    asOfVersion
    pageInfo {
      endCursor
      hasNextPage
    }
    nodes {
      ${FEED_FIELDS}
    }
  }
}
`;

export const NEW_CALLS = `
query NewCalls($filter: CallFilter!, $sinceVersion: Int!) {
  newCallCount(filter: $filter, sinceVersion: $sinceVersion)
}
`;

export const CALL_DETAIL = `
query CallDetail($id: ID!) {
  call(id: $id) {
    id
    generation
    receivedAt
    hasCoordinates
    sourcePresent
    removedAt
    firstSeenAt
    lastChangedAt
    additionalAttributes
    record {
      OBJECTID
      Event_Number
      Complaint_Number
      Tencode
      Tencode_Description
      Tencode_Suffix
      Tencode_Suffix_Description
      Disposition_Code
      Disposition_Description
      Block
      Street_Name
      Unit_Dispatched
      Shift
      Sector
      Mapped_Location
      POINT_X
      POINT_Y
      ZONE_
      Latitude
      Longitude
      RPA
      Call_Received
    }
  }
}
`;

export const MAP_CALLS = `
query MapCalls($filter: CallFilter!, $limit: Int!) {
  mapCalls(filter: $filter, limit: $limit) {
    matching
    withCoordinates
    truncated
    calls {
      id
      receivedAt
      record {
        Latitude
        Longitude
        Tencode_Description
        Block
        Street_Name
      }
    }
  }
}
`;

export const SUMMARY = `
query Summary($filter: CallFilter!, $types: Int!) {
  summary(filter: $filter) {
    total
    withCoordinates
    withoutCoordinates
    types(limit: $types) {
      otherCount
      entries {
        Tencode_Description
        count
      }
    }
    hourly {
      start
      count
    }
  }
}
`;

export const FILTER_VALUES = `
query FilterValues($since: DateTime!, $until: DateTime!) {
  filterValues(since: $since, until: $until) {
    ZONE_
    Sector
    Tencode_Description
    Disposition_Description
  }
}
`;

export const STATUS = `
query Status {
  serverTime
  sourceStatus {
    source
    state
    detail
    lastPollAt
    lastChangeAt
    latestCallReceivedAt
    upstreamEditedAt
    degradedSince
    retryAt
    backfill {
      fraction
      rangesTotal
      rangesCompleted
      completedAt
    }
  }
}
`;
