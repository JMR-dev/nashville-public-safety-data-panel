import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { type Connection, type OpenVersionSource, useDataVersion } from "../api/events.ts";
import {
  LIVE_KEYS,
  useAnchor,
  useFeed,
  useFilterValues,
  useMapCalls,
  useNewCalls,
  useStatus,
  useSummary,
} from "../api/queries.ts";
import type { StatusResult } from "../api/types.ts";
import { callFilter, DEFAULT_FILTERS, describeRange, type Filters, windowFor } from "../filters.ts";
import { todayInChicago } from "../time.ts";
import { useNow } from "../use-now.ts";
import { CallDetails } from "./call-details.tsx";
import { CallMap } from "./call-map.tsx";
import { Feed } from "./feed.tsx";
import { FilterBar } from "./filter-bar.tsx";
import { StatusBar } from "./status-bar.tsx";
import { SummaryPanel } from "./summary-panel.tsx";

type View = "list" | "map";

interface LiveStatusProperties {
  status: StatusResult | undefined;
  error: Error | undefined;
  connection: Connection;
  offset: number;
}

// Re-renders every second so relative times stay current, without re-rendering the dashboard.
function LiveStatus({ status, error, connection, offset }: LiveStatusProperties) {
  const now = useNow(1000);
  return <StatusBar status={status} error={error} connection={connection} now={now + offset} />;
}

interface DashboardProperties {
  filters: Filters;
  // The data version and server time the feed is pinned to; undefined while re-anchoring.
  version: number | undefined;
  anchorTime: number;
  // Server times that slide the live and displayed windows forward.
  liveTime: number;
  shownTime: number;
  paused: boolean;
  backfilling: boolean;
  selectedId: string | undefined;
  view: View;
  onFilters: (filters: Filters) => void;
  onShowNew: () => void;
  onTogglePause: () => void;
  onSelect: (id: string | undefined) => void;
  onView: (view: View) => void;
}

function Dashboard(properties: DashboardProperties) {
  const { filters, selectedId } = properties;
  const shownWindow = windowFor(filters.range, properties.shownTime);
  const feed = useFeed(callFilter(filters, windowFor(filters.range, properties.anchorTime)), properties.version);
  const arrivals = useNewCalls(
    callFilter(filters, windowFor(filters.range, properties.liveTime)),
    properties.version,
  );
  const map = useMapCalls(callFilter(filters, shownWindow));
  const summary = useSummary(callFilter(filters, shownWindow));
  const values = useFilterValues(shownWindow.since, shownWindow.until);
  const rangeText = describeRange(filters.range);

  return (
    <>
      <FilterBar
        filters={filters}
        values={values.data}
        today={todayInChicago(properties.liveTime)}
        onChange={properties.onFilters}
      />
      <SummaryPanel
        summary={summary.data}
        state={summary.status}
        error={summary.error ?? undefined}
        refreshing={summary.isFetching}
        rangeText={rangeText}
        window={shownWindow}
      />
      <div className="view-switch" role="group" aria-label="View">
        <button
          type="button"
          aria-pressed={properties.view === "list"}
          onClick={() => {
            properties.onView("list");
          }}
        >
          List
        </button>
        <button
          type="button"
          aria-pressed={properties.view === "map"}
          onClick={() => {
            properties.onView("map");
          }}
        >
          Map
        </button>
      </div>
      <div className="workspace" data-view={properties.view}>
        <Feed
          calls={feed.data?.pages.flatMap((page) => page.nodes) ?? []}
          state={feed.status}
          error={feed.error ?? undefined}
          refreshing={feed.isFetching && !feed.isFetchingNextPage}
          hasMore={feed.hasNextPage}
          loadingMore={feed.isFetchingNextPage}
          newCalls={arrivals.data ?? 0}
          paused={properties.paused}
          selectedId={selectedId}
          rangeText={rangeText}
          backfilling={properties.backfilling}
          onLoadMore={() => {
            void feed.fetchNextPage();
          }}
          onShowNew={properties.onShowNew}
          onTogglePause={properties.onTogglePause}
          onSelect={properties.onSelect}
          onRetry={() => {
            void feed.refetch();
          }}
        />
        <CallMap
          result={map.data}
          state={map.status}
          error={map.error ?? undefined}
          selectedId={selectedId}
          onSelect={properties.onSelect}
        />
        {selectedId !== undefined && (
          <CallDetails
            key={selectedId}
            id={selectedId}
            onClose={() => {
              properties.onSelect(undefined);
            }}
          />
        )}
      </div>
    </>
  );
}

export function Monitor({ openEvents }: { openEvents: OpenVersionSource }) {
  const client = useQueryClient();
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [generation, setGeneration] = useState(0);
  const [liveAt, setLiveAt] = useState<number>();
  const [frozenAt, setFrozenAt] = useState<number>();
  const [lastVersion, setLastVersion] = useState<number>();
  const [selectedId, setSelectedId] = useState<string>();
  const [view, setView] = useState<View>("list");
  const status = useStatus();
  const anchor = useAnchor(generation);

  const serverTime = anchor.data ? Date.parse(anchor.data.serverTime) : 0;
  const offset = serverTime - anchor.dataUpdatedAt;
  const isPaused = frozenAt !== undefined;
  const liveTime = liveAt ?? serverTime;

  function refresh(): void {
    setLiveAt(Date.now() + offset);
    const keys = isPaused ? ["newCalls", "status"] : [...LIVE_KEYS, "call"];
    for (const key of keys) {
      void client.invalidateQueries({ queryKey: [key] });
    }
  }

  function reanchor(): void {
    setGeneration((current) => current + 1);
    setLiveAt(undefined);
  }

  const connection = useDataVersion(openEvents, {
    onVersion: (version) => {
      if (version === (lastVersion ?? anchor.data?.dataVersion)) {
        return;
      }
      setLastVersion(version);
      refresh();
    },
    onReconnect: refresh,
  });

  return (
    <div className="monitor">
      <LiveStatus
        status={status.data}
        error={status.error ?? undefined}
        connection={connection}
        offset={offset}
      />
      {anchor.isError && (
        <div className="page-note" role="alert">
          <p>The calls could not be loaded ({anchor.error.message}).</p>
          <button
            type="button"
            onClick={() => {
              void anchor.refetch();
            }}
          >
            Try again
          </button>
        </div>
      )}
      {anchor.isPending && <p className="page-note">Loading calls…</p>}
      {anchor.data && (
        <Dashboard
          filters={filters}
          version={anchor.isPlaceholderData ? undefined : anchor.data.dataVersion}
          anchorTime={serverTime}
          liveTime={liveTime}
          shownTime={frozenAt ?? liveTime}
          paused={isPaused}
          backfilling={status.data?.sourceStatus[0]?.state === "BACKFILLING"}
          selectedId={selectedId}
          view={view}
          onFilters={(next) => {
            setFilters(next);
            reanchor();
          }}
          onShowNew={reanchor}
          onTogglePause={() => {
            if (isPaused) {
              setFrozenAt(undefined);
              refresh();
            } else {
              setFrozenAt(liveTime);
            }
          }}
          onSelect={setSelectedId}
          onView={setView}
        />
      )}
    </div>
  );
}
