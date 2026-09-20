import "leaflet/dist/leaflet.css";
import "react-leaflet-cluster/dist/assets/MarkerCluster.css";
import "react-leaflet-cluster/dist/assets/MarkerCluster.Default.css";

import { divIcon, type LeafletKeyboardEvent } from "leaflet";
import { useEffect, useId, useState } from "react";
import { MapContainer, Marker, TileLayer, useMap } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";

import type { MapCall, MapCallsResult } from "../api/types.ts";
import { MAP_CENTER, MAP_ZOOM, TILE_ATTRIBUTION, TILE_URL } from "../config.ts";
import { describeCallType } from "../filters.ts";
import { formatCallTime } from "../time.ts";
import { describePlace } from "./feed.tsx";

export interface CallMapProperties {
  result: MapCallsResult["mapCalls"] | undefined;
  state: "pending" | "error" | "success";
  error: Error | undefined;
  selectedId: string | undefined;
  onSelect: (id: string) => void;
}

const numbers = new Intl.NumberFormat("en-US");

const ACTIVATION_KEYS = new Set(["Enter", " "]);

const markerIcon = divIcon({ className: "call-marker", iconSize: [16, 16] });
const selectedIcon = divIcon({ className: "call-marker selected", iconSize: [22, 22] });

type Located = MapCall & { record: { Latitude: number; Longitude: number } };

// The API returns only calls with usable coordinates; this also narrows the types.
function isLocated(call: MapCall): call is Located {
  return call.record.Latitude !== null && call.record.Longitude !== null;
}

export function markerLabel(call: MapCall): string {
  const type = describeCallType(call.record.Tencode_Description);
  const place = describePlace(call.record.Block, call.record.Street_Name);
  const time = call.receivedAt === null ? "time not published" : formatCallTime(call.receivedAt);
  return `${type}, ${place}, ${time}`;
}

// Leaflet measures its container once. When the container is hidden at first (the list view on
// small screens) or resized by layout, the map must re-measure or tiles only partly render.
function SizeWatcher() {
  const map = useMap();
  useEffect(() => {
    const observer = new ResizeObserver(() => {
      map.invalidateSize();
    });
    observer.observe(map.getContainer());
    return () => {
      observer.disconnect();
    };
  }, [map]);
  return <></>;
}

function Caption({ result }: { result: MapCallsResult["mapCalls"] }) {
  const shown = result.calls.length;
  const unlocated = result.matching - result.withCoordinates;
  return (
    <p className="map-caption">
      {result.truncated
        ? `Showing the ${numbers.format(shown)} most recent of ${numbers.format(result.withCoordinates)} calls with a location.`
        : `Showing ${numbers.format(shown)} calls with a location.`}{" "}
      {unlocated > 0 &&
        `${numbers.format(unlocated)} without a published location appear only in the list. `}
      Locations are approximate.
    </p>
  );
}

export function CallMap({ result, state, error, selectedId, onSelect }: CallMapProperties) {
  const headingId = useId();
  const [tilesFailed, setTilesFailed] = useState(false);
  return (
    <section className="map-panel" aria-labelledby={headingId}>
      <h2 id={headingId}>Map</h2>
      {state === "pending" && <p className="map-caption">Loading map locations…</p>}
      {state === "error" && (
        <p className="map-caption" role="alert">
          Map locations could not be loaded ({error?.message}). The list is unaffected.
        </p>
      )}
      {result && <Caption result={result} />}
      {tilesFailed && (
        <p className="notice">
          Map tiles could not be loaded. Every call is still available in the list.
        </p>
      )}
      <MapContainer className="map" center={MAP_CENTER} zoom={MAP_ZOOM} scrollWheelZoom={false}>
        <SizeWatcher />
        <TileLayer
          url={TILE_URL}
          attribution={TILE_ATTRIBUTION}
          eventHandlers={{
            tileerror: () => {
              setTilesFailed(true);
            },
          }}
        />
        <MarkerClusterGroup chunkedLoading>
          {(result?.calls ?? []).filter(isLocated).map((call) => (
            <Marker
              key={call.id}
              position={[call.record.Latitude, call.record.Longitude]}
              icon={call.id === selectedId ? selectedIcon : markerIcon}
              title={markerLabel(call)}
              alt={markerLabel(call)}
              eventHandlers={{
                click: () => {
                  onSelect(call.id);
                },
                // Leaflet gives markers the button role; activate them the way buttons are.
                keydown: (event: LeafletKeyboardEvent) => {
                  if (ACTIVATION_KEYS.has(event.originalEvent.key)) {
                    onSelect(call.id);
                  }
                },
              }}
            />
          ))}
        </MarkerClusterGroup>
      </MapContainer>
    </section>
  );
}
