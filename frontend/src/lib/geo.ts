import type { MapSettings } from "./types";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";

/* Distances, the map's position in the URL, and Leaflet, loaded on first use
   so the grid never pays for it. */

export interface Point {
  lat: number;
  lng: number;
}

/** Great-circle distance in km. */
export function km(a: Point, b: Point): number {
  const rad = Math.PI / 180;
  const h = Math.sin(((b.lat - a.lat) * rad) / 2) ** 2
    + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(((b.lng - a.lng) * rad) / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(Math.min(1, h)));
}

/** "350 m", "4.2 km", "120 km": as precise as is useful. */
export function distance(kmAway: number): { unit: "m" | "km"; value: number } {
  if (kmAway < 1) return { unit: "m", value: Math.max(10, Math.round((kmAway * 1000) / 10) * 10) };
  return { unit: "km", value: kmAway < 10 ? Math.round(kmAway * 10) / 10 : Math.round(kmAway) };
}

/** The map's position as the URL has it: "lat,lng,zoom". */
export function readAt(at: string): { center: Point; zoom: number } | null {
  const [lat, lng, zoom] = at.split(",").map(Number);
  if (![lat, lng, zoom].every(Number.isFinite) || Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  return { center: { lat, lng }, zoom };
}

/** Four decimals is about 10 m: enough to come back to, short enough to share. */
export const writeAt = (center: Point, zoom: number) =>
  `${center.lat.toFixed(4)},${(((center.lng + 540) % 360) - 180).toFixed(4)},${Math.round(zoom)}`;

type L = typeof import("leaflet");
let loading: Promise<L> | null = null;

/** Leaflet with the cluster plugin, which expects to find it as a global. */
export function leaflet(): Promise<L> {
  loading ??= (async () => {
    const L = (await import("leaflet")).default;
    (globalThis as { L?: L }).L = L;
    await import("leaflet.markercluster");
    return L;
  })();
  return loading;
}

/** The base map: the server's tiles, with the credit they require. */
export function baseLayer(L: L, settings: MapSettings) {
  return L.tileLayer(settings.tiles, { attribution: settings.attribution, maxZoom: 19 });
}
