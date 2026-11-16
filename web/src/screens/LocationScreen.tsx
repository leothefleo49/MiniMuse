import {
  Activity,
  Car,
  Check,
  Compass,
  ExternalLink,
  Gauge,
  Hash,
  Loader2,
  MapPin,
  Navigation,
  Pin,
  Plus,
  Search,
  Star,
  Trash2,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Sheet } from "../components/Sheet";
import { TabHeader } from "../components/TabHeader";
import { useT } from "../i18n";
import { useStore } from "../store";
import type { ContactLocation, LocationPlace, LocationSummary, LocationTrafficEstimate, LocationTrip, MapLinks } from "../types";
import { cx } from "../util";

type SubTab = "places" | "contacts" | "travel";
type MapPref = "google" | "apple" | "waze" | "osm" | "geo";

const PLACE_CATEGORIES = ["home", "work", "friend", "favorite", "frequent", "errand", "food", "travel", "other"];

function directMapUrl(queryOrAddress: string, pref: MapPref, lat?: number | null, lng?: number | null, links?: MapLinks): string {
  if (links) {
    if (pref === "apple") return links.apple_maps;
    if (pref === "waze") return links.waze;
    if (pref === "osm") return links.openstreetmap;
    if (pref === "geo") return links.geo;
    return links.google_maps;
  }
  const q = encodeURIComponent(lat != null && lng != null ? `${lat},${lng}` : queryOrAddress.trim());
  switch (pref) {
    case "apple":
      return `https://maps.apple.com/?q=${q}`;
    case "waze":
      return `https://waze.com/ul?q=${q}&navigate=yes`;
    case "osm":
      return `https://www.openstreetmap.org/search?query=${q}`;
    case "geo":
      return `geo:0,0?q=${q}`;
    default:
      return `https://www.google.com/maps/search/?api=1&query=${q}`;
  }
}

export function LocationScreen() {
  const { state, setTab, refreshSettings, toast } = useStore();
  const t = useT();
  const [summary, setSummary] = useState<LocationSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [subTab, setSubTab] = useState<SubTab>("places");
  const [query, setQuery] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [catFilter, setCatFilter] = useState("");

  const mapPref = (state.settings?.ui?.map_app || "google") as MapPref;

  // Add place modal
  const [placeOpen, setPlaceOpen] = useState(false);
  const [pName, setPName] = useState("");
  const [pAddress, setPAddress] = useState("");
  const [pCategory, setPCategory] = useState("favorite");
  const [pTags, setPTags] = useState("");
  const [pContext, setPContext] = useState("");
  const [pContactName, setPContactName] = useState("");
  const [pFavorite, setPFavorite] = useState(true);
  const [savingPlace, setSavingPlace] = useState(false);

  // Add trip modal
  const [tripOpen, setTripOpen] = useState(false);
  const [tPlaceName, setTPlaceName] = useState("");
  const [tAddress, setTAddress] = useState("");
  const [tOrigin, setTOrigin] = useState("");
  const [tDist, setTDist] = useState("");
  const [tDur, setTDur] = useState("");
  const [tAvgMph, setTAvgMph] = useState("");
  const [tMaxMph, setTMaxMph] = useState("");
  const [tMode, setTMode] = useState("driving");
  const [tTraffic, setTTraffic] = useState("moderate");
  const [tTags, setTTags] = useState("");
  const [tNotes, setTNotes] = useState("");

  // Traffic / Route estimator
  const [routeOrigin, setRouteOrigin] = useState("Home");
  const [routeDest, setRouteDest] = useState("");
  const [trafficEst, setTrafficEst] = useState<LocationTrafficEstimate | null>(null);
  const [checkingTraffic, setCheckingTraffic] = useState(false);
  const [pingingGps, setPingingGps] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.locationSummary();
      setSummary(res);
    } catch (e) {
      toast((e as Error).message || t("Could not load locations"));
    } finally {
      setLoading(false);
    }
  }, [t, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const setMapPref = async (pref: MapPref) => {
    try {
      await api.updateSettings({ ui: { ...(state.settings?.ui ?? {}), map_app: pref } });
      await refreshSettings();
    } catch {
      /* non-fatal */
    }
  };

  const filteredPlaces = useMemo(() => {
    const q = query.trim().toLowerCase().replace(/^#/, "");
    return (summary?.places ?? []).filter((p) => {
      if (tagFilter && !p.tags.includes(tagFilter)) return false;
      if (catFilter && p.category !== catFilter) return false;
      if (!q) return true;
      const hay = `${p.name} ${p.address} ${p.category} ${p.context} ${p.contact_name} ${p.tags.join(" ")}`.toLowerCase();
      return hay.includes(q);
    });
  }, [summary, query, tagFilter, catFilter]);

  const filteredContacts = useMemo(() => {
    const q = query.trim().toLowerCase().replace(/^#/, "");
    return (summary?.contacts ?? []).filter((c) => {
      if (tagFilter && !c.tags.includes(tagFilter)) return false;
      if (!q) return true;
      const hay = `${c.name} ${c.nickname} ${c.address} ${c.org} ${c.note} ${c.tags.join(" ")}`.toLowerCase();
      return hay.includes(q);
    });
  }, [summary, query, tagFilter]);

  const filteredTrips = useMemo(() => {
    const q = query.trim().toLowerCase().replace(/^#/, "");
    return (summary?.trips ?? []).filter((tr) => {
      if (tagFilter && !tr.tags.includes(tagFilter)) return false;
      if (!q) return true;
      const hay = `${tr.place_name} ${tr.address} ${tr.origin_name} ${tr.origin_address} ${tr.traffic_level} ${tr.traffic_note} ${tr.notes} ${tr.tags.join(" ")}`.toLowerCase();
      return hay.includes(q);
    });
  }, [summary, query, tagFilter]);

  // Direct smart map launch by typed query (matches saved place/contact address first, else raw text!)
  const resolveQueryDestination = (): { label: string; url: string } | null => {
    const raw = query.trim();
    if (!raw) return null;
    const firstPlace = filteredPlaces[0];
    if (firstPlace && (firstPlace.address || firstPlace.lat != null)) {
      return {
        label: firstPlace.name,
        url: directMapUrl(firstPlace.address || firstPlace.name, mapPref, firstPlace.lat, firstPlace.lng, firstPlace.map_links),
      };
    }
    const firstContact = filteredContacts[0];
    if (firstContact?.address) {
      return {
        label: firstContact.name,
        url: directMapUrl(firstContact.address, mapPref, null, null, firstContact.map_links),
      };
    }
    return {
      label: raw,
      url: directMapUrl(raw, mapPref),
    };
  };

  const quickMatch = resolveQueryDestination();

  const savePlace = async () => {
    if (!pName.trim() || !pAddress.trim()) return;
    setSavingPlace(true);
    try {
      await api.addLocationPlace({
        name: pName.trim(),
        address: pAddress.trim(),
        category: pCategory,
        tags: pTags,
        context: pContext.trim(),
        contact_name: pContactName.trim(),
        favorite: pFavorite,
      });
      setPlaceOpen(false);
      setPName("");
      setPAddress("");
      setPTags("");
      setPContext("");
      setPContactName("");
      toast(t("Place saved"));
      await load();
    } catch (e) {
      toast((e as Error).message || t("Could not save place"));
    } finally {
      setSavingPlace(false);
    }
  };

  const visitPlace = async (p: LocationPlace) => {
    try {
      await api.visitLocationPlace(p.id);
      toast(t("Logged visit to {name}", { name: p.name }));
      await load();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const deletePlace = async (p: LocationPlace) => {
    try {
      await api.deleteLocationPlace(p.id);
      toast(t("Removed {name}", { name: p.name }));
      await load();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const saveContactAsPlace = async (c: ContactLocation) => {
    try {
      await api.addLocationPlace({
        name: c.nickname || c.name,
        address: c.address,
        category: "friend",
        tags: c.tags,
        context: c.note || c.org,
        contact_id: c.id,
        contact_name: c.name,
        favorite: true,
      });
      toast(t("Pinned {name} to saved places", { name: c.name }));
      await load();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const logTrip = async () => {
    if (!tPlaceName.trim() && !tAddress.trim()) return;
    try {
      await api.logLocationTrip({
        place_name: tPlaceName.trim(),
        address: tAddress.trim(),
        origin_name: tOrigin.trim(),
        distance_mi: Number(tDist) || 0,
        duration_min: Number(tDur) || 0,
        avg_speed_mph: Number(tAvgMph) || 0,
        max_speed_mph: Number(tMaxMph) || 0,
        mode: tMode,
        traffic_level: tTraffic,
        tags: tTags,
        notes: tNotes.trim(),
      });
      setTripOpen(false);
      toast(t("Trip logged"));
      await load();
    } catch (e) {
      toast((e as Error).message || t("Could not log trip"));
    }
  };

  const deleteTrip = async (tr: LocationTrip) => {
    try {
      await api.deleteLocationTrip(tr.id);
      await load();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const pingGps = () => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      toast(t("Geolocation is not available in this browser"));
      return;
    }
    setPingingGps(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const mph = pos.coords.speed != null && pos.coords.speed >= 0 ? Number((pos.coords.speed * 2.23694).toFixed(1)) : 0;
        void api
          .pingLocation({
            lat: pos.coords.latitude,
            lng: pos.coords.longitude,
            speed_mph: mph,
            place_name: "GPS Snapshot",
            mode: mph > 6 ? "driving" : "stationary",
          })
          .then(async () => {
            toast(t("GPS location & speed recorded ({mph} mph)", { mph }));
            await load();
          })
          .catch((e: Error) => toast(e.message))
          .finally(() => setPingingGps(false));
      },
      (err) => {
        setPingingGps(false);
        toast(err.message || t("Could not read GPS location"));
      },
      { enableHighAccuracy: true, timeout: 10000 },
    );
  };

  const checkTraffic = async () => {
    if (!routeOrigin.trim() || !routeDest.trim()) return;
    setCheckingTraffic(true);
    try {
      const est = await api.locationTraffic(routeOrigin.trim(), routeDest.trim());
      setTrafficEst(est);
      if (!est.ok && est.error) toast(est.error);
    } catch (e) {
      toast((e as Error).message || t("Could not estimate route"));
    } finally {
      setCheckingTraffic(false);
    }
  };

  const hideLocationTab = async () => {
    await api.updateSettings({ ui: { ...(state.settings?.ui ?? {}), show_location: "false" } });
    await refreshSettings();
    setTab("chat");
    toast(t("Location tab hidden (re-enable anytime in Settings)"));
  };

  return (
    <div className="flex h-full flex-col">
      <TabHeader
        title={t("Location & Maps")}
        actions={[
          { label: t("Add common address"), onClick: () => setPlaceOpen(true) },
          { label: t("Log trip / speed"), onClick: () => setTripOpen(true) },
          { label: t("Sync Google Contacts"), onClick: () => setTab("connections") },
          { label: t("Hide Location tab"), onClick: () => void hideLocationTab() },
        ]}
      >
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            disabled={pingingGps}
            onClick={pingGps}
            className="flex h-9 items-center gap-1 rounded-full bg-surface-2 px-3 text-[12.5px] font-medium hover:text-accent"
          >
            {pingingGps ? <Loader2 size={14} className="animate-spin" /> : <Compass size={14} />}
            <span className="hidden sm:inline">{t("GPS")}</span>
          </button>
          <button
            type="button"
            onClick={() => setPlaceOpen(true)}
            className="flex h-9 items-center gap-1 rounded-full bg-accent px-3.5 text-[13px] font-medium text-accent-fg shadow-xs"
          >
            <Plus size={15} /> {t("Place")}
          </button>
        </div>
      </TabHeader>

      <div className="flex-1 overflow-y-auto px-4 pb-8 space-y-3">
        {/* Universal Search -> Any Map App */}
        <div className="rounded-3xl border border-border/70 bg-surface p-3 space-y-2.5">
          <div className="flex flex-wrap gap-2">
            <div className="relative flex-1 min-w-[200px]">
              <Search size={14} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("Search by contact, address, context, or #hashtag...")}
                className="w-full rounded-2xl bg-surface-2 pl-9 pr-3 py-2 text-[13.5px] outline-none focus:ring-2 focus:ring-accent/35"
              />
            </div>
            {quickMatch && (
              <a
                href={quickMatch.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-2xl bg-accent px-3.5 py-2 text-[13px] font-medium text-accent-fg"
              >
                <MapPin size={14} /> {t("Open “{name}”", { name: quickMatch.label.slice(0, 22) })} <ExternalLink size={12} />
              </a>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 text-[11.5px]">
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-muted mr-1">{t("Map app:")}</span>
              {(
                [
                  { id: "google", label: "Google Maps" },
                  { id: "apple", label: "Apple Maps" },
                  { id: "waze", label: "Waze" },
                  { id: "osm", label: "OSM" },
                  { id: "geo", label: "geo:" },
                ] as Array<{ id: MapPref; label: string }>
              ).map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => void setMapPref(m.id)}
                  className={cx(
                    "rounded-full border px-2.5 py-0.5 transition",
                    mapPref === m.id ? "border-accent bg-accent/10 font-medium text-accent" : "border-border text-muted hover:text-fg",
                  )}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>

          {(summary?.tags?.length ?? 0) > 0 && (
            <div className="flex flex-wrap gap-1 pt-0.5">
              {summary!.tags.map((tg) => (
                <button
                  key={tg}
                  type="button"
                  onClick={() => setTagFilter((cur) => (cur === tg ? "" : tg))}
                  className={cx(
                    "inline-flex items-center gap-0.5 rounded-full border px-2.5 py-0.5 text-[11px]",
                    tagFilter === tg ? "border-accent bg-accent/10 font-medium text-accent" : "border-border text-muted",
                  )}
                >
                  <Hash size={10} />
                  {tg}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Section Tabs */}
        <div className="flex gap-1.5 rounded-2xl bg-surface-2 p-1 text-[13px]">
          <button
            type="button"
            onClick={() => setSubTab("places")}
            className={cx(
              "flex-1 flex items-center justify-center gap-1.5 rounded-xl py-1.5 font-medium transition",
              subTab === "places" ? "bg-surface text-fg shadow-xs" : "text-muted",
            )}
          >
            <MapPin size={14} /> {t("Saved & Frequent")} ({filteredPlaces.length})
          </button>
          <button
            type="button"
            onClick={() => setSubTab("contacts")}
            className={cx(
              "flex-1 flex items-center justify-center gap-1.5 rounded-xl py-1.5 font-medium transition",
              subTab === "contacts" ? "bg-surface text-fg shadow-xs" : "text-muted",
            )}
          >
            <Users size={14} /> {t("Friends & Contacts")} ({filteredContacts.length})
          </button>
          <button
            type="button"
            onClick={() => setSubTab("travel")}
            className={cx(
              "flex-1 flex items-center justify-center gap-1.5 rounded-xl py-1.5 font-medium transition",
              subTab === "travel" ? "bg-surface text-fg shadow-xs" : "text-muted",
            )}
          >
            <Gauge size={14} /> {t("Speed & Traffic")} ({filteredTrips.length})
          </button>
        </div>

        {loading && !summary ? (
          <div className="flex justify-center py-12 text-muted">
            <Loader2 className="animate-spin" size={20} />
          </div>
        ) : subTab === "places" ? (
          <div className="space-y-3">
            {/* Category chips */}
            <div className="flex flex-wrap gap-1">
              <button
                type="button"
                onClick={() => setCatFilter("")}
                className={cx("rounded-full border px-2.5 py-0.5 text-[11px]", !catFilter ? "border-accent bg-accent/10 text-accent font-medium" : "border-border text-muted")}
              >
                {t("All")}
              </button>
              {PLACE_CATEGORIES.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setCatFilter((cur) => (cur === c ? "" : c))}
                  className={cx("rounded-full border px-2.5 py-0.5 text-[11px] capitalize", catFilter === c ? "border-accent bg-accent/10 text-accent font-medium" : "border-border text-muted")}
                >
                  {c}
                </button>
              ))}
            </div>

            {filteredPlaces.length === 0 ? (
              <div className="rounded-3xl border border-border/70 bg-surface p-6 text-center text-[13px] text-muted">
                {t("No matching common addresses yet. Save Home, Work, favorite spots, or friend places so you never have to re-search them.")}
              </div>
            ) : (
              filteredPlaces.map((p) => (
                <div key={p.id} className="rounded-3xl border border-border/70 bg-surface p-3.5 space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-[15px]">{p.name}</span>
                        <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[10.5px] capitalize text-muted">{p.category}</span>
                        {p.favorite && <Star size={13} className="fill-amber-400 text-amber-400" />}
                        {p.visit_count > 0 && (
                          <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10.5px] font-medium text-accent">
                            {t("{n} visits", { n: p.visit_count })}
                          </span>
                        )}
                      </div>
                      <div className="text-[12.5px] text-fg/85 mt-0.5">{p.address}</div>
                      {p.context && <div className="text-[12px] text-muted mt-0.5">{p.context}</div>}
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => void visitPlace(p)}
                        title={t("Mark visited today")}
                        className="rounded-xl border border-border px-2 py-1 text-[11px] hover:bg-surface-2"
                      >
                        <Check size={12} className="inline mr-0.5" /> +1
                      </button>
                      <button
                        type="button"
                        onClick={() => void deletePlace(p)}
                        aria-label={t("Remove")}
                        className="rounded-xl p-1.5 text-muted hover:text-rose-600"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <a
                      href={directMapUrl(p.address || p.name, mapPref, p.lat, p.lng, p.map_links)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-3 py-1 text-[12px] font-medium text-accent hover:bg-accent/15"
                    >
                      <Navigation size={12} /> {t("Open in Maps")} <ExternalLink size={11} />
                    </a>
                    <a
                      href={p.map_links.google_directions}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-[11.5px] text-fg/80 hover:bg-surface-2"
                    >
                      {t("Directions")}
                    </a>
                    <button
                      type="button"
                      onClick={() => {
                        setRouteDest(p.address || p.name);
                        setSubTab("travel");
                      }}
                      className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-[11.5px] text-muted hover:text-fg"
                    >
                      <Car size={11} /> {t("Check Traffic")}
                    </button>
                    {p.tags.map((tg) => (
                      <span key={tg} className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-muted">
                        #{tg}
                      </span>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        ) : subTab === "contacts" ? (
          <div className="space-y-2.5">
            {filteredContacts.length === 0 ? (
              <div className="rounded-3xl border border-border/70 bg-surface p-6 text-center space-y-2 text-[13px] text-muted">
                <div>{t("No friends or contacts have a street address saved yet.")}</div>
                <p>{t("Sync Google Contacts (People API) or add addresses to contacts so you can open them in one click.")}</p>
              </div>
            ) : (
              filteredContacts.map((c) => (
                <div key={c.id} className="rounded-3xl border border-border/70 bg-surface p-3.5 space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 font-semibold text-[15px]">
                        <span>{c.name}</span>
                        {c.nickname && <span className="text-[12px] font-normal text-muted">({c.nickname})</span>}
                        <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[10px] text-muted">{c.source}</span>
                      </div>
                      <div className="text-[13px] text-fg/90 mt-0.5">{c.address}</div>
                      {(c.org || c.note) && <div className="text-[12px] text-muted mt-0.5">{c.org}{c.org && c.note ? " · " : ""}{c.note}</div>}
                    </div>
                    <button
                      type="button"
                      onClick={() => void saveContactAsPlace(c)}
                      title={t("Pin to Saved Places")}
                      className="rounded-xl border border-border px-2.5 py-1 text-[11.5px] hover:bg-surface-2 shrink-0"
                    >
                      <Pin size={12} className="inline mr-1" /> {t("Pin")}
                    </button>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <a
                      href={directMapUrl(c.address, mapPref, null, null, c.map_links)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-3 py-1 text-[12px] font-medium text-accent hover:bg-accent/15"
                    >
                      <MapPin size={12} /> {t("Open in Maps")} <ExternalLink size={11} />
                    </a>
                    <a
                      href={c.map_links.google_directions}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-[11.5px] text-fg/80 hover:bg-surface-2"
                    >
                      {t("Directions")}
                    </a>
                  </div>
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {/* Speed & Travel Summary Stats */}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <StatTile label={t("Avg Driving")} value={`${summary?.stats.avg_driving_mph ?? 0} mph`} />
              <StatTile label={t("Top Speed")} value={`${summary?.stats.max_speed_mph ?? 0} mph`} />
              <StatTile label={t("Latest Speed")} value={`${summary?.stats.latest_speed_mph ?? 0} mph`} />
              <StatTile label={t("Miles Logged")} value={`${summary?.stats.total_miles ?? 0} mi`} />
            </div>

            {/* Route & Live Traffic Estimator */}
            <div className="rounded-3xl border border-border/70 bg-surface p-3.5 space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-[13.5px] font-semibold">
                  <Activity size={15} className="text-accent" /> {t("Check Route, Speed & Traffic")}
                </span>
                <button
                  type="button"
                  onClick={() => setTripOpen(true)}
                  className="inline-flex items-center gap-1 text-[12px] font-medium text-accent hover:underline"
                >
                  <Plus size={13} /> {t("Log trip")}
                </button>
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <input
                  value={routeOrigin}
                  onChange={(e) => setRouteOrigin(e.target.value)}
                  placeholder={t("Origin (place name, contact, or address)")}
                  className="rounded-2xl bg-surface-2 px-3 py-2 text-[13px] outline-none"
                />
                <input
                  value={routeDest}
                  onChange={(e) => setRouteDest(e.target.value)}
                  placeholder={t("Destination (place, contact, or address)")}
                  className="rounded-2xl bg-surface-2 px-3 py-2 text-[13px] outline-none"
                />
              </div>
              <button
                type="button"
                disabled={checkingTraffic || !routeOrigin.trim() || !routeDest.trim()}
                onClick={() => void checkTraffic()}
                className="w-full rounded-2xl bg-accent py-2 text-[13.5px] font-medium text-accent-fg disabled:opacity-50"
              >
                {checkingTraffic ? t("Checking route & traffic...") : t("Estimate ETA, Speed & Traffic")}
              </button>
              {trafficEst && trafficEst.ok && (
                <div className="rounded-2xl border border-border/60 bg-surface-2/40 p-3 space-y-1.5 text-[12.5px]">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold">
                      {trafficEst.distance_mi} mi · ~{trafficEst.estimated_min} min
                    </span>
                    <span className="rounded-full bg-accent/12 px-2.5 py-0.5 text-[11px] font-medium capitalize text-accent">
                      {trafficEst.traffic_level} traffic · {trafficEst.avg_speed_mph} mph avg
                    </span>
                  </div>
                  <div className="text-muted">{trafficEst.traffic_note}</div>
                  {trafficEst.map_links && (
                    <a
                      href={trafficEst.map_links.google_directions}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-[12px] font-medium text-accent hover:underline"
                    >
                      <Navigation size={12} /> {t("Start live navigation")} <ExternalLink size={11} />
                    </a>
                  )}
                </div>
              )}
            </div>

            {/* Travel History */}
            {filteredTrips.length === 0 ? (
              <div className="rounded-3xl border border-border/70 bg-surface p-6 text-center text-[13px] text-muted">
                {t("No trips recorded yet. Use GPS Snapshot or + Log Trip to track driving speeds, routes, and traffic.")}
              </div>
            ) : (
              filteredTrips.map((tr) => (
                <div key={tr.id} className="rounded-3xl border border-border/70 bg-surface p-3.5 space-y-1.5">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="font-semibold text-[14.5px]">
                        {tr.place_name || tr.address || "GPS Location"}
                      </div>
                      <div className="text-[12px] text-muted">
                        {tr.origin_name ? `${tr.origin_name} -> ` : ""}
                        {tr.address}
                      </div>
                    </div>
                    <button type="button" onClick={() => void deleteTrip(tr)} className="rounded-xl p-1 text-muted hover:text-rose-600">
                      <Trash2 size={14} />
                    </button>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-[12px]">
                    {tr.avg_speed_mph > 0 && (
                      <span className="rounded-full bg-surface-2 px-2.5 py-0.5 font-medium">
                        {tr.avg_speed_mph} mph avg {tr.max_speed_mph > 0 ? `(max ${tr.max_speed_mph})` : ""}
                      </span>
                    )}
                    {tr.distance_mi > 0 && <span className="rounded-full bg-surface-2 px-2.5 py-0.5">{tr.distance_mi} mi</span>}
                    {tr.duration_min > 0 && <span className="rounded-full bg-surface-2 px-2.5 py-0.5">{tr.duration_min} min</span>}
                    <span className="rounded-full bg-accent/10 px-2.5 py-0.5 capitalize text-accent">
                      {tr.mode} · {tr.traffic_level} traffic
                    </span>
                  </div>
                  {tr.notes && <p className="text-[12px] text-muted">{tr.notes}</p>}
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {/* Add Place Modal */}
      <Sheet open={placeOpen} onClose={() => setPlaceOpen(false)} title={t("Save Common Address or Place")}>
        <div className="space-y-3 pb-4">
          <input value={pName} onChange={(e) => setPName(e.target.value)} placeholder={t("Place name (e.g. Home, Mom's House, Gym)")} className="w-full rounded-2xl bg-surface-2 px-4 py-2.5 text-[14.5px] outline-none" />
          <input value={pAddress} onChange={(e) => setPAddress(e.target.value)} placeholder={t("Street address or coordinates")} className="w-full rounded-2xl bg-surface-2 px-4 py-2.5 text-[14px] outline-none" />
          <div className="grid grid-cols-2 gap-2">
            <select value={pCategory} onChange={(e) => setPCategory(e.target.value)} className="rounded-2xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none capitalize">
              {PLACE_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <input value={pContactName} onChange={(e) => setPContactName(e.target.value)} placeholder={t("Contact name (optional)")} className="rounded-2xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none" />
          </div>
          <input value={pTags} onChange={(e) => setPTags(e.target.value)} placeholder={t("#hashtags (e.g. #family #parking #errand)")} className="w-full rounded-2xl bg-surface-2 px-4 py-2 text-[13.5px] outline-none" />
          <textarea value={pContext} onChange={(e) => setPContext(e.target.value)} rows={2} placeholder={t("Context note (gate code, best entrance, hours...)")} className="w-full rounded-2xl bg-surface-2 px-4 py-2 text-[13.5px] outline-none resize-none" />
          <label className="flex items-center gap-2 text-[13px]">
            <input type="checkbox" checked={pFavorite} onChange={(e) => setPFavorite(e.target.checked)} className="accent-[var(--om-accent)]" />
            {t("Pin to Favorites / Common places")}
          </label>
          <button
            type="button"
            disabled={savingPlace || !pName.trim() || !pAddress.trim()}
            onClick={() => void savePlace()}
            className="w-full rounded-2xl bg-accent py-2.5 text-[14.5px] font-semibold text-accent-fg disabled:opacity-50"
          >
            {savingPlace ? t("Saving...") : t("Save Place")}
          </button>
        </div>
      </Sheet>

      {/* Log Trip Modal */}
      <Sheet open={tripOpen} onClose={() => setTripOpen(false)} title={t("Log Trip & Driving Speed")}>
        <div className="space-y-3 pb-4">
          <div className="grid grid-cols-2 gap-2">
            <input value={tPlaceName} onChange={(e) => setTPlaceName(e.target.value)} placeholder={t("Destination name")} className="rounded-2xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none" />
            <input value={tOrigin} onChange={(e) => setTOrigin(e.target.value)} placeholder={t("Started from")} className="rounded-2xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none" />
          </div>
          <input value={tAddress} onChange={(e) => setTAddress(e.target.value)} placeholder={t("Destination address")} className="w-full rounded-2xl bg-surface-2 px-4 py-2 text-[13.5px] outline-none" />
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <input type="number" value={tDist} onChange={(e) => setTDist(e.target.value)} placeholder={t("Miles")} className="rounded-2xl bg-surface-2 px-3 py-2 text-[13px] outline-none" />
            <input type="number" value={tDur} onChange={(e) => setTDur(e.target.value)} placeholder={t("Minutes")} className="rounded-2xl bg-surface-2 px-3 py-2 text-[13px] outline-none" />
            <input type="number" value={tAvgMph} onChange={(e) => setTAvgMph(e.target.value)} placeholder={t("Avg mph")} className="rounded-2xl bg-surface-2 px-3 py-2 text-[13px] outline-none" />
            <input type="number" value={tMaxMph} onChange={(e) => setTMaxMph(e.target.value)} placeholder={t("Max mph")} className="rounded-2xl bg-surface-2 px-3 py-2 text-[13px] outline-none" />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <select value={tMode} onChange={(e) => setTMode(e.target.value)} className="rounded-2xl bg-surface-2 px-3 py-2 text-[13px] outline-none">
              <option value="driving">Driving</option>
              <option value="walking">Walking</option>
              <option value="cycling">Cycling</option>
              <option value="transit">Transit</option>
            </select>
            <select value={tTraffic} onChange={(e) => setTTraffic(e.target.value)} className="rounded-2xl bg-surface-2 px-3 py-2 text-[13px] outline-none">
              <option value="clear">Clear traffic</option>
              <option value="light">Light traffic</option>
              <option value="moderate">Moderate traffic</option>
              <option value="heavy">Heavy traffic</option>
            </select>
          </div>
          <input value={tTags} onChange={(e) => setTTags(e.target.value)} placeholder={t("#tags")} className="w-full rounded-2xl bg-surface-2 px-4 py-2 text-[13.5px] outline-none" />
          <input value={tNotes} onChange={(e) => setTNotes(e.target.value)} placeholder={t("Route or traffic notes")} className="w-full rounded-2xl bg-surface-2 px-4 py-2 text-[13.5px] outline-none" />
          <button
            type="button"
            onClick={() => void logTrip()}
            className="w-full rounded-2xl bg-accent py-2.5 text-[14.5px] font-semibold text-accent-fg"
          >
            {t("Save Trip")}
          </button>
        </div>
      </Sheet>
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-border/70 bg-surface p-3 text-center">
      <div className="text-[11px] font-medium text-muted">{label}</div>
      <div className="mt-0.5 text-[16px] font-bold">{value}</div>
    </div>
  );
}
