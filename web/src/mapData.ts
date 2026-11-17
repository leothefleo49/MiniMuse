import type { LocationPlace, LocationTrip } from "./types";

/** What the map and its stats are built from: pure functions over the places and trips the runtime keeps. */
export interface LatLng {
  lat: number;
  lng: number;
}

/** The kinds of place the runtime accepts (anything else is stored as "other"). */
export const PLACE_CATEGORIES = ["home", "work", "friend", "favorite", "frequent", "errand", "food", "travel", "other"];

export function validCoords(lat: unknown, lng: unknown): boolean {
  return (
    typeof lat === "number" &&
    typeof lng === "number" &&
    Number.isFinite(lat) &&
    Number.isFinite(lng) &&
    Math.abs(lat) <= 90 &&
    Math.abs(lng) <= 180 &&
    !(lat === 0 && lng === 0)
  );
}

/** Places that can be pinned: a saved place without coordinates stays in the list but not on the map. */
export function mappablePlaces(places: LocationPlace[]): LocationPlace[] {
  return places.filter((p) => validCoords(p.lat, p.lng));
}

/** The places visited most, favourites first among equals. */
export function mostVisited(places: LocationPlace[], n = 5): LocationPlace[] {
  return places
    .filter((p) => p.visit_count > 0)
    .sort((a, b) => b.visit_count - a.visit_count || Number(b.favorite) - Number(a.favorite) || a.name.localeCompare(b.name))
    .slice(0, n);
}

export interface RouteStat {
  from: string;
  to: string;
  trips: number;
  miles: number;
  avgMinutes: number;
  avgMph: number;
}

const clean = (s: string | undefined | null) => (s ?? "").trim();

/**
 * A position sample rather than a journey. New ones are stored with the source `gps_ping`; ones saved
 * before that was fixed were stored as `manual`, and are told apart by what a sample never has: a
 * start, a distance or a duration.
 */
export function isPositionSample(tr: LocationTrip): boolean {
  if (tr.source === "gps_ping") return true;
  return (
    validCoords(tr.lat, tr.lng) &&
    !clean(tr.origin_name) &&
    !clean(tr.origin_address) &&
    !(tr.distance_mi > 0) &&
    !(tr.duration_min > 0) &&
    /^(gps snapshot|current gps sample)$/i.test(clean(tr.place_name))
  );
}

/**
 * The routes driven most, from where to where. A trip records its start and its destination by name;
 * it does not record the road it took, so roads cannot be ranked from this data.
 */
export function commonRoutes(trips: LocationTrip[], n = 5): RouteStat[] {
  const groups = new Map<string, { from: string; to: string; list: LocationTrip[] }>();
  for (const tr of trips) {
    if (isPositionSample(tr)) continue; // a position sample, not a journey
    const to = clean(tr.place_name) || clean(tr.address);
    if (!to) continue;
    const from = clean(tr.origin_name) || clean(tr.origin_address);
    const key = `${from.toLowerCase()}→${to.toLowerCase()}`;
    const g = groups.get(key) ?? { from, to, list: [] };
    g.list.push(tr);
    groups.set(key, g);
  }
  const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
  return [...groups.values()]
    .map((g) => {
      const miles = g.list.reduce((s, tr) => s + (tr.distance_mi || 0), 0);
      const minutes = g.list.map((tr) => tr.duration_min).filter((m) => m > 0);
      const speeds = g.list.map((tr) => tr.avg_speed_mph).filter((v) => v > 0);
      return {
        from: g.from,
        to: g.to,
        trips: g.list.length,
        miles,
        avgMinutes: mean(minutes),
        avgMph: mean(speeds),
      };
    })
    .sort((a, b) => b.trips - a.trips || b.miles - a.miles || a.to.localeCompare(b.to))
    .slice(0, n);
}

/** The positions the phone or browser reported, oldest first: the line the map draws for "where I have been". */
export function gpsTrack(trips: LocationTrip[]): LatLng[] {
  return trips
    .filter((tr) => isPositionSample(tr) && validCoords(tr.lat, tr.lng))
    .sort((a, b) => (a.started_at || a.created_at).localeCompare(b.started_at || b.created_at))
    .map((tr) => ({ lat: tr.lat as number, lng: tr.lng as number }));
}

export interface TravelStats {
  trips: number;
  miles: number;
  avgMph: number;
  maxMph: number;
  longestMiles: number;
  topMode: string;
  byMode: Record<string, number>;
}

export function travelStats(trips: LocationTrip[]): TravelStats {
  const real = trips.filter((tr) => !isPositionSample(tr));
  const byMode: Record<string, number> = {};
  for (const tr of real) {
    const mode = clean(tr.mode) || "driving";
    byMode[mode] = (byMode[mode] ?? 0) + 1;
  }
  const speeds = real.map((tr) => tr.avg_speed_mph).filter((v) => v > 0);
  const topMode = Object.entries(byMode).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";
  return {
    trips: real.length,
    miles: real.reduce((s, tr) => s + (tr.distance_mi || 0), 0),
    avgMph: speeds.length ? speeds.reduce((a, b) => a + b, 0) / speeds.length : 0,
    maxMph: real.reduce((m, tr) => Math.max(m, tr.max_speed_mph || 0), 0),
    longestMiles: real.reduce((m, tr) => Math.max(m, tr.distance_mi || 0), 0),
    topMode,
    byMode,
  };
}

/** South-west and north-east corners that hold every point, or null when there is nothing to show. */
export function boundsOf(points: LatLng[]): [[number, number], [number, number]] | null {
  const ok = points.filter((p) => validCoords(p.lat, p.lng));
  if (ok.length === 0) return null;
  const lats = ok.map((p) => p.lat);
  const lngs = ok.map((p) => p.lng);
  return [
    [Math.min(...lats), Math.min(...lngs)],
    [Math.max(...lats), Math.max(...lngs)],
  ];
}

/** "48.8584, 2.2945": what an address is when nothing could name the spot. */
export function coordLabel(lat: number, lng: number): string {
  return `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
}

/** OpenStreetMap's own reverse-geocoder, for naming a tapped spot (one light request per tap). */
export function reverseGeocodeUrl(lat: number, lng: number): string {
  return `https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=18&lat=${lat}&lon=${lng}`;
}

export function fmtMiles(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0";
  return n >= 100 ? String(Math.round(n)) : n.toFixed(1).replace(/\.0$/, "");
}
