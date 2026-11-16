import { CheckCircle2, ExternalLink, Globe, KeyRound, Loader2, Play, Plus, RefreshCw, Search, Sparkles, Trash2, Wand2, Wrench, XCircle, Zap } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { Card, inputCls, primaryBtn, secondaryBtn } from "./Form";
import { useT } from "../i18n";
import { DRAFT_THREAD, useStore } from "../store";
import type { ApiConnection, ApiPreset, ConnectionsData, TestResult } from "../types";
import { cx } from "../util";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="block text-[11.5px] font-medium text-muted">{label}</span>
      {children}
    </label>
  );
}

/**
 * 36+ standard REST/GraphQL APIs + Universal Custom API Builder.
 * Every secret is encrypted in the local vault; the AI discovers OpenAPI schemas
 * or figures out read/write endpoints on the fly via the `api_connector` tool.
 */
export function ApiIntegrationsCard({
  data,
  onChange,
  compact,
}: {
  data: ConnectionsData;
  onChange: () => void;
  compact?: boolean;
}) {
  const { dispatch, openThread, draft, dismissOnboarding, toast } = useStore();
  const t = useT();
  const [open, setOpen] = useState(!!compact);
  const [presets, setPresets] = useState<ApiPreset[]>([]);
  const [apisRaw, setApis] = useState<ApiConnection[]>(() => (Array.isArray(data.apis) ? data.apis : []));
  const apis = Array.isArray(apisRaw) ? apisRaw : [];
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [loadingPresets, setLoadingPresets] = useState(false);

  // Add / edit form state
  const [editingPreset, setEditingPreset] = useState<ApiPreset | null>(null);
  const [editingId, setEditingId] = useState("");
  const [name, setName] = useState("");
  const [presetId, setPresetId] = useState("custom");
  const [baseUrl, setBaseUrl] = useState("");
  const [authType, setAuthType] = useState("bearer");
  const [authHeader, setAuthHeader] = useState("Authorization");
  const [authPrefix, setAuthPrefix] = useState("Bearer");
  const [secret, setSecret] = useState("");
  const [openapiUrl, setOpenapiUrl] = useState("");
  const [notes, setNotes] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [quickBusy, setQuickBusy] = useState<string | null>(null);
  const formRef = useRef<HTMLDivElement>(null);

  // Action & runner state
  const [busyId, setBusyId] = useState<string | null>(null);
  const [testMap, setTestMap] = useState<Record<string, TestResult & { error?: string }>>({});
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [specInputMap, setSpecInputMap] = useState<Record<string, string>>({});
  const [epName, setEpName] = useState("");
  const [epMethod, setEpMethod] = useState("GET");
  const [epPath, setEpPath] = useState("/");
  const [epDesc, setEpDesc] = useState("");
  const [reqMethod, setReqMethod] = useState("GET");
  const [reqPath, setReqPath] = useState("/");
  const [reqBody, setReqBody] = useState("");
  const [reqOutput, setReqOutput] = useState<string | null>(null);

  useEffect(() => {
    if (Array.isArray(data.apis)) setApis(data.apis);
  }, [data.apis]);

  const loadAll = useCallback(async () => {
    setLoadingPresets(true);
    try {
      const [pr, list] = await Promise.all([api.apiPresets(), api.apis()]);
      setPresets(Array.isArray(pr?.presets) ? pr.presets : []);
      setApis(Array.isArray(list) ? list : []);
    } catch {
      /* backend may be busy or starting */
    } finally {
      setLoadingPresets(false);
    }
  }, []);

  useEffect(() => {
    if (open && presets.length === 0) {
      void loadAll();
    }
  }, [open, presets.length, loadAll]);

  const categories = useMemo(() => {
    const set = new Set<string>(["all"]);
    for (const p of presets) {
      if (p.category) set.add(p.category);
    }
    return Array.from(set);
  }, [presets]);

  const filteredPresets = useMemo(() => {
    const q = query.trim().toLowerCase();
    return presets.filter((p) => {
      if (category !== "all" && p.category !== category) return false;
      if (!q) return true;
      return `${p.name} ${p.id} ${p.category} ${p.description}`.toLowerCase().includes(q);
    });
  }, [presets, query, category]);

  const enableFreeApisNow = async () => {
    setQuickBusy("free");
    try {
      await api.enableFreeApis();
      toast(t("Enabled free Weather & Maps/Routing APIs (no key required)!"));
      await loadAll();
      onChange();
    } catch (err) {
      toast((err as Error).message || t("Failed to enable free APIs"));
    } finally {
      setQuickBusy(null);
    }
  };

  const enableGoogleApisNow = async () => {
    setQuickBusy("google");
    try {
      await api.googleEnableApis();
      toast(t("Enabled Gmail, Google Calendar, Tasks & Drive APIs!"));
      await loadAll();
      onChange();
    } catch (err) {
      toast((err as Error).message || t("Failed to enable Google APIs"));
    } finally {
      setQuickBusy(null);
    }
  };

  const startFromPreset = async (p: ApiPreset | null) => {
    // 1-click instant enable for zero-key public APIs (Weather, OpenStreetMap, etc.)
    if (p && p.auth_type === "none" && !p.base_url.includes("<") && !p.base_url.includes("{{")) {
      setQuickBusy(p.id);
      try {
        const saved = await api.upsertApi({
          preset_id: p.id,
          name: p.name,
          base_url: p.base_url,
          auth_type: "none",
          docs_url: p.docs_url || "",
          learned_notes: p.ai_guide || p.description || "",
          enabled: true,
          endpoints: p.endpoints ?? undefined,
        });
        const tRes = await api.testApi(saved.id).catch(() => null);
        if (tRes) setTestMap((m) => ({ ...m, [saved.id]: tRes }));
        toast(t("Connected {name}!", { name: saved.name }));
        await loadAll();
        onChange();
      } catch (err) {
        toast((err as Error).message || t("Failed to save API"));
      } finally {
        setQuickBusy(null);
      }
      return;
    }

    setEditingId("");
    setEditingPreset(p);
    setFormOpen(true);
    if (p) {
      const existingCount = apis.filter((a) => a.preset_id === p.id).length;
      setPresetId(p.id);
      setName(existingCount > 0 ? `${p.name} (${existingCount + 1})` : p.name);
      setBaseUrl(p.base_url);
      setAuthType(p.auth_type || "bearer");
      setAuthHeader(p.auth_header || "Authorization");
      setAuthPrefix(p.auth_prefix ?? (p.auth_type === "bearer" ? "Bearer" : ""));
      setOpenapiUrl(p.docs_url || "");
      setNotes(p.ai_guide || p.description || "");
      setAdvancedOpen(p.base_url.includes("<") || p.base_url.includes("your-domain"));
    } else {
      setPresetId("custom");
      setName("");
      setBaseUrl("https://");
      setAuthType("bearer");
      setAuthHeader("Authorization");
      setAuthPrefix("Bearer");
      setOpenapiUrl("");
      setNotes("");
      setAdvancedOpen(true);
    }
    setSecret("");
    window.setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 60);
  };

  const editExisting = (conn: ApiConnection) => {
    setEditingId(conn.id);
    setEditingPreset(null);
    setFormOpen(true);
    setAdvancedOpen(false);
    setPresetId(conn.preset_id || "custom");
    setName(conn.name);
    setBaseUrl(conn.base_url);
    setAuthType(conn.auth_type || "bearer");
    setAuthHeader(conn.auth_header || "Authorization");
    setAuthPrefix(conn.auth_prefix ?? "");
    setOpenapiUrl(conn.docs_url || "");
    setNotes(conn.learned_notes || conn.ai_guide || "");
    setSecret("");
    window.setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 60);
  };

  const saveApi = async () => {
    if (!name.trim() || !baseUrl.trim()) return;
    setSaving(true);
    try {
      const specOrDocs = openapiUrl.trim();
      const saved = await api.upsertApi({
        id: editingId || undefined,
        name: name.trim(),
        preset_id: presetId || "custom",
        base_url: baseUrl.trim(),
        auth_type: authType,
        auth_header: authHeader.trim(),
        auth_prefix: authPrefix.trim(),
        secret: secret.trim() || undefined,
        docs_url: specOrDocs,
        learned_notes: notes.trim(),
        enabled: true,
        endpoints: editingPreset?.endpoints ?? undefined,
      });
      if (specOrDocs && /\.(json|ya?ml)(\?.*)?$|openapi|swagger|api-docs/i.test(specOrDocs)) {
        await api.discoverApi(saved.id, specOrDocs).catch(() => undefined);
      }
      let testOk = false;
      if (secret.trim() || authType === "none" || saved.has_secret) {
        const res = await api.testApi(saved.id).catch(() => null);
        if (res) {
          setTestMap((m) => ({ ...m, [saved.id]: res }));
          testOk = !!res.ok;
        }
      }
      toast(
        testOk
          ? t("Connected and verified: {name}", { name: saved.name })
          : t("Saved API connection: {name}", { name: saved.name }),
      );
      setFormOpen(false);
      setSecret("");
      await loadAll();
      onChange();
    } catch (err) {
      toast((err as Error).message || t("Failed to save API"));
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (id: string) => {
    setBusyId(id);
    try {
      const res = await api.testApi(id);
      setTestMap((m) => ({ ...m, [id]: res }));
      toast(res.ok ? t("API reachable") : (res.error || t("API check failed")));
    } catch (err) {
      setTestMap((m) => ({ ...m, [id]: { ok: false, error: (err as Error).message } }));
    } finally {
      setBusyId(null);
    }
  };

  const runDiscover = async (conn: ApiConnection) => {
    setBusyId(conn.id);
    try {
      const rawSpec = (specInputMap[conn.id] ?? "").trim();
      const res = await api.discoverApi(conn.id, rawSpec);
      if (res.ok) {
        const count = Number(res.endpoints_count ?? (Array.isArray(res.endpoints) ? res.endpoints.length : 0));
        toast(t("Learned {n} endpoints from OpenAPI schema", { n: String(count) }));
        await loadAll();
        onChange();
      } else {
        toast(String(res.error || t("No OpenAPI schema found at that URL")));
      }
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const removeApi = async (conn: ApiConnection) => {
    setBusyId(conn.id);
    try {
      await api.deleteApi(conn.id);
      toast(t("Removed {name}", { name: conn.name }));
      await loadAll();
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const addEndpoint = async (id: string) => {
    if (!epName.trim() || !epPath.trim()) return;
    setBusyId(id);
    try {
      const slug = epName.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || `ep_${Date.now()}`;
      await api.saveApiEndpoint(id, {
        id: slug,
        name: epName.trim(),
        method: epMethod,
        path: epPath.trim(),
        description: epDesc.trim(),
      });
      setEpName("");
      setEpPath("/");
      setEpDesc("");
      toast(t("Endpoint saved"));
      await loadAll();
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const sendTestRequest = async (id: string) => {
    setBusyId(id);
    setReqOutput(null);
    try {
      let body: unknown = undefined;
      if (reqBody.trim()) {
        try {
          body = JSON.parse(reqBody);
        } catch {
          body = reqBody;
        }
      }
      const res = await api.callApiRequest({
        api_id: id,
        method: reqMethod,
        path: reqPath.trim() || "/",
        body,
      });
      setReqOutput(JSON.stringify(res, null, 2).slice(0, 4000));
    } catch (err) {
      setReqOutput(String((err as Error).message));
    } finally {
      setBusyId(null);
    }
  };

  const askAiForApi = async (target?: ApiConnection) => {
    const prompt = target
      ? `Inspect the configured "${target.name}" API (id: ${target.id}, base URL: ${target.base_url}). Discover or learn its read and write endpoints using api_connector (discover_openapi / save_endpoint / request), summarize what data I can read or update with it, and test a safe read call.`
      : `Help me connect and configure a new REST or GraphQL API using the api_connector tool. Ask me which service/URL I want to integrate (or if I want OpenAPI auto-discovery), store any token cleanly in my local vault, discover its endpoints, and teach yourself how to read and write its data.`;
    if (compact) {
      try {
        await api.onboarded(true);
      } catch {
        /* ok */
      }
      dismissOnboarding();
    }
    dispatch({ type: "draftThread", title: target ? `API: ${target.name}` : "Configure Custom API" });
    openThread(DRAFT_THREAD);
    draft(prompt);
  };

  return (
    <Card
      icon={<Wrench size={19} />}
      title={t("APIs & Universal AI Integrations")}
      summary={
        apis.length > 0
          ? t("{n} connected · {presets}+ built-in & any custom API", { n: apis.length, presets: Math.max(presets.length, 36) })
          : t("36+ standard APIs + any custom REST/GraphQL API the AI learns automatically")
      }
      status={
        apis.some((a) => a.enabled)
          ? { text: t("{n} active", { n: apis.filter((a) => a.enabled).length }), tone: "ok" }
          : { text: t("Ready"), tone: "off" }
      }
      open={open}
      onToggle={compact ? undefined : () => setOpen((v) => !v)}
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[12.5px] text-muted flex-1 min-w-[200px]">
            {t("Pick any service below for 1-click setup & key paste, enable free no-key APIs, or add any custom API. Keys stay encrypted in your vault.")}
          </p>
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              disabled={quickBusy !== null}
              onClick={() => void enableFreeApisNow()}
              className="inline-flex items-center gap-1 rounded-full bg-accent px-3 py-1.5 text-[12px] font-semibold text-accent-fg hover:opacity-90 disabled:opacity-60"
            >
              {quickBusy === "free" ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}{" "}
              {t("Enable Free Weather & Maps (No Key)")}
            </button>
            {data.google?.signed_in && (
              <button
                type="button"
                disabled={quickBusy !== null}
                onClick={() => void enableGoogleApisNow()}
                className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-[12px] font-medium text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/15 disabled:opacity-60"
              >
                {quickBusy === "google" ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}{" "}
                {t("Enable All Google APIs")}
              </button>
            )}
            <button
              type="button"
              onClick={() => void askAiForApi()}
              className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-3 py-1.5 text-[12px] font-medium text-accent hover:bg-accent/15"
            >
              <Wand2 size={13} /> {t("Let AI configure an API")}
            </button>
            <button
              type="button"
              onClick={() => void startFromPreset(null)}
              className={secondaryBtn}
            >
              <Plus size={14} /> {t("Custom Any API")}
            </button>
          </div>
        </div>

        {/* Connected API instances (multiple instances per API supported) */}
        {apis.length > 0 && (
          <div className="space-y-2">
            <div className="text-[11.5px] font-semibold uppercase tracking-wide text-muted">
              {t("Configured APIs ({n})", { n: apis.length })}
            </div>
            {apis.map((conn) => {
              const tRes = testMap[conn.id];
              const isExpanded = expandedId === conn.id;
              return (
                <div key={conn.id} className="rounded-2xl border border-border/70 bg-surface p-3 space-y-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-[13.5px] truncate">{conn.name}</span>
                        <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[10.5px] text-muted">{conn.preset_id}</span>
                        {conn.has_secret && (
                          <span className="inline-flex items-center gap-0.5 rounded-full bg-emerald-500/12 px-2 py-0.5 text-[10.5px] font-medium text-emerald-700 dark:text-emerald-300">
                            <KeyRound size={10} /> {t("Vault")}
                          </span>
                        )}
                      </div>
                      <div className="truncate text-[11.5px] text-muted">{conn.base_url} · {conn.endpoints.length} {t("endpoints")}</div>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <button
                        type="button"
                        disabled={busyId === conn.id}
                        onClick={() => void runTest(conn.id)}
                        className="rounded-xl border border-border px-2.5 py-1 text-[11.5px] font-medium hover:bg-surface-2"
                      >
                        {busyId === conn.id ? <Loader2 size={12} className="animate-spin inline" /> : t("Test")}
                      </button>
                      <button
                        type="button"
                        onClick={() => setExpandedId(isExpanded ? null : conn.id)}
                        className="rounded-xl border border-border px-2.5 py-1 text-[11.5px] font-medium hover:bg-surface-2"
                      >
                        {isExpanded ? t("Close") : t("Endpoints & Try")}
                      </button>
                      <button
                        type="button"
                        onClick={() => void askAiForApi(conn)}
                        className="rounded-xl border border-accent/35 bg-accent/10 px-2.5 py-1 text-[11.5px] font-medium text-accent hover:bg-accent/15"
                      >
                        <Sparkles size={11} className="inline mr-1" />{t("AI learn")}
                      </button>
                      <button
                        type="button"
                        onClick={() => editExisting(conn)}
                        className="rounded-xl border border-border px-2.5 py-1 text-[11.5px] text-muted hover:text-fg"
                      >
                        {t("Edit")}
                      </button>
                      <button
                        type="button"
                        onClick={() => void removeApi(conn)}
                        aria-label={t("Remove")}
                        className="rounded-xl border border-border p-1.5 text-muted hover:text-rose-600"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>

                  {tRes && (
                    <div className={cx("flex items-center gap-1.5 text-[11.5px]", tRes.ok ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400")}>
                      {tRes.ok ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
                      {tRes.ok ? t("Reachable") : (tRes.error || t("Check failed"))}
                    </div>
                  )}

                  {isExpanded && (
                    <div className="border-t border-border/60 pt-2.5 space-y-3 text-[12px]">
                      {/* OpenAPI discovery */}
                      <div className="flex flex-wrap gap-1.5">
                        <input
                          value={specInputMap[conn.id] ?? (/\.json$|\.ya?ml$|openapi|swagger|api-docs/i.test(conn.docs_url || "") ? conn.docs_url : "")}
                          onChange={(e) => setSpecInputMap((m) => ({ ...m, [conn.id]: e.target.value }))}
                          placeholder={t("OpenAPI / Swagger JSON URL (e.g. https://api.example.com/openapi.json)")}
                          className={cx(inputCls, "flex-1 py-1.5 text-[12px]")}
                        />
                        <button
                          type="button"
                          disabled={busyId === conn.id}
                          onClick={() => void runDiscover(conn)}
                          className={secondaryBtn}
                        >
                          <RefreshCw size={12} /> {t("Discover Schema")}
                        </button>
                      </div>

                      {/* Saved endpoints */}
                      {conn.endpoints.length > 0 && (
                        <div className="space-y-1">
                          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">{t("Known read/write endpoints")}</div>
                          <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                            {conn.endpoints.slice(0, 14).map((ep, idx) => (
                              <button
                                key={`${ep.method}-${ep.path}-${idx}`}
                                type="button"
                                onClick={() => {
                                  setReqMethod(ep.method || "GET");
                                  setReqPath(ep.path || "/");
                                }}
                                className="flex items-center gap-2 rounded-xl border border-border/60 bg-surface-2/40 px-2.5 py-1.5 text-left hover:bg-surface-2"
                              >
                                <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-bold text-accent">{ep.method}</span>
                                <span className="truncate font-mono text-[11px]">{ep.path}</span>
                                <span className="ml-auto truncate text-[10.5px] text-muted">{ep.name}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Save custom endpoint */}
                      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-[88px_1fr_1fr_1fr_auto]">
                        <select value={epMethod} onChange={(e) => setEpMethod(e.target.value)} className={cx(inputCls, "py-1.5 text-[12px]")}>
                          {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m} value={m}>{m}</option>)}
                        </select>
                        <input value={epName} onChange={(e) => setEpName(e.target.value)} placeholder={t("Action name")} className={cx(inputCls, "py-1.5 text-[12px]")} />
                        <input value={epPath} onChange={(e) => setEpPath(e.target.value)} placeholder="/v1/items" className={cx(inputCls, "py-1.5 text-[12px]")} />
                        <input value={epDesc} onChange={(e) => setEpDesc(e.target.value)} placeholder={t("What this reads/writes")} className={cx(inputCls, "py-1.5 text-[12px]")} />
                        <button type="button" onClick={() => void addEndpoint(conn.id)} className={secondaryBtn}>{t("Save Endpoint")}</button>
                      </div>

                      {/* Try live call */}
                      <div className="rounded-xl border border-border/60 bg-surface-2/30 p-2.5 space-y-2">
                        <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">{t("Test live API request")}</div>
                        <div className="flex flex-wrap gap-1.5">
                          <select value={reqMethod} onChange={(e) => setReqMethod(e.target.value)} className={cx(inputCls, "w-24 py-1.5 text-[12px]")}>
                            {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m} value={m}>{m}</option>)}
                          </select>
                          <input value={reqPath} onChange={(e) => setReqPath(e.target.value)} placeholder="/v1/me" className={cx(inputCls, "flex-1 py-1.5 font-mono text-[12px]")} />
                          <button type="button" disabled={busyId === conn.id} onClick={() => void sendTestRequest(conn.id)} className={primaryBtn}>
                            <Play size={13} /> {t("Send")}
                          </button>
                        </div>
                        {reqMethod !== "GET" && (
                          <textarea
                            value={reqBody}
                            onChange={(e) => setReqBody(e.target.value)}
                            rows={2}
                            placeholder='{"key": "value"}'
                            className={cx(inputCls, "font-mono text-[11.5px]")}
                          />
                        )}
                        {reqOutput && (
                          <pre className="max-h-48 overflow-auto rounded-xl bg-bg p-2.5 font-mono text-[11px] border border-border/60 whitespace-pre-wrap">
                            {reqOutput}
                          </pre>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Quick Connect / Add / Edit Form */}
        {formOpen && (
          <div ref={formRef} className="rounded-2xl border-2 border-accent/35 bg-surface p-3.5 space-y-2.5">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-[13.5px] font-semibold">
                <Globe size={15} className="text-accent" />
                {editingId ? t("Edit API: {name}", { name }) : editingPreset ? t("Connect {name}", { name: editingPreset.name }) : t("Add Custom REST / GraphQL API")}
              </div>
              <div className="flex items-center gap-2">
                {editingPreset?.key_url && (
                  <a
                    href={editingPreset.key_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 rounded-full bg-accent/12 px-2.5 py-1 text-[11.5px] font-medium text-accent hover:bg-accent/20"
                  >
                    <ExternalLink size={12} /> {t("Get API Key")}
                  </a>
                )}
                <button type="button" onClick={() => setFormOpen(false)} className="text-[12px] text-muted hover:text-fg">
                  {t("Cancel")}
                </button>
              </div>
            </div>
            {editingPreset?.key_hint && (
              <div className="rounded-xl bg-surface-2/60 px-3 py-2 text-[12px] text-muted">
                {editingPreset.key_hint}
              </div>
            )}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <Field label={t("Account / workspace label")}>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Work GitHub or Personal" className={inputCls} />
              </Field>
              {authType !== "none" && (
                <Field label={t("API Key / Token (stored encrypted in vault)")}>
                  <input
                    type="password"
                    value={secret}
                    onChange={(e) => setSecret(e.target.value)}
                    placeholder={
                      editingPreset?.uses_google_oauth && data.google?.signed_in
                        ? t("Optional — already linked to Google OAuth")
                        : editingId
                          ? t("Leave blank to keep existing key")
                          : t("Paste key or token here...")
                    }
                    className={inputCls}
                    autoComplete="off"
                  />
                </Field>
              )}
            </div>

            <button
              type="button"
              onClick={() => setAdvancedOpen((v) => !v)}
              className="text-[11.5px] font-medium text-accent hover:underline"
            >
              {advancedOpen ? t("Hide advanced URL & auth settings") : t("Show advanced URL, headers & OpenAPI settings")}
            </button>

            {advancedOpen && (
              <div className="space-y-2 border-t border-border/50 pt-2">
                <Field label={t("Base URL")}>
                  <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.example.com" className={inputCls} />
                </Field>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                  <Field label={t("Auth mode")}>
                    <select value={authType} onChange={(e) => setAuthType(e.target.value)} className={inputCls}>
                      <option value="bearer">Bearer Token</option>
                      <option value="api_key_header">Header API Key</option>
                      <option value="api_key_query">Query Parameter</option>
                      <option value="basic">Basic Auth</option>
                      <option value="none">No Auth / Public</option>
                    </select>
                  </Field>
                  <Field label={t("Header / param name")}>
                    <input value={authHeader} onChange={(e) => setAuthHeader(e.target.value)} placeholder="Authorization" className={inputCls} />
                  </Field>
                  <Field label={t("Prefix (optional)")}>
                    <input value={authPrefix} onChange={(e) => setAuthPrefix(e.target.value)} placeholder="Bearer " className={inputCls} />
                  </Field>
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <Field label={t("OpenAPI / Swagger URL (optional auto-discover)")}>
                    <input value={openapiUrl} onChange={(e) => setOpenapiUrl(e.target.value)} placeholder="https://.../openapi.json" className={inputCls} />
                  </Field>
                  <Field label={t("Notes / data schema hints for AI")}>
                    <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={t("How to query or mutate resources")} className={inputCls} />
                  </Field>
                </div>
              </div>
            )}

            <div className="flex flex-wrap gap-2 pt-1">
              <button
                type="button"
                disabled={saving || !name.trim() || !baseUrl.trim()}
                onClick={() => void saveApi()}
                className={primaryBtn}
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                {editingId ? t("Save & Test") : t("Save & Test API")}
              </button>
              <button type="button" onClick={() => setFormOpen(false)} className={secondaryBtn}>{t("Cancel")}</button>
            </div>
          </div>
        )}

        {/* Standard APIs Directory (36+ built-in presets) */}
        <div className="space-y-2 pt-1">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[180px]">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("Search {n}+ built-in APIs (GitHub, Stripe, Notion, Slack, Spotify, Linear...)", { n: Math.max(presets.length, 36) })}
                className={cx(inputCls, "pl-8 py-1.5 text-[12.5px]")}
              />
            </div>
          </div>
          <div className="flex flex-wrap gap-1">
            {categories.map((cat) => (
              <button
                key={cat}
                type="button"
                onClick={() => setCategory(cat)}
                className={cx(
                  "rounded-full border px-2.5 py-0.5 text-[11px] capitalize",
                  category === cat ? "border-accent bg-accent/10 font-medium text-accent" : "border-border/70 text-muted hover:text-fg",
                )}
              >
                {cat}
              </button>
            ))}
          </div>

          {loadingPresets ? (
            <div className="flex items-center gap-2 py-4 text-[12.5px] text-muted">
              <Loader2 size={14} className="animate-spin" /> {t("Loading API directory...")}
            </div>
          ) : (
            <div className="grid max-h-[260px] grid-cols-1 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-2">
              {filteredPresets.map((p) => {
                const count = apis.filter((a) => a.preset_id === p.id).length;
                return (
                  <button
                    key={p.id}
                    type="button"
                    disabled={quickBusy === p.id}
                    onClick={() => void startFromPreset(p)}
                    className="flex items-start justify-between gap-2 rounded-2xl border border-border/60 bg-surface-2/35 p-2.5 text-left transition hover:border-accent/40 hover:bg-surface-2/70"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 text-[12.5px] font-medium">
                        <span className="truncate">{p.name}</span>
                        {p.auth_type === "none" ? (
                          <span className="shrink-0 rounded-full bg-emerald-500/15 px-1.5 py-0.2 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300">
                            {t("1-Click")}
                          </span>
                        ) : p.popular ? (
                          <span className="shrink-0 rounded-full bg-accent/15 px-1.5 py-0.2 text-[10px] font-semibold text-accent">
                            {t("Popular")}
                          </span>
                        ) : null}
                        {count > 0 && (
                          <span className="rounded-full bg-accent/15 px-1.5 py-0.2 text-[10px] font-semibold text-accent">
                            {count}
                          </span>
                        )}
                      </div>
                      <div className="truncate text-[11px] text-muted">{p.description}</div>
                    </div>
                    {quickBusy === p.id ? (
                      <Loader2 size={14} className="mt-0.5 shrink-0 animate-spin text-accent" />
                    ) : (
                      <Plus size={14} className="mt-0.5 shrink-0 text-accent" />
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
