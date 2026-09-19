// Deployment-specific map tiles. Any tile server must be credited in its attribution.

interface MapSettings {
  VITE_TILE_URL?: string;
  VITE_TILE_ATTRIBUTION?: string;
}

const configured = import.meta.env as MapSettings;

export const TILE_URL =
  configured.VITE_TILE_URL ?? "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

export const TILE_ATTRIBUTION =
  configured.VITE_TILE_ATTRIBUTION ??
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

// Downtown Nashville.
export const MAP_CENTER: [number, number] = [36.1627, -86.7816];
export const MAP_ZOOM = 11;
