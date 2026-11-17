import { describe, expect, it } from "vitest";
import { boundsOf, commonRoutes, coordLabel, fmtMiles, gpsTrack, isPositionSample, mappablePlaces, mostVisited, travelStats, validCoords } from "./mapData";
import type { LocationPlace, LocationTrip } from "./types";

const place = (over: Partial<LocationPlace>): LocationPlace =>
  ({
    id: "p",
    name: "Place",
    address: "",
    lat: null,
    lng: null,
    category: "other",
    tags: [],
    context: "",
    contact_id: "",
    contact_name: "",
    visit_count: 1,
    last_visited_at: "",
    favorite: false,
    created_at: "",
    updated_at: "",
    map_links: {} as LocationPlace["map_links"],
    ...over,
  }) as LocationPlace;

const trip = (over: Partial<LocationTrip>): LocationTrip =>
  ({
    id: "t",
    place_id: "",
    place_name: "",
    address: "",
    lat: null,
    lng: null,
    origin_name: "",
    origin_address: "",
    started_at: "",
    ended_at: "",
    duration_min: 0,
    distance_mi: 0,
    avg_speed_mph: 0,
    max_speed_mph: 0,
    current_speed_mph: 0,
    mode: "driving",
    traffic_level: "unknown",
    traffic_note: "",
    tags: [],
    notes: "",
    source: "manual",
    created_at: "",
    map_links: {} as LocationTrip["map_links"],
    ...over,
  }) as LocationTrip;

describe("coordinates", () => {
  it("accepts real positions and rejects everything else", () => {
    expect(validCoords(48.85, 2.29)).toBe(true);
    expect(validCoords(-33.9, 151.2)).toBe(true);
    expect(validCoords(null, 2)).toBe(false);
    expect(validCoords(91, 0)).toBe(false);
    expect(validCoords(0, 181)).toBe(false);
    expect(validCoords(Number.NaN, 1)).toBe(false);
    expect(validCoords(0, 0)).toBe(false); // the "no fix yet" value some devices send
  });

  it("keeps a place without coordinates off the map but in the list", () => {
    const list = [place({ id: "a", lat: 1, lng: 2 }), place({ id: "b" }), place({ id: "c", lat: 5, lng: null })];
    expect(mappablePlaces(list).map((p) => p.id)).toEqual(["a"]);
    expect(list).toHaveLength(3);
  });

  it("formats a spot that has no name", () => {
    expect(coordLabel(48.85841, 2.29449)).toBe("48.85841, 2.29449");
  });
});

describe("most visited", () => {
  it("ranks by visits, then favourites, and ignores places never visited", () => {
    const top = mostVisited([
      place({ id: "a", name: "Gym", visit_count: 3 }),
      place({ id: "b", name: "Home", visit_count: 12, favorite: true }),
      place({ id: "c", name: "Cafe", visit_count: 3, favorite: true }),
      place({ id: "d", name: "Never", visit_count: 0 }),
    ]);
    expect(top.map((p) => p.id)).toEqual(["b", "c", "a"]);
  });

  it("returns at most n", () => {
    const many = Array.from({ length: 9 }, (_, i) => place({ id: String(i), name: `P${i}`, visit_count: i + 1 }));
    expect(mostVisited(many, 3)).toHaveLength(3);
  });
});

describe("common routes", () => {
  const trips = [
    trip({ id: "1", origin_name: "Home", place_name: "Work", distance_mi: 10, duration_min: 20, avg_speed_mph: 30 }),
    trip({ id: "2", origin_name: "home", place_name: "work", distance_mi: 11, duration_min: 30, avg_speed_mph: 22 }),
    trip({ id: "3", origin_name: "Home", place_name: "Gym", distance_mi: 3, duration_min: 8, avg_speed_mph: 22 }),
    trip({ id: "4", place_name: "GPS Snapshot", source: "gps_ping", lat: 1, lng: 2 }),
    trip({ id: "5", origin_name: "Home" }), // no destination: not a journey
  ];

  it("groups the same A to B, whatever the capitalisation, and counts it", () => {
    const routes = commonRoutes(trips);
    expect(routes[0]).toMatchObject({ trips: 2, miles: 21, avgMinutes: 25 });
    expect(routes[0].to.toLowerCase()).toBe("work");
    expect(routes).toHaveLength(2);
  });

  it("does not count GPS samples or trips without a destination as journeys", () => {
    const names = commonRoutes(trips).map((r) => r.to);
    expect(names).not.toContain("GPS Snapshot");
  });

  it("averages only the trips that recorded a speed or a duration", () => {
    const r = commonRoutes([
      trip({ origin_name: "A", place_name: "B", duration_min: 10, avg_speed_mph: 40 }),
      trip({ origin_name: "A", place_name: "B", duration_min: 0, avg_speed_mph: 0 }),
    ])[0];
    expect(r.avgMinutes).toBe(10);
    expect(r.avgMph).toBe(40);
  });

  it("handles no trips at all", () => {
    expect(commonRoutes([])).toEqual([]);
  });
});

describe("position samples saved before the source was recorded", () => {
  it("are recognised by what a sample never has, and never hide a real trip", () => {
    const old = trip({ source: "manual", place_name: "GPS Snapshot", lat: 40.7, lng: -111.9 });
    expect(isPositionSample(old)).toBe(true);
    expect(isPositionSample(trip({ source: "gps_ping" }))).toBe(true);
    // a real trip to a place that happens to share the name keeps its start and distance
    expect(isPositionSample(trip({ place_name: "GPS Snapshot", lat: 1, lng: 2, origin_name: "Home", distance_mi: 5 }))).toBe(false);
    expect(isPositionSample(trip({ place_name: "Work", origin_name: "Home", distance_mi: 5, lat: 1, lng: 2 }))).toBe(false);
    expect(travelStats([old, trip({ origin_name: "A", place_name: "B", distance_mi: 3 })]).trips).toBe(1);
    expect(commonRoutes([old])).toEqual([]);
  });
});

describe("gps track", () => {
  it("keeps only position samples with a real fix, oldest first", () => {
    const track = gpsTrack([
      trip({ id: "b", source: "gps_ping", lat: 2, lng: 2, started_at: "2026-01-02T00:00:00Z" }),
      trip({ id: "a", source: "gps_ping", lat: 1, lng: 1, started_at: "2026-01-01T00:00:00Z" }),
      trip({ id: "x", source: "manual", lat: 9, lng: 9 }),
      trip({ id: "n", source: "gps_ping", lat: 0, lng: 0 }),
    ]);
    expect(track).toEqual([
      { lat: 1, lng: 1 },
      { lat: 2, lng: 2 },
    ]);
  });
});

describe("travel stats", () => {
  it("summarises journeys, not position samples", () => {
    const s = travelStats([
      trip({ distance_mi: 10, avg_speed_mph: 30, max_speed_mph: 60, mode: "driving" }),
      trip({ distance_mi: 2, avg_speed_mph: 20, max_speed_mph: 25, mode: "walking" }),
      trip({ distance_mi: 8, avg_speed_mph: 40, max_speed_mph: 70, mode: "driving" }),
      trip({ source: "gps_ping", distance_mi: 99, max_speed_mph: 200 }),
    ]);
    expect(s.trips).toBe(3);
    expect(s.miles).toBe(20);
    expect(s.avgMph).toBe(30);
    expect(s.maxMph).toBe(70);
    expect(s.longestMiles).toBe(10);
    expect(s.topMode).toBe("driving");
    expect(s.byMode).toEqual({ driving: 2, walking: 1 });
  });

  it("is all zeroes with no trips", () => {
    expect(travelStats([])).toMatchObject({ trips: 0, miles: 0, avgMph: 0, maxMph: 0, topMode: "" });
  });
});

describe("bounds and numbers", () => {
  it("wraps every point, ignoring bad ones", () => {
    expect(boundsOf([{ lat: 1, lng: 5 }, { lat: 3, lng: 2 }, { lat: 200, lng: 0 }])).toEqual([
      [1, 2],
      [3, 5],
    ]);
    expect(boundsOf([])).toBeNull();
  });

  it("prints miles compactly", () => {
    expect(fmtMiles(0)).toBe("0");
    expect(fmtMiles(12.04)).toBe("12");
    expect(fmtMiles(3.5)).toBe("3.5");
    expect(fmtMiles(250.6)).toBe("251");
    expect(fmtMiles(Number.NaN)).toBe("0");
  });
});
