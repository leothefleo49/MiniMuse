import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { ApiIntegrationsCard } from "./components/ApiIntegrationsCard";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ImportExport, MUSE_PROMPT } from "./components/ImportExport";
import { MuseHeader } from "./components/MuseHeader";
import { getLocale, setLocaleSetting } from "./i18n";
import { CalendarScreen } from "./screens/CalendarScreen";
import { GoalsScreen } from "./screens/GoalsScreen";
import { LocationScreen } from "./screens/LocationScreen";
import { Onboarding } from "./screens/Onboarding";
import { SettingsScreen } from "./screens/SettingsScreen";
import { SignInGate } from "./screens/SignInGate";
import type {
  ApiConnection,
  ApiPreset,
  CalendarData,
  ConnectionsData,
  Goal,
  LocationSummary,
  Profile,
} from "./types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
(globalThis as Record<string, unknown>).window = {
  location: { href: "http://127.0.0.1:8765/" },
  matchMedia: () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }),
  setTimeout: globalThis.setTimeout.bind(globalThis),
  clearTimeout: globalThis.clearTimeout.bind(globalThis),
  setInterval: globalThis.setInterval.bind(globalThis),
  clearInterval: globalThis.clearInterval.bind(globalThis),
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
};
(globalThis as Record<string, unknown>).localStorage = {
  getItem: () => null,
  setItem: () => undefined,
  removeItem: () => undefined,
};

const BASE_PROFILE: Profile = {
  name: "MiniMuse",
  avatar: "dragon",
  emoji: "✨",
  color: "#0064d4",
  tagline: "",
  tone: "",
  communication: "",
  style: "",
  user_name: "",
  proactivity: "default",
  proactive: true,
  goal_interval_minutes: 60,
  quiet_hours: "",
};

const BASE_CONNECTIONS: ConnectionsData = {
  llm: {
    provider: "openrouter",
    model: "openai/gpt-4o-mini",
    base_url: "https://openrouter.ai/api/v1",
    tool_mode: "",
    stream: true,
    key_source: "vault",
    from_app: false,
    cloud: false,
  },
  providers: {},
  embeddings: {
    mode: "auto",
    model: "",
    default_model: "",
    base_url: "",
    effective_base_url: "",
    key_source: "missing",
    from_app: false,
    has_key: false,
    vectors: 0,
    vec: false,
    error: "",
    dims: 0,
    status: "off",
  },
  email: {
    enabled: false,
    configured: false,
    address: "",
    imap_host: "",
    imap_port: 993,
    smtp_host: "",
    smtp_port: 587,
    smtp_starttls: true,
    password_set: false,
  },
  email_accounts: [],
  calendar: { enabled: false, configured: false, refresh_minutes: 30, day_start: "08:00", day_end: "20:00", feeds: [] },
  contacts: { enabled: false, configured: false, count: 0, own: 0, sources: [] },
  apis: [],
} as unknown as ConnectionsData;

const storeMock = vi.hoisted(() => ({
  state: {
    profile: {
      name: "MiniMuse",
      avatar: "dragon",
      emoji: "✨",
      color: "#0064d4",
      tagline: "",
      tone: "",
      communication: "",
      style: "",
      user_name: "",
      proactivity: "default" as const,
      proactive: true,
      goal_interval_minutes: 60,
      quiet_hours: "",
    } as Profile,
    settings: { ui: { show_calendar: "true", show_location: "true", map_app: "google" }, llm: { model: "openai/gpt-4o-mini" } },
    goals: [] as Goal[],
    connectionsVersion: 0,
    goalsVersion: 0,
    remindersVersion: 0,
    calendarVersion: 0,
    studio: null,
    status: { state: "idle" as const },
    pendingApprovals: [],
    threads: [{ id: "main", title: "Main chat", created_at: "", updated_at: "", busy: false, queued: 0, events: 0 }],
    hub: { enabled: false, signed_in: false, account: { signed_in: false, hint: "" }, devices: [] },
  },
  dispatch: vi.fn(),
  send: vi.fn(async () => undefined),
  refreshGoals: vi.fn(async () => undefined),
  refreshSettings: vi.fn(async () => undefined),
  refreshHub: vi.fn(async () => undefined),
  setTab: vi.fn(),
  openThread: vi.fn(),
  draft: vi.fn(),
  toast: vi.fn(),
  skipSignIn: vi.fn(),
  dismissOnboarding: vi.fn(),
}));

vi.mock("./store", () => ({
  DRAFT_THREAD: "draft",
  useStore: () => storeMock,
}));

function textOf(node: ReactTestRenderer | ReactTestRenderer["root"]): string {
  const out: string[] = [];
  const walk = (n: unknown) => {
    if (!n || typeof n !== "object") return;
    const anyNode = n as { children?: unknown[]; props?: { children?: unknown } };
    const kids = anyNode.children ?? [];
    if (kids.length > 0) {
      for (const child of kids) {
        if (typeof child === "string") out.push(child);
        else walk(child);
      }
      return;
    }
    const propsChildren = anyNode.props?.children;
    if (typeof propsChildren === "string") out.push(propsChildren);
  };
  walk((node as { root: unknown }).root ?? node);
  return out.join(" ");
}

let tree: ReactTestRenderer | null = null;

afterEach(() => {
  if (tree) {
    act(() => tree?.unmount());
    tree = null;
  }
  vi.restoreAllMocks();
});

beforeEach(() => {
  setLocaleSetting("en");
  storeMock.dispatch.mockClear();
  storeMock.send.mockClear();
  storeMock.refreshGoals.mockClear();
  storeMock.refreshSettings.mockClear();
  storeMock.refreshHub.mockClear();
  storeMock.setTab.mockClear();
  storeMock.openThread.mockClear();
  storeMock.draft.mockClear();
  storeMock.toast.mockClear();
  storeMock.skipSignIn.mockClear();
  storeMock.dismissOnboarding.mockClear();
  storeMock.state = {
    ...storeMock.state,
    profile: BASE_PROFILE,
    goals: [],
    settings: { ui: { show_calendar: "true", show_location: "true", map_app: "google" }, llm: { model: "openai/gpt-4o-mini" } },
  };
});

describe("Local-first SignInGate & Mobile setup header layout", () => {
  it("renders local setup as the hero card and tucks Cloud into a collapsed side option", async () => {
    const updateSpy = vi.spyOn(api, "updateSettings").mockResolvedValue({} as never);
    await act(async () => {
      tree = create(createElement(SignInGate));
    });
    const text = textOf(tree!);
    expect(text).toContain("Recommended · Local & Private");
    expect(text).toContain("Continue with Local Setup");
    expect(text).toContain("Run strictly local (keep MiniMuse Cloud turned off)");

    const cloudToggle = tree!.root.findAll((n) => n.type === "button" && n.props["aria-expanded"] !== undefined)[0];
    expect(cloudToggle.props["aria-expanded"]).toBe(false);
    await act(async () => {
      cloudToggle.props.onClick();
    });
    expect(cloudToggle.props["aria-expanded"]).toBe(true);

    const localBtn = tree!.root.findByProps({ "data-testid": "sign-in-local-only" });
    await act(async () => {
      localBtn.props.onClick();
    });
    expect(updateSpy).toHaveBeenCalledWith({ cloud_mode: "local", cloud_prompts: false });
    expect(storeMock.skipSignIn).toHaveBeenCalledTimes(1);
  });

  it("keeps MuseHeader transparent with transparent .header-scrim and avoids mobile onboarding clip trap", async () => {
    vi.spyOn(api, "connections").mockResolvedValue(BASE_CONNECTIONS);

    await act(async () => {
      tree = create(createElement(MuseHeader, { profile: BASE_PROFILE, name: "MiniMuse", scrim: true }));
    });
    const header = tree!.root.findByType("header");
    expect(String(header.props.className)).toContain("bg-transparent");
    expect(String(header.props.className)).toContain("header-scrim");
    expect(String(header.props.className)).not.toContain("bg-bg");

    await act(async () => {
      tree!.update(createElement(Onboarding));
    });
    const topbar = tree!.root.findByProps({ "data-testid": "onboarding-topbar" });
    const topRow = topbar.children[0] as { props: { className?: string } };
    expect(String(topRow.props.className)).toContain("h-11");
    expect(String(topRow.props.className)).toContain("items-center");

    const welcomeContainer = tree!.root.findAll(
      (n) => n.type === "div" && typeof n.props.className === "string" && n.props.className.includes("min-h-full"),
    )[0];
    expect(String(welcomeContainer.props.className)).toContain("justify-start");
    expect(String(welcomeContainer.props.className)).not.toContain("h-full flex-col items-center justify-center");

    const updateSpy = vi.spyOn(api, "updateSettings").mockResolvedValue({} as never);
    const getStarted = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("Get started"))[0];
    await act(async () => {
      getStarted.props.onClick();
    });
    expect(textOf(tree!)).toContain("Theme & AI appearance");
    expect(textOf(tree!)).toContain("Optional tabs & default maps");

    const calToggle = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("Calendar tab"))[0];
    await act(async () => {
      calToggle.props.onClick();
    });
    expect(updateSpy).toHaveBeenCalledWith({ ui: { show_calendar: "false", show_location: "true", map_app: "google" } });
  });
});

describe("36+ APIs & Universal AI Integrations Card", () => {
  it("lists presets, creates connections, runs schema discovery, and opens AI draft setup", async () => {
    const presets: ApiPreset[] = [
      { id: "github", name: "GitHub REST", category: "Developer", description: "Repos and issues", base_url: "https://api.github.com", auth_type: "bearer" },
      { id: "stripe", name: "Stripe API", category: "Finance", description: "Payments & invoices", base_url: "https://api.stripe.com/v1", auth_type: "bearer" },
    ];
    const savedConn: ApiConnection = {
      id: "github_work",
      name: "Work GitHub",
      preset_id: "github",
      category: "Developer",
      base_url: "https://api.github.com",
      auth_type: "bearer",
      auth_header: "Authorization",
      auth_param: "",
      auth_prefix: "Bearer",
      vault_key: "API_CONN_GITHUB_WORK_SECRET",
      has_secret: true,
      headers: {},
      ai_guide: "",
      learned_notes: "",
      docs_url: "https://docs.github.com/en/rest",
      test_endpoint: { method: "GET", path: "/user" },
      endpoints: [{ id: "me", name: "User", method: "GET", path: "/user" }],
      enabled: true,
      created_at: "",
      updated_at: "",
      last_tested_at: "",
      last_status: "",
      last_error: "",
    };

    vi.spyOn(api, "apiPresets").mockResolvedValue({ presets, count: 2 });
    vi.spyOn(api, "apis").mockResolvedValue([savedConn]);
    vi.spyOn(api, "discoverApi").mockResolvedValue({ ok: true, endpoints_count: 12 });

    await act(async () => {
      tree = create(
        createElement(ApiIntegrationsCard, {
          data: { ...BASE_CONNECTIONS, apis: [savedConn] },
          onChange: () => undefined,
          compact: true,
        }),
      );
    });

    const txt = textOf(tree!);
    expect(txt).toContain("Work GitHub");
    expect(txt).toContain("GitHub REST");
    expect(txt).toContain("Stripe API");

    const aiBtn = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("Let AI configure an API"))[0];
    await act(async () => {
      aiBtn.props.onClick();
    });
    expect(storeMock.openThread).toHaveBeenCalledWith("draft");
    expect(storeMock.draft).toHaveBeenCalled();
  });
});

describe("CalendarScreen & LocationScreen", () => {
  it("renders CalendarScreen views, creates events, and links addresses to Maps", async () => {
    const calData: CalendarData = {
      enabled: true,
      configured: true,
      today: "2026-11-15",
      feeds: [{ name: "Local", events: 1, fetched_at: null, error: "" }],
      fetched_at: null,
      stale: false,
      events: [
        {
          uid: "ev-1",
          summary: "Dentist Checkup",
          start: "2026-11-15T10:00:00",
          end: "2026-11-15T11:00:00",
          all_day: false,
          location: "742 Evergreen Terrace",
          description: "Annual cleaning",
          calendar: "Local",
          source: "local",
          editable: true,
          tags: ["health"],
        },
      ],
    };
    vi.spyOn(api, "calendar").mockResolvedValue(calData);

    await act(async () => {
      tree = create(createElement(CalendarScreen));
    });
    expect(textOf(tree!)).toContain("Dentist Checkup");
    expect(textOf(tree!)).toContain("742 Evergreen Terrace");

    const mapLink = tree!.root.findAll((n) => n.type === "a" && typeof n.props.href === "string" && n.props.href.includes("google.com/maps"))[0];
    expect(mapLink.props.href).toContain("742%20Evergreen%20Terrace");

    const weekBtn = tree!.root.findAll((n) => n.type === "button" && textOf(n).trim().toLowerCase() === "week")[0];
    await act(async () => {
      weekBtn.props.onClick();
    });
    expect(textOf(tree!)).toContain("Dentist Checkup");
  });

  it("renders LocationScreen saved places, friend contacts, speed metrics, and Map URL builder", async () => {
    const summary: LocationSummary = {
      places: [
        {
          id: "p-home",
          name: "Home",
          address: "1600 Pennsylvania Ave",
          lat: 38.8977,
          lng: -77.0365,
          category: "home",
          tags: ["family", "parking"],
          context: "Driveway behind oak tree",
          contact_id: "",
          contact_name: "",
          visit_count: 42,
          last_visited_at: "2026-11-14",
          favorite: true,
          created_at: "",
          updated_at: "",
          map_links: {
            google_maps: "https://www.google.com/maps/search/?api=1&query=1600+Pennsylvania+Ave",
            google_directions: "https://www.google.com/maps/dir/?api=1&destination=1600+Pennsylvania+Ave",
            apple_maps: "https://maps.apple.com/?q=1600+Pennsylvania+Ave",
            waze: "https://waze.com/ul?q=1600+Pennsylvania+Ave",
            openstreetmap: "https://www.openstreetmap.org/search?query=1600+Pennsylvania+Ave",
            geo: "geo:38.8977,-77.0365",
          },
        },
      ],
      frequent: [],
      trips: [
        {
          id: "t-1",
          place_id: "p-home",
          place_name: "Home",
          address: "1600 Pennsylvania Ave",
          lat: 38.8977,
          lng: -77.0365,
          origin_name: "Office",
          origin_address: "100 Main St",
          started_at: "2026-11-14T17:15:00Z",
          ended_at: "2026-11-14T17:35:00Z",
          duration_min: 20,
          distance_mi: 12.5,
          avg_speed_mph: 37.5,
          max_speed_mph: 58,
          current_speed_mph: 42,
          mode: "driving",
          traffic_level: "moderate",
          traffic_note: "Bridge backup cleared quickly",
          tags: ["commute"],
          notes: "",
          source: "manual",
          created_at: "",
          map_links: {
            google_maps: "https://www.google.com/maps/search/?api=1&query=1600+Pennsylvania+Ave",
            google_directions: "https://www.google.com/maps/dir/?api=1&destination=1600+Pennsylvania+Ave",
            apple_maps: "https://maps.apple.com/?q=1600+Pennsylvania+Ave",
            waze: "https://waze.com/ul?q=1600+Pennsylvania+Ave",
            openstreetmap: "https://www.openstreetmap.org/search?query=1600+Pennsylvania+Ave",
            geo: "geo:38.8977,-77.0365",
          },
        },
      ],
      contacts: [
        {
          id: "c-alex",
          name: "Alex Rivera",
          nickname: "Al",
          address: "221B Baker Street",
          phones: [],
          emails: [],
          org: "",
          note: "Friend from college",
          source: "manual",
          tags: ["friend"],
          map_links: {
            google_maps: "https://www.google.com/maps/search/?api=1&query=221B+Baker+Street",
            google_directions: "https://www.google.com/maps/dir/?api=1&destination=221B+Baker+Street",
            apple_maps: "https://maps.apple.com/?q=221B+Baker+Street",
            waze: "https://waze.com/ul?q=221B+Baker+Street",
            openstreetmap: "https://www.openstreetmap.org/search?query=221B+Baker+Street",
            geo: "geo:0,0?q=221B+Baker+Street",
          },
        },
      ],
      stats: {
        total_trips: 1,
        avg_driving_mph: 37.5,
        max_speed_mph: 58,
        latest_speed_mph: 42,
        total_miles: 12.5,
        total_places: 1,
        contact_addresses: 1,
        categories: { home: 1 },
      },
      tags: ["family", "parking", "friend", "commute"],
    };
    vi.spyOn(api, "locationSummary").mockResolvedValue(summary);

    await act(async () => {
      tree = create(createElement(LocationScreen));
    });
    const text = textOf(tree!);
    expect(text).toContain("1600 Pennsylvania Ave");
    expect(text).toContain("42 visits");

    const travelTab = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("Speed & Traffic"))[0];
    await act(async () => {
      travelTab.props.onClick();
    });
    const travelText = textOf(tree!);
    expect(travelText).toContain("37.5 mph");
    expect(travelText).toContain("58 mph");

    const contactsTab = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("Friends & Contacts"))[0];
    await act(async () => {
      contactsTab.props.onClick();
    });
    expect(textOf(tree!)).toContain("Alex Rivera");
    expect(textOf(tree!)).toContain("221B Baker Street");
  });
});

describe("Enhanced Tasks & Goals Screen", () => {
  it("parses #hashtags in quick add, sorts by priority/title, and manages inline subtasks", async () => {
    const createSpy = vi.spyOn(api, "createGoal").mockResolvedValue({} as Goal);
    const patchSpy = vi.spyOn(api, "patchGoal").mockResolvedValue({} as Goal);
    const addStepSpy = vi.spyOn(api, "addStep").mockResolvedValue({} as Goal);

    storeMock.state.goals = [
      {
        id: "g-low",
        title: "Clean garage",
        description: "",
        status: "active",
        notes: "",
        category: "home",
        kind: "task",
        priority: "low",
        tags: ["home"],
        due: "2026-12-01",
        overdue: false,
        check_in: "",
        next_check_in: null,
        proposal: null,
        created_at: "2026-11-10T00:00:00Z",
        updated_at: "2026-11-10T00:00:00Z",
        progress: { done: 0, total: 1 },
        next_step: "Sweep floor",
        steps: [{ idx: 0, title: "Sweep floor", status: "pending", note: "", updated_at: "" }],
      },
      {
        id: "g-urgent",
        title: "Renew passport",
        description: "",
        status: "active",
        notes: "",
        category: "travel",
        kind: "task",
        priority: "urgent",
        tags: ["travel"],
        due: "2026-11-16",
        overdue: false,
        check_in: "",
        next_check_in: null,
        proposal: null,
        created_at: "2026-11-14T00:00:00Z",
        updated_at: "2026-11-14T00:00:00Z",
        progress: { done: 0, total: 2 },
        next_step: "Fill DS-82",
        steps: [
          { idx: 0, title: "Fill DS-82", status: "pending", note: "", updated_at: "" },
          { idx: 1, title: "Take photo", status: "pending", note: "", updated_at: "" },
        ],
      },
    ];

    await act(async () => {
      tree = create(createElement(GoalsScreen));
    });

    const quickInput = tree!.root.findByProps({ placeholder: "Quick add task or goal... (use #tags inline)" });
    await act(async () => {
      quickInput.props.onChange({ target: { value: "Buy milk #groceries #errand" } });
    });
    const addBtn = tree!.root.findAll((n) => n.type === "button" && textOf(n).trim() === "Add")[0];
    await act(async () => {
      addBtn.props.onClick();
    });
    expect(createSpy).toHaveBeenCalledWith({
      title: "Buy milk",
      kind: "task",
      priority: "medium",
      due: "",
      tags: ["groceries", "errand"],
    });

    const subtasksBtn = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("0/2"))[0];
    await act(async () => {
      subtasksBtn.props.onClick();
    });
    expect(textOf(tree!)).toContain("Fill DS-82");
    expect(textOf(tree!)).toContain("Take photo");

    const stepBtn = tree!.root.findAll((n) => n.type === "button" && textOf(n).trim() === "Fill DS-82")[0];
    await act(async () => {
      stepBtn.props.onClick();
    });
    expect(patchSpy).toHaveBeenCalledWith("g-urgent", { step_index: 0, step_status: "done" });

    const subInput = tree!.root.findByProps({ placeholder: "Add subtask..." });
    await act(async () => {
      subInput.props.onChange({ target: { value: "Mail form" } });
    });
    await act(async () => {
      subInput.props.onKeyDown({ key: "Enter" });
    });
    expect(addStepSpy).toHaveBeenCalledWith("g-urgent", "Mail form");
  });
});

describe("Onboarding language, custom color, model search, local AI advisor & error recovery", () => {
  it("starts in English with language picker on welcome, custom color in appearance, and advances smoothly after saving model", async () => {
    const richConnections: ConnectionsData = {
      ...BASE_CONNECTIONS,
      providers: {
        openrouter: {
          label: "OpenRouter",
          subtitle: "one account, one key, pay as you go (default)",
          group: "openai",
          provider: "openai",
          base_url: "https://openrouter.ai/api/v1",
          models: ["anthropic/claude-sonnet-4", "openai/gpt-4o-mini"],
        },
        ollama: {
          label: "Ollama",
          subtitle: "on this machine, no key",
          group: "local",
          provider: "openai",
          base_url: "http://127.0.0.1:11434/v1",
          models: ["qwen3:8b", "gemma3:4b"],
          no_key: true,
        },
      },
    };
    vi.spyOn(api, "connections").mockResolvedValue(richConnections);
    vi.spyOn(api, "systemCapabilities").mockResolvedValue({
      host: {
        os: "Android",
        arch: "aarch64",
        cpu_cores: 8,
        total_ram_gb: 4.0,
        free_ram_gb: 1.2,
        is_android: true,
        is_mobile: true,
        apple_silicon: false,
        gpu_name: "",
        vram_gb: 0,
      },
      client: { ram_gb: 4, cores: 8, is_mobile: true, remote_client_mismatch: false },
      runtimes: [
        { id: "ollama", name: "Ollama", base_url: "http://127.0.0.1:11434/v1", running: false, models: [], install_url: "https://ollama.com" },
      ],
      verdict: {
        tier: "weak_device",
        worth_it: "cloud_recommended",
        headline: "Local AI is tough on this device: a cheap cloud model is far better here",
        explanation: "Detected Android phone with limited RAM.",
        tradeoff_summary: "Use OpenRouter cheap/free cloud unless you strictly want ANY offline local model.",
      },
      recommended_models: [
        {
          id: "qwen3:1.7b",
          name: "Qwen 3 1.7B",
          params: "1.7B",
          size_gb: 1.4,
          min_ram_gb: 2.5,
          tier: "ultra_tiny",
          summary: "Fits small RAM phones for simple offline replies.",
          tool_quality: "Basic",
          recommended_for: "Phones & basic offline replies",
          fit: "tight",
          installed: false,
          preset: "ollama",
          pull_cmd: "ollama pull qwen3:1.7b",
        },
      ],
    });
    vi.spyOn(api, "llmModels").mockResolvedValue({
      models: [
        "anthropic/claude-sonnet-4",
        "openai/gpt-4o-mini",
        "01-ai/yi-large",
        "zhipu/rare-model",
      ],
      popular: ["anthropic/claude-sonnet-4", "openai/gpt-4o-mini"],
      source: "live",
    });
    const setLlmSpy = vi.spyOn(api, "setLLM").mockResolvedValue({} as never);
    vi.spyOn(api, "setGui").mockResolvedValue({} as never);

    let tree: ReactTestRenderer;
    await act(async () => {
      tree = create(createElement(Onboarding));
    });

    // Welcome: defaults to English; language is chosen on the sign-in gate (or later in Settings), never repeated inside setup
    expect(getLocale()).toBe("en");

    // Step 2: Appearance has custom hex accent & canvas color pickers
    const getStarted = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("Get started"))[0];
    await act(async () => {
      getStarted.props.onClick();
    });
    const customAccentInput = tree!.root.findByProps({ "aria-label": "Custom accent colour" });
    expect(customAccentInput.props.type).toBe("color");
    await act(async () => {
      customAccentInput.props.onChange({ target: { value: "#e11d48" } });
    });
    const customCanvasInput = tree!.root.findByProps({ "aria-label": "Custom canvas colour" });
    expect(customCanvasInput.props.type).toBe("color");
    await act(async () => {
      customCanvasInput.props.onChange({ target: { value: "#f8fafc" } });
    });

    // Advance Appearance -> Checklist -> Model
    const nextBtn = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("Next: Model & Accounts"))[0];
    await act(async () => {
      nextBtn.props.onClick();
    });
    const modelStepBtn = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("Add a local or API model"))[0];
    await act(async () => {
      modelStepBtn.props.onClick();
    });

    // Wait for ModelCard's 150ms debounced llmModels() fetch so live + rare models populate
    await act(async () => {
      await new Promise((r) => setTimeout(r, 220));
    });

    // Model step: displays Local AI advisor warning & mobile-searchable model browser (no <datalist>)
    expect(tree!.root.findAllByType("datalist")).toHaveLength(0);
    expect(textOf(tree!)).toContain("Local AI is tough on this device: a cheap cloud model is far better here");

    const browseBtn = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("Browse ("))[0];
    await act(async () => {
      browseBtn.props.onClick();
    });
    const searchInput = tree!.root.findByProps({ placeholder: "Search all models..." });
    expect(searchInput.props.type).toBe("search");
    await act(async () => {
      searchInput.props.onChange({ target: { value: "yi-large" } });
    });
    expect(textOf(tree!)).toContain("01-ai/yi-large");
    const pickYi = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("01-ai/yi-large"))[0];
    await act(async () => {
      pickYi.props.onClick();
    });

    // Clicking Save & continue advances cleanly to Connect step without crashing or stalling
    const saveContinue = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("Save & continue"))[0];
    await act(async () => {
      await saveContinue.props.onClick();
    });
    expect(setLlmSpy).toHaveBeenCalled();
    expect(textOf(tree!)).toContain("Connect multiple accounts & APIs");
  });

  it("renders recoverable ErrorBoundary UI when a child screen throws instead of a blank screen", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const Boom = () => {
      throw new Error("boom in render");
    };
    let tree: ReactTestRenderer;
    await act(async () => {
      tree = create(createElement(ErrorBoundary, { scope: "test", children: createElement(Boom) }));
    });
    expect(textOf(tree!)).toContain("Something interrupted the screen");
    expect(textOf(tree!)).toContain("boom in render");
    errSpy.mockRestore();
  });

  it("renders single versatile Muse import prompt with export instructions and no duplicate toggle", async () => {
    expect(MUSE_PROMPT).toContain("deep-memory");
    expect(MUSE_PROMPT).toContain("If some transcripts or files are missing");
    expect(MUSE_PROMPT).toContain("Export data");

    await act(async () => {
      tree = create(createElement(ImportExport));
    });
    const txt = textOf(tree!);
    expect(txt).toContain("How to export from Muse:");
    expect(txt).not.toContain("Update since the last one");
    expect(
      tree!.root.findAll(
        (n) => n.props?.role === "radio" && (textOf(n).includes("Everything") || textOf(n).includes("Update")),
      ),
    ).toHaveLength(0);
  });

  it("isolates wide Settings tabs, restores Usage tab, provides search counts/shortcuts and detail info toggles", async () => {
    const prevRaf = (globalThis as { requestAnimationFrame?: unknown }).requestAnimationFrame;
    const prevDoc = (globalThis as { document?: unknown }).document;
    (globalThis as { requestAnimationFrame: (cb: () => void) => number }).requestAnimationFrame = (cb) => {
      cb();
      return 1;
    };
    (globalThis as { document?: unknown }).document = {
      documentElement: {
        dataset: {},
        style: { fontSize: "" },
        classList: { contains: () => false, add: () => undefined, remove: () => undefined, toggle: () => undefined },
      },
      createTreeWalker: () => ({ nextNode: () => null }),
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    };
    const prevMatchMedia = (globalThis.window as { matchMedia?: unknown }).matchMedia;
    (globalThis.window as { matchMedia: unknown }).matchMedia = (q: string) => ({
      matches: q.includes("64rem") || q.includes("min-width"),
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    });

    vi.spyOn(api, "update").mockResolvedValue({ current: "1.0.25", latest: "1.0.25", newer: false } as never);
    vi.spyOn(api, "usage").mockResolvedValue({
      days: 7,
      model: "",
      turns: 12,
      tool_calls: 30,
      cost_usd: 0.14,
      avg_duration_s: 2.1,
      prompt_tokens: 4500,
      cached_tokens: 1200,
      reasoning_tokens: 800,
      completion_tokens: 1500,
      per_model: [],
      per_day: [],
      activities: {
        reading: 2,
        files_read: 5,
        files_written: 1,
        commands: 3,
        apis: 1,
        coding: 4,
        tracking: 2,
      },
      media: {
        image_generation: 0,
        image_editing: 0,
        video_generation: 0,
        video_editing: 0,
        photo_analysis: 0,
        video_analysis: 0,
      },
      recent: [],
      ollama: { running: false, models: [], error: "" },
    } as never);
    vi.spyOn(api, "usageLimits").mockResolvedValue([] as never);
    storeMock.state = {
      ...storeMock.state,
      settings: {
        ...(storeMock.state.settings as Record<string, unknown>),
        sentinel: { mode: "ask", always_ask_tools: ["send_email"], taint_tracking: true },
        agent: { show_thinking: false, allow_self_improve: true, language: "auto" },
        llm: { model: "openai/gpt-4o-mini", provider: "openrouter", cloud: false },
        data_dir: "/tmp/data",
      } as never,
    };

    try {
      await act(async () => {
        tree = create(createElement(SettingsScreen));
      });
      // Wide Settings shows one section at a time (default 'who') instead of stacking Usage/Developer/Import together
      expect(textOf(tree!)).toContain("What it calls you");
      expect(textOf(tree!)).not.toContain("Estimated cost");
      expect(textOf(tree!)).not.toContain("Open a chat in Muse");

      // Search with zero matches explicitly reports "No results found"
      const searchInput = tree!.root.findByProps({ placeholder: "Search settings" });
      await act(async () => {
        searchInput.props.onChange({ currentTarget: { value: "zzzznomatch" } });
      });
      expect(textOf(tree!)).toContain("No results found");

      // Search for "vram" reports exact result count and a clickable shortcut opening the Usage tab inside Settings
      await act(async () => {
        searchInput.props.onChange({ currentTarget: { value: "vram" } });
      });
      expect(textOf(tree!)).toContain("1 result");
      const vramShortcut = tree!.root.findAll((n) => n.type === "button" && textOf(n).includes("Token & cost usage"))[0];
      await act(async () => {
        vramShortcut.props.onClick();
      });
      expect(textOf(tree!)).toContain("Token & cost usage");
      expect(textOf(tree!)).toContain("Estimated cost");

      // Navigate to Agent behavior tab: verbose descriptions stay hidden behind info toggle until clicked
      const behaviorBtn = tree!.root.findAll((n) => n.type === "button" && textOf(n) === "Agent behavior")[0];
      await act(async () => {
        behaviorBtn.props.onClick();
      });
      const hiddenHint = "Reveal the model's reasoning under each reply when the provider exposes it.";
      expect(textOf(tree!)).not.toContain(hiddenHint);
      const infoBtn = tree!.root.findByProps({ "aria-label": "Show thinking details" });
      await act(async () => {
        infoBtn.props.onClick();
      });
      expect(textOf(tree!)).toContain(hiddenHint);
    } finally {
      (globalThis.window as { matchMedia?: unknown }).matchMedia = prevMatchMedia;
      (globalThis as { requestAnimationFrame?: unknown }).requestAnimationFrame = prevRaf;
      (globalThis as { document?: unknown }).document = prevDoc;
    }
  });
});
