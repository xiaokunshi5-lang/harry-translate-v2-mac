import {
  ArrowRight,
  BookmarkPlus,
  Captions,
  Languages,
  Radio,
  Settings2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { QuickTermBar } from "../components/glossary/QuickTermBar";
import { SummaryWidget } from "../components/summary/SummaryWidget";
import { LanguageStatusCapsule } from "../components/status/LanguageStatusCapsule";
import { sanitizeTargetForLang } from "../lib/subtitles/languagePurity";
import { getConfig, getHealth, switchSession } from "../shared/apiClient";
import type {
  ConfigResponse,
  EngineEvent,
  HealthResponse,
  LanguageCode,
  SubtitleEmphasis,
  SummarySnapshot,
  UserSettings,
} from "../shared/types";
import { ChannelSocket, type SocketPhase } from "../shared/wsClient";

type Channel = "main" | "en" | "zh";

interface ChannelState {
  phase: SocketPhase;
  engineState: string;
  healthStatus: string;
  backoffSeconds: number;
  reconnectAttempt: number;
  reason: string;
  source: string;
  target: string;
  final: boolean;
  sentId: number | null;
  sourceHash: string;
  paraId: string;
  patchVersion: number;
  windowId: "main";
}

const INITIAL_CHANNEL: ChannelState = {
  phase: "idle",
  engineState: "idle",
  healthStatus: "idle",
  backoffSeconds: 0,
  reconnectAttempt: 0,
  reason: "",
  source: "",
  target: "",
  final: false,
  sentId: null,
  sourceHash: "",
  paraId: "",
  patchVersion: 0,
  windowId: "main",
};

const TARGET_LANGUAGES: Array<{
  value: Exclude<LanguageCode, "auto">;
  label: string;
}> = [
  { value: "en", label: "英语" },
  { value: "zh", label: "中文" },
  { value: "ja", label: "日语" },
  { value: "ko", label: "韩语" },
  { value: "fr", label: "法语" },
  { value: "es", label: "西班牙语" },
  { value: "de", label: "德语" },
  { value: "it", label: "意大利语" },
];

function useChannel(
  channel: Channel,
  needsSetup: boolean,
  targetLang: string,
  enabled = true,
  onSummaryUpdate?: (snapshot: SummarySnapshot) => void,
  onConfigUpdate?: (settings: UserSettings) => void,
): ChannelState {
  const [state, setState] = useState<ChannelState>(INITIAL_CHANNEL);
  const targetLangRef = useRef(targetLang);
  targetLangRef.current = targetLang;
  const summaryUpdateRef = useRef(onSummaryUpdate);
  summaryUpdateRef.current = onSummaryUpdate;
  const configUpdateRef = useRef(onConfigUpdate);
  configUpdateRef.current = onConfigUpdate;

  useEffect(() => {
    setState(INITIAL_CHANNEL);
    if (!enabled) return;
    if (needsSetup) {
      setState({
        ...INITIAL_CHANNEL,
        reason: "needs_setup",
      });
      return;
    }
    const socket = new ChannelSocket(channel, {
      onPhase: (phase) => setState((current) => ({ ...current, phase })),
      onEvent: (event: EngineEvent) => {
        if (event.type === "config_updated" && event.settings) {
          configUpdateRef.current?.(event.settings);
          return;
        }
        if (event.type === "summary.update") {
          summaryUpdateRef.current?.({
            enabled: event.enabled === true,
            mode: event.mode,
            model: event.model,
            engine_used: event.engine_used,
            pending_words: Number(event.pending_words || 0),
            pinned: Array.isArray(event.pinned) ? event.pinned : [],
            archived: Array.isArray(event.archived) ? event.archived : [],
          });
          return;
        }
        if (event.type === "transcript.patch") {
          setState((current) => {
            const patchSentId = event.sent_id;
            const hasPatchPayload =
              event.window_id === "main" &&
              typeof event.patched_source === "string" &&
              typeof event.patched_target === "string" &&
              typeof event.new_source_hash === "string";
            if (
              !hasPatchPayload ||
              !current.final ||
              current.patchVersion > 0 ||
              current.sentId !== patchSentId ||
              current.sourceHash !== event.source_hash
            ) {
              return current;
            }
            const source = event.patched_source as string;
            const rawTarget = event.patched_target as string;
            return {
              ...current,
              source,
              target: sanitizeTargetForLang(
                rawTarget,
                source,
                targetLangRef.current,
              ),
              sourceHash: event.new_source_hash as string,
              patchVersion: current.patchVersion + 1,
            };
          });
          return;
        }
        if (event.type === "session_reset") {
          summaryUpdateRef.current?.({
            enabled: true,
            pinned: [],
            archived: [],
          });
          setState((current) => ({
            ...current,
            source: "",
            target: "",
            final: false,
            sentId: null,
            sourceHash: "",
            paraId: "",
            patchVersion: 0,
          }));
          return;
        }
        if (event.type === "engine_state") {
          setState((current) => ({
            ...current,
            engineState: event.state || "idle",
            healthStatus: event.health_status || event.state || "idle",
            backoffSeconds: Number(event.backoff_seconds || 0),
            reconnectAttempt: Number(event.reconnect_attempt || 0),
            reason: event.reason || "",
          }));
          return;
        }
        if (event.type === "subtitle" || event.type === "subtitle.final") {
          setState((current) => {
            const nextSource = event.source_text ?? event.source ?? current.source;
            const hasIncomingTarget =
              event.target_text !== undefined || event.target !== undefined;
            const rawTarget = String(event.target_text ?? event.target ?? "");
            const nextTarget = hasIncomingTarget
              ? sanitizeTargetForLang(rawTarget, nextSource, targetLangRef.current)
              : current.target;
            const final = Boolean(
              event.is_final || event.type === "subtitle.final",
            );
            const idValue = final
              ? event.merged_with_prev_id ?? event.sent_id
              : event.sent_id;
            return {
              ...current,
              source: nextSource,
              target: nextTarget,
              final,
              sentId:
                idValue === undefined || !Number.isFinite(Number(idValue))
                  ? current.sentId
                  : Math.trunc(Number(idValue)),
              sourceHash: event.source_hash ?? current.sourceHash,
              paraId: event.para_id ?? current.paraId,
              patchVersion: final ? 0 : current.patchVersion,
            };
          });
        }
      },
    });
    socket.connect();
    return () => socket.close();
  }, [channel, enabled, needsSetup]);

  return state;
}

function emphasisWeight(
  role: "source" | "target",
  emphasis: SubtitleEmphasis,
): { fontWeight: number; opacity: number } {
  if (emphasis === "balanced") return { fontWeight: 500, opacity: 0.9 };
  const primary =
    (emphasis === "translation_primary" && role === "target") ||
    (emphasis === "source_primary" && role === "source");
  return primary
    ? { fontWeight: 600, opacity: 1 }
    : { fontWeight: 400, opacity: 0.72 };
}

function backgroundOpacity(opacity: number): string {
  return `rgba(17, 31, 27, ${Math.max(0.08, Math.min(1, opacity / 100))})`;
}

function SubtitleRows({
  settings,
  state,
}: {
  settings: UserSettings;
  state: ChannelState;
}) {
  const showSource = settings.subtitle_display_mode !== "translation";
  const showTarget = settings.subtitle_display_mode !== "original";
  const sourceEmphasis = emphasisWeight("source", settings.subtitle_emphasis);
  const targetEmphasis = emphasisWeight("target", settings.subtitle_emphasis);
  const candidateRows = [
    showSource
      ? {
          role: "source" as const,
          content: state.source,
          style: {
            ...sourceEmphasis,
            color: settings.mic_subtitle_color,
            fontSize: `${settings.source_font_size}px`,
          },
        }
      : null,
    showTarget
      ? {
          role: "target" as const,
          content: state.target,
          style: {
            ...targetEmphasis,
            color: settings.subtitle_color,
            fontSize: `${settings.target_font_size}px`,
          },
        }
      : null,
  ];
  const rows = candidateRows.filter(
    (row): row is NonNullable<typeof row> =>
      row !== null && row.content.trim().length > 0,
  );
  if (settings.bilingual_layout_order === "target_top") rows.reverse();

  return (
    <div
      className="monitor-focus__subtitle"
      style={{
        background: backgroundOpacity(settings.background_opacity),
        lineHeight: settings.line_height_multiplier,
        textAlign: settings.subtitle_alignment,
      }}
    >
      {rows.map((row) => (
        <div
          key={row.role}
          className={`monitor-subtitle-row monitor-subtitle-row--${row.role}${
            state.final ? " is-final" : ""
          }`}
          data-window-id={state.windowId}
          data-sent-id={state.sentId ?? undefined}
          data-source-hash={state.sourceHash || undefined}
          data-para-id={state.paraId || undefined}
          style={row.style}
        >
          {row.content}
        </div>
      ))}
    </div>
  );
}

function Frame({
  children,
  needsSetup,
  health,
  quickOptionId,
}: {
  children: React.ReactNode;
  needsSetup: boolean;
  health: HealthResponse | null;
  quickOptionId: string;
}) {
  const [quickOpen, setQuickOpen] = useState(false);

  return (
    <main className="monitor-shell">
      <header className="monitor-topbar">
        <a href="/" className="monitor-brand">
          <span>H</span>
          <strong>HARRY Translate</strong>
        </a>
        <span className={`monitor-global-state ${needsSetup ? "is-warning" : "is-ready"}`}>
          <i />
          {needsSetup ? "等待配置" : "本地服务可用"}
          {health?.port ? ` · :${health.port}` : ""}
        </span>
        <button
          type="button"
          className={`monitor-quick-toggle${quickOpen ? " is-active" : ""}`}
          aria-expanded={quickOpen}
          onClick={() => setQuickOpen((value) => !value)}
        >
          <BookmarkPlus size={16} />
          快捷词条
        </button>
        <a href="/settings" className="monitor-settings">
          <Settings2 size={17} />
          设置
        </a>
      </header>
      <div className="monitor-quick-drawer">
        <QuickTermBar
          compact
          open={quickOpen}
          defaultOptionId={quickOptionId}
          onOpenChange={setQuickOpen}
        />
      </div>
      {children}
    </main>
  );
}

export function MonitorScreens() {
  const path = window.location.pathname;
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState("");
  const [summarySnapshot, setSummarySnapshot] =
    useState<SummarySnapshot | null>(null);
  const needsSetup = health?.needs_setup ?? true;
  const settings = config?.user_settings || config?.settings || null;
  const selectedChannel: Channel =
    path === "/en" ? "en" : path === "/zh" ? "zh" : "main";
  const effectiveTarget: Exclude<LanguageCode, "auto"> =
    path === "/en"
      ? "en"
      : path === "/zh"
      ? "zh"
      : settings?.target_lang || "zh";
  const handleConfigUpdate = useCallback((nextSettings: UserSettings) => {
    setConfig((current) =>
      current
        ? {
            ...current,
            settings: nextSettings,
            user_settings: nextSettings,
          }
        : current,
    );
    if (!nextSettings.summary_enabled) {
      setSummarySnapshot({ enabled: false, pinned: [], archived: [] });
    }
  }, []);
  const channelState = useChannel(selectedChannel, needsSetup, effectiveTarget, true, setSummarySnapshot, handleConfigUpdate);

  useEffect(() => {
    void Promise.all([getHealth(), getConfig()])
      .then(([nextHealth, nextConfig]) => {
        setHealth(nextHealth);
        setConfig(nextConfig);
      })
      .catch(() => {
        setHealth(null);
        setConfig(null);
      });
  }, []);

  const routeTitle = useMemo(() => {
    if (path === "/en") return "中译英单窗大屏";
    if (path === "/zh") return "英译中单窗大屏";
    if (path === "/split") return "单窗双语监看";
    return "实时同传控制台";
  }, [path]);

  const canSwitchTarget = path !== "/en" && path !== "/zh";
  const quickOptionId =
    effectiveTarget === "zh"
      ? "terms_trans:en->zh"
      : effectiveTarget === "fr"
      ? "terms_trans:zh->fr"
      : effectiveTarget === "ja"
      ? "terms_trans:zh->ja"
      : "terms_trans:zh->en";

  const changeTarget = async (targetLang: string) => {
    if (!settings || targetLang === effectiveTarget) return;
    setSwitching(true);
    setSwitchError("");
    try {
      const response = await switchSession({
        source_lang: settings.source_lang,
        target_lang: targetLang,
      });
      setConfig((current) =>
        current
          ? {
              ...current,
              settings: response.user_settings,
              user_settings: response.user_settings,
            }
          : current,
      );
    } catch (error) {
      setSwitchError(error instanceof Error ? error.message : "切换失败");
    } finally {
      setSwitching(false);
    }
  };

  const handleSummaryDisabled = useCallback(() => {
    setSummarySnapshot({ enabled: false, pinned: [], archived: [] });
    setConfig((current) =>
      current
        ? {
            ...current,
            settings: { ...current.settings, summary_enabled: false },
            user_settings: { ...current.user_settings, summary_enabled: false },
          }
        : current,
    );
  }, []);

  return (
    <Frame needsSetup={needsSetup} health={health} quickOptionId={quickOptionId}>
      <section className="monitor-focus monitor-focus--single">
        <header className="monitor-focus__top">
          <div>
            <span className="monitor-eyebrow">SINGLE WINDOW · BILINGUAL</span>
            <h1>{routeTitle}</h1>
          </div>
          <div className="monitor-focus__controls">
            {canSwitchTarget ? (
              <label className="monitor-language-switch">
                <Languages size={15} />
                <span>目标语言</span>
                <select
                  aria-label="目标语言快速切换"
                  value={effectiveTarget}
                  disabled={!settings || switching}
                  onChange={(event) => void changeTarget(event.target.value)}
                >
                  {TARGET_LANGUAGES.map((language) => (
                    <option key={language.value} value={language.value}>
                      {language.label}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <LanguageStatusCapsule
              engineState={channelState.engineState}
              healthStatus={channelState.healthStatus}
              reason={channelState.reason}
              backoffSeconds={channelState.backoffSeconds}
              reconnectAttempt={channelState.reconnectAttempt}
              needsSetup={needsSetup}
              switching={switching}
            />
          </div>
        </header>

        <div className="monitor-focus__status" aria-live="polite">
          <span>
            {channelState.phase === "open" ? "本地单通道已连接" : "本地单通道等待中"}
          </span>
          {channelState.reason ? <small>{channelState.reason}</small> : null}
          {switchError ? <small>{switchError}</small> : null}
        </div>

        {settings ? (
          <SubtitleRows settings={settings} state={channelState} />
        ) : (
          <div className="monitor-focus__loading">正在读取单窗字幕配置…</div>
        )}

        <footer className="monitor-focus__footer">
          <nav className="monitor-route-links" aria-label="视图与设置">
            <a href="/en">
              <Captions size={15} />
              中译英
            </a>
            <a href="/zh">
              <Radio size={15} />
              英译中
            </a>
            <a href="/settings">
              字幕与热词设置
              <ArrowRight size={14} />
            </a>
          </nav>
          <div className="monitor-focus__facts">
            <span>50 ms 微切片</span>
            <span>单一上游会话</span>
            <span>零连接不推流</span>
          </div>
        </footer>
      </section>
      <SummaryWidget
        enabled={settings?.summary_enabled === true}
        liveSnapshot={summarySnapshot}
        onDisabled={handleSummaryDisabled}
      />
    </Frame>
  );
}
