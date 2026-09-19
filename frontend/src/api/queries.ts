import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import {
  ANCHOR,
  CALL_DETAIL,
  FEED,
  FILTER_VALUES,
  MAP_CALLS,
  NEW_CALLS,
  STATUS,
  SUMMARY,
} from "./documents.ts";
import { requestGraphQL } from "./graphql.ts";
import type {
  AnchorResult,
  CallDetailResult,
  CallFilter,
  FeedResult,
  FilterValuesResult,
  MapCallsResult,
  NewCallsResult,
  StatusResult,
  SummaryResult,
} from "./types.ts";

export const FEED_PAGE_SIZE = 50;
export const MAP_LIMIT = 2000;
export const TYPE_LIMIT = 8;
// Polls the status even without data changes so freshness and staleness stay current.
export const STATUS_INTERVAL_MS = 15_000;

// Query keys whose data changes when committed call data changes.
export const LIVE_KEYS = ["feed", "newCalls", "map", "summary", "filterValues", "status"] as const;

// The current data version and server time, taken when the reader (re)loads the feed.
// While a new anchor loads, the previous one stays available as placeholder data.
export function useAnchor(generation: number) {
  return useQuery({
    queryKey: ["anchor", generation],
    queryFn: ({ signal }) => requestGraphQL<AnchorResult>(ANCHOR, {}, signal),
    placeholderData: keepPreviousData,
  });
}

// Feed pages pinned to one data version, so arrivals never shift the list being read.
export function useFeed(filter: CallFilter, version: number | undefined) {
  return useInfiniteQuery({
    queryKey: ["feed", filter, version],
    enabled: version !== undefined,
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) => {
      const variables = { filter, first: FEED_PAGE_SIZE, after: pageParam, asOfVersion: version };
      const result = await requestGraphQL<FeedResult>(FEED, variables, signal);
      return result.calls;
    },
    getNextPageParam: (page) => {
      const cursor = page.pageInfo.endCursor;
      return cursor !== null && page.pageInfo.hasNextPage ? cursor : undefined;
    },
    placeholderData: keepPreviousData,
  });
}

export function useNewCalls(filter: CallFilter, sinceVersion: number | undefined) {
  return useQuery({
    queryKey: ["newCalls", filter, sinceVersion],
    enabled: sinceVersion !== undefined,
    queryFn: async ({ signal }) => {
      const result = await requestGraphQL<NewCallsResult>(NEW_CALLS, { filter, sinceVersion }, signal);
      return result.newCallCount;
    },
  });
}

export function useMapCalls(filter: CallFilter) {
  return useQuery({
    queryKey: ["map", filter],
    queryFn: async ({ signal }) => {
      const result = await requestGraphQL<MapCallsResult>(MAP_CALLS, { filter, limit: MAP_LIMIT }, signal);
      return result.mapCalls;
    },
    placeholderData: keepPreviousData,
  });
}

export function useSummary(filter: CallFilter) {
  return useQuery({
    queryKey: ["summary", filter],
    queryFn: async ({ signal }) => {
      const result = await requestGraphQL<SummaryResult>(SUMMARY, { filter, types: TYPE_LIMIT }, signal);
      return result.summary;
    },
    placeholderData: keepPreviousData,
  });
}

export function useFilterValues(since: string, until: string) {
  return useQuery({
    queryKey: ["filterValues", since, until],
    queryFn: async ({ signal }) => {
      const result = await requestGraphQL<FilterValuesResult>(FILTER_VALUES, { since, until }, signal);
      return result.filterValues;
    },
    placeholderData: keepPreviousData,
  });
}

export function useStatus() {
  return useQuery({
    queryKey: ["status"],
    queryFn: ({ signal }) => requestGraphQL<StatusResult>(STATUS, {}, signal),
    refetchInterval: STATUS_INTERVAL_MS,
  });
}

export function useCallDetail(id: string) {
  return useQuery({
    queryKey: ["call", id],
    queryFn: async ({ signal }) => {
      const result = await requestGraphQL<CallDetailResult>(CALL_DETAIL, { id }, signal);
      return result.call;
    },
  });
}
