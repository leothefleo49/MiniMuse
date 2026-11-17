import { Check, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { useT } from "../i18n";
import { cx } from "../util";

type Lineup = Awaited<ReturnType<typeof api.models>>;
type Pick = { key: string; model: string; label: string; body: Record<string, unknown> };

const shortModel = (m: string) => (m.includes("/") ? m.split("/").pop() || m : m);

/**
 * Settings > Model: the model that is answering right now, by its real name, and every model
 * that can be picked instead (the saved ones, then each added endpoint's), one tap to switch.
 */
export function ActiveModelList() {
  const { state, toast, refreshSettings } = useStore();
  const t = useT();
  const [lineup, setLineup] = useState<Lineup | null>(null);
  const [switching, setSwitching] = useState("");
  useEffect(() => {
    api
      .models()
      .then(setLineup)
      .catch(() => setLineup(null));
  }, [state.connectionsVersion, state.settings?.llm.model]);

  const active = lineup?.default?.model || state.settings?.llm?.model || "";
  const picks: Pick[] = [];
  const seen = new Set<string>();
  for (const opt of lineup?.options ?? []) {
    const body: Record<string, unknown> = { model: opt.model };
    if (opt.endpoint) body.endpoint = opt.endpoint;
    else {
      if (opt.provider) body.provider = opt.provider;
      if (opt.base_url) body.base_url = opt.base_url;
    }
    const key = `opt:${opt.endpoint || opt.base_url || ""}:${opt.model}`;
    seen.add(opt.model);
    picks.push({ key, model: opt.model, label: opt.label || opt.model, body });
  }
  for (const ep of lineup?.endpoints ?? []) {
    for (const model of ep.models) {
      if (seen.has(model)) continue;
      picks.push({ key: `ep:${ep.id}:${model}`, model, label: `${model} (${ep.label})`, body: { endpoint: ep.id, model } });
    }
  }

  const choose = (pick: Pick) => {
    setSwitching(pick.key);
    api
      .setLLM(pick.body)
      .then(async () => {
        toast(t("Switched to {model}", { model: shortModel(pick.model) }));
        await refreshSettings().catch(() => undefined);
        setLineup(await api.models().catch(() => lineup));
      })
      .catch((e: Error) => toast(e.message))
      .finally(() => setSwitching(""));
  };

  return (
    <div className="space-y-2" data-testid="active-model-list">
      <div className="flex items-center justify-between gap-3 text-[13.5px]">
        <span className="text-muted">{t("Answering with")}</span>
        <span className="min-w-0 truncate font-mono text-[12.5px]" title={active}>
          {active ? shortModel(active) : t("Not set yet")}
        </span>
      </div>
      {picks.length > 0 && (
        <div className="rounded-2xl border border-border/70 bg-surface p-1">
          {picks.map((pick) => {
            const selected = pick.model === active;
            return (
              <button
                key={pick.key}
                type="button"
                onClick={() => choose(pick)}
                disabled={switching !== ""}
                className={cx(
                  "flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left active:bg-surface-2",
                  selected && "bg-accent/10",
                )}
              >
                {switching === pick.key ? (
                  <Loader2 size={14} className="shrink-0 animate-spin text-accent" />
                ) : selected ? (
                  <Check size={14} className="shrink-0 text-accent" />
                ) : (
                  <span className="h-[14px] w-[14px] shrink-0" />
                )}
                <span className="min-w-0">
                  <span className="block truncate text-[13px] font-medium">{shortModel(pick.label)}</span>
                  <span className="block truncate text-[11.5px] text-muted">{pick.model}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
