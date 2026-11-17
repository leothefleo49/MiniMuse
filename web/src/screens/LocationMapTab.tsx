import { Crosshair, Loader2, MapPin, Star } from "lucide-react";
import { Suspense, lazy, useMemo, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n";
import {
  PLACE_CATEGORIES,
  commonRoutes,
  coordLabel,
  fmtMiles,
  gpsTrack,
  mappablePlaces,
  mostVisited,
  reverseGeocodeUrl,
  travelStats,
  validCoords,
  type LatLng,
} from "../mapData";
import { useStore } from "../store";
import type { LocationPlace, LocationSummary } from "../types";
import { cx } from "../util";

// The map library reads `window` the moment it loads, and weighs more than the rest of this screen:
// it is fetched only when the Map tab is opened.
const PlacesMap = lazy(() => import("../components/PlacesMap").then((m) => ({ default: m.PlacesMap })));

/** A short name for a tapped spot from OpenStreetMap's reverse lookup; empty when it cannot be had. */
async function lookupSpot(at: LatLng): Promise<{ name: string; address: string }> {
  try {
    const ctl = new AbortController();
    const timer = window.setTimeout(() => ctl.abort(), 4000);
    const res = await fetch(reverseGeocodeUrl(at.lat, at.lng), { signal: ctl.signal, headers: { Accept: "application/json" } });
    window.clearTimeout(timer);
    if (!res.ok) return { name: "", address: "" };
    const data = (await res.json()) as { name?: string; display_name?: string };
    const address = (data.display_name ?? "").trim();
    return { name: (data.name ?? address.split(",")[0] ?? "").trim(), address };
  } catch {
    return { name: "", address: "" };
  }
}

/**
 * The Map tab of Location: every saved place on a real map, where you are now, a tap to add a place,
 * and what the trips say (most visited, common routes, speeds). Roads are not ranked: a trip keeps
 * where it started and where it ended, not the road it took.
 */
export function LocationMapTab({ summary, reload }: { summary: LocationSummary | null; reload: () => Promise<void> }) {
  const t = useT();
  const { toast } = useStore();
  const [current, setCurrent] = useState<(LatLng & { accuracy?: number }) | null>(null);
  const [locating, setLocating] = useState(false);
  const [pending, setPending] = useState<LatLng | null>(null);
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [category, setCategory] = useState("favorite");
  const [favorite, setFavorite] = useState(true);
  const [looking, setLooking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [focus, setFocus] = useState<(LatLng & { seq: number }) | null>(null);
  const [fitSeq, setFitSeq] = useState(0);

  const places = useMemo(() => summary?.places ?? [], [summary]);
  const trips = useMemo(() => summary?.trips ?? [], [summary]);
  const track = useMemo(() => gpsTrack(trips), [trips]);
  const stats = useMemo(() => travelStats(trips), [trips]);
  const top = useMemo(() => mostVisited(places), [places]);
  const routes = useMemo(() => commonRoutes(trips), [trips]);
  const pinned = mappablePlaces(places).length;

  const locate = () => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      toast(t("Geolocation is not available in this browser"));
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const here = { lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: pos.coords.accuracy };
        setCurrent(here);
        setFocus({ ...here, seq: Date.now() });
        setLocating(false);
      },
      (err) => {
        setLocating(false);
        toast(
          err.code === 1
            ? t("Location is off for MiniMuse. Allow it under Settings, Permissions, then try again.")
            : err.message || t("Could not read GPS location"),
        );
      },
      { enableHighAccuracy: true, timeout: 12000 },
    );
  };

  const startPlace = async (at: LatLng) => {
    setPending(at);
    setName("");
    setAddress("");
    setLooking(true);
    const spot = await lookupSpot(at);
    setLooking(false);
    // keep what the person already typed if they were quicker than the lookup
    setName((cur) => cur || spot.name);
    setAddress((cur) => cur || spot.address);
  };

  const save = async () => {
    if (!pending || !name.trim()) return;
    setSaving(true);
    try {
      await api.addLocationPlace({
        name: name.trim(),
        address: address.trim() || coordLabel(pending.lat, pending.lng),
        lat: pending.lat,
        lng: pending.lng,
        category,
        favorite,
      });
      toast(t("Place saved"));
      setPending(null);
      await reload();
    } catch (e) {
      toast((e as Error).message || t("Could not save place"));
    } finally {
      setSaving(false);
    }
  };

  const flyTo = (p: LocationPlace) => {
    if (validCoords(p.lat, p.lng)) setFocus({ lat: p.lat as number, lng: p.lng as number, seq: Date.now() });
    else toast(t("This place has no coordinates yet; tap the map to pin it."));
  };

  const tile = (label: string, value: string) => (
    <div className="rounded-2xl bg-surface-2 px-3 py-2.5">
      <div className="text-[11.5px] font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-0.5 text-[19px] font-semibold leading-6 text-fg">{value}</div>
    </div>
  );

  return (
    <div className="space-y-3 wide:grid wide:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] wide:items-start wide:gap-4 wide:space-y-0" data-testid="location-map-tab">
      <div className="space-y-2.5">
        <Suspense
          fallback={
            <div className="flex h-[320px] w-full items-center justify-center rounded-3xl border border-border/70 bg-surface-2 text-muted wide:h-[520px]">
              <Loader2 size={20} className="animate-spin" />
            </div>
          }
        >
          <PlacesMap
            places={places}
            track={track}
            current={current}
            pending={pending}
            focus={focus}
            fitSeq={fitSeq}
            onPick={(at) => void startPlace(at)}
            onSelectPlace={flyTo}
          />
        </Suspense>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={locating}
            onClick={locate}
            className="inline-flex items-center gap-1.5 rounded-full bg-accent px-3.5 py-2 text-[13px] font-semibold text-accent-fg disabled:opacity-60"
          >
            {locating ? <Loader2 size={14} className="animate-spin" /> : <Crosshair size={14} />}
            {t("My location")}
          </button>
          <button
            type="button"
            onClick={() => setFitSeq((n) => n + 1)}
            className="rounded-full border border-border px-3.5 py-2 text-[13px] font-medium text-fg hover:bg-surface-2"
          >
            {t("Show all places")}
          </button>
          <span className="text-[12px] text-muted">
            {pinned === 0 ? t("Tap the map to add a place there.") : t("{n} places on the map. Tap empty ground to add another.", { n: pinned })}
          </span>
        </div>

        {pending && (
          <div className="space-y-2 rounded-3xl border border-accent/40 bg-surface p-3.5" data-testid="map-new-place">
            <div className="flex items-center gap-2 text-[13.5px] font-semibold text-fg">
              <MapPin size={15} className="text-rose-500" />
              {t("New place here")}
              <span className="font-mono text-[11.5px] font-normal text-muted">{coordLabel(pending.lat, pending.lng)}</span>
              {looking && <Loader2 size={13} className="animate-spin text-muted" />}
            </div>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("Name this place")}
              aria-label={t("Name this place")}
              className="w-full rounded-2xl bg-surface-2 px-3.5 py-2 text-[14px] outline-none focus:ring-2 focus:ring-accent/35"
            />
            <input
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              placeholder={t("Address (optional)")}
              aria-label={t("Address (optional)")}
              className="w-full rounded-2xl bg-surface-2 px-3.5 py-2 text-[13px] outline-none focus:ring-2 focus:ring-accent/35"
            />
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                aria-label={t("Category")}
                className="rounded-2xl bg-surface-2 px-3 py-2 text-[13px] outline-none"
              >
                {PLACE_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <label className="flex cursor-pointer items-center gap-1.5 text-[13px] text-fg">
                <input type="checkbox" checked={favorite} onChange={(e) => setFavorite(e.target.checked)} className="rounded border-border" />
                <Star size={13} className="text-amber-500" /> {t("Favorite")}
              </label>
              <span className="flex-1" />
              <button type="button" onClick={() => setPending(null)} className="rounded-full border border-border px-3.5 py-2 text-[13px] font-medium text-fg hover:bg-surface-2">
                {t("Cancel")}
              </button>
              <button
                type="button"
                disabled={saving || !name.trim()}
                onClick={() => void save()}
                className="inline-flex items-center gap-1.5 rounded-full bg-accent px-4 py-2 text-[13px] font-semibold text-accent-fg disabled:opacity-60"
              >
                {saving && <Loader2 size={13} className="animate-spin" />}
                {t("Save place")}
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          {tile(t("Trips"), String(stats.trips))}
          {tile(t("Miles"), fmtMiles(stats.miles))}
          {tile(t("Average speed"), t("{n} mph", { n: Math.round(stats.avgMph) }))}
          {tile(t("Top speed"), t("{n} mph", { n: Math.round(stats.maxMph) }))}
        </div>

        <section className="rounded-3xl border border-border/70 bg-surface p-3.5">
          <h3 className="mb-2 text-[13.5px] font-semibold text-fg">{t("Most visited places")}</h3>
          {top.length === 0 ? (
            <p className="text-[12.5px] text-muted">{t("Places you visit show up here.")}</p>
          ) : (
            <ul className="space-y-1">
              {top.map((p) => (
                <li key={p.id}>
                  <button
                    type="button"
                    onClick={() => flyTo(p)}
                    className="flex w-full items-center gap-2 rounded-xl px-2 py-1.5 text-left hover:bg-surface-2"
                  >
                    {p.favorite ? <Star size={14} className="shrink-0 text-amber-500" /> : <MapPin size={14} className="shrink-0 text-accent" />}
                    <span className="min-w-0 flex-1 truncate text-[13.5px] text-fg">{p.name}</span>
                    <span className={cx("shrink-0 text-[12px]", validCoords(p.lat, p.lng) ? "text-muted" : "text-amber-600 dark:text-amber-400")}>
                      {t("{n} visits", { n: p.visit_count })}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-3xl border border-border/70 bg-surface p-3.5">
          <h3 className="mb-2 text-[13.5px] font-semibold text-fg">{t("Common routes")}</h3>
          {routes.length === 0 ? (
            <p className="text-[12.5px] text-muted">{t("Log a trip, or let MiniMuse add one from your travels, and the routes you drive most appear here.")}</p>
          ) : (
            <ul className="space-y-1.5">
              {routes.map((r) => (
                <li key={`${r.from}→${r.to}`} className="rounded-xl bg-surface-2 px-3 py-2">
                  <div className="truncate text-[13.5px] font-medium text-fg">
                    {(r.from || t("Somewhere")) + " → " + r.to}
                  </div>
                  <div className="mt-0.5 text-[12px] text-muted">
                    {r.trips === 1 ? t("1 trip") : t("{n} trips", { n: r.trips })} · {t("{n} mi", { n: fmtMiles(r.miles) })}
                    {r.avgMinutes > 0 && <> · {t("{n} min", { n: Math.round(r.avgMinutes) })}</>}
                    {r.avgMph > 0 && <> · {t("{n} mph", { n: Math.round(r.avgMph) })}</>}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-[11.5px] leading-snug text-muted">
            {t("Routes go from place to place. Road-by-road stats need tracked trips, which are not recorded yet.")}
          </p>
        </section>
      </div>
    </div>
  );
}
