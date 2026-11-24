import {
  BookOpenText,
  Captions,
  CheckCircle2,
  Download,
  ExternalLink,
  Gauge,
  LoaderCircle,
  LogOut,
  Mic2,
  SlidersHorizontal,
  Wifi,
  WifiOff,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { AudioLanguagePanel } from "../components/settings/AudioLanguagePanel";
import { Toggle } from "../components/settings/Controls";
import { GeneralDiagnosticsPanel } from "../components/settings/GeneralDiagnosticsPanel";
import { GlossaryInsightsPanel } from "../components/settings/GlossaryInsightsPanel";
import { PreviewStage } from "../components/settings/PreviewStage";
import { ProvidersPanel } from "../components/settings/ProvidersPanel";
import { SubtitlePanel } from "../components/settings/SubtitlePanel";
import { LanguageStatusCapsule } from "../components/status/LanguageStatusCapsule";
import {
  isTauriRuntime,
  tauriInvoke,
} from "../lib/backendBridge";
import {
  getOverlayStatus,
  getLatestSession,
  getSessionExportUrl,
  hideOverlay,
  isOverlayRealtimeActive,
  launchOverlay,
} from "../shared/apiClient";
import type {
  EngineEvent,
  OverlayControlResponse,
  OverlayStatus,
} from "../shared/types";
import { ChannelSocket, type SocketPhase } from "../shared/wsClient";
import {
  transcriptSessionRecorder,
  type SessionExportFormat,
} from "../shared/wsClient";
import { useSettingsStore } from "../store/settingsStore";

type TabId = "providers" | "audio" | "subtitles" | "glossary" | "general";

const TABS: Array<{
  id: TabId;
  label: string;
  caption: string;
  icon: typeof SlidersHorizontal;
}> = [
  { id: "providers", label: "模型与服务", caption: "Provider", icon: SlidersHorizontal },
  { id: "audio", label: "声音与语言", caption: "Audio", icon: Mic2 },
  { id: "subtitles", label: "字幕与外观", caption: "Subtitle", icon: Captions },
  { id: "glossary", label: "热词与总结", caption: "Insights", icon: BookOpenText },
  { id: "general", label: "通用与诊断", caption: "System", icon: Gauge },
];

// Settings only subscribes to the passive /ws/config feed, never /ws/main.
const CONFIG_SOCKET_CHANNEL = "config";
const DEFAULT_BROWSER_FALLBACK_URL = "/hud?fallback=browser";
const INITIAL_OVERLAY_STATUS: OverlayStatus = {
  running: false,
  visible: false,
  realtime_enabled: false,
  stop_error: null,
  registered: false,
  binary_available: false,
  fallback: null,
  control_url: null,
};

type BrowserFallbackHost = {
  open: (
    url?: string | URL,
    target?: string,
    features?: string,
  ) => Window | null;
  location: { assign: (url: string) => void };
};

export function configSyncOptions(
  event: Pick<EngineEvent, "needs_setup" | "key_mask">,
): { needsSetup?: boolean; keyMask?: string } {
  return {
    needsSetup: event.needs_setup,
    keyMask: event.key_mask,
  };
}

export function fallbackUrlForLaunch(
  result: Pick<OverlayControlResponse, "action" | "fallback_url">,
): string | null {
  if (result.action !== "browser_fallback") return null;
  return result.fallback_url || DEFAULT_BROWSER_FALLBACK_URL;
}

export function openBrowserFallback(
  url: string,
  browserWindow?: BrowserFallbackHost,
): "new_tab" | "same_tab" | "unavailable" {
  const targetWindow =
    browserWindow ||
    (typeof window === "undefined"
      ? null
      : (window as unknown as BrowserFallbackHost));
  if (!targetWindow) return "unavailable";
  const opened = targetWindow.open(url, "harry-translate-browser-fallback");
  if (opened) {
    try {
      opened.opener = null;
      opened.focus();
    } catch {
      // The window is already usable even if opener hardening is unavailable.
    }
    return "new_tab";
  }
  try {
    targetWindow.location.assign(url);
    return "same_tab";
  } catch {
    return "unavailable";
  }
}

export function SettingsView() {
  const [activeTab, setActiveTab] = useState<TabId>("providers");
  const [overlayStatus, setOverlayStatus] = useState<OverlayStatus>(
    INITIAL_OVERLAY_STATUS,
  );
  const [overlayAction, setOverlayAction] = useState<"launch" | "hide" | null>(
    null,
  );
  const [overlayError, setOverlayError] = useState("");
  const [auxiliaryError, setAuxiliaryError] = useState("");
  const [appQuitBusy, setAppQuitBusy] = useState(false);
  const [appQuitError, setAppQuitError] = useState("");
  const [configSocketPhase, setConfigSocketPhase] =
    useState<SocketPhase>("idle");
  const [latestSessionId, setLatestSessionId] = useState<string | null>(null);
  const [exportMessage, setExportMessage] = useState("");
  const initialized = useRef(false);
  const mountedRef = useRef(true);
  const overlayBusyRef = useRef(false);
  const overlayStatusRequestId = useRef(0);
  const {
    status,
    saveState,
    error,
    config,
    draft,
    catalog,
    health,
    testState,
    testResult,
    init,
    flush,
    applyImmediate,
    syncExternalSettings,
    saveSecret,
    runConnectionTest,
  } = useSettingsStore();

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    void init();
  }, [init]);

  const refreshOverlayStatus =
    useCallback(async (): Promise<OverlayStatus | null> => {
      const requestId = ++overlayStatusRequestId.current;
      try {
        const nextStatus = await getOverlayStatus();
        if (
          mountedRef.current &&
          requestId === overlayStatusRequestId.current
        ) {
          setOverlayStatus((current) => ({ ...current, ...nextStatus }));
        }
        return nextStatus;
      } catch {
        return null;
      }
    }, []);

  useEffect(() => {
    mountedRef.current = true;
    void refreshOverlayStatus();
    return () => {
      mountedRef.current = false;
    };
  }, [refreshOverlayStatus]);

  useEffect(() => {
    const refreshWhenActive = () => {
      if (overlayBusyRef.current) return;
      if (
        document.visibilityState === "visible" ||
        document.hasFocus()
      ) {
        void refreshOverlayStatus();
      }
    };
    window.addEventListener("focus", refreshWhenActive);
    document.addEventListener("visibilitychange", refreshWhenActive);
    return () => {
      window.removeEventListener("focus", refreshWhenActive);
      document.removeEventListener("visibilitychange", refreshWhenActive);
    };
  }, [refreshOverlayStatus]);

  useEffect(() => {
    let active = true;
    getLatestSession()
      .then((result) => {
        if (active) setLatestSessionId(result.session?.id || null);
      })
      .catch(() => {
        // Export remains available for the current recorder when the API is unavailable.
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const socket = new ChannelSocket(CONFIG_SOCKET_CHANNEL, {
      onPhase: setConfigSocketPhase,
      onEvent: (event) => {
        if (event.type === "config_updated" && event.settings) {
          syncExternalSettings(event.settings, configSyncOptions(event));
        }
        if (
          event.type === "overlay_status" &&
          event.overlay &&
          !overlayBusyRef.current
        ) {
          overlayStatusRequestId.current += 1;
          setOverlayStatus((current) => ({ ...current, ...event.overlay }));
          setOverlayError("");
        }
      },
    });
    socket.connect();
    return () => socket.close();
  }, [syncExternalSettings]);

  if (status === "loading" || status === "idle") {
    return (
      <main className="settings-loading">
        <LoaderCircle size={24} className="spin" />
        <span>正在读取本地配置</span>
      </main>
    );
  }

  if (status === "error" || !draft) {
    return (
      <main className="settings-loading">
        <strong>配置读取失败</strong>
        <span>{error || "request_failed"}</span>
        <button type="button" className="settings-button" onClick={() => void init()}>
          重试
        </button>
      </main>
    );
  }

  const needsSetup = config?.needs_setup ?? true;
  const nativeRuntime = isTauriRuntime();
  const apiKeyMask = config?.api_key_masked || config?.key_mask || "";
  const activeLabel = TABS.find((tab) => tab.id === activeTab)?.label || "";
  const mainEngine = config?.engine_states?.main;
  const overlayBusy = overlayAction !== null;
  const isRealtimeOn = isOverlayRealtimeActive(overlayStatus);
  const overlayStateText = overlayBusy
    ? "正在切换…"
    : isRealtimeOn
      ? "实时翻译已开启"
      : needsSetup
        ? "配置密钥后可开启实时翻译"
        : overlayStatus.visible
          ? "实时翻译已关闭，悬浮窗仍可见"
          : "已待机（悬浮窗已隐藏）";

  const waitForAuthoritativeStatus = async (
    expectedEnabled: boolean,
    allowStarting: boolean,
  ): Promise<OverlayStatus | null> => {
    const attempts = allowStarting ? 13 : 1;
    let latest: OverlayStatus | null = null;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      latest = await refreshOverlayStatus();
      if (!latest) return null;
      const confirmed = expectedEnabled
        ? isOverlayRealtimeActive(latest)
        : latest.visible !== true && latest.realtime_enabled === false;
      if (confirmed || attempt === attempts - 1) return latest;
      await new Promise<void>((resolve) => {
        window.setTimeout(resolve, 150);
      });
    }
    return latest;
  };

  const toggleRealtimeTranslation = async (nextEnabled: boolean) => {
    if (overlayBusyRef.current) return;
    if (needsSetup) {
      setOverlayError("配置密钥后才可开启实时翻译");
      return;
    }

    overlayBusyRef.current = true;
    setOverlayAction(nextEnabled ? "launch" : "hide");
    setOverlayError("");
    try {
      const result = nextEnabled ? await launchOverlay() : await hideOverlay();
      const browserFallbackUrl = nextEnabled
        ? fallbackUrlForLaunch(result)
        : null;
      if (result.ok === false) {
        const current = await refreshOverlayStatus();
        const truthDetail =
          current === null
            ? ""
            : isOverlayRealtimeActive(current)
              ? "；服务端当前为开启状态"
              : current.visible
                ? "；悬浮窗仍可见"
                : "；服务端仍为关闭状态";
        const stopDetail = current?.stop_error
          ? `；停止错误：${current.stop_error}`
          : "";
        setOverlayError(
          `${nextEnabled ? "开启" : "关闭"}实时翻译失败：${
            result.message || "实时翻译切换未完成"
          }${truthDetail}${stopDetail}`,
        );
        return;
      }

      if (browserFallbackUrl) {
        const opened = openBrowserFallback(browserFallbackUrl);
        await refreshOverlayStatus();
        setOverlayError(
          opened === "new_tab"
            ? "原生壳不可用，已打开浏览器辅助大屏；实时翻译开关未开启"
            : opened === "same_tab"
              ? "原生壳不可用，已切换到浏览器辅助大屏；实时翻译开关未开启"
              : "浏览器拦截了辅助窗口，请使用“浏览器大屏（辅助）”链接",
        );
        return;
      }

      const current = await waitForAuthoritativeStatus(
        nextEnabled,
        nextEnabled && result.action === "starting",
      );
      if (!current) {
        throw new Error("无法读取实时翻译真实状态");
      }
      const confirmed = nextEnabled
        ? isOverlayRealtimeActive(current)
        : current.visible !== true && current.realtime_enabled === false;
      if (!confirmed) {
        setOverlayError(
          nextEnabled
            ? "实时翻译未能开启，开关仍保持后端真实状态"
            : "实时翻译未能完全关闭，请检查悬浮窗与音频状态",
        );
      }
    } catch (requestError) {
      const current = await refreshOverlayStatus();
      const truthDetail =
        current === null
          ? "；无法确认服务端状态"
          : isOverlayRealtimeActive(current)
            ? "；服务端当前为开启状态"
            : current.visible
              ? "；悬浮窗仍可见"
              : "；已回退到关闭状态";
      const message =
        requestError instanceof Error
          ? requestError.message
          : "请求失败";
      const stopDetail = current?.stop_error
        ? `；停止错误：${current.stop_error}`
        : "";
      setOverlayError(
        `${nextEnabled ? "开启" : "关闭"}实时翻译失败：${message}${truthDetail}${stopDetail}`,
      );
    } finally {
      overlayBusyRef.current = false;
      setOverlayAction(null);
    }
  };
  const syncTitle =
    saveState === "error"
      ? error || "实时同步失败"
      : saveState === "saving"
        ? "正在实时同步设置"
        : saveState === "saved"
          ? "设置已实时同步"
          : "实时同步已启用";
  const syncDetail =
    saveState === "saving"
      ? "正在合并连续操作并串行写入本机配置。"
      : saveState === "saved"
        ? "更改已推送到控制台与原生悬浮窗。"
        : saveState === "error"
          ? "本地草稿已保留，可继续操作后重试同步。"
          : "开关、选项与滑块会自动同步，无需手动保存。";
  const socketConnected = configSocketPhase === "open";
  const exportSessionId =
    transcriptSessionRecorder.count > 0
      ? transcriptSessionRecorder.currentSessionId
      : latestSessionId;

  const downloadSession = async (format: SessionExportFormat) => {
    if (!exportSessionId) {
      setExportMessage("还没有可导出的会话");
      return;
    }
    try {
      const url = await getSessionExportUrl(exportSessionId, format);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.rel = "noopener";
      anchor.click();
      setExportMessage(`已准备下载 ${format.toUpperCase()} 文件`);
    } catch (requestError) {
      setExportMessage(
        requestError instanceof Error ? requestError.message : "导出失败",
      );
    }
  };

  const openAuxiliaryMonitor = async () => {
    setAuxiliaryError("");
    try {
      await tauriInvoke("open_auxiliary_monitor_in_browser");
    } catch (requestError) {
      setAuxiliaryError(
        requestError instanceof Error
          ? requestError.message
          : "无法打开浏览器辅助大屏",
      );
    }
  };

  const quitApplication = async () => {
    if (appQuitBusy) return;
    const confirmed = window.confirm(
      "退出 HARRY Translate？实时翻译将立即停止，本地服务也会关闭。",
    );
    if (!confirmed) return;
    setAppQuitBusy(true);
    setAppQuitError("");
    try {
      await tauriInvoke("app_quit");
    } catch (requestError) {
      setAppQuitBusy(false);
      setAppQuitError(
        requestError instanceof Error ? requestError.message : "退出请求失败",
      );
    }
  };

  return (
    <main className="settings-shell">
      <aside className="settings-sidebar">
        <div className="settings-brand" aria-label="HARRY Translate">
          <span className="settings-brand__mark">H</span>
          <span>
            <strong>HARRY</strong>
            <small>Translate</small>
          </span>
        </div>

        <nav className="settings-nav" aria-label="配置中心">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                type="button"
                className={activeTab === tab.id ? "is-selected" : ""}
                aria-current={activeTab === tab.id ? "page" : undefined}
                onClick={() => setActiveTab(tab.id)}
              >
                <Icon size={19} />
                <span>
                  <strong>{tab.label}</strong>
                  <small>{tab.caption}</small>
                </span>
              </button>
            );
          })}
        </nav>

        <div
          className={`settings-sidebar__footer${
            nativeRuntime ? " settings-sidebar__footer--native" : ""
          }`}
        >
          <span className={needsSetup ? "is-warning" : "is-ready"}>
            <i />
            {needsSetup ? "等待配置" : "密钥已就绪"}
          </span>
          {nativeRuntime ? (
            <div className="settings-native-exit">
              <button
                type="button"
                className="settings-native-exit__button"
                disabled={appQuitBusy}
                aria-busy={appQuitBusy || undefined}
                aria-describedby="settings-native-exit-help"
                onClick={() => void quitApplication()}
              >
                <LogOut size={14} />
                {appQuitBusy ? "正在退出…" : "退出应用"}
              </button>
              <small id="settings-native-exit-help">
                停止实时翻译并关闭本地服务
              </small>
              {appQuitError ? (
                <small role="alert" className="settings-native-exit__error">
                  {appQuitError}
                </small>
              ) : null}
            </div>
          ) : null}
          <small>Apple Silicon · macOS</small>
        </div>
      </aside>

      <section className="settings-content">
        <header className="settings-topbar">
          <div className="settings-topbar__title">
            <div className="settings-auxiliary-action">
              {nativeRuntime ? (
                <button
                  type="button"
                  className="settings-back settings-back--auxiliary"
                  onClick={() => void openAuxiliaryMonitor()}
                >
                  <ExternalLink size={15} />
                  浏览器大屏（辅助）
                </button>
              ) : (
                <a href="/" className="settings-back settings-back--auxiliary">
                  <ExternalLink size={15} />
                  浏览器大屏（辅助）
                </a>
              )}
              {auxiliaryError ? (
                <small role="alert" className="settings-action-feedback--error">
                  {auxiliaryError}
                </small>
              ) : null}
            </div>
            <h1>{activeLabel}</h1>
          </div>
          <div className="settings-topbar__status">
            <span className="settings-engine-pill">
              <LanguageStatusCapsule
                compact
                engineState={mainEngine?.state || "idle"}
                healthStatus={mainEngine?.health_status || mainEngine?.state || "idle"}
                reason={mainEngine?.reason || (needsSetup ? "needs_setup" : "")}
                backoffSeconds={mainEngine?.backoff_seconds}
                reconnectAttempt={mainEngine?.reconnect_attempt}
                needsSetup={needsSetup}
              />
            </span>
            <span className="settings-port">:{health?.port || "—"}</span>
          </div>
          <div className="settings-launch-actions settings-realtime-actions">
            <div className="settings-realtime-switch">
              <span className="settings-realtime-switch__copy">
                <strong>实时翻译</strong>
                <small
                  id="realtime-switch-state"
                  aria-live="polite"
                >
                  {overlayStateText}
                </small>
              </span>
              <Toggle
                id="realtime-translation-switch"
                checked={isRealtimeOn}
                disabled={needsSetup || overlayBusy}
                busy={overlayBusy}
                label="实时翻译"
                ariaDescribedBy={
                  overlayError
                    ? "realtime-switch-state realtime-switch-error"
                    : "realtime-switch-state"
                }
                className="settings-realtime-switch__toggle"
                onChange={(nextEnabled) => {
                  void toggleRealtimeTranslation(nextEnabled);
                }}
              />
            </div>
            {overlayError ? (
              <small
                id="realtime-switch-error"
                role="alert"
                className="settings-action-feedback settings-action-feedback--error"
              >
                {overlayError}
              </small>
            ) : null}
          </div>
        </header>

        <div className="settings-scroll">
          <PreviewStage draft={draft} needsSetup={needsSetup} />

          <div className="settings-panel" role="tabpanel" aria-label={activeLabel}>
            {activeTab === "providers" ? (
              <ProvidersPanel
                draft={draft}
                catalog={catalog}
                apiKeyMask={apiKeyMask}
                needsSetup={needsSetup}
                error={error}
                testState={testState}
                testResult={testResult}
                onImmediate={applyImmediate}
                onSaveSecret={saveSecret}
                onTest={runConnectionTest}
              />
            ) : null}
            {activeTab === "audio" ? (
              <AudioLanguagePanel
                draft={draft}
                catalog={catalog}
                health={health}
                onImmediate={applyImmediate}
                onExternalSettings={syncExternalSettings}
              />
            ) : null}
            {activeTab === "subtitles" ? (
              <SubtitlePanel draft={draft} onImmediate={applyImmediate} />
            ) : null}
            {activeTab === "glossary" ? (
              <GlossaryInsightsPanel
                draft={draft}
                onImmediate={applyImmediate}
              />
            ) : null}
            {activeTab === "general" ? (
              <>
                <GeneralDiagnosticsPanel health={health} needsSetup={needsSetup} />
                <div className="settings-export">
                  <div>
                    <strong>会话导出</strong>
                    <small>仅导出当前唯一主会话，不包含 Token 或费用字段。</small>
                  </div>
                  <div className="settings-export__actions">
                    {(["md", "srt", "json"] as const).map((format) => (
                      <button
                        key={format}
                        type="button"
                        className="settings-button settings-button--quiet"
                        disabled={!exportSessionId}
                        onClick={() => void downloadSession(format)}
                      >
                        <Download size={15} />
                        {format.toUpperCase()}
                      </button>
                    ))}
                  </div>
                  {exportMessage ? (
                    <small className="settings-action-feedback" aria-live="polite">
                      {exportMessage}
                    </small>
                  ) : null}
                </div>
              </>
            ) : null}
          </div>
        </div>

        <footer className="settings-savebar">
          <span
            className={`settings-savebar__message settings-savebar__message--${saveState}`}
            aria-live="polite"
          >
            {saveState === "error" ? (
              <WifiOff size={18} />
            ) : saveState === "saving" ? (
              <LoaderCircle size={18} className="spin" />
            ) : (
              <CheckCircle2 size={18} />
            )}
            <span>
              <strong>{syncTitle}</strong>
              <small>{syncDetail}</small>
            </span>
          </span>
          <div className="settings-savebar__actions">
            {saveState === "error" ? (
              <button
                type="button"
                className="settings-button settings-savebar__retry"
                onClick={flush}
              >
                重试同步
              </button>
            ) : null}
            <span
              className={`settings-sync-indicator${
                socketConnected ? " is-connected" : ""
              }`}
            >
              {socketConnected ? <Wifi size={15} /> : <WifiOff size={15} />}
              {socketConnected ? "实时配置通道已连接" : "正在连接实时配置"}
            </span>
          </div>
        </footer>
      </section>
    </main>
  );
}
