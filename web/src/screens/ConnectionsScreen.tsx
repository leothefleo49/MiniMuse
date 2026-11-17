import {
  Bot,
  BrainCircuit,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronUp,
  Clapperboard,
  Contact as ContactIcon,
  ExternalLink,
  Eye,
  EyeOff,
  Globe,
  Image as ImageIcon,
  KeyRound,
  Loader2,
  LogIn,
  Mail,
  Plug,
  Plus,
  RefreshCw,
  Search,
  Smartphone,
  Sparkles,
  Trash2,
  Unplug,
  Upload,
  Wand2,
  X,
  Zap,
} from "lucide-react";
import {
  Children,
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { accessibilityState, androidApp, deviceGoogleEmails, requestPhoneCalendarAndContactsSync } from "../android";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { api } from "../api";
import { ApiIntegrationsCard } from "../components/ApiIntegrationsCard";
import { OWN_KEY_DOCS, takePresetHint } from "../components/AllowanceWays";
import { PageBar } from "../components/BackBar";
import { ChatGptSignIn } from "../components/ChatGptSignIn";
import { CloudCard } from "../components/CloudCard";
import { Card, inputCls, primaryBtn, secondaryBtn } from "../components/Form";
import { LocalAiAdvisor } from "../components/LocalAiAdvisor";
import { ModelPicker } from "../components/ModelPicker";
import { ModelSearchField, splitPopularAndAlphabetical } from "../components/ModelSearchField";
import { getLocale, useT } from "../i18n";
import type { ModelGroup } from "../model-list";
import { DRAFT_THREAD, useStore } from "../store";
import {
  CLOUD_ID,
  CLOUD_KEY_REF,
  CUSTOM_ID,
  currentChoice,
  endpointChoice,
  endpointGroups,
  endpointLabel,
  endpointOf,
  handsValue,
  mediaChoices,
  slotValue,
  type MediaChoice,
} from "../models";
import { CATALOGUE, catalogueIdFor, coversLine, editionFor, invalidateProviders, presetFor, providerName, regionOf, unavailableLine, type Capability } from "../providers";
import { modelSees, orderPresets } from "../region";
import type {
  Contact,
  ConnectionsData,
  CustomEndpoint,
  EmailAccount,
  LlmRoutingView,
  LlmUsageStatusView,
  ProviderKeySlotView,
  ProviderPreset,
  ProviderUsageStatus,
  SavedModelOption,
  SharedConnector,
  TestResult,
} from "../types";
import { cx, relativeTime } from "../util";

/** the runtime's preset for the account's model, present in `providers` while signed in */
const CLOUD_PRESET = "nanomuse_cloud";
/** the provider tile that adds a custom endpoint to the runtime's list (#294); not a preset of the runtime's */
const ADD_ENDPOINT = "add-endpoint";

/**
 * Connections: the model, your mailbox, your calendar, a browser, MCP servers — plugged in and out from the
 * phone. Anything secret is typed here and lands in the vault on the server; the model only
 * ever gets the tools that result, never the key or the password.
 */
export function ConnectionsScreen() {
  const { state } = useStore();
  const [data, setData] = useState<ConnectionsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const name = state.profile?.name ?? "MiniMuse";
  const t = useT();

  const load = useCallback(async () => {
    try {
      setData(await api.connections());
      setError(null);
    } catch (e) {
      setError(t((e as Error).message));
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load, state.connectionsVersion]);

  return (
    <div className="flex h-full flex-col">
      <PageBar title={t("Connections")} description={t("What {name} can reach; keys stay in the vault on this machine.", { name })} />
      <div className="flex-1 overflow-y-auto px-4 pb-8 space-y-4">
        {error && (
          <div className="rounded-2xl bg-rose-500/12 text-rose-700 dark:text-rose-300 p-3 text-[13.5px]">
            {error}
          </div>
        )}
        {!data && !error && (
          <div className="flex justify-center py-10 text-muted">
            <Loader2 className="animate-spin" size={20} />
          </div>
        )}
        {data && (
          <>
            <SensitivityCard />
            <GroupLabel>{t("Model & media")}</GroupLabel>
            <ModelCard data={data} onChange={load} />
            {data.image && <MediaSlotCard slot="image" data={data} onChange={load} />}
            {data.video && <MediaSlotCard slot="video" data={data} onChange={load} />}
            {(state.hub?.account.signed_in ||
              state.settings?.cloud_mode === "cloud" ||
              state.settings?.cloud_prompts) && (
              <CloudCard account={state.hub?.account ?? null} onChange={load} />
            )}
            <GroupLabel>{t("Memory & search")}</GroupLabel>
            {data.embeddings.memory_enabled && (
              <RecallCard data={data} onChange={load} />
            )}
            <SearchCard data={data} onChange={load} />
            <GroupLabel>{t("Your accounts & data")}</GroupLabel>
            <EmailCard data={data} onChange={load} />
            <CalendarCard data={data} onChange={load} />
            <ContactsCard data={data} onChange={load} />
            <GroupLabel>{t("Devices & control")}</GroupLabel>
            <BrowserCard data={data} onChange={load} />
            <PhoneCard data={data} onChange={load} />
            <GroupLabel>{t("Integrations & extensions")}</GroupLabel>
            <ApiIntegrationsCard data={data} onChange={load} />
            <MCPCard data={data} onChange={load} />
            <VaultCard data={data} onChange={load} />
          </>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ grouping
function GroupLabel({ children }: { children: ReactNode }) {
  return (
    <div className="pt-3 text-[11px] font-semibold uppercase tracking-wider text-muted px-1">{children}</div>
  );
}

// ------------------------------------------------------------------ sensitivity
// What each connected thing may do, in plain words: reads_only never touches the
// outside world, moderate acts on your data and always asks first, full can act on
// your devices and accounts (still approval-gated per action). Lives at the top so
// it is read before anyone flips a switch.
const SENSITIVITY_ROWS: Array<{ id: string; label: string; level: string; tone: string }> = [
  { id: "email", label: "Email (read + drafts)", level: "Moderate", tone: "text-amber-600 dark:text-amber-400" },
  { id: "calendar", label: "Calendar", level: "Moderate", tone: "text-amber-600 dark:text-amber-400" },
  { id: "contacts", label: "Contacts", level: "Moderate", tone: "text-amber-600 dark:text-amber-400" },
  { id: "browser", label: "Browser (browses and clicks)", level: "Full", tone: "text-rose-600 dark:text-rose-400" },
  { id: "phone", label: "Phone bridge (texts, calls)", level: "Full", tone: "text-rose-600 dark:text-rose-400" },
  { id: "mcp", label: "MCP integrations (Zapier et al.)", level: "Full", tone: "text-rose-600 dark:text-rose-400" },
  { id: "vault", label: "Vault (your saved keys)", level: "Full", tone: "text-rose-600 dark:text-rose-400" },
];

function SensitivityCard() {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <Card
      icon={<KeyRound size={19} />}
      title={t("What each connection can do")}
      summary={t("Read this before turning switches on.")}
      status={{ text: t("Info"), tone: "" }}
      open={open}
      onToggle={() => setOpen((s) => !s)}
    >
      <div className="space-y-1.5">
        {SENSITIVITY_ROWS.map((row) => (
          <div key={row.id} className="flex items-center justify-between gap-3 text-[13px]">
            <span className="text-foreground/90">{t(row.label)}</span>
            <span className={`shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-semibold ${row.tone}`}>
              {t(row.level)}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ model
export function ModelCard({
  data,
  onChange,
  compact,
  onSaved,
}: {
  data: ConnectionsData;
  onChange: () => void;
  compact?: boolean;
  onSaved?: () => void;
}) {
  const { toast, state } = useStore();
  const t = useT();
  const [open, setOpen] = useState(!!compact);
  const [showAdvisor, setShowAdvisor] = useState(!!compact);
  const [switchSearch, setSwitchSearch] = useState("");
  const presets = data.providers ?? {};
  // the custom endpoints of the runtime's list (#294); a runtime without one offers the single
  // "custom" slot as before
  const endpoints = data.endpoints ?? [];
  const hasList = data.endpoints !== undefined;
  const currentEndpoint = endpoints.find((e) => e.id === data.llm.endpoint) ?? null;
  // a member of the relay may name any model of the provider's for its kind, not only the menu's
  const anyModel = !!state.hub?.account?.any_model;
  const openrouterPreset = presets.openrouter;
  const isUnconfigured =
    !data.llm.from_app &&
    data.llm.key_source === "none" &&
    !data.llm.cloud &&
    !currentEndpoint &&
    !!openrouterPreset;
  const currentPreset =
    (currentEndpoint ? endpointChoice(currentEndpoint.id) : null) ??
    (isUnconfigured
      ? "openrouter"
      : Object.entries(presets).find(
          ([id, p]) =>
            id !== "custom" &&
            p.base_url &&
            data.llm.base_url.startsWith(p.base_url) &&
            p.provider === data.llm.provider,
        )?.[0] ??
        Object.entries(presets).find(
          ([id, p]) =>
            id !== "custom" &&
            p.base_url &&
            data.llm.base_url.startsWith(p.base_url),
        )?.[0] ??
        (data.llm.base_url ? "custom" : "openrouter"));
  // a tile's preset: the runtime's, the one that adds an endpoint, or one standing for an
  // endpoint of the list
  const presetOf = (id: string): ProviderPreset | undefined => {
    if (id === ADD_ENDPOINT) return { label: t("Add an endpoint"), subtitle: presets.custom?.subtitle, group: "local", provider: "openai", base_url: "", key_optional: true };
    const own = endpointOf(id);
    if (!own) return presets[id];
    const e = endpoints.find((x) => x.id === own);
    return e ? { label: endpointLabel(e), group: "local", provider: "openai", base_url: e.base_url, models: e.models, no_key: true } : undefined;
  };
  const [preset, setPreset] = useState(currentPreset);
  // the endpoint tile just added: chosen as soon as the list arrives with it
  const wantEndpoint = useRef("");
  const initialModel = isUnconfigured
    ? (openrouterPreset?.models?.[0] ?? data.llm.model)
    : data.llm.model;
  const initialBaseUrl = isUnconfigured
    ? (openrouterPreset?.base_url ?? data.llm.base_url)
    : data.llm.base_url;
  const [model, setModel] = useState(initialModel);
  const [baseUrl, setBaseUrl] = useState(initialBaseUrl);
  const [selectedOptions, setSelectedOptions] = useState<SavedModelOption[]>(() => {
    if (data.llm.model_options?.length) return data.llm.model_options;
    if (isUnconfigured && openrouterPreset?.models?.length) {
      return openrouterPreset.models.slice(0, 3).map((m) => ({
        model: m,
        label: m,
        provider: openrouterPreset.provider ?? "openai",
        base_url: initialBaseUrl,
        endpoint: "",
      }));
    }
    return initialModel
      ? [{ model: initialModel, label: initialModel, provider: isUnconfigured ? "openai" : data.llm.provider, base_url: initialBaseUrl, endpoint: currentEndpoint?.id ?? "" }]
      : [];
  });
  const [key, setKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  // the ChatGPT sign-in, folded under the provider tiles
  const [chatgptOpen, setChatgptOpen] = useState(false);
  const [noKey, setNoKey] = useState(
    data.llm.key_source === "none" && !!presets[currentPreset]?.key_optional,
  );
  const [toolMode, setToolMode] = useState(data.llm.tool_mode || "auto");
  // `[llm] proxy` for this provider's requests; the runtime masks credentials in what it
  // shows, so the field is sent only once edited (typed = set, emptied = cleared)
  const [proxy, setProxy] = useState(data.llm.proxy ?? "");
  const [proxyTouched, setProxyTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);
  // the hands model (contract C4): "" = the default — the relay's hands model with the
  // account, the chat model otherwise; set here, it goes to [gui] model
  const [guiModel, setGuiModel] = useState(data.gui?.model ?? "");
  // the endpoint of the list the hands model answers through, when not the chat model's (#294)
  const [guiEndpoint, setGuiEndpoint] = useState(data.gui?.endpoint ?? "");
  // the endpoint's own list of models, fetched when the provider, URL or key changes
  const [models, setModels] = useState<{
    list: string[];
    popular: string[];
    image: string[];
    video: string[];
    // MiniMuse Cloud, for a member: the menu and the other models under the Cloud key, grouped
    menu: string[];
    catalog: string[];
    imageCatalog: string[];
    videoCatalog: string[];
    vision: string[];
    /** the relay's hands models, recommended first (contract C4) */
    gui: string[];
    source: "live" | "catalogue" | "loading";
  }>({
    list: presetOf(currentPreset)?.models ?? [],
    popular: presetOf(currentPreset)?.models ?? [],
    image: [],
    video: [],
    menu: [],
    catalog: [],
    imageCatalog: [],
    videoCatalog: [],
    vision: [],
    gui: presets[currentPreset]?.gui_models ?? [],
    source: "catalogue",
  });
  // true once the user edits the model field by hand in this session: that value is never replaced
  const [typed, setTyped] = useState(false);
  // a member on MiniMuse Cloud picks from the grouped list; this flips the field to typing an id
  const [typingModel, setTypingModel] = useState(false);
  const [applyingDefaults, setApplyingDefaults] = useState(false);
  const [showDefaultTasks, setShowDefaultTasks] = useState(false);
  const [multiProviderKeys, setMultiProviderKeys] = useState<Record<string, string>>({});

  const prevLlmSig = useRef<string>(
    `${data.llm.provider}|${data.llm.base_url}|${data.llm.model}|${data.llm.endpoint ?? ""}|${data.llm.key_source}|${data.llm.from_app ? 1 : 0}|${JSON.stringify(data.llm.model_options ?? [])}`,
  );
  useEffect(() => {
    const sig = `${data.llm.provider}|${data.llm.base_url}|${data.llm.model}|${data.llm.endpoint ?? ""}|${data.llm.key_source}|${data.llm.from_app ? 1 : 0}|${JSON.stringify(data.llm.model_options ?? [])}`;
    const changed = sig !== prevLlmSig.current;
    prevLlmSig.current = sig;

    const added = endpoints.find((e) => e.id === wantEndpoint.current);
    if (added) {
      // the endpoint just added stands selected, its first model in the field
      wantEndpoint.current = "";
      setPreset(endpointChoice(added.id));
      setBaseUrl(added.base_url);
      if (!(added.models ?? []).includes(data.llm?.model ?? "")) setModel(added.models?.[0] ?? "");
      setTyped(false);
      return;
    }

    if (!changed) return;
    if (isUnconfigured) {
      setModel(openrouterPreset?.models?.[0] ?? data.llm.model);
      setBaseUrl(openrouterPreset?.base_url ?? data.llm.base_url);
      setPreset("openrouter");
    } else {
      setModel(data.llm.model);
      setBaseUrl(data.llm.base_url);
      setPreset(currentPreset);
    }
    setToolMode(data.llm.tool_mode || "auto");
    setProxy(data.llm.proxy ?? "");
    setProxyTouched(false);
    setGuiModel(data.gui?.model ?? "");
    setGuiEndpoint(data.gui?.endpoint ?? "");
    if (data.llm.model_options?.length) {
      setSelectedOptions(data.llm.model_options);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.llm, data.gui?.model, data.gui?.endpoint, data.endpoints]);

  const p = presetOf(preset);
  // the endpoint of the list the chosen tile stands for, if any
  const ep = endpoints.find((e) => endpointChoice(e.id) === preset) ?? null;
  // the tile that adds an endpoint to the list: its form instead of the model fields
  const adding = preset === ADD_ENDPOINT;
  const effectiveUrl =
    preset === "custom" || !p?.base_url ? baseUrl : p.base_url;

  // ask the endpoint what it serves; the typed model is never replaced by the answer
  useEffect(() => {
    if (!open || adding) return;
    let alive = true;
    const handle = setTimeout(
      () => {
        setModels((m) => ({ ...m, source: "loading" }));
        api
          .llmModels(
            ep
              ? { endpoint: ep.id }
              : {
                  preset,
                  base_url: effectiveUrl,
                  api_key: key || undefined,
                },
          )
          .then((r) => {
            if (!alive) return;
            const list = r.models ?? [];
            setModels({
              list,
              popular: r.popular ?? [],
              image: r.image_models ?? [],
              video: r.video_models ?? [],
              menu: r.menu ?? [],
              catalog: r.catalog ?? [],
              imageCatalog: r.image_catalog ?? [],
              videoCatalog: r.video_catalog ?? [],
              vision: r.vision ?? [],
              gui: r.gui ?? presets[preset]?.gui_models ?? [],
              source: r.source,
            });
            // a model left over from another provider yields to what this endpoint actually serves
            if (r.source === "live" && list.length && !typed)
              setModel((m) => (list.includes(m) ? m : list[0]));
          })
          .catch(
            () =>
              alive &&
              setModels({
                list: p?.models ?? [],
                popular: p?.models ?? [],
                image: [],
                video: [],
                menu: [],
                catalog: [],
                imageCatalog: [],
                videoCatalog: [],
                vision: [],
                gui: p?.gui_models ?? [],
                source: "catalogue",
              }),
          );
      },
      key ? 600 : 150,
    );
    return () => {
      alive = false;
      clearTimeout(handle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, preset, effectiveUrl, key]);

  const optionKey = (o: { model: string; base_url?: string; endpoint?: string }) =>
    `${o.endpoint || ""}::${(o.base_url || "").replace(/\/+$/, "")}::${o.model.trim()}`;

  const currentProviderMeta = () => ({
    provider: p?.provider ?? "openai",
    base_url: effectiveUrl.trim(),
    endpoint: ep?.id ?? "",
  });

  const hasOption = (modelId: string) => {
    const meta = currentProviderMeta();
    const target = optionKey({ model: modelId, ...meta });
    return selectedOptions.some((o) => optionKey(o) === target || (!o.base_url && !o.endpoint && o.model.trim() === modelId.trim()));
  };

  const toggleOption = (modelId: string) => {
    const id = modelId.trim();
    if (!id) return;
    const meta = currentProviderMeta();
    const target = optionKey({ model: id, ...meta });
    setSelectedOptions((prev) => {
      const exists = prev.some((o) => optionKey(o) === target || (!o.base_url && !o.endpoint && o.model.trim() === id));
      if (exists) {
        return prev.filter((o) => optionKey(o) !== target && !(!o.base_url && !o.endpoint && o.model.trim() === id));
      }
      return [...prev, { model: id, label: id, ...meta }];
    });
  };

  const removeOption = (opt: SavedModelOption) => {
    const target = optionKey(opt);
    setSelectedOptions((prev) => prev.filter((o) => optionKey(o) !== target));
  };

  const mergedOptions = (): SavedModelOption[] => {
    const out: SavedModelOption[] = [];
    const seen = new Set<string>();
    const addOne = (opt: SavedModelOption) => {
      const m = opt.model.trim();
      if (!m) return;
      const k = optionKey(opt);
      if (seen.has(k)) return;
      seen.add(k);
      out.push({
        model: m,
        label: opt.label?.trim() || m,
        provider: opt.provider || "openai",
        base_url: opt.base_url || "",
        endpoint: opt.endpoint || "",
      });
    };
    const primary = model.trim();
    if (primary && !p?.cloud) {
      addOne({ model: primary, label: primary, ...currentProviderMeta() });
    }
    for (const o of selectedOptions) addOne(o);
    return out;
  };

  const pick = (id: string) => {
    setPreset(id);
    const next = presetOf(id);
    if (!next) return;
    if (next.base_url) setBaseUrl(next.base_url);
    else if (p?.base_url) setBaseUrl("");
    // a model typed by hand stays; an empty field or one from another preset's catalogue moves on
    const cameFromCatalogue =
      !model || Object.values(presets).some((q) => q.models?.includes(model)) || endpoints.some((e) => e.models.includes(model));
    if (
      cameFromCatalogue &&
      next.models?.length &&
      !next.models.includes(model)
    )
      setModel(next.models[0]);
    setModels({
      list: next.models ?? [],
      popular: next.models ?? [],
      image: [],
      video: [],
      menu: [],
      catalog: [],
      imageCatalog: [],
      videoCatalog: [],
      vision: [],
      gui: next.gui_models ?? [],
      source: "catalogue",
    });
    // the preset's own hands model comes along, unless the person set one by hand
    if (!data.gui?.model || Object.values(presets).some((q) => q.gui_model === data.gui?.model)) setGuiModel(next.cloud ? "" : (next.gui_model ?? ""));
    setTypingModel(false);
    setTyped(false);
    setNoKey(false);
  };

  // "Set it up" on the allowance card lands here with a provider already chosen (Alibaba
  // Cloud Bailian by default): the card opens on that preset, the key field waits for a paste.
  useEffect(() => {
    const hint = takePresetHint();
    if (hint && presets[hint]) {
      setOpen(true);
      pick(hint);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const keyless = !!p?.no_key || (noKey && !!p?.key_optional);
  const status = data.llm.cloud
    ? { text: t("Your account's model"), tone: "ok" }
    : data.llm.key_source === "none" && currentEndpoint
      ? { text: t("No key"), tone: "ok" }
    : data.llm.key_source === "none" &&
        !presets[currentPreset]?.no_key &&
        !presets[currentPreset]?.key_optional
      ? { text: t("No key yet"), tone: "warn" }
      : data.llm.key_source === "missing"
        ? { text: t("Key missing from vault"), tone: "warn" }
        : {
            text:
              data.llm.key_source === "vault"
                ? t("Key in vault")
                : data.llm.key_source === "config"
                  ? t("Key from config")
                  : t("No key needed"),
            tone: "ok",
          };

  const save = async () => {
    setSaving(true);
    setTest(null);
    try {
      const optionsPayload = mergedOptions();
      if (p?.cloud) {
        // the account's key stays the key; only the model is chosen here
        await api.cloudUseAsModel(model.trim());
        if (optionsPayload.length) await api.setModelOptions(optionsPayload);
      } else if (ep) {
        // an endpoint of the list: its address and key by id, never retyped here
        await api.setLLM({
          endpoint: ep.id,
          model: model.trim(),
          tool_mode: toolMode,
          proxy: proxyTouched && data.llm.proxy !== undefined ? proxy.trim() : null,
          model_options: optionsPayload,
        });
      } else {
        await api.setLLM({
          provider: p?.provider ?? "openai",
          model: model.trim(),
          base_url: effectiveUrl.trim(),
          tool_mode: toolMode,
          api_key: key ? key : keyless ? "" : null,
          // untouched: null leaves the runtime's value; "" clears it
          proxy: proxyTouched && data.llm.proxy !== undefined ? proxy.trim() : null,
          model_options: optionsPayload,
        });
      }
      // the hands model rides on the chat model's endpoint and key (contract C4), unless it
      // was picked from another endpoint of the list, which then lends its address and key
      const guiWas = { model: data.gui?.model ?? "", endpoint: data.gui?.endpoint ?? "" };
      if (guiModel.trim() !== guiWas.model || guiEndpoint !== guiWas.endpoint) {
        if (guiEndpoint) await api.setGui({ endpoint: guiEndpoint, model: guiModel.trim() });
        else if (guiWas.endpoint) await api.setGui({ model: guiModel.trim(), provider: "", base_url: "", api_key: "" });
        else await api.setGui({ model: guiModel.trim() });
      }
      const nonEmptyMultiKeys = Object.fromEntries(
        Object.entries(multiProviderKeys).filter(([, v]) => v && v.trim()),
      );
      if (Object.keys(nonEmptyMultiKeys).length > 0) {
        await api.setLlmRouting({ provider_keys: nonEmptyMultiKeys });
        setMultiProviderKeys({});
      }
      setKey("");
      invalidateProviders();
      toast(t("Model saved"));
      onChange();
      if (!compact) setOpen(false);
      onSaved?.();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const applyRecommendedSet = async () => {
    setApplyingDefaults(true);
    try {
      const nonEmptyMultiKeys = Object.fromEntries(
        Object.entries(multiProviderKeys).filter(([, v]) => v && v.trim()),
      );
      const res = await api.applyRecommendedDefaults({
        preset: preset && preset !== ADD_ENDPOINT ? preset : "openrouter",
        api_key: key ? key : undefined,
        provider_keys: Object.keys(nonEmptyMultiKeys).length ? nonEmptyMultiKeys : undefined,
      });
      if (res.llm?.model) {
        setModel(res.llm.model);
        setTyped(true);
      }
      if (res.gui?.model) {
        setGuiModel(res.gui.model);
      }
      if (res.llm?.model_options?.length) {
        setSelectedOptions(res.llm.model_options);
      }
      setKey("");
      setMultiProviderKeys({});
      invalidateProviders();
      toast(t("Applied recommended best model set for all tasks."));
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setApplyingDefaults(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    try {
      setTest(await api.testLLM());
    } catch (e) {
      setTest({ ok: false, error: (e as Error).message });
    } finally {
      setTesting(false);
    }
  };

  const groups: Array<{ id: string; title: string; ids: string[] }> = [
    { id: "cloud", title: t("Your account"), ids: [] },
    { id: "openai", title: t("OpenAI-compatible · Chat Completions"), ids: [] },
    { id: "responses", title: t("Responses API"), ids: [] },
    { id: "local", title: t("Local or your own endpoint"), ids: [] },
  ];
  for (const [id, q] of Object.entries(presets))
    (groups.find((g) => g.id === (q.group ?? "openai")) ?? groups[0]).ids.push(
      id,
    );
  // the region's own-key provider first (contract C5): Bailian on the mainland, OpenRouter elsewhere
  for (const g of groups) g.ids = orderPresets(g.ids, presets, state.hub?.account);
  // the endpoints of the list stand first, the tile that adds one last (#294); the single
  // "custom" slot stays only while the chat model is on an unlisted host outside the list
  // (set in config.toml, say), or on a runtime without the list
  const local = groups.find((g) => g.id === "local");
  if (local) {
    const rest = local.ids.filter((id) => id !== "custom" || !hasList || currentPreset === "custom");
    local.ids = [...endpoints.map((e) => endpointChoice(e.id)), ...rest, ...(hasList ? [ADD_ENDPOINT] : [])];
  }
  const needsUrl = !ep && !adding && (preset === "custom" || !p?.base_url);
  // what the chosen provider covers, from the catalogue (contract C11); the account's model
  // covers all four through the relay, "custom" is whatever the endpoint happens to serve
  const catalogueEntry = p?.cloud || ep || adding ? null : CATALOGUE.find((c) => c.id === catalogueIdFor(preset)) ?? null;
  const region = regionOf(state.hub?.account);
  const locale = getLocale();
  const lacks = (cap: Capability) => !!catalogueEntry && !catalogueEntry.user_capabilities && !catalogueEntry.capabilities.includes(cap);
  // the hands with nothing set here (the Models contract §3): the account's hands model
  // on MiniMuse Cloud; an own provider's own hands model when it sees; the account's when
  // it cannot and the account is signed in; else the chat model
  const cloudPreset = presets[CLOUD_PRESET];
  const handsDefault = p?.cloud
    ? ((models.gui ?? [])[0] ?? p.gui_model ?? "")
    : catalogueEntry && !catalogueEntry.user_capabilities
      ? catalogueEntry.capabilities?.includes("vision")
        ? catalogueEntry.defaults?.hands || model.trim()
        : (cloudPreset?.gui_model ?? model.trim())
      : model.trim();
  const handsOnCloud = !p?.cloud && lacks("vision") && !!cloudPreset;
  // what the hands use right now, as the runtime resolved it (the saved state, not the form)
  const handsNow = data.gui ? handsValue(data.gui, data.llm, locale, CATALOGUE, endpoints) : "";
  const handsOptions = Array.from(
    new Set([
      ...(p?.cloud ? models.gui : []),
      ...(p?.gui_model ? [p.gui_model] : []),
      ...(models.list ?? []).filter((m) => (models.vision ?? []).includes(m) || modelSees(m) === true),
    ]),
  ).filter(Boolean);
  // the hands picker's one group: this provider's models that see, the catalogue's hands
  // default first (the relay's recommended one on MiniMuse Cloud), folded past eight
  const handsProviderLabel = p?.cloud ? "MiniMuse Cloud" : ep ? endpointLabel(ep) : catalogueEntry ? providerName(catalogueEntry, locale) : hostOf(effectiveUrl) || preset;
  const handsPreset = p?.cloud ? ((models.gui ?? [])[0] ?? p.gui_model ?? "") : (catalogueEntry?.defaults?.hands ?? p?.gui_model ?? "");
  // ... and one group per other endpoint of the list, which lends the hands its address and key (#294)
  const handsGroups: ModelGroup[] = [
    { key: "hands", label: handsProviderLabel, rows: handsOptions.map((id) => ({ id, name: id })), ...(handsPreset ? { default: handsPreset } : {}) },
    ...endpointGroups(endpoints, ep?.id ?? ""),
  ];
  // the hands model picked from an endpoint that is the chat model's own stands in its group
  const handsGroup = guiEndpoint && guiEndpoint !== ep?.id ? endpointChoice(guiEndpoint) : "hands";
  const handsEndpoint = endpoints.find((e) => e.id === guiEndpoint) ?? null;
  const handsText = guiModel ? `${handsGroup === "hands" ? handsProviderLabel : handsEndpoint ? endpointLabel(handsEndpoint) : ""} · ${guiModel}` : t("Automatic");
  const willAppendV1 = needsUrl && /^https?:\/\/[^/]+\/?$/.test(baseUrl.trim());

  return (
    <Card
      icon={<Bot size={19} />}
      title={t("Chat model")}
      summary={
        data.llm.cloud
          ? `MiniMuse Cloud · ${data.llm.model || t("No model yet")}`
          : currentEndpoint
            ? `${data.llm.model || t("No model yet")} · ${endpointLabel(currentEndpoint)}`
            : `${data.llm.model || t("No model yet")}${data.llm.base_url ? ` · ${hostOf(data.llm.base_url)}` : ""}`
      }
      status={status}
      open={open}
      onToggle={compact ? undefined : () => setOpen(!open)}
    >
      {compact ? (
        <LocalAiAdvisor
          compact
          selectedPreset={preset}
          selectedModel={model}
          onPickLocalPreset={(pId, mId) => {
            pick(pId);
            if (mId) {
              setModel(mId);
              setTyped(true);
            }
          }}
          onPickCloud={() => pick("openrouter")}
        />
      ) : (
        <div className="mb-2">
          <button
            type="button"
            onClick={() => setShowAdvisor((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2/60 px-3 py-1 text-[12px] font-medium text-fg hover:border-accent"
          >
            <Sparkles size={13} className="text-accent" />
            {showAdvisor ? t("Hide local AI device advisor") : t("Check what local AI this device can handle")}
          </button>
          {showAdvisor && (
            <div className="mt-2">
              <LocalAiAdvisor
                selectedPreset={preset}
                selectedModel={model}
                onPickLocalPreset={(pId, mId) => {
                  pick(pId);
                  if (mId) {
                    setModel(mId);
                    setTyped(true);
                  }
                }}
                onPickCloud={() => pick("openrouter")}
              />
            </div>
          )}
        </div>
      )}
      {!adding && (
        <div className="mb-3 rounded-2xl border border-accent/35 bg-accent/[0.06] p-3 space-y-2 text-left">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 text-[13px] font-semibold text-fg">
                <Wand2 size={14} className="text-accent shrink-0" />
                <span>{t("One-Click Recommended Best Model Set")}</span>
              </div>
              <p className="mt-0.5 text-[11.5px] text-muted leading-snug">
                {t("Pre-assigns the best updated models for every task (normal chat, vision/hands, fast background tasks, heavy coding/reasoning, and local fallback).")}
              </p>
            </div>
            <button
              type="button"
              disabled={applyingDefaults}
              onClick={() => void applyRecommendedSet()}
              className="shrink-0 inline-flex items-center gap-1.5 rounded-full bg-accent px-3.5 py-1.5 text-[12px] font-semibold text-accent-fg shadow-sm hover:opacity-95 disabled:opacity-50"
            >
              {applyingDefaults ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
              {t("Use Default")}
            </button>
          </div>
          <div>
            <button
              type="button"
              onClick={() => setShowDefaultTasks((s) => !s)}
              className="inline-flex items-center gap-1 text-[11.5px] font-medium text-accent hover:underline"
            >
              {showDefaultTasks ? t("Hide task assignments") : t("See what Use Default sets for each task")}
              {showDefaultTasks ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            </button>
            {showDefaultTasks && (
              <div className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-2 text-[11.5px]">
                <div className="rounded-xl border border-border/70 bg-bg/90 px-2.5 py-1.5">
                  <div className="font-semibold text-fg">{t("Normal Chat & Tools")}</div>
                  <div className="font-mono text-[11px] text-accent">
                    {preset === "deepseek" ? "deepseek-v4.1-flash-1017" : (data.recommended_defaults?.tasks?.chat?.model ?? "deepseek/deepseek-v4.1-flash-1017")}
                  </div>
                  <div className="text-[10.5px] text-muted">{t("Fast, low-cost daily chat & agent tool calling")}</div>
                </div>
                <div className="rounded-xl border border-border/70 bg-bg/90 px-2.5 py-1.5">
                  <div className="font-semibold text-fg">{t("Vision & Screen Hands")}</div>
                  <div className="font-mono text-[11px] text-accent">
                    {data.recommended_defaults?.tasks?.hands?.model ?? "google/gemini-2.5-flash"}
                  </div>
                  <div className="text-[10.5px] text-muted">{t("Reads screenshots, photos, and controls UI")}</div>
                </div>
                <div className="rounded-xl border border-border/70 bg-bg/90 px-2.5 py-1.5">
                  <div className="font-semibold text-fg">{t("Light / Background Tasks")}</div>
                  <div className="font-mono text-[11px] text-accent">
                    {data.recommended_defaults?.tasks?.background?.model ?? "google/gemini-3.1-flash-lite"}
                  </div>
                  <div className="text-[10.5px] text-muted">{t("Summaries, memory tidying, quick notifications")}</div>
                </div>
                <div className="rounded-xl border border-border/70 bg-bg/90 px-2.5 py-1.5">
                  <div className="font-semibold text-fg">{t("Heavy Use & Deep Reasoning")}</div>
                  <div className="font-mono text-[11px] text-accent">
                    {data.recommended_defaults?.tasks?.heavy_reasoning?.model ?? "anthropic/claude-sonnet-4.5"} · {"deepseek/deepseek-v4.1-pro-1017"}
                  </div>
                  <div className="text-[10.5px] text-muted">{t("Added to quick-switch bar for complex coding & math")}</div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
      <Field label={t("Provider")}>
        <div className="space-y-2.5">
          {groups
            .filter((g) => g.ids.length)
            .map((g) => (
              <div key={g.id}>
                <div className="mb-1 text-[11px] uppercase tracking-wide text-muted">
                  {g.title}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {g.ids.map((id) => {
                    const q = presetOf(id);
                    if (!q) return null;
                    const own = endpoints.find((e) => endpointChoice(e.id) === id);
                    // an endpoint's tile reads its name over its host
                    const subtitle = own ? (hostOf(own.base_url) !== q.label ? hostOf(own.base_url) : undefined) : q.subtitle ? t(q.subtitle) : undefined;
                    return (
                      <button
                        key={id}
                        type="button"
                        onClick={() => pick(id)}
                        className={cx(
                          "rounded-2xl px-3 py-1.5 text-left border leading-tight",
                          preset === id
                            ? "border-accent bg-accent/10 text-accent"
                            : "border-border text-muted",
                        )}
                      >
                        <span
                          className={cx(
                            "block text-[13px]",
                            preset === id && "font-medium",
                            id === ADD_ENDPOINT && "inline-flex items-center gap-1",
                          )}
                        >
                          {id === ADD_ENDPOINT && <Plus size={13} />}
                          {q.label}
                          {id === "openrouter" && (
                            <span className="ml-1.5 rounded-full bg-accent/15 px-1.5 py-0.5 text-[10px] font-medium text-accent">
                              {t("Default")}
                            </span>
                          )}
                        </span>
                        {subtitle && (
                          <span className="block text-[10.5px] opacity-80">
                            {subtitle}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          {catalogueEntry && (
            <p className="text-[12px] text-muted">
              <span className="font-medium text-fg">{providerName(catalogueEntry, locale)}</span> · {coversLine(t, catalogueEntry.capabilities)}
              {". "}
              {locale === "zh-CN" ? catalogueEntry.note_zh : catalogueEntry.note}
            </p>
          )}
          <div>
            <button type="button" onClick={() => setChatgptOpen(!chatgptOpen)} className="inline-flex items-center gap-1.5 text-[12.5px] text-accent">
              <LogIn size={13} /> {t("Or sign in with a ChatGPT plan")} {chatgptOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </button>
            {chatgptOpen && (
              <div className="mt-2">
                <ChatGptSignIn
                  compact
                  onChanged={() => {
                    onChange();
                  }}
                />
              </div>
            )}
          </div>
        </div>
      </Field>
      {adding && (
        <AddEndpointForm
          onAdded={(e) => {
            wantEndpoint.current = e.id;
            invalidateProviders();
            onChange();
          }}
        />
      )}
      {ep && (
        <Field label={t("Endpoint")}>
          <EndpointCard
            endpoint={ep}
            onChange={() => {
              invalidateProviders();
              onChange();
            }}
            onModels={(list) => {
              setModels((m) => ({ ...m, list, source: "live" }));
              if (list.length && !typed && !list.includes(model)) setModel(list[0]);
            }}
          />
        </Field>
      )}
      {needsUrl && (
        <Field
          label={t("Base URL")}
          hint={
            willAppendV1
              ? t("/v1 is added when the URL has no path.")
              : t(
                  "The endpoint that serves /chat/completions, for example http://127.0.0.1:8000/v1.",
                )
          }
        >
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            className={inputCls}
            placeholder="https://host/v1"
            inputMode="url"
            autoCapitalize="off"
          />
        </Field>
      )}
      {!adding && (
      <Field
        label={t("Chat model")}
        hint={
          p?.cloud && anyModel && models.catalog.length
            ? t(
                "{n} models your account may use: the menu first, then everything else the provider lists under the Cloud key. Pick one; the ones beyond the menu are priced as the dearest menu model of their kind.",
                { n: models.list.length },
              )
            : p?.cloud && anyModel
            ? t(
                "Your account may name any model the provider has, not only these: type its id (a chat model here, a picture or clip model below) and it goes through as typed.",
              )
            : models.source === "live"
              ? t("{n} models from the endpoint", { n: models.list.length })
              : models.source === "loading"
              ? t("Asking the endpoint…")
              : models.list.length && ep
                ? t("{n} models saved with this endpoint", { n: models.list.length })
              : models.list.length
                ? t(
                    "Could not list the endpoint's models; these are the usual ones.",
                  )
                : undefined
        }
      >
        {p?.cloud && models.catalog.length > 0 && !typingModel && (!model || models.list.includes(model)) ? (
          // a member: the whole list to pick from, in groups — no id to type
          <select
            value={model}
            onChange={(e) => {
              if (e.target.value === OTHER_MODEL) {
                setTypingModel(true);
                return;
              }
              setModel(e.target.value);
              setTyped(true);
            }}
            className={cx(inputCls, "text-fg")}
          >
            {!model && <option value="">{t("model name")}</option>}
            <optgroup label={t("Menu")}>
              {models.menu.map((m) => (
                <option key={m} value={m}>
                  {models.vision.includes(m) ? `${m} · ${t("reads pictures")}` : m}
                </option>
              ))}
            </optgroup>
            <optgroup label={t("More models on your account")}>
              {models.catalog.map((m) => (
                <option key={m} value={m}>
                  {models.vision.includes(m) ? `${m} · ${t("reads pictures")}` : m}
                </option>
              ))}
            </optgroup>
            <option value={OTHER_MODEL}>{t("Type a model id…")}</option>
          </select>
        ) : (
          <>
            <ModelSearchField
              value={model}
              onChange={(nextId) => {
                setModel(nextId);
                setTyped(true);
              }}
              models={models.menu.length ? Array.from(new Set([...models.menu, ...models.list])) : models.list}
              popular={models.popular}
              recommended={models.popular[0] ?? p?.models?.[0]}
              placeholder={t("model name")}
              loading={models.source === "loading"}
              compact={compact}
              catalogInfo={data.model_catalog_info}
            />
            {p?.cloud && models.catalog.length > 0 && (
              <button
                type="button"
                className="mt-1.5 text-[12px] text-accent"
                onClick={() => {
                  setTypingModel(false);
                  if (!models.list.includes(model)) setModel(models.menu[0] ?? models.list[0] ?? "");
                }}
              >
                {t("Pick from the list")}
              </button>
            )}
          </>
        )}
        {!p?.cloud && (() => {
          const allSwitchCandidates = models.menu.length
            ? Array.from(new Set([...models.menu, ...models.list]))
            : models.list;
          const { popularList, otherAlphabetical } = splitPopularAndAlphabetical(
            allSwitchCandidates,
            models.popular,
            models.popular[0] ?? p?.models?.[0],
          );
          const q = switchSearch.trim().toLowerCase();
          const quickPopular = [...popularList, ...otherAlphabetical].slice(0, 10);
          const matchedPopular = q ? popularList.filter((m) => m.toLowerCase().includes(q)) : [];
          const matchedOther = q ? otherAlphabetical.filter((m) => m.toLowerCase().includes(q)) : [];
          const hasExactMatch = allSwitchCandidates.some((m) => m.toLowerCase() === q);
          return (
            <div className="mt-3 rounded-xl border border-border/70 bg-soft/40 p-2.5 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[12px] font-medium text-fg flex items-center gap-1.5">
                  <Sparkles size={13} className="text-accent" />
                  {t("Switchable model options")}
                </span>
                <span className="text-[11px] text-muted">
                  {t("{n} saved for chat header", { n: mergedOptions().length })}
                </span>
              </div>
              <p className="text-[11.5px] text-muted">
                {t("Select multiple models you want available for instant switching inside chat.")}
              </p>
              {quickPopular.length > 0 && !q && (
                <div className="flex flex-wrap gap-1.5">
                  {quickPopular.map((m) => {
                    const picked = hasOption(m) || m === model.trim();
                    return (
                      <button
                        key={`opt-${m}`}
                        type="button"
                        onClick={() => {
                          if (m === model.trim() && hasOption(m)) return;
                          toggleOption(m);
                        }}
                        className={cx(
                          "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11.5px] border transition-colors",
                          picked
                            ? "border-accent bg-accent/10 text-accent font-medium"
                            : "border-border text-muted hover:text-fg",
                        )}
                      >
                        {picked ? <Check size={11} /> : <Plus size={11} />}
                        {m}
                      </button>
                    );
                  })}
                </div>
              )}
              <div className="flex gap-1.5 pt-0.5">
                <div className="relative min-w-0 flex-1">
                  <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
                  <input
                    type="search"
                    inputMode="search"
                    value={switchSearch}
                    onChange={(e) => setSwitchSearch(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && switchSearch.trim()) {
                        e.preventDefault();
                        const exact = allSwitchCandidates.find(
                          (m) => m.toLowerCase() === switchSearch.trim().toLowerCase(),
                        );
                        const toAdd = exact ?? switchSearch.trim();
                        if (!hasOption(toAdd)) toggleOption(toAdd);
                        setSwitchSearch("");
                      }
                    }}
                    placeholder={t("Search models to add (or type ID)...")}
                    aria-label={t("Search switchable models")}
                    className={cx(inputCls, "py-1.5 pl-8 text-[12px]")}
                    autoCapitalize="off"
                    autoCorrect="off"
                  />
                </div>
                {switchSearch.trim() && !hasExactMatch && (
                  <button
                    type="button"
                    onClick={() => {
                      const v = switchSearch.trim();
                      if (!v) return;
                      if (!hasOption(v)) toggleOption(v);
                      setSwitchSearch("");
                    }}
                    className="shrink-0 rounded-xl border border-border px-2.5 py-1.5 text-[12px] text-fg hover:bg-soft"
                  >
                    {t("Add")}
                  </button>
                )}
              </div>
              {q && (
                <div className="max-h-[180px] space-y-1.5 overflow-y-auto overscroll-contain rounded-xl border border-border bg-bg p-2">
                  {matchedPopular.length === 0 && matchedOther.length === 0 ? (
                    <div className="text-[11.5px] text-muted">{t("No listed model matches. Tap Add to use custom ID.")}</div>
                  ) : (
                    <>
                      {matchedPopular.length > 0 && (
                        <div className="space-y-1">
                          <div className="px-1 text-[10.5px] font-semibold uppercase tracking-wide text-accent">
                            {t("Popular & recommended")}
                          </div>
                          {matchedPopular.slice(0, 20).map((m) => {
                            const picked = hasOption(m) || m === model.trim();
                            return (
                              <button
                                key={`sr-pop-${m}`}
                                type="button"
                                onClick={() => toggleOption(m)}
                                className={cx(
                                  "flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left font-mono text-[11.5px]",
                                  picked ? "bg-accent/15 text-accent font-medium" : "hover:bg-surface-2",
                                )}
                              >
                                <span className="truncate">{m}</span>
                                {picked ? <Check size={12} /> : <Plus size={12} />}
                              </button>
                            );
                          })}
                        </div>
                      )}
                      {matchedOther.length > 0 && (
                        <div className="space-y-1">
                          <div className="px-1 text-[10.5px] font-semibold uppercase tracking-wide text-muted">
                            {t("All models (A-Z)")}
                          </div>
                          {matchedOther.slice(0, 30).map((m) => {
                            const picked = hasOption(m) || m === model.trim();
                            return (
                              <button
                                key={`sr-all-${m}`}
                                type="button"
                                onClick={() => toggleOption(m)}
                                className={cx(
                                  "flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left font-mono text-[11.5px]",
                                  picked ? "bg-accent/15 text-accent font-medium" : "hover:bg-surface-2",
                                )}
                              >
                                <span className="truncate">{m}</span>
                                {picked ? <Check size={12} /> : <Plus size={12} />}
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
              {mergedOptions().length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-0.5">
                {mergedOptions().map((o) => {
                  const isPrimary = o.model === model.trim() && (o.base_url || "") === (effectiveUrl.trim() || "");
                  return (
                    <span
                      key={optionKey(o)}
                      className="inline-flex items-center gap-1 rounded-lg bg-panel px-2 py-0.5 text-[11px] border border-border/70 text-fg"
                    >
                      <button
                        type="button"
                        onClick={() => {
                          setModel(o.model);
                          if (o.base_url) setBaseUrl(o.base_url);
                        }}
                        className="hover:text-accent truncate max-w-[180px]"
                        title={o.base_url ? `${o.model} · ${o.base_url}` : o.model}
                      >
                        {o.label || o.model}
                      </button>
                      {isPrimary ? (
                        <span className="text-[10px] text-accent font-medium">{t("Primary")}</span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => removeOption(o)}
                          className="text-muted hover:text-fg ml-0.5"
                          aria-label={t("Remove model option")}
                        >
                          <X size={11} />
                        </button>
                      )}
                    </span>
                  );
                })}
              </div>
            )}
          </div>
          );
        })()}
      </Field>
      )}
      {!adding && (
      <Field
        label={t("Hands model")}
        hint={
          p?.cloud
            ? t("The model that looks at screens when the hands run: the phone, this computer, a page the browser cannot read. Your account's hands model is {model}; the chat model above is a different pick.", { model: handsDefault || t("none") })
            : handsOnCloud
              ? t("The model that looks at screens when the hands run. This provider has no model that sees, so the hands use your account's, {model}, unless you pick one here.", { model: handsDefault || t("none") })
              : endpoints.length > (ep ? 1 : 0)
                ? t("The model that looks at screens when the hands run: a small, fast one that takes pictures. Pick one of this provider's, or one from another endpoint of your list, which then lends its address and key.")
                : t("The model that looks at screens when the hands run: a small, fast one that takes pictures, on the same endpoint and key. Default: this provider's own hands model.")
        }
      >
        <ModelPicker
          label={t("Hands model")}
          text={handsText}
          heads={[{ value: "", label: t("Automatic") }]}
          headValue={guiModel ? undefined : ""}
          groups={handsGroups}
          current={guiModel ? { group: handsGroup, id: guiModel } : undefined}
          onHead={() => {
            setGuiModel("");
            setGuiEndpoint("");
          }}
          onPick={(group, row) => {
            setGuiModel(row.id);
            setGuiEndpoint(endpointOf(group) ?? "");
          }}
        />
        {handsNow && <p className="mt-1.5 text-[12px] text-muted">{t("Currently {value}", { value: handsNow })}</p>}
        {!p?.cloud && model.trim() && !guiModel && modelSees(model.trim()) === false && (
          <div className="mt-1.5 text-[12px] text-amber-700 dark:text-amber-300">
            {t("{model} does not take pictures, so the hands would be blind with it. Pick a model that sees for them.", { model: model.trim() })}
          </div>
        )}
        {lacks("vision") && !handsOnCloud && (
          <div className="mt-1.5 text-[12px] text-amber-700 dark:text-amber-300">
            {unavailableLine(t, "vision", region, locale)} <OwnKeyHow />
          </div>
        )}
      </Field>
      )}
      {!p?.no_key && !adding && (
        <Field
          label={t("API key")}
          hint={
            noKey && p?.key_optional
              ? t("No key will be sent.")
              : data.llm.key_source === "vault" && preset === currentPreset
                ? t("A key is in the vault. Leave blank to keep it.")
                : t("Stored encrypted in the vault as LLM_API_KEY.")
          }
        >
          <div className="relative">
            <input
              type={showKey ? "text" : "password"}
              value={key}
              onChange={(e) => setKey(e.target.value)}
              disabled={noKey && !!p?.key_optional}
              aria-label={t("API key")}
              className={cx(inputCls, "pr-10 disabled:opacity-50")}
              placeholder={
                data.llm.key_source === "vault" && preset === currentPreset
                  ? "••••••••"
                  : p?.key_hint || "sk-…"
              }
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
            />
            <button
              type="button"
              onClick={() => setShowKey(!showKey)}
              aria-label={showKey ? t("Hide key") : t("Show key")}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1.5 text-muted"
            >
              {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <div className="mt-1.5 flex items-center justify-between gap-2 text-[12px]">
            {p?.key_url ? (
              <a
                href={p.key_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-accent"
              >
                {t("Get a key from {vendor}", { vendor: p.label })}{" "}
                <ExternalLink size={12} />
              </a>
            ) : (
              <span />
            )}
            {p?.key_optional && (
              <label className="inline-flex items-center gap-1.5 text-muted">
                <input
                  type="checkbox"
                  checked={noKey}
                  onChange={(e) => setNoKey(e.target.checked)}
                  className="accent-accent"
                />{" "}
                {t("No key")}
              </label>
            )}
          </div>
        </Field>
      )}
      {!adding && (
        <MultiApiSmartRoutingSection
          routing={data.llm_routing}
          usageStatus={data.llm_usage_status}
          multiKeys={multiProviderKeys}
          onChangeMultiKeys={setMultiProviderKeys}
          onSaved={onChange}
        />
      )}
      {!adding && (
      <Field
        label={t("Tool calling")}
        hint={t(
          "Auto uses the API's function calling and falls back to describing tools in the prompt when the endpoint rejects them. Prompt: for endpoints that silently ignore tools.",
        )}
      >
        <div className="flex gap-1.5">
          {TOOL_MODES.map(([m, label]) => (
            <button
              key={m}
              type="button"
              aria-pressed={toolMode === m}
              onClick={() => setToolMode(m)}
              className={cx(
                "rounded-full px-3 py-1.5 text-[13px] border",
                toolMode === m
                  ? "border-accent bg-accent/10 text-accent font-medium"
                  : "border-border text-muted",
              )}
            >
              {t(label)}
            </button>
          ))}
        </div>
      </Field>
      )}
      {!p?.cloud && !adding && data.llm.proxy !== undefined && (
        <Field
          label={t("Proxy (optional)")}
          hint={t("Only this provider's requests go through it. MiniMuse Cloud never does.")}
        >
          <input
            className={inputCls}
            value={proxy}
            onChange={(e) => {
              setProxy(e.target.value);
              setProxyTouched(true);
            }}
            // a masked password cannot be edited in place: the field empties to be retyped
            onFocus={() => proxy.includes("••••") && !proxyTouched && setProxy("")}
            onBlur={() => !proxyTouched && setProxy(data.llm.proxy ?? "")}
            placeholder="http://host:port / socks5://host:port"
            autoComplete="off"
            spellCheck={false}
          />
        </Field>
      )}
      {!adding && (
      <div className="flex gap-2 pt-1">
        <button
          type="button"
          disabled={saving || !model.trim() || (needsUrl && !baseUrl.trim())}
          onClick={() => void save()}
          className={primaryBtn}
        >
          {saving ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Check size={16} />
          )}{" "}
          {onSaved ? t("Save & continue") : t("Save")}
        </button>
        <button
          type="button"
          disabled={testing}
          onClick={() => void runTest()}
          className={secondaryBtn}
        >
          {testing ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Plug size={16} />
          )}{" "}
          {t("Test")}
        </button>
      </div>
      )}
      {test && (
        <TestLine
          result={test}
          okText={t('Replied "{reply}" in {ms} ms', {
            reply: test.reply ?? "",
            ms: test.ms ?? 0,
          })}
        />
      )}
    </Card>
  );
}

/** the `tool_mode` values the runtime accepts, with the word each chip shows (translated) */
const TOOL_MODES: Array<[string, string]> = [
  ["auto", "Auto"],
  ["native", "Native"],
  ["prompt", "Prompt"],
];

const DEFAULT_PROVIDER_SLOTS: ProviderKeySlotView[] = [
  {
    id: "google_ai_studio",
    label: "Google AI Studio (Gemini Free / Paid)",
    vault_key: "GEMINI_API_KEY",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    provider: "openai",
    key_hint: "AIzaSy...",
    key_url: "https://aistudio.google.com/apikey",
    quota_kind: "free_daily_and_rpm",
    default_reset: "daily",
    has_balance_api: false,
    configured: false,
    rule: {
      enabled: true,
      priority: 1,
      quota_kind: "free_daily_and_rpm",
      reset_period: "daily",
      daily_request_limit: 0,
      daily_token_limit: 0,
      monthly_budget_usd: 0,
      on_exhausted: "fallback_openrouter",
      fallback_model: "",
    },
  },
  {
    id: "deepseek",
    label: "DeepSeek Platform (Direct Credits)",
    vault_key: "DEEPSEEK_API_KEY",
    base_url: "https://api.deepseek.com/v1",
    provider: "openai",
    key_hint: "sk-...",
    key_url: "https://platform.deepseek.com/api_keys",
    quota_kind: "prepaid_credit_balance",
    default_reset: "never",
    has_balance_api: true,
    configured: false,
    rule: {
      enabled: true,
      priority: 2,
      quota_kind: "prepaid_credit_balance",
      reset_period: "never",
      daily_request_limit: 0,
      daily_token_limit: 0,
      monthly_budget_usd: 0,
      on_exhausted: "fallback_openrouter",
      fallback_model: "",
    },
  },
  {
    id: "openrouter",
    label: "OpenRouter (Universal Multi-Model & Fallback)",
    vault_key: "OPENROUTER_API_KEY",
    base_url: "https://openrouter.ai/api/v1",
    provider: "openai",
    key_hint: "sk-or-v1-...",
    key_url: "https://openrouter.ai/keys",
    quota_kind: "prepaid_credit_and_free_daily",
    default_reset: "daily",
    has_balance_api: true,
    configured: false,
    rule: {
      enabled: true,
      priority: 3,
      quota_kind: "prepaid_credit_and_free_daily",
      reset_period: "daily",
      daily_request_limit: 0,
      daily_token_limit: 0,
      monthly_budget_usd: 0,
      on_exhausted: "fallback_local",
      fallback_model: "",
    },
  },
  {
    id: "groq",
    label: "Groq Cloud (Fast Free / Tiered)",
    vault_key: "GROQ_API_KEY",
    base_url: "https://api.groq.com/openai/v1",
    provider: "openai",
    key_hint: "gsk_...",
    key_url: "https://console.groq.com/keys",
    quota_kind: "token_and_message_rate_limit",
    default_reset: "daily",
    has_balance_api: false,
    configured: false,
    rule: {
      enabled: true,
      priority: 4,
      quota_kind: "token_and_message_rate_limit",
      reset_period: "daily",
      daily_request_limit: 0,
      daily_token_limit: 0,
      monthly_budget_usd: 0,
      on_exhausted: "fallback_openrouter",
      fallback_model: "",
    },
  },
  {
    id: "openai",
    label: "OpenAI Platform (Direct)",
    vault_key: "OPENAI_API_KEY",
    base_url: "https://api.openai.com/v1",
    provider: "openai",
    key_hint: "sk-proj-...",
    key_url: "https://platform.openai.com/api-keys",
    quota_kind: "prepaid_credit_and_rpm",
    default_reset: "rolling_minute",
    has_balance_api: false,
    configured: false,
    rule: {
      enabled: true,
      priority: 5,
      quota_kind: "prepaid_credit_and_rpm",
      reset_period: "rolling_minute",
      daily_request_limit: 0,
      daily_token_limit: 0,
      monthly_budget_usd: 0,
      on_exhausted: "fallback_openrouter",
      fallback_model: "",
    },
  },
  {
    id: "anthropic",
    label: "Anthropic Console (Direct)",
    vault_key: "ANTHROPIC_API_KEY",
    base_url: "https://api.anthropic.com",
    provider: "anthropic",
    key_hint: "sk-ant-...",
    key_url: "https://console.anthropic.com/settings/keys",
    quota_kind: "prepaid_credit_and_rpm",
    default_reset: "rolling_minute",
    has_balance_api: false,
    configured: false,
    rule: {
      enabled: true,
      priority: 6,
      quota_kind: "prepaid_credit_and_rpm",
      reset_period: "rolling_minute",
      daily_request_limit: 0,
      daily_token_limit: 0,
      monthly_budget_usd: 0,
      on_exhausted: "fallback_openrouter",
      fallback_model: "",
    },
  },
];

function MultiApiSmartRoutingSection({
  routing,
  usageStatus,
  multiKeys,
  onChangeMultiKeys,
  onSaved,
}: {
  routing?: LlmRoutingView;
  usageStatus?: LlmUsageStatusView;
  multiKeys: Record<string, string>;
  onChangeMultiKeys: (next: Record<string, string>) => void;
  onSaved: () => void;
}) {
  const { toast } = useStore();
  const t = useT();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [liveUsage, setLiveUsage] = useState<LlmUsageStatusView | undefined>(usageStatus);
  const [openrouterFallback, setOpenrouterFallback] = useState(routing?.openrouter_fallback ?? true);
  const [fallbackModel, setFallbackModel] = useState(routing?.task_models?.fallback ?? "");
  const [policies, setPolicies] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const s of routing?.provider_slots ?? DEFAULT_PROVIDER_SLOTS) {
      init[s.id] = s.rule?.on_exhausted || "fallback_openrouter";
    }
    return init;
  });

  useEffect(() => {
    if (usageStatus) setLiveUsage(usageStatus);
  }, [usageStatus]);

  useEffect(() => {
    if (!routing) return;
    setOpenrouterFallback(routing.openrouter_fallback ?? true);
    setFallbackModel(routing.task_models?.fallback ?? "");
    const next: Record<string, string> = {};
    for (const s of routing.provider_slots ?? []) {
      next[s.id] = s.rule?.on_exhausted || "fallback_openrouter";
    }
    setPolicies((prev) => ({ ...prev, ...next }));
  }, [routing]);

  const slots = routing?.provider_slots?.length ? routing.provider_slots : DEFAULT_PROVIDER_SLOTS;
  const configuredCount = slots.filter((s) => s.configured || (multiKeys[s.id] && multiKeys[s.id].trim())).length;

  const refreshBalances = async () => {
    setRefreshing(true);
    try {
      const next = await api.refreshLlmUsageStatus();
      setLiveUsage(next);
      toast(t("Updated live API credit balances & quota status."));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  };

  const saveRouting = async () => {
    setSaving(true);
    try {
      const provider_rules: Record<string, Record<string, unknown>> = {};
      for (const [provider_id, on_exhausted] of Object.entries(policies)) {
        provider_rules[provider_id] = {
          on_exhausted,
          fallback_model: fallbackModel.trim(),
        };
      }
      await api.setLlmRouting({
        enabled: true,
        openrouter_fallback: openrouterFallback,
        task_models: fallbackModel.trim() ? { fallback: fallbackModel.trim() } : undefined,
        provider_keys: multiKeys,
        provider_rules,
      });
      const updatedUsage = await api.getLlmUsageStatus().catch(() => undefined);
      if (updatedUsage) setLiveUsage(updatedUsage);
      onChangeMultiKeys({});
      toast(t("Saved multi-API keys & smart credit routing rules."));
      onSaved();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const clearProviderCooldown = async (pid: string) => {
    try {
      await api.setLlmRouting({ clear_cooldown: pid });
      const updatedUsage = await api.getLlmUsageStatus().catch(() => undefined);
      if (updatedUsage) setLiveUsage(updatedUsage);
      toast(t("Cleared cooldown; provider is active again."));
      onSaved();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const formatBalance = (st?: ProviderUsageStatus) => {
    const b = st?.live_balance;
    if (!b) return null;
    if (typeof b.balance === "number") {
      const sym = b.currency === "CNY" ? "¥" : "$";
      return `${sym}${b.balance.toFixed(2)} ${t("remaining")}`;
    }
    if (typeof st?.remaining?.balance === "number") {
      const sym = st.remaining.balance_currency === "CNY" ? "¥" : "$";
      return `${sym}${st.remaining.balance.toFixed(2)} ${t("remaining")}`;
    }
    if (typeof b.usage === "number") {
      return `$${b.usage.toFixed(2)} ${t("used")}`;
    }
    return null;
  };

  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    const id = window.setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 30_000);
    return () => window.clearInterval(id);
  }, []);

  const formatResetCountdown = (epoch?: number | null) => {
    if (!epoch) return null;
    const diffSec = Math.max(0, Math.round(epoch - nowSec));
    if (diffSec <= 0) return t("Ready now");
    if (diffSec < 60) return `${diffSec}s`;
    if (diffSec < 3600) return `${Math.ceil(diffSec / 60)}m`;
    return `${(diffSec / 3600).toFixed(1)}h`;
  };

  return (
    <div className="rounded-2xl border border-border/80 bg-surface-2/40 p-3 space-y-2.5 text-left">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 flex-1 items-center justify-between gap-2 text-left"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[13px] font-semibold text-fg">
              <KeyRound size={14} className="text-accent shrink-0" />
              <span>{t("Multi-API Keys, Smart Credit Routing & Live Usage")}</span>
              {configuredCount > 0 && (
                <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[10.5px] font-semibold text-accent">
                  {t("{n} keys active", { n: configuredCount })}
                </span>
              )}
            </div>
            <p className="mt-0.5 text-[11.5px] text-muted leading-snug">
              {t("Combine free Google AI Studio keys for Gemini, DeepSeek direct credits, and OpenRouter fallback — automatically tracks live balances, learns daily/minute limits, and switches when a key runs out.")}
            </p>
          </div>
          {open ? <ChevronUp size={15} className="text-muted shrink-0" /> : <ChevronDown size={15} className="text-muted shrink-0" />}
        </button>
      </div>

      {open && (
        <div className="space-y-3 pt-1 border-t border-border/60">
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-bg/80 px-3 py-2 border border-border/60">
            <label className="flex items-center gap-2 text-[12px] text-fg cursor-pointer">
              <input
                type="checkbox"
                checked={openrouterFallback}
                onChange={(e) => setOpenrouterFallback(e.target.checked)}
                className="accent-accent"
              />
              <span>{t("Use OpenRouter automatically when a direct provider key (Gemini, DeepSeek, etc.) runs out of credits or free quota")}</span>
            </label>
            <button
              type="button"
              disabled={refreshing}
              onClick={() => void refreshBalances()}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-2 px-2.5 py-1 text-[11px] font-medium text-fg hover:border-accent disabled:opacity-50"
            >
              <RefreshCw size={11} className={refreshing ? "animate-spin" : ""} />
              {t("Refresh live balances")}
            </button>
          </div>

          <div className="space-y-2">
            {slots.map((slot) => {
              const st = liveUsage?.providers?.[slot.id];
              const balText = formatBalance(st);
              const resetIn = formatResetCountdown(st?.cooldown_until || st?.next_reset_at);
              const typedVal = multiKeys[slot.id] ?? "";
              const policy = policies[slot.id] ?? slot.rule?.on_exhausted ?? "fallback_openrouter";
              const dayReqs = st?.usage?.day?.requests ?? 0;
              const dayToks = st?.usage?.day?.tokens ?? 0;
              const learnedCap = st?.learned?.observed_request_ceiling;

              return (
                <div key={slot.id} className="rounded-xl border border-border/70 bg-bg p-2.5 space-y-2">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[12.5px] font-semibold text-fg">{slot.label}</span>
                        {slot.configured && (
                          <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300">
                            {t("Key in vault")}
                          </span>
                        )}
                        {balText && (
                          <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-semibold text-sky-700 dark:text-sky-300">
                            {balText}
                          </span>
                        )}
                        {st?.in_cooldown && (
                          <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300">
                            {t("Out of quota / cooling down")}
                            {resetIn ? ` (${resetIn})` : ""}
                            <button
                              type="button"
                              onClick={() => void clearProviderCooldown(slot.id)}
                              className="ml-1 underline hover:no-underline"
                            >
                              {t("Reset")}
                            </button>
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 text-[11px] text-muted leading-snug">
                        {slot.quota_kind} · {slot.default_reset}
                      </p>
                    </div>
                    {slot.key_url && (
                      <a
                        href={slot.key_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-[11px] font-medium text-accent shrink-0"
                      >
                        {t("Get key")} <ExternalLink size={11} />
                      </a>
                    )}
                  </div>

                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <div>
                      <label className="block text-[10.5px] font-medium text-muted mb-0.5">
                        {t("API Key")} ({slot.vault_key})
                      </label>
                      <input
                        type="password"
                        value={typedVal}
                        onChange={(e) => onChangeMultiKeys({ ...multiKeys, [slot.id]: e.target.value })}
                        placeholder={slot.configured ? "•••••••• (saved in vault)" : (slot.key_hint || "sk-...")}
                        className={cx(inputCls, "py-1.5 text-[12px]")}
                        autoComplete="off"
                      />
                    </div>
                    <div>
                      <label className="block text-[10.5px] font-medium text-muted mb-0.5">
                        {t("When out of credits or free quota")}
                      </label>
                      <select
                        value={policy}
                        onChange={(e) => setPolicies({ ...policies, [slot.id]: e.target.value })}
                        className={cx(inputCls, "py-1.5 text-[12px] text-fg")}
                      >
                        <option value="fallback_openrouter">{t("Use OpenRouter for this model")}</option>
                        <option value="switch_model">{t("Switch to fallback model below")}</option>
                        <option value="cooldown_skip">{t("Pause this AI until quota resets")}</option>
                        <option value="fallback_local">{t("Fallback to local AI (Ollama)")}</option>
                        <option value="stop">{t("Stop & don't use until refilled")}</option>
                      </select>
                    </div>
                  </div>

                  {st && (dayReqs > 0 || learnedCap || st.remaining?.header_remaining_requests !== null || st.exhausted_reason) && (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-surface-2/60 px-2.5 py-1 text-[10.5px] text-muted">
                      <span>
                        {t("Today:")} <strong className="text-fg">{dayReqs}</strong> {t("msgs")} · <strong className="text-fg">{dayToks.toLocaleString()}</strong> {t("tokens")}
                      </span>
                      {learnedCap && (
                        <span>
                          {t("Learned daily cap:")} <strong className="text-fg">{learnedCap}</strong> {t("msgs")}
                        </span>
                      )}
                      {st.remaining?.header_remaining_requests !== null && st.remaining?.header_remaining_requests !== undefined && (
                        <span>
                          {t("API quota left:")} <strong className="text-fg">{String(st.remaining.header_remaining_requests)}</strong>
                        </span>
                      )}
                      {resetIn && (
                        <span>
                          {t("Resets in:")} <strong className="text-fg">{resetIn}</strong> ({st.reset_period})
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 items-end pt-1">
            <div>
              <label className="block text-[11px] font-medium text-muted mb-0.5">
                {t("Optional fallback model when switching models on quota exhaustion")}
              </label>
              <input
                value={fallbackModel}
                onChange={(e) => setFallbackModel(e.target.value)}
                placeholder="e.g. google/gemini-3.1-flash-lite or qwen3.5:9b-32k"
                className={cx(inputCls, "py-1.5 text-[12px]")}
              />
            </div>
            <div className="flex justify-end">
              <button
                type="button"
                disabled={saving}
                onClick={() => void saveRouting()}
                className="inline-flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-[12px] font-semibold text-accent-fg shadow-sm hover:opacity-95 disabled:opacity-50"
              >
                {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                {t("Save Multi-API & Routing Rules")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ endpoints of one's own (#294)
/** what a slot is called in an endpoint's "used by" line */
const SLOT_NAMES: Record<CustomEndpoint["slots"][number], string> = {
  chat: "the chat model",
  hands: "the hands",
  image: "pictures",
  video: "clips",
};

/**
 * The form that adds a custom OpenAI-compatible endpoint to the runtime's list: a name, the
 * address, a key of its own (or none), and the ids of its models when it does not list them
 * at /models. The runtime keeps the key in the vault under the endpoint's own name; the
 * entry then stands in the provider tiles and in the hands, pictures and clips pickers.
 */
function AddEndpointForm({ onAdded }: { onAdded: (e: CustomEndpoint) => void }) {
  const t = useT();
  const { toast } = useStore();
  const [label, setLabel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [key, setKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [noKey, setNoKey] = useState(false);
  const [typedModels, setTypedModels] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const willAppendV1 = /^https?:\/\/[^/]+\/?$/.test(baseUrl.trim());

  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      const added = await api.addEndpoint({
        label: label.trim(),
        base_url: baseUrl.trim(),
        api_key: noKey ? "" : key,
        models: typedModels,
      });
      toast(t("Endpoint added"));
      onAdded(added);
    } catch (e) {
      setError(t((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 rounded-2xl border border-border p-3">
      <p className="text-[12.5px] text-muted leading-snug">
        {t("An endpoint joins your list with its own key and stays there; add as many as you use. Pick one as the chat model, or use its models for the hands, pictures and clips.")}
      </p>
      <Field label={t("Name (optional)")} hint={t("How the endpoint reads in the lists; its host when left empty.")}>
        <input value={label} onChange={(e) => setLabel(e.target.value)} className={inputCls} placeholder={t("for example: my gateway")} autoCapitalize="off" />
      </Field>
      <Field
        label={t("Base URL")}
        hint={willAppendV1 ? t("/v1 is added when the URL has no path.") : t("The endpoint that serves /chat/completions, for example http://127.0.0.1:8000/v1.")}
      >
        <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className={inputCls} placeholder="https://host/v1" inputMode="url" autoCapitalize="off" />
      </Field>
      <Field label={t("API key")} hint={noKey ? t("No key will be sent.") : t("Stored encrypted in the vault under this endpoint's own name.")}>
        <div className="relative">
          <input
            type={showKey ? "text" : "password"}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            disabled={noKey}
            aria-label={t("API key")}
            className={cx(inputCls, "pr-10 disabled:opacity-50")}
            placeholder="sk-…"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
          />
          <button type="button" onClick={() => setShowKey(!showKey)} aria-label={showKey ? t("Hide key") : t("Show key")} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1.5 text-muted">
            {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>
        <label className="mt-1.5 inline-flex items-center gap-1.5 text-[12px] text-muted">
          <input type="checkbox" checked={noKey} onChange={(e) => setNoKey(e.target.checked)} className="accent-accent" /> {t("No key")}
        </label>
      </Field>
      <Field
        label={t("Models (optional)")}
        hint={t("Leave empty when the endpoint lists its models at /models. Otherwise type their ids, separated by commas.")}
      >
        <input value={typedModels} onChange={(e) => setTypedModels(e.target.value)} className={inputCls} placeholder="model-a, model-b" autoCapitalize="off" autoCorrect="off" spellCheck={false} />
      </Field>
      {error && <div className="rounded-2xl bg-rose-500/12 px-3 py-2 text-[12.5px] text-rose-700 dark:text-rose-300">{error}</div>}
      <button type="button" disabled={busy || !baseUrl.trim()} onClick={() => void add()} className={primaryBtn}>
        {busy ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />} {t("Add endpoint")}
      </button>
    </div>
  );
}

/**
 * One endpoint of the list, under its tile: name and host, where its key is, the models it
 * serves and the slots that answer through it, with the ways to change it: rename, change
 * the key, retype the models, ask it for its models again, remove it. The slots that use
 * the endpoint follow a rename, a new key or a move; removing it needs the chat model
 * elsewhere first, and sets the hands, pictures or clips on it back to automatic.
 */
function EndpointCard({
  endpoint,
  onChange,
  onModels,
}: {
  endpoint: CustomEndpoint;
  onChange: () => void;
  /** the endpoint listed its models again: the chat model picker follows */
  onModels: (list: string[]) => void;
}) {
  const t = useT();
  const { toast } = useStore();
  const [mode, setMode] = useState<"" | "rename" | "key" | "models" | "remove">("");
  const [label, setLabel] = useState(endpoint.label);
  const [key, setKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [noKey, setNoKey] = useState(false);
  const [typedModels, setTypedModels] = useState(endpoint.models.join(", "));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const modelsText = endpoint.models.join(", ");

  useEffect(() => {
    setLabel(endpoint.label);
    setTypedModels(modelsText);
    setMode("");
    setKey("");
    setNoKey(false);
    setNote(null);
  }, [endpoint.id, endpoint.label, modelsText]);

  const run = async (work: () => Promise<unknown>, done: string) => {
    setBusy(true);
    setNote(null);
    try {
      await work();
      if (done) toast(done);
      setMode("");
      onChange();
    } catch (e) {
      setNote(t((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await api.refreshEndpointModels(endpoint.id);
      if (r.source === "live") {
        onModels(r.models);
        toast(t("{n} models from the endpoint", { n: r.models.length }));
        onChange();
      } else {
        setNote(t("The endpoint did not list its models; the saved list stands.") + (r.error ? ` ${t(r.error)}` : ""));
      }
    } catch (e) {
      setNote(t((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  const keyLine =
    endpoint.key_source === "vault"
      ? t("Key in vault")
      : endpoint.key_source === "config"
        ? t("Key from config")
        : endpoint.key_source === "missing"
          ? t("Key missing from vault")
          : t("No key");
  const usedBy = endpoint.slots.map((s) => t(SLOT_NAMES[s])).join(getLocale() === "zh-CN" ? "、" : ", ");
  const chatUsesIt = endpoint.slots.includes("chat");
  const linkCls = "text-[12.5px] text-accent disabled:opacity-50";

  return (
    <div className="space-y-2 rounded-2xl border border-border p-3 text-[13px]">
      <div>
        <span className="font-medium text-fg">{endpointLabel(endpoint)}</span>
        <span className="text-muted"> · {hostOf(endpoint.base_url)}</span>
      </div>
      <div className="text-[12px] text-muted">
        {keyLine} · {t("{n} models", { n: endpoint.models.length })}
        {usedBy ? ` · ${t("Used by {slots}", { slots: usedBy })}` : ""}
      </div>
      {mode === "" && (
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          <button type="button" className={linkCls} disabled={busy} onClick={() => setMode("rename")}>
            {t("Rename")}
          </button>
          <button type="button" className={linkCls} disabled={busy} onClick={() => setMode("key")}>
            {t("Change key")}
          </button>
          <button type="button" className={linkCls} disabled={busy} onClick={() => setMode("models")}>
            {t("Edit models")}
          </button>
          <button type="button" className={linkCls} disabled={busy} onClick={() => void refresh()}>
            {busy ? <Loader2 size={12} className="inline animate-spin" /> : <RefreshCw size={12} className="inline" />} {t("Ask for its models")}
          </button>
          <button type="button" className={cx(linkCls, "text-rose-600 dark:text-rose-300")} disabled={busy} onClick={() => setMode("remove")}>
            {t("Remove")}
          </button>
        </div>
      )}
      {mode === "rename" && (
        <div className="space-y-2">
          <input value={label} onChange={(e) => setLabel(e.target.value)} className={inputCls} placeholder={hostOf(endpoint.base_url)} autoCapitalize="off" aria-label={t("Name")} />
          <div className="flex gap-2">
            <button type="button" disabled={busy} className={primaryBtn} onClick={() => void run(() => api.updateEndpoint(endpoint.id, { label: label.trim() }), t("Endpoint renamed"))}>
              {t("Save")}
            </button>
            <button type="button" className={secondaryBtn} onClick={() => setMode("")}>
              {t("Cancel")}
            </button>
          </div>
        </div>
      )}
      {mode === "key" && (
        <div className="space-y-2">
          <div className="relative">
            <input
              type={showKey ? "text" : "password"}
              value={key}
              onChange={(e) => setKey(e.target.value)}
              disabled={noKey}
              className={cx(inputCls, "pr-10 disabled:opacity-50")}
              placeholder={endpoint.key_source === "vault" ? "••••••••" : "sk-…"}
              aria-label={t("API key")}
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
            />
            <button type="button" onClick={() => setShowKey(!showKey)} aria-label={showKey ? t("Hide key") : t("Show key")} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1.5 text-muted">
              {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <label className="inline-flex items-center gap-1.5 text-[12px] text-muted">
            <input type="checkbox" checked={noKey} onChange={(e) => setNoKey(e.target.checked)} className="accent-accent" /> {t("No key")}
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy || (!noKey && !key.trim())}
              className={primaryBtn}
              onClick={() => void run(() => api.updateEndpoint(endpoint.id, { api_key: noKey ? "" : key.trim() }), t("Key saved"))}
            >
              {t("Save")}
            </button>
            <button type="button" className={secondaryBtn} onClick={() => setMode("")}>
              {t("Cancel")}
            </button>
          </div>
        </div>
      )}
      {mode === "models" && (
        <div className="space-y-2">
          <input value={typedModels} onChange={(e) => setTypedModels(e.target.value)} className={inputCls} placeholder="model-a, model-b" aria-label={t("Models")} autoCapitalize="off" autoCorrect="off" spellCheck={false} />
          <div className="text-[12px] text-muted">{t("The ids of the models this endpoint serves, separated by commas; what it lists at /models is added after them.")}</div>
          <div className="flex gap-2">
            <button type="button" disabled={busy} className={primaryBtn} onClick={() => void run(() => api.updateEndpoint(endpoint.id, { models: typedModels }), t("Models saved"))}>
              {t("Save")}
            </button>
            <button type="button" className={secondaryBtn} onClick={() => setMode("")}>
              {t("Cancel")}
            </button>
          </div>
        </div>
      )}
      {mode === "remove" && (
        <div className="space-y-2">
          <div className="text-[12.5px] text-muted">
            {chatUsesIt
              ? t("The chat model answers through this endpoint; pick another chat model first.")
              : t("Its key goes with it, and the hands, pictures or clips set on it go back to automatic.")}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy || chatUsesIt}
              className={cx(primaryBtn, "bg-rose-600 hover:bg-rose-600/90")}
              onClick={() => void run(() => api.removeEndpoint(endpoint.id), t("Endpoint removed"))}
            >
              <Trash2 size={16} /> {t("Remove")}
            </button>
            <button type="button" className={secondaryBtn} onClick={() => setMode("")}>
              {t("Cancel")}
            </button>
          </div>
        </div>
      )}
      {note && <div className="rounded-2xl bg-rose-500/12 px-3 py-2 text-[12.5px] text-rose-700 dark:text-rose-300">{note}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ pictures and clips
/**
 * One of the two media rows of the Models contract — *Making pictures* (`[image]`) and
 * *Making clips* (`[video]`). The value is `<provider> · <model>` as the runtime resolves it:
 * an explicit choice, else the chat provider's own model, else the account's. The picker
 * offers MiniMuse Cloud when signed in, the chat model's provider when it has the capability,
 * and every catalogue provider that has it (a key to add); nothing without it.
 */
export function MediaSlotCard({
  slot,
  data,
  onChange,
}: {
  slot: "image" | "video";
  data: ConnectionsData;
  onChange: () => void;
}) {
  const { toast, state } = useStore();
  const t = useT();
  const info = data[slot]!;
  const cap: Capability = slot;
  const account = state.hub?.account ?? null;
  const signedIn = !!account?.signed_in;
  const region = regionOf(account);
  const locale = getLocale();
  const cloudBaseUrl = data.providers[CLOUD_PRESET]?.base_url ?? "";
  // the custom endpoints of the runtime's list (#294), each with its own key
  const endpoints = data.endpoints ?? [];
  const choices = mediaChoices(cap, {
    signedIn,
    chat: { provider: data.llm.provider, base_url: data.llm.base_url, cloud: !!data.llm.cloud },
    region,
    locale,
    endpoints,
  });
  const current = currentChoice(info, cloudBaseUrl);
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState(current);
  const [model, setModel] = useState(info.model);
  const [baseUrl, setBaseUrl] = useState(info.base_url);
  const [key, setKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [models, setModels] = useState<{ list: string[]; more: string[]; source: "live" | "catalogue" | "loading" }>({ list: [], more: [], source: "catalogue" });

  useEffect(() => {
    setChoice(current);
    setModel(info.model);
    setBaseUrl(info.base_url);
    setKey("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info]);

  const chosen = choices.find((c) => c.value === choice) ?? null;
  const entry = chosen?.entry ?? null;
  const edition = entry ? editionFor(entry, region) : null;
  // an endpoint of the list, chosen by id: its address and key are the runtime's to look up
  const own = endpoints.find((e) => endpointChoice(e.id) === choice) ?? null;
  const endpointUrl = choice === CLOUD_ID ? cloudBaseUrl : own ? own.base_url : choice === CUSTOM_ID ? baseUrl.trim() : (edition?.base_url ?? "");
  // the key the slot would use: one typed here, the one saved for this very provider, the
  // chat model's on its own host, the account's on MiniMuse Cloud
  const keySaved = current === choice && current !== "" && (info.key_source === "vault" || info.key_source === "config");
  const needsKey = !!chosen?.needsKey && !key && !keySaved;
  const defaultModel = choice === CLOUD_ID ? (models.list[0] ?? "") : (entry?.defaults[slot] ?? "");

  // the endpoint's picture or clip models, when it lists any
  useEffect(() => {
    if (!open || !choice || (choice !== CLOUD_ID && !endpointUrl)) {
      setModels({ list: [], more: [], source: "catalogue" });
      return;
    }
    let alive = true;
    const handle = setTimeout(
      () => {
        setModels((m) => ({ ...m, source: "loading" }));
        api
          .llmModels(
            choice === CLOUD_ID
              ? { preset: CLOUD_PRESET }
              : own
                ? { endpoint: own.id }
                : { preset: entry ? presetFor(entry.id) : "", base_url: endpointUrl, api_key: key || undefined },
          )
          .then((r) => {
            if (!alive) return;
            const list = (slot === "image" ? r.image_models : r.video_models) ?? [];
            const more = (slot === "image" ? r.image_catalog : r.video_catalog) ?? [];
            setModels({ list: list.filter((m) => !more.includes(m)), more, source: r.source });
          })
          .catch(() => alive && setModels({ list: [], more: [], source: "catalogue" }));
      },
      key ? 600 : 150,
    );
    return () => {
      alive = false;
      clearTimeout(handle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, choice, endpointUrl, key]);

  const title = slot === "image" ? t("Making pictures") : t("Making clips");
  const subtitle = slot === "image" ? t("Portraits of your Muse.") : t("Short clips of your Muse.");
  const value = slotValue(info, locale, CATALOGUE, endpoints);
  const sentence = unavailableLine(t, cap, region, locale);
  const status = !info.effective_provider
    ? { text: t("Not set up"), tone: "warn" }
    : info.effective_source === "cloud"
      ? { text: t("Your account"), tone: "ok" }
      : info.effective_source === "chat"
        ? { text: t("The chat model's provider"), tone: "ok" }
        : { text: t("Your key"), tone: "ok" };
  const save = async () => {
    setSaving(true);
    try {
      if (!choice) {
        await api.setMedia(slot, { provider: "", model: "", base_url: "", api_key: "" });
      } else if (choice === CLOUD_ID) {
        await api.setMedia(slot, { provider: "openai", base_url: cloudBaseUrl, model: model.trim(), api_key: CLOUD_KEY_REF });
      } else if (own) {
        // an endpoint of the list lends the slot its address and key, by id
        await api.setMedia(slot, { endpoint: own.id, model: model.trim() });
      } else if (choice === CUSTOM_ID) {
        await api.setMedia(slot, { provider: "openai", base_url: baseUrl.trim(), model: model.trim(), api_key: key ? key : null });
      } else {
        // the chat provider's own slot rides on the chat key: an empty key here says so
        await api.setMedia(slot, { provider: choice, base_url: "", model: model.trim(), api_key: key ? key : chosen?.group === "chat" ? "" : null });
      }
      setKey("");
      invalidateProviders();
      toast(t("Saved"));
      onChange();
      setOpen(false);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const groupTitle = (g: MediaChoice["group"]) =>
    g === "cloud" ? t("Your account") : g === "chat" ? t("The chat model's provider") : g === "endpoints" ? t("Your endpoints") : t("Add a provider");
  const groups = (["cloud", "chat", "endpoints", "add"] as const).map((g) => ({ g, items: choices.filter((c) => c.group === g) })).filter((x) => x.items.length);
  const vaultName = slot === "image" ? "IMAGE_API_KEY" : "VIDEO_API_KEY";

  return (
    <Card
      icon={slot === "image" ? <ImageIcon size={19} /> : <Clapperboard size={19} />}
      title={title}
      summary={value || sentence}
      status={status}
      open={open}
      onToggle={() => setOpen(!open)}
    >
      <p className="text-[12.5px] text-muted">{subtitle}</p>
      <Field label={t("Provider")} hint={!value && !choice ? sentence : undefined}>
        <select
          value={choice}
          onChange={(e) => {
            setChoice(e.target.value);
            setModel("");
            setKey("");
          }}
          className={cx(inputCls, "text-fg")}
        >
          <option value="">{t("Automatic")}</option>
          {groups.map(({ g, items }) => (
            <optgroup key={g} label={groupTitle(g)}>
              {items.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.value === CUSTOM_ID ? t("Other OpenAI-compatible endpoint") : c.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        {value && <p className="mt-1.5 text-[12px] text-muted">{t("Currently {value}", { value })}</p>}
        {!value && !choice && (
          <button type="button" className="mt-1.5 inline-flex items-center gap-1 text-[12.5px] text-accent" onClick={() => setChoice(choices.find((c) => c.group === "add")?.value ?? "")}>
            <Plus size={13} /> {t("Add a provider")}
          </button>
        )}
        {entry && (
          <p className="mt-1.5 text-[12px] text-muted">
            <span className="font-medium text-fg">{providerName(entry, locale)}</span> · {coversLine(t, entry.capabilities)}
            {". "}
            {locale === "zh-CN" ? entry.note_zh : entry.note}
          </p>
        )}
      </Field>
      {choice === CUSTOM_ID && (
        <Field label={t("Base URL")} hint={t("The endpoint that serves /images/generations, for example https://host/v1.")}>
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className={inputCls} placeholder="https://host/v1" inputMode="url" autoCapitalize="off" />
        </Field>
      )}
      {choice && (
        <StudioModelPick
          label={slot === "image" ? t("Picture model") : t("Clip model")}
          provider={choice === CLOUD_ID ? "MiniMuse Cloud" : choice === CUSTOM_ID ? hostOf(baseUrl) || t("Other OpenAI-compatible endpoint") : (chosen?.label ?? choice)}
          value={model}
          options={models.list}
          more={models.more}
          onChange={setModel}
          other={choice !== CLOUD_ID || !!account?.any_model}
          placeholder={defaultModel || undefined}
        />
      )}
      {choice && choice !== CLOUD_ID && chosen?.group !== "chat" && !own && (
        <Field
          label={t("API key")}
          hint={keySaved ? t("A key is in the vault. Leave blank to keep it.") : t("Stored encrypted in the vault as {name}.", { name: vaultName })}
        >
          <div className="relative">
            <input
              type={showKey ? "text" : "password"}
              value={key}
              onChange={(e) => setKey(e.target.value)}
              aria-label={t("API key")}
              className={cx(inputCls, "pr-10")}
              placeholder={keySaved ? "••••••••" : (entry?.key_hint || "sk-…")}
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
            />
            <button type="button" onClick={() => setShowKey(!showKey)} aria-label={showKey ? t("Hide key") : t("Show key")} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1.5 text-muted">
              {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          {edition?.key_url && (
            <a href={edition.key_url} target="_blank" rel="noreferrer" className="mt-1.5 inline-flex items-center gap-1 text-[12px] text-accent">
              {t("Get a key from {vendor}", { vendor: entry ? providerName(entry, locale) : "" })} <ExternalLink size={12} />
            </a>
          )}
        </Field>
      )}
      {chosen?.group === "chat" && <p className="text-[12px] text-muted">{t("Uses the chat model's key.")}</p>}
      {own && <p className="text-[12px] text-muted">{t("Uses the key saved with this endpoint.")}</p>}
      <div className="flex gap-2 pt-1">
        <button type="button" disabled={saving || needsKey || (choice === CUSTOM_ID && !baseUrl.trim())} onClick={() => void save()} className={primaryBtn}>
          {saving ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />} {t("Save")}
        </button>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ recall by meaning
/**
 * Memories embedded once, the message embedded per turn, the closest fused with the keyword
 * hits — so "写邮件给房东" finds "the landlord is Bob Li". Works with the model's own endpoint
 * when it has /embeddings (OpenAI, Ollama, most gateways); DeepSeek has none, so this card is
 * where an Ollama next door gets pointed at.
 */
export function RecallCard({
  data,
  onChange,
}: {
  data: ConnectionsData;
  onChange: () => void;
}) {
  const { toast } = useStore();
  const t = useT();
  const e = data.embeddings;
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState(e.mode);
  const [own, setOwn] = useState(!!e.base_url);
  const [baseUrl, setBaseUrl] = useState(e.base_url);
  const [model, setModel] = useState(e.model);
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    setMode(e.mode);
    setOwn(!!e.base_url);
    setBaseUrl(e.base_url);
    setModel(e.model);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.embeddings]);

  const status =
    e.mode === "off"
      ? { text: t("Off"), tone: "off" }
      : e.available === true
        ? { text: t("On"), tone: "ok" }
        : e.available === false
          ? {
              text: e.mode === "on" ? t("Not reachable") : t("By keyword"),
              tone: "warn",
            }
          : { text: t("Not tried yet"), tone: "off" };
  const summary =
    e.mode === "off"
      ? t("Keyword recall only")
      : e.available === true
        ? t("{model} · {n} of {total} memories indexed", {
            model: e.model || e.default_model,
            n: e.indexed,
            total: e.total,
          })
        : e.available === false
          ? e.reason
          : t("{model} at {host}", {
              model: e.model || e.default_model,
              host: hostOf(e.effective_base_url),
            });

  const save = async () => {
    setSaving(true);
    setTest(null);
    try {
      await api.setEmbeddings({
        mode,
        base_url: own ? baseUrl.trim() : "",
        model: model.trim(),
        api_key: own ? (key ? key : undefined) : "",
      });
      setKey("");
      toast(t("Saved"));
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const runTest = async () => {
    setTesting(true);
    try {
      setTest(await api.testEmbeddings());
      onChange();
    } catch (err) {
      setTest({ ok: false, error: (err as Error).message });
    } finally {
      setTesting(false);
    }
  };
  const chip = (on: boolean) =>
    cx(
      "rounded-full px-3 py-1.5 text-[13px] border",
      on
        ? "border-accent bg-accent/10 text-accent font-medium"
        : "border-border text-muted",
    );

  return (
    <Card
      icon={<BrainCircuit size={19} />}
      title={t("Recall by meaning")}
      summary={summary}
      status={status}
      open={open}
      onToggle={() => setOpen(!open)}
    >
      <p className="text-[12.5px] text-muted leading-snug">
        {t(
          "Memories are embedded once and a message finds the ones that mean the same thing across different wording: “email the landlord” finds “the landlord is Bob Li”. Keyword recall stays; the two are fused.",
        )}
      </p>
      <Field
        label={t("Mode")}
        hint={t(
          "Auto uses the model's endpoint when it has embeddings and falls back to keywords when it does not. On insists and warns. Off: keywords only.",
        )}
      >
        <div className="flex gap-1.5">
          {(["auto", "on", "off"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={chip(mode === m)}
            >
              {m === "auto" ? t("Auto") : m === "on" ? t("On") : t("Off")}
            </button>
          ))}
        </div>
      </Field>
      {mode !== "off" && (
        <>
          <Field
            label={t("Endpoint")}
            hint={
              own
                ? t(
                    "Any OpenAI-compatible /embeddings: Ollama with an embedding model pulled, OpenAI, a gateway.",
                  )
                : t(
                    "The model's endpoint and key. DeepSeek has no embeddings; pick another endpoint.",
                  )
            }
          >
            <div className="flex gap-1.5">
              <button
                type="button"
                onClick={() => setOwn(false)}
                className={chip(!own)}
              >
                {t("Same as the model")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setOwn(true);
                  if (!baseUrl) setBaseUrl("http://127.0.0.1:11434/v1");
                }}
                className={chip(own)}
              >
                {t("Another endpoint")}
              </button>
            </div>
          </Field>
          {own && (
            <>
              <Field label={t("Base URL")}>
                <input
                  value={baseUrl}
                  onChange={(ev) => setBaseUrl(ev.target.value)}
                  className={inputCls}
                  placeholder="http://127.0.0.1:11434/v1"
                  inputMode="url"
                />
              </Field>
              <Field
                label={t("API key")}
                hint={
                  e.key_source === "vault"
                    ? t("A key is in the vault. Leave blank to keep it.")
                    : t(
                        "Stored encrypted in the vault as EMBEDDINGS_API_KEY. Ollama needs none.",
                      )
                }
              >
                <input
                  type="password"
                  value={key}
                  onChange={(ev) => setKey(ev.target.value)}
                  className={inputCls}
                  placeholder={e.key_source === "vault" ? "••••••••" : "sk-…"}
                  autoComplete="off"
                />
              </Field>
            </>
          )}
          <Field
            label={t("Embedding model")}
            hint={t(
              "Blank uses the endpoint's default: {model}. On Ollama: ollama pull qwen3-embedding:0.6b (reads Chinese and English).",
              { model: e.default_model },
            )}
          >
            <input
              value={model}
              onChange={(ev) => setModel(ev.target.value)}
              className={inputCls}
              placeholder={e.default_model}
              autoComplete="off"
            />
          </Field>
        </>
      )}
      <div className="flex gap-2 pt-1">
        <button
          type="button"
          disabled={saving || (own && !/^https?:\/\//.test(baseUrl.trim()))}
          onClick={() => void save()}
          className={primaryBtn}
        >
          {saving ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Check size={16} />
          )}{" "}
          {t("Save")}
        </button>
        <button
          type="button"
          disabled={testing || e.mode === "off"}
          onClick={() => void runTest()}
          className={secondaryBtn}
        >
          {testing ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Plug size={16} />
          )}{" "}
          {t("Test")}
        </button>
      </div>
      {test && (
        <TestLine
          result={test}
          okText={t("{model} · {dims} dims · {n} memories indexed · {ms} ms", {
            model: test.model ?? "",
            dims: test.dims ?? 0,
            n: test.indexed ?? 0,
            ms: test.ms ?? 0,
          })}
        />
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ web search
/**
 * Who answers `web_search`. DuckDuckGo needs nothing and is the default but is scraped, so it
 * rate-limits now and then; Brave and Tavily take a key, SearXNG the URL of an instance you run.
 * Whatever is picked, a failed search falls back to DuckDuckGo once, with a note.
 */
export function SearchCard({
  data,
  onChange,
}: {
  data: ConnectionsData;
  onChange: () => void;
}) {
  const { toast } = useStore();
  const t = useT();
  const s = data.search;
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState(s.provider);
  const [baseUrl, setBaseUrl] = useState(s.base_url);
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    setProvider(s.provider);
    setBaseUrl(s.base_url);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.search]);

  const info = s.providers.find((p) => p.id === s.provider);
  const chosen = s.providers.find((p) => p.id === provider);
  const needsKey = !!chosen?.needs_key;
  // You.com: works without a key, a key raises the daily limit; the field shows, as optional
  const takesKey = needsKey || !!chosen?.optional_key;
  const status = s.configured
    ? { text: t("On"), tone: "ok" }
    : { text: t("Not set up"), tone: "warn" };
  const summary = s.configured
    ? s.provider === "searxng"
      ? t("{provider} at {host}", {
          provider: info?.label ?? s.provider,
          host: hostOf(s.base_url),
        })
      : (info?.label ?? s.provider)
    : t("{provider} needs {what}; searches use DuckDuckGo until then", {
        provider: info?.label ?? s.provider,
        what: info?.needs_key ? t("a key") : t("an instance URL"),
      });

  const save = async () => {
    setSaving(true);
    setTest(null);
    try {
      await api.setSearch({
        provider,
        base_url: provider === "searxng" ? baseUrl.trim() : "",
        api_key: takesKey ? (key ? key : undefined) : "",
      });
      setKey("");
      toast(t("Saved"));
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const runTest = async () => {
    setTesting(true);
    try {
      setTest(await api.testSearch());
    } catch (err) {
      setTest({ ok: false, error: (err as Error).message });
    } finally {
      setTesting(false);
    }
  };
  const chip = (on: boolean) =>
    cx(
      "rounded-full px-3 py-1.5 text-[13px] border",
      on
        ? "border-accent bg-accent/10 text-accent font-medium"
        : "border-border text-muted",
    );
  const canSave = provider !== "searxng" || /^https?:\/\//.test(baseUrl.trim());

  return (
    <Card
      icon={<Search size={19} />}
      title={t("Web search")}
      summary={summary}
      status={status}
      open={open}
      onToggle={() => setOpen(!open)}
    >
      <p className="text-[12.5px] text-muted leading-snug">
        {t(
          "DuckDuckGo needs nothing, but it is scraped and rate-limits now and then. For searches that always work, use a provider with an API; You.com answers 100 a day without a key. Whichever you pick, a failed search falls back to DuckDuckGo with a note.",
        )}
      </p>
      <Field label={t("Provider")}>
        <div className="flex flex-wrap gap-1.5">
          {s.providers.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => setProvider(p.id as typeof provider)}
              className={chip(provider === p.id)}
            >
              {p.label}
            </button>
          ))}
        </div>
      </Field>
      {takesKey && (
        <Field
          label={needsKey ? t("API key") : t("API key (optional)")}
          hint={
            s.key_source === "vault" && s.provider === provider
              ? t("A key is in the vault. Leave blank to keep it.")
              : needsKey
                ? t("Stored encrypted in the vault as SEARCH_API_KEY.")
                : t(
                    "Without a key, 100 searches a day. A key raises the limit; stored encrypted in the vault as SEARCH_API_KEY.",
                  )
          }
        >
          <input
            type="password"
            value={key}
            onChange={(ev) => setKey(ev.target.value)}
            aria-label={t("API key")}
            className={inputCls}
            placeholder={
              s.key_source === "vault" && s.provider === provider
                ? "••••••••"
                : "…"
            }
            autoComplete="off"
          />
          {chosen?.keys_url && (
            <a
              href={chosen.keys_url}
              target="_blank"
              rel="noreferrer"
              className="mt-1 inline-block text-[12px] text-accent"
            >
              {t("Get a key from {label}", { label: chosen.label })}
            </a>
          )}
        </Field>
      )}
      {provider === "searxng" && (
        <Field
          label={t("Instance URL")}
          hint={t(
            "A SearXNG you run, with the JSON format enabled in its settings.",
          )}
        >
          <input
            value={baseUrl}
            onChange={(ev) => setBaseUrl(ev.target.value)}
            className={inputCls}
            placeholder="http://127.0.0.1:8080"
            inputMode="url"
          />
        </Field>
      )}
      <div className="flex gap-2 pt-1">
        <button
          type="button"
          disabled={saving || !canSave}
          onClick={() => void save()}
          className={primaryBtn}
        >
          {saving ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Check size={16} />
          )}{" "}
          {t("Save")}
        </button>
        <button
          type="button"
          disabled={testing}
          onClick={() => void runTest()}
          className={secondaryBtn}
        >
          {testing ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Plug size={16} />
          )}{" "}
          {t("Test")}
        </button>
      </div>
      {test && (
        <TestLine
          result={test}
          okText={t("{provider} · {n} results · {ms} ms", {
            provider: test.provider ?? "",
            n: test.results ?? 0,
            ms: test.ms ?? 0,
          })}
        />
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ email
const MAIL_PRESETS: Array<{
  id: string;
  label: string;
  imap: string;
  smtp: string;
  port: number;
  starttls: boolean;
}> = [
  {
    id: "gmail",
    label: "Gmail",
    imap: "imap.gmail.com",
    smtp: "smtp.gmail.com",
    port: 587,
    starttls: true,
  },
  {
    id: "outlook",
    label: "Outlook",
    imap: "outlook.office365.com",
    smtp: "smtp.office365.com",
    port: 587,
    starttls: true,
  },
  {
    id: "icloud",
    label: "iCloud",
    imap: "imap.mail.me.com",
    smtp: "smtp.mail.me.com",
    port: 587,
    starttls: true,
  },
  {
    id: "qq",
    label: "QQ",
    imap: "imap.qq.com",
    smtp: "smtp.qq.com",
    port: 587,
    starttls: true,
  },
  {
    id: "163",
    label: "163",
    imap: "imap.163.com",
    smtp: "smtp.163.com",
    port: 465,
    starttls: false,
  },
  {
    id: "fastmail",
    label: "Fastmail",
    imap: "imap.fastmail.com",
    smtp: "smtp.fastmail.com",
    port: 587,
    starttls: true,
  },
];

function useAiSetupLauncher(data: ConnectionsData) {
  const { state, dispatch, setTab, send, dismissOnboarding, toast } = useStore();
  const t = useT();
  const llm = data.llm;
  const providers = data.providers ?? {};
  const modelName = llm?.model || state?.settings?.llm?.model || "";
  const shortModel = modelName ? (modelName.split("/").pop() || modelName) : "MiniMuse";

  const launch = async (area: "gmail" | "calendar" | "contacts", compact?: boolean) => {
    const modelReady =
      llm?.key_source === "vault" ||
      llm?.key_source === "config" ||
      !!llm?.cloud ||
      !!llm?.endpoint ||
      !!providers[Object.keys(providers).find((k) => providers[k]?.no_key) || ""];
    if (!modelReady && !llm?.from_app) {
      toast(t("Set up your AI chat model first so it can guide you."));
      return;
    }
    const prompts: Record<"gmail" | "calendar" | "contacts", string> = {
      gmail:
        "Help me connect my Gmail / Email account to MiniMuse. Explain both the quick Google App Password popup flow (without ever reading my password directly) and optional Google OAuth + IMAP/SMTP setup, and walk me through each step interactively.",
      calendar:
        "Help me connect and sync my Google Calendar (or other calendar) to MiniMuse using either Google Sync or my private .ics calendar link. Walk me through step by step.",
      contacts:
        "Help me connect and sync my Google Contacts (or other address book) to MiniMuse using either Google Sync or exporting/uploading a .vcf file. Walk me through step by step.",
    };
    const titles: Record<"gmail" | "calendar" | "contacts", string> = {
      gmail: t("Mail Setup Assistant"),
      calendar: t("Calendar Setup Assistant"),
      contacts: t("Contacts Setup Assistant"),
    };
    if (compact) {
      try {
        await api.onboarded(true);
      } catch {
        /* ok */
      }
      dismissOnboarding();
    }
    dispatch({ type: "draftThread", title: titles[area] });
    dispatch({ type: "activeThread", thread: DRAFT_THREAD });
    setTab("chat");
    try {
      await send(DRAFT_THREAD, prompts[area]);
    } catch {
      /* send handles offline/error toasts */
    }
  };

  return { launch, shortModel };
}

function openCenteredPopup(url: string, name: string) {
  const w = 560;
  const h = 700;
  const left = Math.max(0, Math.round((window.screen?.width ?? 1280) / 2 - w / 2));
  const top = Math.max(0, Math.round((window.screen?.height ?? 800) / 2 - h / 2));
  window.open(url, name, `popup=yes,width=${w},height=${h},left=${left},top=${top},noopener,noreferrer`);
}

export async function syncPhoneGoogleToServer() {
  const payload = await requestPhoneCalendarAndContactsSync();
  if (payload.ics && /BEGIN:VCALENDAR/i.test(payload.ics)) {
    await api.importCalendar(payload.calendar_name || "Phone & Google Calendar", payload.ics);
  }
  if (payload.vcf && /BEGIN:VCARD/i.test(payload.vcf)) {
    await api.importContacts(payload.contacts_name || "Phone & Google Contacts", payload.vcf);
  }
  return payload;
}

function detectMailPresetId(email: string): string | null {
  const dom = (email.split("@")[1] || "").trim().toLowerCase();
  if (!dom) return null;
  if (dom === "gmail.com" || dom === "googlemail.com") return "gmail";
  if (dom === "outlook.com" || dom === "hotmail.com" || dom === "live.com" || dom === "msn.com") return "outlook";
  if (dom === "icloud.com" || dom === "me.com" || dom === "mac.com") return "icloud";
  if (dom === "qq.com" || dom === "foxmail.com") return "qq";
  if (dom === "163.com") return "163";
  if (dom === "fastmail.com" || dom === "fastmail.fm") return "fastmail";
  return null;
}

export function EmailCard({
  data,
  onChange,
  compact,
}: {
  data: ConnectionsData;
  onChange: () => void;
  compact?: boolean;
}) {
  const { toast } = useStore();
  const t = useT();
  const { launch: launchAiSetup, shortModel } = useAiSetupLauncher(data);
  const e = data.email;
  const g = data.google;
  const accounts: EmailAccount[] = data.email_accounts ?? [];
  const googleAccounts = g?.accounts ?? (g?.signed_in && g.email ? [{ email: g.email, scopes: [], primary: true }] : []);

  const [open, setOpen] = useState(!!compact);
  const [editingAccountId, setEditingAccountId] = useState("");
  const [label, setLabel] = useState("");
  const [address, setAddress] = useState(e.address);
  const [password, setPassword] = useState("");
  const [imap, setImap] = useState(e.imap_host);
  const [imapPort, setImapPort] = useState(e.imap_port);
  const [smtp, setSmtp] = useState(e.smtp_host);
  const [smtpPort, setSmtpPort] = useState(e.smtp_port);
  const [starttls, setStarttls] = useState(e.smtp_starttls);
  const [makePrimary, setMakePrimary] = useState(true);
  const [busy, setBusy] = useState<"save" | "test" | "off" | "gauth" | "gsync" | "phonesync" | "gapis" | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);
  const [oauthOpen, setOauthOpen] = useState(false);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");
  const [googleTip, setGoogleTip] = useState(false);
  const [showServerSettings, setShowServerSettings] = useState(false);
  const [showAdvancedGoogle, setShowAdvancedGoogle] = useState(false);
  const hasPhoneSync = !!androidApp()?.syncPhoneCalendarAndContacts;

  useEffect(() => {
    if (!editingAccountId) {
      if (e.address) {
        setAddress(e.address);
      } else {
        const detected = deviceGoogleEmails()[0];
        if (detected) {
          setAddress(detected);
          const gmail = MAIL_PRESETS.find((p) => p.id === "gmail");
          if (gmail && !e.imap_host) {
            setImap(gmail.imap);
            setImapPort(993);
            setSmtp(gmail.smtp);
            setSmtpPort(gmail.port);
            setStarttls(gmail.starttls);
          }
        }
      }
      if (e.imap_host) setImap(e.imap_host);
      setImapPort(e.imap_port);
      if (e.smtp_host) setSmtp(e.smtp_host);
      setSmtpPort(e.smtp_port);
      setStarttls(e.smtp_starttls);
    }
  }, [e, editingAccountId]);

  const loadAccountIntoForm = (acct: EmailAccount) => {
    setEditingAccountId(acct.id);
    setLabel(acct.label || "");
    setAddress(acct.address);
    setPassword("");
    setImap(acct.imap_host);
    setImapPort(acct.imap_port);
    setSmtp(acct.smtp_host);
    setSmtpPort(acct.smtp_port);
    setStarttls(acct.smtp_starttls);
    setMakePrimary(acct.primary);
  };

  const resetForNewAccount = () => {
    setEditingAccountId("__new__");
    setLabel("");
    setAddress("");
    setPassword("");
    setImap("");
    setImapPort(993);
    setSmtp("");
    setSmtpPort(587);
    setStarttls(true);
    setMakePrimary(accounts.length === 0);
    setTest(null);
  };

  const applyPreset = (p: (typeof MAIL_PRESETS)[number]) => {
    setImap(p.imap);
    setImapPort(993);
    setSmtp(p.smtp);
    setSmtpPort(p.port);
    setStarttls(p.starttls);
  };

  const runPhoneSync = async () => {
    setBusy("phonesync");
    try {
      const payload = await syncPhoneGoogleToServer();
      const firstEmail = payload.emails?.[0];
      if (firstEmail && !address.trim()) {
        setAddress(firstEmail);
        const gmail = MAIL_PRESETS.find((p) => p.id === "gmail");
        if (gmail) applyPreset(gmail);
      }
      toast(
        t("Synced from phone: {cal} calendar events, {con} contacts", {
          cal: payload.events ?? 0,
          con: payload.contacts ?? 0,
        }),
      );
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const enableGoogleWorkspaceApis = async () => {
    setBusy("gapis");
    try {
      await api.googleEnableApis();
      toast(t("Enabled Gmail, Google Calendar, Tasks & Drive APIs!"));
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const startGoogleAppPasswordPopup = () => {
    const gmailPreset = MAIL_PRESETS.find((p) => p.id === "gmail");
    if (gmailPreset) applyPreset(gmailPreset);
    setGoogleTip(true);
    openCenteredPopup("https://myaccount.google.com/apppasswords", "minimuse_google_apppw");
  };

  const startGoogleOAuthPopup = async () => {
    setBusy("gauth");
    try {
      const res = await api.googleOAuthStart({
        client_id: clientId.trim() || undefined,
        client_secret: clientSecret.trim() || undefined,
      });
      openCenteredPopup(res.auth_url, "minimuse_google_oauth");
      toast(t("Complete sign-in in the Google popup window"));
      const knownCount = googleAccounts.length;
      let attempts = 0;
      const timer = window.setInterval(() => {
        attempts += 1;
        api
          .googleStatus()
          .then((st) => {
            const nextCount = st.accounts?.length ?? (st.signed_in ? 1 : 0);
            if (!st.pending && (st.signed_in || nextCount > knownCount)) {
              window.clearInterval(timer);
              setBusy(null);
              toast(t("Connected to Google as {email}", { email: st.email }));
              onChange();
            } else if (!st.pending || attempts >= 80) {
              window.clearInterval(timer);
              setBusy(null);
              if (st.auth_error) toast(st.auth_error);
            }
          })
          .catch(() => {
            if (attempts >= 80) {
              window.clearInterval(timer);
              setBusy(null);
            }
          });
      }, 1500);
    } catch (err) {
      setBusy(null);
      toast((err as Error).message);
    }
  };

  const finishGoogleCallback = async () => {
    if (!callbackUrl.trim()) return;
    setBusy("gauth");
    try {
      const st = await api.googleOAuthCallback(callbackUrl.trim());
      setCallbackUrl("");
      toast(t("Connected to Google as {email}", { email: st.email }));
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const runGoogleSync = async (acctEmail?: string) => {
    setBusy("gsync");
    try {
      const res = await api.googleSync(acctEmail);
      toast(
        res.ok
          ? t("Synced Google: {cal} calendar events, {con} contacts", {
              cal: res.calendar_events ?? 0,
              con: res.contacts ?? 0,
            })
          : (res.errors?.[0] ?? t("Google sync finished with warnings")),
      );
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const logoutGoogle = async (acctEmail?: string) => {
    setBusy("off");
    try {
      await api.googleLogout(acctEmail);
      toast(acctEmail ? t("Disconnected {email}", { email: acctEmail }) : t("Signed out of Google"));
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const connect = async () => {
    setBusy("save");
    setTest(null);
    try {
      const list = await api.upsertEmailAccount({
        id: editingAccountId && editingAccountId !== "__new__" ? editingAccountId : undefined,
        label: label.trim() || undefined,
        address: address.trim(),
        password: password || undefined,
        imap_host: imap.trim(),
        imap_port: imapPort,
        smtp_host: smtp.trim(),
        smtp_port: smtpPort,
        smtp_starttls: starttls,
        primary: makePrimary,
        enabled: true,
      });
      const saved = list.find((a) => a.address.toLowerCase() === address.trim().toLowerCase()) ?? list[0];
      setPassword("");
      setEditingAccountId("");
      const result = saved ? await api.testEmailAccount(saved.id) : await api.testEmail();
      setTest(result);
      toast(result.ok ? t("Email account connected") : t("Saved, but the test failed"));
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const setAccountPrimary = async (acct: EmailAccount) => {
    setBusy("save");
    try {
      await api.upsertEmailAccount({ id: acct.id, address: acct.address, primary: true });
      toast(t("{email} set as primary mailbox", { email: acct.address }));
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const removeExtraAccount = async (acct: EmailAccount) => {
    setBusy("off");
    try {
      await api.removeEmailAccount(acct.id);
      toast(t("Removed {email}", { email: acct.address }));
      if (editingAccountId === acct.id) setEditingAccountId("");
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const testExtraAccount = async (acct: EmailAccount) => {
    setBusy("test");
    try {
      const res = await api.testEmailAccount(acct.id);
      setTest(res);
      toast(res.ok ? t("{email} verified", { email: acct.address }) : t("Test failed for {email}", { email: acct.address }));
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const disconnect = async () => {
    setBusy("off");
    try {
      await api.disconnectEmail();
      setTest(null);
      toast(t("Email disconnected"));
      onChange();
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const totalMailboxes = Math.max(accounts.length, e.configured ? 1 : 0);
  const status =
    (e.enabled && e.configured) || accounts.some((a) => a.enabled)
      ? { text: totalMailboxes > 1 ? t("{n} mailboxes", { n: totalMailboxes }) : t("Connected"), tone: "ok" }
      : e.enabled
        ? { text: t("Incomplete"), tone: "warn" }
        : { text: t("Not connected"), tone: "off" };

  return (
    <Card
      icon={<Mail size={19} />}
      title={t("Email & Google Accounts")}
      summary={
        totalMailboxes > 0 || googleAccounts.length > 0
          ? t("{m} email · {g} Google connected", { m: totalMailboxes, g: googleAccounts.length })
          : t("Connect multiple Email & Google accounts (reads & drafts)")
      }
      status={status}
      open={open}
      onToggle={compact ? undefined : () => setOpen(!open)}
    >
      <p className="text-[12.5px] text-muted -mt-1">
        {t(
          "Connect as many Google and Email accounts as you need. Reading is automatic; sending always asks you first.",
        )}
      </p>

      {/* Multiple Google Accounts */}
      <div className="rounded-2xl border border-border/70 bg-soft/40 p-3 space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-[12.5px] font-medium text-fg flex items-center gap-1.5">
            <Globe size={14} className="text-accent" />
            {googleAccounts.length > 0
              ? t("Google accounts ({n})", { n: googleAccounts.length })
              : t("Easy Gmail & Google setup")}
          </div>
          <button
            type="button"
            onClick={() => void launchAiSetup("gmail", compact)}
            className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2.5 py-1 text-[11.5px] font-medium text-accent hover:bg-accent/15"
          >
            <Wand2 size={12} /> {t("Let {model} set this up", { model: shortModel })}
          </button>
        </div>

        {googleAccounts.length > 0 && (
          <div className="space-y-1.5">
            {googleAccounts.map((ga) => (
              <div key={ga.email} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border/60 bg-surface px-2.5 py-2 text-[12px]">
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 font-medium">
                    <span className="truncate">{ga.email}</span>
                    {ga.primary && <span className="rounded-full bg-accent/12 px-1.5 py-0.2 text-[10px] font-semibold text-accent">{t("Primary")}</span>}
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void runGoogleSync(ga.email)}
                    className="rounded-lg border border-border px-2 py-1 text-[11px] hover:bg-surface-2"
                  >
                    <RefreshCw size={11} className="inline mr-1" />{t("Sync")}
                  </button>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void logoutGoogle(ga.email)}
                    className="rounded-lg border border-border px-2 py-1 text-[11px] text-rose-600 hover:bg-surface-2"
                  >
                    {t("Disconnect")}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {hasPhoneSync && (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void runPhoneSync()}
                className={primaryBtn}
              >
                {busy === "phonesync" ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />}{" "}
                {t("Sync Google Calendar & Contacts from Phone (1 Tap)")}
              </button>
            )}
            {googleAccounts.length > 0 && (
              <>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void runGoogleSync()}
                  className={primaryBtn}
                >
                  {busy === "gsync" ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}{" "}
                  {t("Sync All Google")}
                </button>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void enableGoogleWorkspaceApis()}
                  className={secondaryBtn}
                >
                  {busy === "gapis" ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />}{" "}
                  {t("Enable All Google APIs")}
                </button>
              </>
            )}
            <button
              type="button"
              onClick={startGoogleAppPasswordPopup}
              className={googleAccounts.length > 0 || hasPhoneSync ? secondaryBtn : primaryBtn}
            >
              <ExternalLink size={15} /> {t("Get 16-Char Gmail App Password")}
            </button>
            <button
              type="button"
              onClick={() => setOauthOpen(!oauthOpen)}
              className={secondaryBtn}
            >
              <LogIn size={15} /> {oauthOpen ? t("Hide Google OAuth") : googleAccounts.length > 0 ? t("Add Google Account (OAuth)") : t("Google OAuth Sign-In")}
            </button>
          </div>
          {googleTip && (
            <div className="rounded-xl bg-panel/80 border border-border/60 px-3 py-2 text-[12px] text-muted leading-relaxed">
              {t("Type your @gmail.com address below, create/copy the 16-character code from Google, paste it into App Password (spaces are stripped automatically), and tap Connect.")}
            </div>
          )}
          {oauthOpen && (
            <div className="rounded-xl border border-border/70 bg-panel p-2.5 space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-[11.5px] text-muted leading-snug flex-1">
                  {g?.has_client_id
                    ? t("Google OAuth Client ID is ready ({id}). Click Sign in with Google Popup to auto-sync Gmail, Calendar, Contacts, Drive & Tasks.", { id: g.client_id_preview || "vault" })
                    : t("For full Google OAuth + 1-click Calendar, Contacts, Gmail, Drive & Tasks API sync, create a Desktop OAuth Client ID once:")}
                </p>
                {showAdvancedGoogle && !g?.has_client_id && (
                  <a
                    href="https://console.cloud.google.com/apis/credentials"
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 rounded-full bg-accent/12 px-2.5 py-1 text-[11px] font-medium text-accent hover:bg-accent/20"
                  >
                    <ExternalLink size={11} /> {t("Create OAuth Client ID")}
                  </a>
                )}
              </div>
              {showAdvancedGoogle && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                  <input
                    value={clientId}
                    onChange={(ev) => setClientId(ev.target.value)}
                    placeholder={t("Google OAuth Client ID (optional override)")}
                    className={cx(inputCls, "py-1.5 text-[12px]")}
                    autoCapitalize="off"
                  />
                  <input
                    value={clientSecret}
                    onChange={(ev) => setClientSecret(ev.target.value)}
                    type="password"
                    placeholder={t("Client Secret (optional)")}
                    className={cx(inputCls, "py-1.5 text-[12px]")}
                    autoComplete="off"
                  />
                </div>
              )}
              <button
                type="button"
                onClick={() => setShowAdvancedGoogle((v) => !v)}
                className="text-[11.5px] text-muted hover:text-fg underline"
              >
                {showAdvancedGoogle ? t("Hide advanced") : t("Advanced: use your own Google Cloud project")}
              </button>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy === "gauth" || (!g?.has_client_id && !clientId.trim())}
                  onClick={() => void startGoogleOAuthPopup()}
                  className={primaryBtn}
                >
                  {busy === "gauth" ? <Loader2 size={15} className="animate-spin" /> : <LogIn size={15} />}{" "}
                  {t("Sign in with Google Popup")}
                </button>
              </div>
              {busy === "gauth" && (
                <div className="flex gap-1.5 pt-1">
                  <input
                    value={callbackUrl}
                    onChange={(ev) => setCallbackUrl(ev.target.value)}
                    placeholder={t("Or paste the http://127.0.0.1 redirect URL here")}
                    className={cx(inputCls, "py-1.5 text-[12px]")}
                  />
                  <button
                    type="button"
                    disabled={!callbackUrl.trim()}
                    onClick={() => void finishGoogleCallback()}
                    className={secondaryBtn}
                  >
                    {t("Complete")}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Connected Multi-Account Mailboxes */}
      {accounts.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-[11.5px] font-semibold uppercase tracking-wide text-muted">
              {t("Saved email accounts ({n})", { n: accounts.length })}
            </span>
            <button type="button" onClick={resetForNewAccount} className="inline-flex items-center gap-1 text-[12px] font-medium text-accent hover:underline">
              <Plus size={13} /> {t("Add another email account")}
            </button>
          </div>
          {accounts.map((acct) => (
            <div key={acct.id} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-border/70 bg-surface p-2.5 text-[12.5px]">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 font-medium">
                  <span className="truncate">{acct.label || acct.address}</span>
                  {acct.primary && <span className="rounded-full bg-accent/12 px-1.5 py-0.2 text-[10px] font-semibold text-accent">{t("Primary")}</span>}
                </div>
                <div className="truncate text-[11px] text-muted">{acct.address} · {acct.imap_host}</div>
              </div>
              <div className="flex flex-wrap items-center gap-1">
                {!acct.primary && (
                  <button type="button" onClick={() => void setAccountPrimary(acct)} className="rounded-lg border border-border px-2 py-1 text-[11px] hover:bg-surface-2">
                    {t("Primary")}
                  </button>
                )}
                <button type="button" onClick={() => void testExtraAccount(acct)} className="rounded-lg border border-border px-2 py-1 text-[11px] hover:bg-surface-2">
                  {t("Test")}
                </button>
                <button type="button" onClick={() => loadAccountIntoForm(acct)} className="rounded-lg border border-border px-2 py-1 text-[11px] hover:bg-surface-2">
                  {t("Edit")}
                </button>
                <button type="button" onClick={() => void removeExtraAccount(acct)} aria-label={t("Remove")} className="rounded-lg border border-border p-1 text-muted hover:text-rose-600">
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <Field label={t("Provider")}>
        <div className="flex flex-wrap gap-1.5">
          {MAIL_PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => applyPreset(p)}
              className={cx(
                "rounded-full px-3 py-1.5 text-[13px] border",
                imap === p.imap
                  ? "border-accent bg-accent/10 text-accent font-medium"
                  : "border-border text-muted",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </Field>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Field label={t("Account label (optional)")}>
          <input
            value={label}
            onChange={(ev) => setLabel(ev.target.value)}
            className={inputCls}
            placeholder={t("e.g. Work or Personal")}
          />
        </Field>
        <Field label={t("Address")}>
          <input
            value={address}
            onChange={(ev) => {
              const nextAddr = ev.target.value;
              setAddress(nextAddr);
              const detectedId = detectMailPresetId(nextAddr);
              if (detectedId) {
                const preset = MAIL_PRESETS.find((p) => p.id === detectedId);
                if (preset) applyPreset(preset);
              }
            }}
            className={inputCls}
            placeholder="you@gmail.com"
            inputMode="email"
            autoComplete="off"
          />
        </Field>
      </div>
      <Field
        label={imap === "imap.gmail.com" ? t("Gmail App Password (16 characters)") : t("Password")}
        hint={
          editingAccountId && editingAccountId !== "__new__"
            ? t("Leave blank to keep this account's saved password.")
            : e.password_set && !editingAccountId
              ? t("A password is in the vault. Leave blank to keep it.")
              : imap === "imap.gmail.com"
                ? t("Spaces are stripped automatically. Stored encrypted in your local vault.")
                : t("Stored encrypted in your local vault.")
        }
      >
        <input
          type="password"
          value={password}
          onChange={(ev) => {
            const raw = ev.target.value;
            setPassword(imap === "imap.gmail.com" ? raw.replace(/\s+/g, "") : raw);
          }}
          className={inputCls}
          placeholder={e.password_set && !editingAccountId ? "••••••••" : imap === "imap.gmail.com" ? t("Paste 16-char Google App Password") : t("app password")}
          autoComplete="off"
        />
      </Field>
      {imap && smtp && !showServerSettings ? (
        <div className="flex items-center justify-between rounded-xl bg-surface-2/50 px-3 py-1.5 text-[11.5px] text-muted">
          <span className="truncate">{t("Server auto-configured: {imap} · {smtp}", { imap, smtp })}</span>
          <button
            type="button"
            onClick={() => setShowServerSettings(true)}
            className="shrink-0 font-medium text-accent hover:underline"
          >
            {t("Edit servers")}
          </button>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-[1fr_84px] gap-2">
            <Field label={t("IMAP server")}>
              <input
                value={imap}
                onChange={(ev) => setImap(ev.target.value)}
                className={inputCls}
                placeholder="imap.example.com"
              />
            </Field>
            <Field label={t("Port")}>
              <input
                type="number"
                value={imapPort}
                onChange={(ev) => setImapPort(Number(ev.target.value))}
                className={inputCls}
              />
            </Field>
            <Field label={t("SMTP server")}>
              <input
                value={smtp}
                onChange={(ev) => setSmtp(ev.target.value)}
                className={inputCls}
                placeholder="smtp.example.com"
              />
            </Field>
            <Field label={t("Port")}>
              <input
                type="number"
                value={smtpPort}
                onChange={(ev) => setSmtpPort(Number(ev.target.value))}
                className={inputCls}
              />
            </Field>
          </div>
          <div className="flex flex-wrap items-center gap-4 text-[13px]">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={starttls}
                onChange={(ev) => setStarttls(ev.target.checked)}
                className="accent-[var(--om-accent)]"
              />{" "}
              {t("STARTTLS for SMTP (off for port 465)")}
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={makePrimary}
                onChange={(ev) => setMakePrimary(ev.target.checked)}
                className="accent-[var(--om-accent)]"
              />{" "}
              {t("Set as primary sender")}
            </label>
          </div>
        </>
      )}
      <div className="flex flex-wrap gap-2 pt-1">
        <button
          type="button"
          disabled={
            busy !== null ||
            !address.trim() ||
            !imap.trim() ||
            !smtp.trim() ||
            (!password && (!e.password_set || editingAccountId === "__new__") && e.auth_mode !== "oauth")
          }
          onClick={() => void connect()}
          className={primaryBtn}
        >
          {busy === "save" ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Plug size={16} />
          )}{" "}
          {editingAccountId === "__new__" ? t("Add Email Account") : e.configured ? t("Save & test") : t("Connect")}
        </button>
        {editingAccountId && (
          <button
            type="button"
            onClick={() => setEditingAccountId("")}
            className={secondaryBtn}
          >
            {t("Cancel edit")}
          </button>
        )}
        {e.enabled && accounts.length === 0 && (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void disconnect()}
            className={secondaryBtn}
          >
            {busy === "off" ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Unplug size={16} />
            )}{" "}
            {t("Disconnect")}
          </button>
        )}
      </div>
      {test && (
        <TestLine
          result={test}
          okText={t("Signed in · {n} messages in the inbox", {
            n: test.inbox ?? "?",
          })}
        />
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ calendar
/** Where the private .ics link hides, per provider — the one thing people get stuck on. */
const CALENDAR_HINTS: Array<{ id: string; label: string; hint: string }> = [
  {
    id: "google",
    label: "Google",
    hint: "Settings → your calendar → Integrate calendar → Secret address in iCal format",
  },
  {
    id: "outlook",
    label: "Outlook",
    hint: "Settings → Calendar → Shared calendars → Publish a calendar → ICS link",
  },
  {
    id: "icloud",
    label: "iCloud",
    hint: "Share Calendar → Public Calendar → copy the webcal:// link",
  },
  {
    id: "fastmail",
    label: "Fastmail",
    hint: "Settings → Calendars → Export → Calendar URL",
  },
  {
    id: "file",
    label: ".ics file",
    hint: "A path on the machine where MiniMuse runs, for example ~/calendar.ics",
  },
];

export function CalendarCard({
  data,
  onChange,
  compact,
}: {
  data: ConnectionsData;
  onChange: () => void;
  compact?: boolean;
}) {
  const { toast } = useStore();
  const t = useT();
  const { launch: launchAiSetup, shortModel } = useAiSetupLauncher(data);
  const c = data.calendar;
  const g = data.google;
  const [open, setOpen] = useState(!!compact);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [hint, setHint] = useState<string | null>(null);
  const [dayStart, setDayStart] = useState(c.day_start);
  const [dayEnd, setDayEnd] = useState(c.day_end);
  const [busy, setBusy] = useState<string | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);

  useEffect(() => {
    setDayStart(c.day_start);
    setDayEnd(c.day_end);
  }, [c.day_start, c.day_end]);

  const add = async () => {
    setBusy("add");
    setTest(null);
    try {
      const view = await api.addCalendarFeed(name.trim(), url.trim());
      if (view.error) {
        toast(
          t("Added, but it could not be read: {error}", { error: view.error }),
        );
      } else {
        const feed = view.feeds.find((f) => f.name === name.trim());
        toast(`${t("Calendar connected")} · ${evs(feed?.events ?? 0)}`);
        setName("");
        setUrl("");
        setAdding(false);
      }
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const remove = async (n: string) => {
    setBusy(n);
    try {
      await api.removeCalendarFeed(n);
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const saveHours = async () => {
    setBusy("hours");
    try {
      await api.setCalendar({ day_start: dayStart, day_end: dayEnd });
      toast(t("Saved"));
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const runTest = async () => {
    setBusy("test");
    try {
      setTest(await api.testCalendar());
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const total = c.feeds.reduce((n, f) => n + f.events, 0);
  const broken = c.feeds.filter((f) => f.error).length;
  const evs = (n: number) => (n === 1 ? t("1 event") : t("{n} events", { n }));
  const status = c.configured
    ? broken
      ? { text: t("{n} not reading", { n: broken }), tone: "warn" }
      : { text: t("Connected"), tone: "ok" }
    : { text: t("Not connected"), tone: "off" };

  return (
    <Card
      icon={<CalendarDays size={19} />}
      title={t("Calendar")}
      summary={
        !c.configured
          ? t("Your agenda, free time, and events it can draft")
          : c.feeds.length === 1
            ? `${c.feeds[0].name} · ${evs(total)}`
            : `${t("{n} calendars", { n: c.feeds.length })} · ${evs(total)}`
      }
      status={status}
      open={open}
      onToggle={compact ? undefined : () => setOpen(!open)}
    >
      <p className="text-[12.5px] text-muted -mt-1">
        {t(
          "Reads your calendar from its private .ics link; the link stays in the vault. It never changes your calendar; an event it proposes comes as a file you add with a tap.",
        )}
      </p>
      {c.feeds.length > 0 && (
        <ul className="space-y-1.5">
          {c.feeds.map((f) => (
            <li
              key={f.name}
              className="flex items-center gap-2.5 rounded-2xl bg-surface-2/60 px-3 py-2"
            >
              <span
                className={cx(
                  "h-2 w-2 rounded-full",
                  f.error
                    ? "bg-rose-500"
                    : f.fetched_at
                      ? "bg-emerald-500"
                      : "bg-amber-500",
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-medium truncate">
                  {f.name}
                </div>
                <div className="text-[11.5px] text-muted truncate">
                  {f.error
                    ? f.error
                    : f.fetched_at
                      ? `${evs(f.events)} · ${t("read {when}", { when: relativeTime(f.fetched_at) })}`
                      : t("not read yet")}
                  {!f.from_app && ` · ${t("from config.toml")}`}
                </div>
              </div>
              {f.from_app && (
                <button
                  type="button"
                  aria-label={t("Remove")}
                  disabled={busy === f.name}
                  onClick={() => void remove(f.name)}
                  className="p-1.5 rounded-full text-muted hover:bg-surface-2"
                >
                  {busy === f.name ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <Trash2 size={15} />
                  )}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <div className="space-y-2.5 rounded-2xl border border-border/70 p-3">
          <Field label={t("Where is the link?")}>
            <div className="flex flex-wrap gap-1.5">
              {CALENDAR_HINTS.map((h) => (
                <button
                  key={h.id}
                  type="button"
                  onClick={() => setHint(hint === h.id ? null : h.id)}
                  className={cx(
                    "rounded-full px-3 py-1.5 text-[13px] border",
                    hint === h.id
                      ? "border-accent bg-accent/10 text-accent font-medium"
                      : "border-border text-muted",
                  )}
                >
                  {t(h.label)}
                </button>
              ))}
            </div>
            {hint && (
              <div className="mt-1.5 text-[11.5px] text-muted leading-snug">
                {t(CALENDAR_HINTS.find((h) => h.id === hint)!.hint)}
              </div>
            )}
          </Field>
          <Field label={t("Name")}>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={inputCls}
              placeholder={t("Work")}
            />
          </Field>
          <Field
            label={t("Private .ics link or file path")}
            hint={
              t("Stored encrypted in the vault as CALENDAR_{name}.", {
                name:
                  name
                    .trim()
                    .toUpperCase()
                    .replace(/[^A-Z0-9]+/g, "_")
                    .replace(/^_+|_+$/g, "") || "NAME",
              }) +
              " " +
              t("MiniMuse fetches whatever address you put here, from this machine; only paste links you trust.")
            }
          >
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className={inputCls}
              placeholder="https://calendar.google.com/calendar/ical/…/basic.ics"
              inputMode="url"
              autoComplete="off"
            />
          </Field>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy === "add" || !name.trim() || !url.trim()}
              onClick={() => void add()}
              className={primaryBtn}
            >
              {busy === "add" ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Plug size={16} />
              )}{" "}
              {t("Connect")}
            </button>
            <button
              type="button"
              onClick={() => setAdding(false)}
              className={secondaryBtn}
            >
              {t("Cancel")}
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {!!androidApp()?.syncPhoneCalendarAndContacts && (
            <button
              type="button"
              disabled={busy === "phonesync"}
              onClick={async () => {
                setBusy("phonesync");
                try {
                  const res = await syncPhoneGoogleToServer();
                  toast(t("Synced {n} Google & phone events!", { n: res.events ?? 0 }));
                  onChange();
                } catch (err) {
                  toast((err as Error).message);
                } finally {
                  setBusy(null);
                }
              }}
              className={primaryBtn}
            >
              {busy === "phonesync" ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />}{" "}
              {t("Sync Google Calendar from Phone (1 Tap)")}
            </button>
          )}
          <button
            type="button"
            onClick={() => setAdding(true)}
            className={secondaryBtn}
          >
            <Plus size={16} /> {t("Add a calendar")}
          </button>
          <button
            type="button"
            onClick={() => {
              openCenteredPopup("https://calendar.google.com/calendar/u/0/r/settings", "minimuse_gcal");
              setName((n) => n || "Google Calendar");
              setHint("google");
              setAdding(true);
            }}
            className={secondaryBtn}
          >
            <ExternalLink size={15} /> {t("Google Calendar Popup")}
          </button>
          {(g?.signed_in || c.feeds.length > 0) && (
            <button
              type="button"
              disabled={busy === "sync"}
              onClick={async () => {
                setBusy("sync");
                try {
                  const res = await api.syncCalendar();
                  setTest(res);
                  toast(res.ok ? t("Calendar synced") : (res.error ?? t("Calendar sync failed")));
                  onChange();
                } catch (err) {
                  toast((err as Error).message);
                } finally {
                  setBusy(null);
                }
              }}
              className={secondaryBtn}
            >
              {busy === "sync" ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}{" "}
              {t("Sync now")}
            </button>
          )}
          <button
            type="button"
            onClick={() => void launchAiSetup("calendar", compact)}
            className="inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent/10 px-3 py-1.5 text-[12.5px] font-medium text-accent hover:bg-accent/15"
          >
            <Wand2 size={14} /> {t("Let {model} set this up", { model: shortModel })}
          </button>
        </div>
      )}
      {c.configured && (
        <>
          <Field
            label={t("Working hours")}
            hint={t("Free time is looked for inside these hours.")}
          >
            <div className="flex items-center gap-2">
              <input
                type="time"
                value={dayStart}
                onChange={(e) => setDayStart(e.target.value)}
                aria-label={t("Working hours start")}
                className={cx(inputCls, "w-auto")}
              />
              <span className="text-muted">{t("to")}</span>
              <input
                type="time"
                value={dayEnd}
                onChange={(e) => setDayEnd(e.target.value)}
                aria-label={t("Working hours end")}
                className={cx(inputCls, "w-auto")}
              />
              {(dayStart !== c.day_start || dayEnd !== c.day_end) && (
                <button
                  type="button"
                  disabled={busy === "hours"}
                  onClick={() => void saveHours()}
                  className={cx(primaryBtn, "px-3")}
                  aria-label={t("Save")}
                >
                  {busy === "hours" ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : (
                    <Check size={16} />
                  )}
                </button>
              )}
            </div>
          </Field>
          <button
            type="button"
            disabled={busy === "test"}
            onClick={() => void runTest()}
            className={secondaryBtn}
          >
            {busy === "test" ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <RefreshCw size={16} />
            )}{" "}
            {t("Read again")}
          </button>
          {test && (
            <TestLine
              result={test}
              okText={
                c.feeds.length === 1
                  ? evs(test.events ?? 0)
                  : `${evs(test.events ?? 0)} · ${t("{n} calendars", { n: c.feeds.length })}`
              }
            />
          )}
        </>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ contacts
export function ContactsCard({
  data,
  onChange,
  compact,
}: {
  data: ConnectionsData;
  onChange: () => void;
  compact?: boolean;
}) {
  const { toast } = useStore();
  const t = useT();
  const { launch: launchAiSetup, shortModel } = useAiSetupLauncher(data);
  const c = data.contacts;
  const g = data.google;
  const [open, setOpen] = useState(!!compact);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Contact[] | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const people = (n: number) =>
    n === 1 ? t("1 person") : t("{n} people", { n });

  const finish = (
    view: ConnectionsData["contacts"] & { error?: string },
    added: string,
  ) => {
    if (view.error) {
      toast(
        t("Added, but it could not be read: {error}", { error: view.error }),
      );
    } else {
      const src = view.sources.find((x) => x.name === added);
      toast(`${t("Address book connected")} · ${people(src?.contacts ?? 0)}`);
      setName("");
      setUrl("");
      setAdding(false);
    }
    onChange();
  };

  const add = async () => {
    setBusy("add");
    setTest(null);
    try {
      finish(await api.addContactsSource(name.trim(), url.trim()), name.trim());
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const upload = async (file: File) => {
    setBusy("upload");
    setTest(null);
    try {
      const label = (
        name.trim() ||
        file.name.replace(/\.vcf$/i, "") ||
        "Imported"
      ).slice(0, 40);
      finish(await api.importContacts(label, await file.text()), label);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const remove = async (n: string) => {
    setBusy(n);
    try {
      await api.removeContactsSource(n);
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const runTest = async () => {
    setBusy("test");
    try {
      setTest(await api.testContacts());
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    if (!query.trim()) {
      setHits(null);
      return;
    }
    // a slower answer from an earlier query must not land under the newer one
    let latest = true;
    setHits(null);
    const handle = setTimeout(() => {
      api
        .contacts(query.trim(), 5)
        .then((r) => {
          if (latest) setHits(r.people);
        })
        .catch(() => {
          if (latest) setHits([]);
        });
    }, 250);
    return () => {
      latest = false;
      clearTimeout(handle);
    };
  }, [query]);

  const broken = c.sources.filter((f) => f.error).length;
  const status = !c.enabled
    ? { text: t("Off"), tone: "off" }
    : c.configured
      ? broken
        ? { text: t("{n} not reading", { n: broken }), tone: "warn" }
        : { text: t("Connected"), tone: "ok" }
      : { text: t("Not connected"), tone: "off" };

  return (
    <Card
      icon={<ContactIcon size={19} />}
      title={t("Contacts")}
      summary={
        !c.configured
          ? t("Who is who, so it never guesses an address")
          : c.sources.length === 0
            ? `${people(c.count)} · ${t("told to it in chat")}`
            : `${people(c.count)} · ${c.sources.length === 1 ? c.sources[0].name : t("{n} address books", { n: c.sources.length })}`
      }
      status={status}
      open={open}
      onToggle={compact ? undefined : () => setOpen(!open)}
    >
      <p className="text-[12.5px] text-muted -mt-1">
        {t(
          "Import a .vcf export from Google Contacts, iCloud, Outlook or your phone, or paste a link to one. People you mention in chat go into its own book. It looks people up before writing to them and warns when an address is unknown.",
        )}
      </p>
      {(c.sources.length > 0 || c.own > 0) && (
        <ul className="space-y-1.5">
          {c.own > 0 && (
            <li className="flex items-center gap-2.5 rounded-2xl bg-surface-2/60 px-3 py-2">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-medium truncate">
                  {t("My contacts")}
                </div>
                <div className="text-[11.5px] text-muted truncate">{`${people(c.own)} · ${t("added in chat")}`}</div>
              </div>
            </li>
          )}
          {c.sources.map((f) => (
            <li
              key={f.name}
              className="flex items-center gap-2.5 rounded-2xl bg-surface-2/60 px-3 py-2"
            >
              <span
                className={cx(
                  "h-2 w-2 rounded-full",
                  f.error
                    ? "bg-rose-500"
                    : f.fetched_at
                      ? "bg-emerald-500"
                      : "bg-amber-500",
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-medium truncate">
                  {f.name}
                </div>
                <div className="text-[11.5px] text-muted truncate">
                  {f.error
                    ? f.error
                    : f.fetched_at
                      ? `${people(f.contacts)} · ${t("read {when}", { when: relativeTime(f.fetched_at) })}`
                      : t("not read yet")}
                  {!f.from_app && ` · ${t("from config.toml")}`}
                </div>
              </div>
              {f.from_app && (
                <button
                  type="button"
                  aria-label={t("Remove")}
                  disabled={busy === f.name}
                  onClick={() => void remove(f.name)}
                  className="p-1.5 rounded-full text-muted hover:bg-surface-2"
                >
                  {busy === f.name ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <Trash2 size={15} />
                  )}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <div className="space-y-2.5 rounded-2xl border border-border/70 p-3">
          <Field label={t("Name")}>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={inputCls}
              placeholder={t("iPhone")}
            />
          </Field>
          <Field
            label={t("A .vcf file from your phone or computer")}
            hint={t(
              "Google Contacts: Export → vCard. iPhone: Contacts → select all → Share → Save to Files. Outlook: People → Manage → Export.",
            )}
          >
            <input
              ref={fileInput}
              type="file"
              accept=".vcf,text/vcard,text/x-vcard"
              className="hidden"
              onChange={(e) =>
                e.target.files?.[0] && void upload(e.target.files[0])
              }
            />
            <button
              type="button"
              disabled={busy === "upload"}
              onClick={() => fileInput.current?.click()}
              className={secondaryBtn}
            >
              {busy === "upload" ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Upload size={16} />
              )}{" "}
              {t("Choose a .vcf file")}
            </button>
          </Field>
          <Field
            label={t("Or a link / path to one")}
            hint={
              t("A link is stored encrypted in the vault as CONTACTS_{name}.", {
                name:
                  name
                    .trim()
                    .toUpperCase()
                    .replace(/[^A-Z0-9]+/g, "_")
                    .replace(/^_+|_+$/g, "") || "NAME",
              }) +
              " " +
              t("MiniMuse fetches whatever address you put here, from this machine; only paste links you trust.")
            }
          >
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className={inputCls}
              placeholder="https://… /contacts.vcf"
              inputMode="url"
              autoComplete="off"
            />
          </Field>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy === "add" || !name.trim() || !url.trim()}
              onClick={() => void add()}
              className={primaryBtn}
            >
              {busy === "add" ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Plug size={16} />
              )}{" "}
              {t("Connect")}
            </button>
            <button
              type="button"
              onClick={() => setAdding(false)}
              className={secondaryBtn}
            >
              {t("Cancel")}
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {!!androidApp()?.syncPhoneCalendarAndContacts && (
            <button
              type="button"
              disabled={busy === "phonesync"}
              onClick={async () => {
                setBusy("phonesync");
                try {
                  const res = await syncPhoneGoogleToServer();
                  toast(t("Synced {n} Google & phone contacts!", { n: res.contacts ?? 0 }));
                  onChange();
                } catch (err) {
                  toast((err as Error).message);
                } finally {
                  setBusy(null);
                }
              }}
              className={primaryBtn}
            >
              {busy === "phonesync" ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />}{" "}
              {t("Sync Google Contacts from Phone (1 Tap)")}
            </button>
          )}
          <button
            type="button"
            onClick={() => setAdding(true)}
            className={secondaryBtn}
          >
            <Plus size={16} /> {t("Add an address book")}
          </button>
          <button
            type="button"
            onClick={() => {
              openCenteredPopup("https://contacts.google.com/", "minimuse_gcontacts");
              setName((n) => n || "Google Contacts");
              setAdding(true);
            }}
            className={secondaryBtn}
          >
            <ExternalLink size={15} /> {t("Google Contacts Popup")}
          </button>
          {(g?.signed_in || c.sources.length > 0) && (
            <button
              type="button"
              disabled={busy === "sync"}
              onClick={async () => {
                setBusy("sync");
                try {
                  const res = await api.syncContacts();
                  setTest(res);
                  toast(res.ok ? t("Contacts synced") : (res.error ?? t("Contacts sync failed")));
                  onChange();
                } catch (err) {
                  toast((err as Error).message);
                } finally {
                  setBusy(null);
                }
              }}
              className={secondaryBtn}
            >
              {busy === "sync" ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}{" "}
              {t("Sync now")}
            </button>
          )}
          <button
            type="button"
            onClick={() => void launchAiSetup("contacts", compact)}
            className="inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent/10 px-3 py-1.5 text-[12.5px] font-medium text-accent hover:bg-accent/15"
          >
            <Wand2 size={14} /> {t("Let {model} set this up", { model: shortModel })}
          </button>
        </div>
      )}
      {c.configured && (
        <>
          <div className="relative">
            <Search
              size={15}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
            />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label={t("Find someone, as the agent would")}
              className={cx(inputCls, "pl-9")}
              placeholder={t("Find someone, as the agent would")}
              autoComplete="off"
            />
          </div>
          {hits !== null && (
            <ul className="space-y-1">
              {hits.length === 0 && (
                <li className="text-[12.5px] text-muted px-1">
                  {t("No one matches.")}
                </li>
              )}
              {hits.map((p) => (
                <li
                  key={p.id}
                  className="rounded-2xl bg-surface-2/60 px-3 py-2"
                >
                  <div className="text-[13.5px] font-medium">
                    {p.name}
                    {p.org && (
                      <span className="text-muted font-normal"> · {p.org}</span>
                    )}
                  </div>
                  <div className="text-[12px] text-muted break-words">
                    {[...p.emails, ...p.phones].join(" · ")}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {c.sources.length > 0 && (
            <button
              type="button"
              disabled={busy === "test"}
              onClick={() => void runTest()}
              className={secondaryBtn}
            >
              {busy === "test" ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <RefreshCw size={16} />
              )}{" "}
              {t("Read again")}
            </button>
          )}
          {test && (
            <TestLine result={test} okText={people(test.contacts ?? 0)} />
          )}
        </>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ browser
function BrowserCard({
  data,
  onChange,
}: {
  data: ConnectionsData;
  onChange: () => void;
}) {
  const { toast } = useStore();
  const t = useT();
  const b = data.browser;
  const [busy, setBusy] = useState(false);
  const [install, setInstall] = useState<{ status: string; step: string; message: string } | null>(null);
  const installing = install?.status === "running";
  useEffect(() => {
    if (!installing) return;
    const timer = window.setInterval(() => {
      api
        .browserInstallStatus()
        .then((s) => {
          setInstall(s);
          if (s.status === "done") {
            toast(t("Browser control is installed."));
            onChange();
          }
        })
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [installing, onChange, t, toast]);
  const startInstall = () => {
    setInstall({ status: "running", step: t("Starting..."), message: "" });
    api
      .installBrowser()
      .then(setInstall)
      .catch((e: Error) => setInstall({ status: "error", step: "", message: e.message }));
  };
  const flip = async () => {
    setBusy(true);
    try {
      await api.setBrowser(!b.enabled);
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card
      icon={<Globe size={19} />}
      title={t("Browser")}
      summary={
        b.available
          ? t("Open pages, click, fill forms, screenshot")
          : t("Playwright is not installed on the server")
      }
      status={
        b.enabled && b.available
          ? { text: t("On"), tone: "ok" }
          : b.enabled
            ? { text: t("Unavailable"), tone: "warn" }
            : { text: t("Off"), tone: "off" }
      }
      open={false}
      trailing={
        <button
          type="button"
          role="switch"
          aria-checked={b.enabled}
          disabled={busy}
          onClick={() => void flip()}
          className={cx(
            "relative h-7 w-12 shrink-0 rounded-full transition",
            b.enabled ? "bg-accent" : "bg-surface-2 border border-border",
          )}
        >
          <span
            className={cx(
              "absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition",
              b.enabled ? "left-[22px]" : "left-0.5",
            )}
          />
        </button>
      }
    >
      {!b.available && (
        <div className="space-y-2">
          <p className="text-[12.5px] text-muted">
            {t("Browser control needs a browser on this computer. One tap installs it; it takes a few minutes the first time.")}
          </p>
          <button
            type="button"
            onClick={startInstall}
            disabled={installing}
            className="flex items-center gap-2 rounded-full bg-accent px-4 py-2 text-[13px] font-semibold text-accent-fg disabled:opacity-60"
          >
            {installing && <Loader2 size={14} className="animate-spin" />}
            {installing ? install?.step || t("Installing...") : t("Install browser control")}
          </button>
          {install?.status === "error" && <p className="text-[12.5px] text-red-500">{install.message}</p>}
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ phone (GUI)
function PhoneCard({
  data,
  onChange,
}: {
  data: ConnectionsData;
  onChange: () => void;
}) {
  const { toast } = useStore();
  const t = useT();
  const g = data.gui;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmAction, setConfirmAction] = useState<"enable" | "accessibility" | "app_settings" | null>(null);
  const [model, setModel] = useState(g.model);
  const [baseUrl, setBaseUrl] = useState(g.base_url);
  const [apiKey, setApiKey] = useState("");
  const [result, setResult] = useState<TestResult | null>(null);
  useEffect(() => {
    setModel(g.model);
    setBaseUrl(g.base_url);
  }, [g.model, g.base_url]);

  const flip = async () => {
    setBusy(true);
    try {
      await api.setGui({ enabled: !g.enabled });
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    setBusy(true);
    setResult(null);
    try {
      await api.setGui({
        model: model.trim(),
        base_url: baseUrl.trim(),
        ...(apiKey ? { api_key: apiKey.trim() } : {}),
      });
      setApiKey("");
      onChange();
      setResult(await api.testGui());
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const phone = g.phone;
  // the Android app: its own accessibility service does the operating — re-read when the
  // sheet is opened again or the app comes back from Settings
  const [a11y, setA11y] = useState(accessibilityState);
  const a11yRef = useRef(a11y);
  useEffect(() => {
    if (!open) return;
    const again = () => {
      const now = accessibilityState();
      if (now !== a11yRef.current) {
        a11yRef.current = now;
        setA11y(now);
        // switched on or off in Settings: the server knows already (the app announced it),
        // so the card's header should catch up too
        setTimeout(onChange, 400);
      }
    };
    again();
    document.addEventListener("visibilitychange", again);
    window.addEventListener("focus", again);
    return () => {
      document.removeEventListener("visibilitychange", again);
      window.removeEventListener("focus", again);
    };
  }, [open, onChange]);
  const summary = phone.connected
    ? t("{name} is connected", { name: phone.device?.name ?? t("A phone") })
    : g.enabled
      ? t("No phone connected. Open the app on the phone")
      : t("Tap, type and swipe in the apps on your phone");
  return (
    <>
    <Card
      icon={<Smartphone size={19} />}
      title={t("Phone")}
      summary={summary}
      status={
        g.enabled && phone.connected
          ? { text: t("On"), tone: "ok" }
          : g.enabled
            ? { text: t("Waiting"), tone: "warn" }
            : { text: t("Off"), tone: "off" }
      }
      open={open}
      onToggle={() => setOpen((v) => !v)}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="text-[13px] leading-snug">
          <div className="font-medium">{t("Operate the phone")}</div>
          <div className="text-[12px] text-muted">
            {t(
              "When on, the agent can read the screen and act in the apps on the connected phone: 12306, WeChat, Alipay… It asks before paying, sending or deleting.",
            )}
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={g.enabled}
          disabled={busy}
          onClick={() => {
            if (g.enabled) void flip();
            else setConfirmAction("enable");
          }}
          className={cx(
            "relative h-7 w-12 shrink-0 rounded-full transition",
            g.enabled ? "bg-accent" : "bg-surface-2 border border-border",
          )}
        >
          <span
            className={cx(
              "absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition",
              g.enabled ? "left-[22px]" : "left-0.5",
            )}
          />
        </button>
      </div>
      {a11y !== null && (
        <div className="rounded-2xl bg-surface-2 px-3 py-2.5 text-[12.5px] leading-snug space-y-1.5">
          <div className="flex items-center justify-between gap-3">
            <span className="font-medium">{t("This phone")}</span>
            <span
              className={cx(
                "rounded-full px-2 py-0.5 text-[11px] font-medium",
                a11y === "on" && "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
                a11y === "off" && "bg-amber-500/15 text-amber-700 dark:text-amber-300",
                a11y === "unsupported" && "bg-surface text-muted",
              )}
            >
              {a11y === "on" ? t("Ready") : a11y === "off" ? t("Not yet") : t("Not on this Android")}
            </span>
          </div>
          {a11y === "unsupported" && (
            <div className="text-muted">
              {t("Operating the screen needs Android 11 or newer; this phone can still be used for everything else.")}
            </div>
          )}
          {a11y === "off" && (
            <>
              <div className="text-muted">
                {t(
                  "Turn on the MiniMuse accessibility service; that is how it sees the screen and taps for you, only while a task runs, with a Stop button on screen.",
                )}
              </div>
              <div className="flex flex-wrap gap-2 pt-0.5">
                <button
                  type="button"
                  onClick={() => setConfirmAction("accessibility")}
                  className="rounded-full bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white"
                >
                  {t("Open Accessibility settings")}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmAction("app_settings")}
                  className="rounded-full border border-border px-3 py-1.5 text-[12.5px]"
                >
                  {t("App settings")}
                </button>
              </div>
              <div className="text-muted">
                {t(
                  "Greyed out with “Restricted setting”? Android 13+ does that for apps installed from a download: in App settings tap ⋮ → Allow restricted settings, then come back.",
                )}
              </div>
            </>
          )}
          {a11y === "on" && (
            <div className="text-muted">
              {t("Android may switch the service off after an update or a battery clean-up; if the phone stops answering, come back here.")}
            </div>
          )}
        </div>
      )}
      {phone.connected && phone.device && (
        <div className="rounded-2xl bg-surface-2 px-3 py-2 text-[12.5px] text-muted">
          {t("{name} ({platform}), {n} apps", {
            name: phone.device.name,
            platform: phone.device.platform,
            n: String(phone.device.apps),
          })}
          {phone.last_screen && (
            <>
              {" "}
              ·{" "}
              {t("last seen in {app}", {
                app: phone.last_screen.app_name || phone.last_screen.app,
              })}
            </>
          )}
        </div>
      )}
      <div className="text-[12px] text-muted leading-snug">
        {t(
          "The operator's model looks at screens step by step: a small, fast model that takes pictures. The Hands model row under Chat model picks one on the same endpoint and key; the fields here are for a different endpoint. Empty = {model}.",
          { model: g.default_model || t("the chat model") },
        )}
      </div>
      <Field label={t("Model")}>
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder={g.default_model || t("same as the chat model")}
          className={inputCls}
        />
      </Field>
      <Field label={t("Base URL")}>
        <input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder={t("same as the chat model")}
          className={inputCls}
        />
      </Field>
      <Field
        label={t("API key")}
        hint={
          g.key_source === "vault"
            ? t("A key is in the vault; leave empty to keep it.")
            : g.key_source === "config"
              ? t("Set in config.toml.")
              : t("Leave empty to use the main model's key.")
        }
      >
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="sk-…"
          className={inputCls}
          autoComplete="off"
        />
      </Field>
      <button
        type="button"
        disabled={busy}
        onClick={() => void save()}
        className={primaryBtn}
      >
        {busy ? (
          <Loader2 size={16} className="animate-spin" />
        ) : (
          <Check size={16} />
        )}
        {t("Save and test")}
      </button>
      {result && (
        <TestLine result={result} okText={t("The operator's model answers.")} />
      )}
    </Card>
    <ConfirmDialog
      open={confirmAction !== null}
      title={
        confirmAction === "enable"
          ? t("Allow MiniMuse to operate the phone?")
          : confirmAction === "accessibility"
            ? t("Leave MiniMuse for Android settings?")
            : t("Open Android app info?")
      }
      body={
        confirmAction === "enable"
          ? t(
              "When turned on, MiniMuse can read the phone screen and tap inside your apps while a task runs. You can turn it off or tap Stop any time.",
            )
          : confirmAction === "accessibility"
            ? t(
                "This opens Android's system Accessibility settings screen. Enabling accessibility lets MiniMuse read and interact with on-screen apps.",
              )
            : t("This opens Android's App Info screen so you can manage permissions or allow restricted settings.")
      }
      confirmLabel={confirmAction === "enable" ? t("Turn on") : t("Open settings")}
      onCancel={() => setConfirmAction(null)}
      onConfirm={() => {
        const action = confirmAction;
        setConfirmAction(null);
        if (action === "enable") void flip();
        else if (action === "accessibility") androidApp()?.openAccessibilitySettings?.();
        else if (action === "app_settings") androidApp()?.openAppSettings?.();
      }}
    />
    </>
  );
}

// ------------------------------------------------------------------ mcp
/**
 * `KEY=value` lines typed in the MCP form, as the runtime's `env` block. A line without
 * `=` or with an empty name is skipped; the value keeps everything after the first `=`.
 * The runtime moves each value into the vault and keeps a `{{vault:MCP_<SERVER>_<KEY>}}`
 * reference in app-settings.json; a value typed as a reference already is kept as typed.
 */
export function parseEnvLines(text: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    if (key) env[key] = line.slice(eq + 1).trim();
  }
  return env;
}

function MCPCard({
  data,
  onChange,
}: {
  data: ConnectionsData;
  onChange: () => void;
}) {
  const { toast } = useStore();
  const t = useT();
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [url, setUrl] = useState("");
  const [envText, setEnvText] = useState("");
  const [risk, setRisk] = useState("moderate");
  const [busy, setBusy] = useState<string | null>(null);

  const add = async () => {
    setBusy("add");
    try {
      const parts = command.trim().split(/\s+/).filter(Boolean);
      await api.addMCP({
        name: name.trim(),
        command: parts[0] ?? null,
        args: parts.slice(1),
        env: parseEnvLines(envText),
        url: url.trim() || null,
        risk,
      });
      setName("");
      setCommand("");
      setUrl("");
      setEnvText("");
      setAdding(false);
      toast(t("Server connected"));
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const remove = async (n: string) => {
    setBusy(n);
    try {
      await api.removeMCP(n);
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const connected = data.mcp.filter((m) => m.connected).length;
  // what the account's other devices connected and this one has not (contract C3): shown
  // with the server's own sign-in as the action — the key never travels
  const elsewhere = (data.shared ?? []).filter((c) => c.enabled && !c.here);
  const prefill = (c: SharedConnector) => {
    setName(c.id);
    setUrl(c.url);
    setCommand("");
    setAdding(true);
    setOpen(true);
  };
  return (
    <Card
      icon={<Plug size={19} />}
      title={t("MCP servers")}
      summary={
        data.mcp.length
          ? t("{n} of {total} connected · {tools} tools", {
              n: connected,
              total: data.mcp.length,
              tools: data.mcp.reduce((n, m) => n + m.tools, 0),
            })
          : t("Add tools from any Model Context Protocol server")
      }
      status={
        data.mcp.length
          ? {
              text: t("{n} on", { n: connected }),
              tone: connected ? "ok" : "warn",
            }
          : { text: t("None"), tone: "off" }
      }
      open={open}
      onToggle={() => setOpen(!open)}
    >
      {data.mcp.length > 0 && (
        <ul className="space-y-1.5">
          {data.mcp.map((m) => (
            <li
              key={m.name}
              className="flex items-center gap-2.5 rounded-2xl bg-surface-2/60 px-3 py-2"
            >
              <span
                className={cx(
                  "h-2 w-2 rounded-full",
                  m.connected ? "bg-emerald-500" : "bg-rose-500",
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-medium truncate">
                  {m.builtin ? t("This phone") : m.name}
                </div>
                <div className="text-[11.5px] text-muted truncate">
                  {m.builtin ? t("clipboard, notifications, calendar, contacts, location, alarms, photos") : (m.url ?? [m.command, ...m.args].join(" "))} ·{" "}
                  {t("{n} tools", { n: m.tools })} · {t(m.risk)}
                  {!m.from_app && !m.builtin && ` · ${t("from config.toml")}`}
                </div>
                {(m.env?.length ?? 0) > 0 && (
                  <div className="text-[11.5px] text-muted truncate">
                    {t("Environment: {names}", { names: (m.env ?? []).map((e) => e.name).join(", ") })}
                  </div>
                )}
                {(m.env ?? []).some((e) => !e.vault) && (
                  <div className="text-[11.5px] text-amber-700 dark:text-amber-300">
                    {t("{names} is written as plain text in config.toml; keep it in the vault and write {{vault:NAME}} instead", {
                      names: (m.env ?? []).filter((e) => !e.vault).map((e) => e.name).join(", "),
                    })}
                  </div>
                )}
              </div>
              {m.from_app && (
                <button
                  type="button"
                  aria-label={t("Remove")}
                  disabled={busy === m.name}
                  onClick={() => void remove(m.name)}
                  className="p-1.5 rounded-full text-muted hover:bg-surface-2"
                >
                  {busy === m.name ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <Trash2 size={15} />
                  )}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {elsewhere.length > 0 && (
        <ul className="space-y-1.5">
          {elsewhere.map((c) => (
            <li
              key={`${c.device_id}:${c.id}`}
              className="flex items-center gap-2.5 rounded-2xl border border-dashed border-border/70 px-3 py-2"
            >
              <span className="h-2 w-2 rounded-full bg-border" />
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-medium truncate">{c.label || c.id}</div>
                <div className="text-[11.5px] text-muted truncate">
                  {t("Connected on {device}; sign in here to use it on this device", { device: c.device || c.device_id })}
                  {c.url ? ` · ${c.url}` : ""}
                </div>
              </div>
              {c.url && (
                <button type="button" onClick={() => prefill(c)} className={cx(secondaryBtn, "py-1.5 text-[12.5px]")}>
                  {t("Sign in here")}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <div className="space-y-2.5 rounded-2xl border border-border/70 p-3">
          <Field label={t("Name")}>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={inputCls}
              placeholder="filesystem"
            />
          </Field>
          <Field
            label={t("Command")}
            hint={t(
              "Run locally over stdio, for example: npx -y @modelcontextprotocol/server-filesystem ./workspace",
            )}
          >
            <input
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              className={inputCls}
              placeholder="npx -y @modelcontextprotocol/server-…"
            />
          </Field>
          <Field
            label={t("Environment")}
            hint={t(
              "One KEY=value per line for the command. Each value goes into the vault; the settings file keeps a reference, not the value.",
            )}
          >
            <textarea
              value={envText}
              onChange={(e) => setEnvText(e.target.value)}
              rows={2}
              className={cx(inputCls, "resize-none font-mono text-[12.5px]")}
              placeholder={"AMAP_MAPS_API_KEY=…"}
              spellCheck={false}
            />
          </Field>
          <Field
            label={t("or URL")}
            hint={t("A remote server over Streamable HTTP.")}
          >
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className={inputCls}
              placeholder="https://host/mcp"
              inputMode="url"
            />
          </Field>
          <Field
            label={t("Risk of its tools")}
            hint={t("Decides when the Sentinel asks you before a call.")}
          >
            <div className="flex gap-1.5">
              {/* the runtime's three risk levels (nanomuse/schema.py RiskLevel); any other word is a 400 */}
              {["safe", "moderate", "sensitive"].map((r) => (
                <button
                  key={r}
                  type="button"
                  onClick={() => setRisk(r)}
                  aria-pressed={risk === r}
                  className={cx(
                    "rounded-full px-3 py-1.5 text-[13px] border",
                    risk === r
                      ? "border-accent bg-accent/10 text-accent font-medium"
                      : "border-border text-muted",
                  )}
                >
                  {t(r)}
                </button>
              ))}
            </div>
          </Field>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={
                busy === "add" ||
                !name.trim() ||
                !(command.trim() || url.trim())
              }
              onClick={() => void add()}
              className={primaryBtn}
            >
              {busy === "add" ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Plug size={16} />
              )}{" "}
              {t("Connect")}
            </button>
            <button
              type="button"
              onClick={() => setAdding(false)}
              className={secondaryBtn}
            >
              {t("Cancel")}
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className={secondaryBtn}
        >
          <Plus size={16} /> {t("Add a server")}
        </button>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ vault
function VaultCard({
  data,
  onChange,
}: {
  data: ConnectionsData;
  onChange: () => void;
}) {
  const { toast } = useStore();
  const t = useT();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const add = async () => {
    setBusy("add");
    try {
      await api.vaultSet(name.trim(), value);
      setName("");
      setValue("");
      toast(t("Stored in the vault"));
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const remove = async (n: string) => {
    setBusy(n);
    try {
      await api.vaultDelete(n);
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card
      icon={<KeyRound size={19} />}
      title={t("Vault")}
      summary={
        data.vault.length
          ? t("{n} secrets · encrypted on your machine", {
              n: data.vault.length,
            })
          : t("Encrypted secrets the model never sees")
      }
      status={{ text: `${data.vault.length}`, tone: "off" }}
      open={open}
      onToggle={() => setOpen(!open)}
    >
      <p className="text-[12.5px] text-muted -mt-1">
        {t("Refer to a secret as")}{" "}
        <code className="rounded bg-surface-2 px-1">{"{{vault:NAME}}"}</code>{" "}
        {t(
          "in a tool call or config: the value is filled in after the Sentinel approves and is redacted from everything the model reads.",
        )}
      </p>
      {data.vault.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {data.vault.map((n) => (
            <li
              key={n}
              className="flex items-center gap-1 rounded-full bg-surface-2 pl-3 pr-1 py-1 text-[12.5px] font-mono"
            >
              {n}
              <button
                type="button"
                aria-label={t("Delete {name}", { name: n })}
                disabled={busy === n}
                onClick={() => void remove(n)}
                className="p-1 rounded-full text-muted hover:text-rose-500"
              >
                {busy === n ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Trash2 size={13} />
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="grid grid-cols-[1fr_1fr_auto] gap-2 items-end">
        <Field label={t("Name")}>
          <input
            value={name}
            onChange={(e) =>
              setName(
                e.target.value.toUpperCase().replace(/[^A-Z0-9_.-]/g, "_"),
              )
            }
            className={cx(inputCls, "font-mono")}
            placeholder="GITHUB_TOKEN"
          />
        </Field>
        <Field label={t("Value")}>
          <input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className={inputCls}
            placeholder={t("secret")}
            autoComplete="off"
          />
        </Field>
        <button
          type="button"
          disabled={busy === "add" || !name || !value}
          onClick={() => void add()}
          className={cx(primaryBtn, "px-3")}
          aria-label={t("Store")}
        >
          {busy === "add" ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Plus size={16} />
          )}
        </button>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ bits
/** one of the studio's two model pickers: the endpoint's candidates when it lists any, a
 *  free field otherwise; the empty choice leaves the pick to the runtime */
const OTHER_MODEL = "\u0000other";

/**
 * A model from the list, or one typed by its id when `other` (the list's last entry opens
 * the field). The list is the picker of the Models contract (`ModelPicker`): one group
 * named after the provider with its catalogue default first, or on MiniMuse Cloud the menu
 * and the account's other models as two groups; each folds past eight rows, and a search
 * field appears once the rows pass eight in all.
 */
function StudioModelPick({
  label,
  provider,
  value,
  options,
  more = [],
  onChange,
  other = true,
  placeholder,
}: {
  label: string;
  /** the provider's name, for the group's title and the button's `<provider> · <model>` */
  provider: string;
  value: string;
  options: string[];
  /** a second group — on MiniMuse Cloud, the other models under the Cloud key a member may use */
  more?: string[];
  onChange: (v: string) => void;
  other?: boolean;
  /** what "Automatic" means for this provider: the catalogue's default model */
  placeholder?: string;
}) {
  const t = useT();
  const listed = options.includes(value) || more.includes(value);
  const [typing, setTyping] = useState(false);
  const automatic = placeholder ? t("Automatic · {model}", { model: placeholder }) : t("Automatic");
  const asRows = (ids: string[]) => ids.map((id) => ({ id, name: id }));
  const groups: ModelGroup[] = more.length
    ? [
        { key: "menu", label: t("Menu"), rows: asRows(options), ...(placeholder ? { default: placeholder } : {}) },
        { key: "more", label: t("More models on your account"), rows: asRows(more) },
      ]
    : [{ key: "list", label: provider, rows: asRows(options), ...(placeholder ? { default: placeholder } : {}) }];
  const current = value && listed ? { group: options.includes(value) ? groups[0].key : "more", id: value } : undefined;
  return (
    <div className="block text-[12.5px] text-muted">
      <span className="block mb-1">{label}</span>
      {(options.length || more.length) && (listed || !value) && !typing ? (
        <ModelPicker
          label={label}
          text={value ? `${provider} · ${value}` : automatic}
          heads={[{ value: "", label: automatic }]}
          headValue={value ? undefined : ""}
          groups={groups}
          current={current}
          onHead={() => onChange("")}
          onPick={(_group, row) => onChange(row.id)}
          tail={other ? t("Other model…") : undefined}
          onTail={() => {
            setTyping(true);
            onChange("");
          }}
        />
      ) : (
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={automatic}
          aria-label={label}
          className={cx(inputCls, "text-fg")}
          spellCheck={false}
        />
      )}
    </div>
  );
}

/** The "(how)" at the end of an unavailable line: the guide to one's own key. */
function OwnKeyHow() {
  const t = useT();
  return (
    <a href={OWN_KEY_DOCS} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-accent">
      {t("How")} <ExternalLink size={11} />
    </a>
  );
}

type ControlProps = { id?: string; "aria-describedby"?: string };

const CONTROL_TAGS = new Set(["input", "select", "textarea"]);

function isControl(node: ReactNode): node is ReactElement<ControlProps> {
  return isValidElement(node) && typeof node.type === "string" && CONTROL_TAGS.has(node.type);
}

/** `children` with its one native control (a direct child, or one level down inside a
 * fragment or a wrapper element) given an id and the hint's id, so the field's label can
 * point at it; `null` when there is no single control to point at. */
function wireControl(
  children: ReactNode,
  id: string,
  hintId?: string,
): { node: ReactNode; id: string } | null {
  const wire = (el: ReactElement<ControlProps>) => {
    const used = el.props.id ?? id;
    const node = cloneElement(el, {
      id: used,
      "aria-describedby": el.props["aria-describedby"] ?? hintId,
    });
    return { node, id: used };
  };
  if (isControl(children)) return wire(children);
  if (!isValidElement<{ children?: ReactNode }>(children)) return null;
  const inner = Children.toArray(children.props.children);
  const controls = inner.filter(isControl);
  if (controls.length !== 1) return null;
  const one = wire(controls[0]);
  const wired = inner.map((n) => (n === controls[0] ? one.node : n));
  return { node: cloneElement(children, undefined, ...wired), id: one.id };
}

/** A labelled control. The label is tied to the control by id when the children hold one
 * native input, select or textarea; a set of controls (chips, two inputs) is a group named by
 * the label instead. */
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const labelId = `${id}-label`;
  const wired = wireControl(children, id, hintId);
  return (
    <div>
      <label id={labelId} htmlFor={wired?.id} className="text-[12px] text-muted">
        {label}
      </label>
      <div
        className="mt-1"
        role={wired ? undefined : "group"}
        aria-labelledby={wired ? undefined : labelId}
        aria-describedby={wired ? undefined : hintId}
      >
        {wired ? wired.node : children}
      </div>
      {hint && (
        <div id={hintId} className="mt-1 text-[11.5px] text-muted leading-snug">
          {hint}
        </div>
      )}
    </div>
  );
}

/** The outcome of a connection test: the sentence in the person's language; a failed model
 * test keeps the provider's own words one tap away. */
function TestLine({ result, okText }: { result: TestResult; okText: string }) {
  const t = useT();
  const [showDetail, setShowDetail] = useState(false);
  const detail = !result.ok && result.detail && result.detail !== result.error ? result.detail : null;
  return (
    <div
      className={cx(
        "rounded-2xl px-3 py-2 text-[12.5px]",
        result.ok
          ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300"
          : "bg-rose-500/12 text-rose-700 dark:text-rose-300",
      )}
    >
      {result.ok ? okText : t(result.error ?? "The model provider did not answer.")}
      {detail && (
        <>
          {" "}
          <button type="button" onClick={() => setShowDetail((v) => !v)} className="underline decoration-dotted underline-offset-2 opacity-70 hover:opacity-100">
            {showDetail ? t("Hide details") : t("Details")}
          </button>
          {showDetail && <div className="mt-1.5 break-all font-mono text-[11px] opacity-80">{detail}</div>}
        </>
      )}
    </div>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

export { inputCls, primaryBtn, secondaryBtn };
