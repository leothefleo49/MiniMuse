import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef } from "react";
import { useT } from "../i18n";
import { boundsOf, mappablePlaces, type LatLng } from "../mapData";
import type { LocationPlace } from "../types";
import { cx } from "../util";

export interface PlacesMapProps {
  places: LocationPlace[];
  /** where the phone or browser has been, oldest first */
  track: LatLng[];
  /** the current position, with the accuracy radius in metres when known */
  current: (LatLng & { accuracy?: number }) | null;
  /** the spot that was just tapped, waiting to be named */
  pending: LatLng | null;
  /** fly to this point; a new `seq` flies again even to the same point */
  focus?: (LatLng & { seq: number }) | null;
  /** change to refit the view to everything shown */
  fitSeq?: number;
  onPick: (at: LatLng) => void;
  onSelectPlace?: (place: LocationPlace) => void;
  className?: string;
}

const ACCENT = "#0064d4";
const AMBER = "#f59e0b";
const ROSE = "#e11d48";

/**
 * An interactive map of the saved places on OpenStreetMap tiles (no key, no account): pan and zoom,
 * tap a pin for its name, tap empty ground to start a new place there, and see the current position
 * and the track of positions reported so far.
 */
export function PlacesMap({ places, track, current, pending, focus, fitSeq = 0, onPick, onSelectPlace, className }: PlacesMapProps) {
  const t = useT();
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layers = useRef<L.LayerGroup | null>(null);
  const fitted = useRef(-1);
  // the handlers change on every render; the map is created once and reads them from here
  const pick = useRef(onPick);
  const select = useRef(onSelectPlace);
  useEffect(() => {
    pick.current = onPick;
    select.current = onSelectPlace;
  });

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const m = L.map(el, { zoomControl: true, worldCopyJump: true, attributionControl: true }).setView([20, 0], 2);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors',
    }).addTo(m);
    layers.current = L.layerGroup().addTo(m);
    m.on("click", (e: L.LeafletMouseEvent) => pick.current({ lat: e.latlng.lat, lng: e.latlng.lng }));
    map.current = m;
    // the box can be laid out after the map is made (drawer closing, tabs switching)
    const ro = new ResizeObserver(() => m.invalidateSize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      m.remove();
      map.current = null;
      layers.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    const group = layers.current;
    if (!m || !group) return;
    group.clearLayers();
    const pins = mappablePlaces(places);
    if (track.length > 1) {
      L.polyline(
        track.map((p) => [p.lat, p.lng] as [number, number]),
        { color: ACCENT, weight: 3, opacity: 0.55, dashArray: "6 6" },
      ).addTo(group);
    }
    for (const p of pins) {
      const pin = L.circleMarker([p.lat as number, p.lng as number], {
        radius: p.favorite ? 9 : 7,
        color: "#ffffff",
        weight: 2,
        fillColor: p.favorite ? AMBER : ACCENT,
        fillOpacity: 1,
      }).addTo(group);
      pin.bindTooltip(p.name, { direction: "top", offset: [0, -6] });
      pin.on("click", (e: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(e); // a pin is not "empty ground"
        select.current?.(p);
      });
    }
    if (current) {
      if (current.accuracy && current.accuracy > 0) {
        L.circle([current.lat, current.lng], { radius: current.accuracy, color: "#2563eb", weight: 1, fillOpacity: 0.12 }).addTo(group);
      }
      L.circleMarker([current.lat, current.lng], { radius: 8, color: "#ffffff", weight: 3, fillColor: "#2563eb", fillOpacity: 1 })
        .bindTooltip(t("You are here"), { direction: "top", offset: [0, -6] })
        .addTo(group);
    }
    if (pending) {
      L.circleMarker([pending.lat, pending.lng], { radius: 10, color: ROSE, weight: 3, fillColor: "#ffffff", fillOpacity: 0.9, dashArray: "3 4" }).addTo(group);
    }
    // fit the view once, and again only when asked: panning must never be undone by new data
    if (fitted.current !== fitSeq) {
      const box = boundsOf([...pins.map((p) => ({ lat: p.lat as number, lng: p.lng as number })), ...(current ? [current] : []), ...track]);
      if (box) {
        m.fitBounds(box, { padding: [32, 32], maxZoom: 15 });
        fitted.current = fitSeq;
      }
    }
  }, [places, track, current, pending, fitSeq, t]);

  useEffect(() => {
    const m = map.current;
    if (m && focus) m.flyTo([focus.lat, focus.lng], Math.max(m.getZoom(), 15), { duration: 0.6 });
  }, [focus]);

  return (
    <div
      ref={host}
      role="application"
      aria-label={t("Map of your places")}
      data-testid="places-map"
      className={cx("isolate z-0 h-[320px] w-full overflow-hidden rounded-3xl border border-border/70 bg-surface-2 wide:h-[520px]", className)}
    />
  );
}
