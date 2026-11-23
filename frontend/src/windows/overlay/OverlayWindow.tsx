import { Maximize2, Minimize2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  isTauriRuntime,
  listenTauriEvent,
  tauriInvoke,
} from "../../lib/backendBridge";
import { SummaryWidget } from "../../components/summary/SummaryWidget";
import { sanitizeTargetForLang } from "../../lib/subtitles/languagePurity";
import {
  applySubtitleEvent,
  applySubtitlePatch,
  createSubtitlePipelineState,
} from "../../lib/subtitles/subtitlePipeline";
import {
  IMMERSIVE_TEXT_SHADOW,
  subtitleBackgroundColor,
} from "../../lib/subtitles/subtitleAppearance";
import { getConfig, getHealth, saveSettings } from "../../shared/apiClient";
import type {
  EngineEvent,
  HealthResponse,
  HudGeometry,
  SummarySnapshot,
  UserSettings,
} from "../../shared/types";
import { ChannelSocket, type SocketPhase } from "../../shared/wsClient";
import { OverlayDragArea } from "./interaction/OverlayDragArea";
import { ResizeHandles } from "./interaction/ResizeHandles";
import {
  clampExpandedDimensions,
  computeResizeForOnlyMode,
  invokeOverlayCommitGeometry,
  invokeOverlayGeometry,
  invokeOverlayMode,
  invokeOverlayState,
  invokeOverlaySyncPresentation,
  listenOverlayPresentationChanged,
  OVERLAY_METRICS,
  type OverlayMode,
  type OverlaySnapshot,
  type ResizeEdge,
} from "./interaction/resizeIpc";
import { Timeline } from "./timeline/Timeline";
import "./layout/overlayChrome.css";

interface SubtitleStreamState {
  phase: SocketPhase;
  engineState: string;
  reason: string;
  source: string;
  target: string;
  final: boolean;
  sentId: number | null;
  sourceHash: string;
}

type ConfigUpdateEvent = EngineEvent & { needs_setup?: boolean };

const DEFAULT_GEOMETRY: HudGeometry = {
  x: 200,
  y: 120,
  width: OVERLAY_METRICS.DEFAULT_WIDTH,
  height: OVERLAY_METRICS.DEFAULT_HEIGHT,
  mode: "expanded",
  locked: false,
};

function overlayVisibilityFromPayload(payload: unknown): boolean | null {
  if (typeof payload === "boolean") return payload;
  if (typeof payload === "string") {
    if (payload === "visible") return true;
    if (payload === "hidden") return false;
    return null;
  }
  if (!payload || typeof payload !== "object") return null;
  const record = payload as Record<string, unknown>;
  if (typeof record.visible === "boolean") return record.visible;
  if (typeof record.hidden === "boolean") return !record.hidden;
  return null;
}

function nativePresentationChanged(
  previous: UserSettings | null,
  next: UserSettings,
): boolean {
  return (
    previous?.overlay_immersive !== next.overlay_immersive ||
    previous?.is_overlay_locked !== next.is_overlay_locked ||
    previous?.overlay_click_through !== next.overlay_click_through ||
    previous?.subtitle_display_mode !== next.subtitle_display_mode
  );
}

function geometryFromSnapshot(snapshot: OverlaySnapshot): HudGeometry {
  return {
    x: Math.round(snapshot.userFrame.x),
    y: Math.round(snapshot.userFrame.y),
    width: Math.round(snapshot.userFrame.width),
    height: Math.round(snapshot.userFrame.height),
    mode: snapshot.mode,
    locked: snapshot.locked,
  };
}

export function OverlayWindow() {
  const inTauri = isTauriRuntime();
  const isBrowserFallback =
    !inTauri ||
    new URLSearchParams(window.location.search).get("fallback") === "browser";

  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [setupComplete, setSetupComplete] = useState<boolean | null>(null);
  const [overlayVisible, setOverlayVisible] = useState<boolean | null>(
    inTauri ? null : true,
  );
  const [geometry, setGeometry] = useState<HudGeometry>(DEFAULT_GEOMETRY);
  const [stream, setStream] = useState<SubtitleStreamState>({
    phase: "idle",
    engineState: "idle",
    reason: "",
    source: "",
    target: "",
    final: false,
    sentId: null,
    sourceHash: "",
  });
  const [pipeline, setPipeline] = useState(createSubtitlePipelineState);
  const [summarySnapshot, setSummarySnapshot] =
    useState<SummarySnapshot | null>(null);
  const pipelineRef = useRef(pipeline);
  const settingsRef = useRef<UserSettings | null>(null);
  const geometryRef = useRef<HudGeometry>(DEFAULT_GEOMETRY);
  const bilingualBaseHeightRef = useRef<number>(DEFAULT_GEOMETRY.height);
  geometryRef.current = geometry;

  const rememberSettings = (next: UserSettings) => {
    const previousMode = settingsRef.current?.subtitle_display_mode;
    settingsRef.current = next;
    setSettings(next);
    const nextLocked = next.is_overlay_locked === true;
    setGeometry((current) => {
      const stored = next.hud_geometry;
      const dimensions = stored
        ? clampExpandedDimensions(stored.width, stored.height)
        : { width: current.width, height: current.height };
      const merged: HudGeometry = {
        ...current,
        ...(stored || {}),
        ...dimensions,
        locked: nextLocked,
      };
      if (
        !inTauri &&
        previousMode &&
        previousMode !== next.subtitle_display_mode &&
        merged.mode === "expanded"
      ) {
        const compensated = computeResizeForOnlyMode(
          merged,
          previousMode,
          next.subtitle_display_mode,
          bilingualBaseHeightRef.current,
        );
        bilingualBaseHeightRef.current = compensated.bilingualBaseHeight;
        return {
          ...merged,
          ...compensated.frame,
        };
      }
      if (next.subtitle_display_mode === "bilingual") {
        bilingualBaseHeightRef.current = merged.height;
      } else if (!previousMode) {
        bilingualBaseHeightRef.current = Math.min(
          OVERLAY_METRICS.MAX_HEIGHT,
          Math.max(
            OVERLAY_METRICS.MIN_HEIGHT,
            Math.round(merged.height / OVERLAY_METRICS.ONLY_MODE_HEIGHT_RATIO),
          ),
        );
      }
      return merged;
    });
  };

  const syncPresentation = (next: UserSettings) => {
    if (!inTauri) return;
    const immersive = next.overlay_immersive === true;
    void invokeOverlaySyncPresentation(
      immersive,
      next.is_overlay_locked === true,
      next.overlay_click_through === true,
      next.subtitle_display_mode,
      false,
    );
  };

  useEffect(() => {
    if (isBrowserFallback) return;
    const prevHtmlBg = document.documentElement.style.background;
    const prevBodyBg = document.body.style.background;
    const prevBodyMinWidth = document.body.style.minWidth;
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
    document.body.style.minWidth = "0";
    return () => {
      document.documentElement.style.background = prevHtmlBg;
      document.body.style.background = prevBodyBg;
      document.body.style.minWidth = prevBodyMinWidth;
    };
  }, [isBrowserFallback]);

  useEffect(() => {
    let active = true;
    void Promise.all([getHealth(), getConfig(), invokeOverlayState().catch(() => null)])
      .then(([nextHealth, nextConfig, nativeState]) => {
        if (!active) return;
        const loadedSettings = nextConfig.user_settings || nextConfig.settings;
        setHealth(nextHealth);
        setSetupComplete(!nextConfig.needs_setup);
        rememberSettings(loadedSettings);
        const initialGeo = nativeState
          ? geometryFromSnapshot(nativeState)
          : {
              ...(loadedSettings.hud_geometry || DEFAULT_GEOMETRY),
              ...clampExpandedDimensions(
                loadedSettings.hud_geometry?.width ?? DEFAULT_GEOMETRY.width,
                loadedSettings.hud_geometry?.height ?? DEFAULT_GEOMETRY.height,
              ),
            };
        setGeometry(initialGeo);
        if (inTauri) {
          if (!nativeState) {
            void invokeOverlayGeometry({
              x: initialGeo.x,
              y: initialGeo.y,
              width: initialGeo.width,
              height: initialGeo.height,
            });
          }
          syncPresentation(loadedSettings);
        }
      })
      .catch(() => {
        if (active) setHealth(null);
      });
    return () => {
      active = false;
    };
  }, [inTauri]);

  const needsSetup =
    setupComplete === null ? (health?.needs_setup ?? true) : !setupComplete;

  useEffect(() => {
    if (!inTauri || !settings) return;
    void tauriInvoke<void>("summary_set_enabled", {
      enabled: settings.summary_enabled === true,
    }).catch(() => undefined);
  }, [inTauri, settings?.summary_enabled]);

  const handleSummaryDisabled = useCallback(() => {
    setSummarySnapshot({ enabled: false, pinned: [], archived: [] });
    setSettings((current) =>
      current ? { ...current, summary_enabled: false } : current,
    );
  }, []);

  const applyConfigUpdate = (event: EngineEvent) => {
    const setupRequired = (event as ConfigUpdateEvent).needs_setup;
    if (typeof setupRequired === "boolean") {
      setSetupComplete(!setupRequired);
    }
    if (!event.settings) return;
    const previous = settingsRef.current;
    const shouldSyncPresentation = nativePresentationChanged(
      previous,
      event.settings,
    );
    rememberSettings(event.settings);
    if (shouldSyncPresentation) syncPresentation(event.settings);
  };

  useEffect(() => {
    const socket = new ChannelSocket("config", {
      onPhase: () => undefined,
      onEvent: (event) => {
        if (event.type === "config_updated") applyConfigUpdate(event);
      },
    });
    socket.connect();
    return () => socket.close();
  }, []);

  useEffect(() => {
    if (!inTauri) {
      setOverlayVisible(true);
      return;
    }
    let active = true;
    void tauriInvoke<unknown>("overlay_get_visibility")
      .then((payload) => {
        if (!active) return;
        setOverlayVisible(overlayVisibilityFromPayload(payload) === true);
      })
      .catch(() => {
        if (active) setOverlayVisible(false);
      });
    return () => {
      active = false;
    };
  }, [inTauri]);

  useEffect(() => {
    if (needsSetup) {
      setStream((current) => ({ ...current, reason: "needs_setup" }));
      return;
    }
    if (overlayVisible !== true) return;
    setStream((current) =>
      current.reason === "needs_setup"
        ? { ...current, reason: "" }
        : current,
    );
    const socket = new ChannelSocket("main", {
      onPhase: (phase) => setStream((current) => ({ ...current, phase })),
      onEvent: (event: EngineEvent) => {
        if (event.type === "engine_state") {
          setStream((current) => ({
            ...current,
            engineState: event.state || "idle",
            reason: event.reason || "",
          }));
          return;
        }
        if (event.type === "session_reset") {
          setSummarySnapshot((current) =>
            current
              ? { ...current, pinned: [], archived: [], pending_words: 0 }
              : current,
          );
          setStream((current) => ({
            ...current,
            source: "",
            target: "",
            final: false,
            sentId: null,
            sourceHash: "",
          }));
          const resetPipeline = createSubtitlePipelineState();
          pipelineRef.current = resetPipeline;
          setPipeline(resetPipeline);
          return;
        }
        if (event.type === "summary.update") {
          const summary: SummarySnapshot = {
            enabled: event.enabled === true,
            mode: event.mode,
            model: event.model,
            engine_used: event.engine_used,
            pending_words: Number(event.pending_words || 0),
            pinned: Array.isArray(event.pinned) ? event.pinned : [],
            archived: Array.isArray(event.archived) ? event.archived : [],
          };
          setSummarySnapshot(summary);
          if (inTauri) {
            void tauriInvoke<void>("summary_forward_update", {
              event: { type: "summary.update", ...summary },
            }).catch(() => undefined);
          }
          return;
        }
        if (event.type === "transcript.patch") {
          const targetLang = settingsRef.current?.target_lang || "zh";
          const hasPatchPayload =
            typeof event.patched_source === "string" &&
            typeof event.patched_target === "string" &&
            typeof event.new_source_hash === "string";
          const patchedSource = hasPatchPayload
            ? (event.patched_source as string)
            : "";
          const patchedTarget = hasPatchPayload
            ? sanitizeTargetForLang(
                event.patched_target as string,
                patchedSource,
                targetLang,
              )
            : "";
          const result = applySubtitlePatch(
            pipelineRef.current,
            hasPatchPayload
              ? {
                  ...event,
                  patched_source: patchedSource,
                  patched_target: patchedTarget,
                }
              : event,
          );
          pipelineRef.current = result.state;
          setPipeline(result.state);
          if (!result.applied) return;
          setStream((current) => {
            const patchSentId = event.sent_id;
            if (
              current.sentId !== patchSentId ||
              current.sourceHash !== event.source_hash
            ) {
              return current;
            }
            return {
              ...current,
              source: patchedSource,
              target: patchedTarget,
              sourceHash: event.new_source_hash || current.sourceHash,
            };
          });
          return;
        }
        if (event.type === "subtitle" || event.type === "subtitle.final") {
          setStream((current) => {
            const nextSource = String(
              event.source_text ?? event.source ?? current.source,
            );
            const hasIncomingTarget =
              event.target_text !== undefined || event.target !== undefined;
            const rawTarget = String(event.target_text ?? event.target ?? "");
            const targetLang = settingsRef.current?.target_lang || "zh";
            const nextTarget = hasIncomingTarget
              ? sanitizeTargetForLang(rawTarget, nextSource, targetLang)
              : current.target;
            const final = Boolean(
              event.is_final || event.type === "subtitle.final",
            );
            const idValue = final
              ? event.merged_with_prev_id ?? event.sent_id
              : event.sent_id;
            const nextSentId =
              idValue !== undefined && Number.isFinite(Number(idValue))
                ? Math.trunc(Number(idValue))
                : current.sentId;
            return {
              ...current,
              source: nextSource,
              target: nextTarget,
              final,
              sentId: nextSentId,
              sourceHash: event.source_hash ?? current.sourceHash,
            };
          });
          const currentPipeline = pipelineRef.current;
          const fallbackSource = currentPipeline.draft?.source ?? "";
          const source =
            event.source_text ?? event.source ?? fallbackSource;
          const hasIncomingTarget =
            event.target_text !== undefined || event.target !== undefined;
          const rawTarget = String(event.target_text ?? event.target ?? "");
          const targetLang = settingsRef.current?.target_lang || "zh";
          const normalizedTarget = hasIncomingTarget
            ? sanitizeTargetForLang(
                rawTarget,
                String(source),
                targetLang,
              )
            : currentPipeline.draft?.target;
          const nextPipeline = applySubtitleEvent(currentPipeline, {
            ...event,
            source_text: String(source),
            target_text: normalizedTarget,
          });
          pipelineRef.current = nextPipeline;
          setPipeline(nextPipeline);
        }
      },
    });
    socket.connect();
    return () => socket.close();
  }, [needsSetup, overlayVisible]);

  useEffect(() => {
    if (!inTauri) return;
    let active = true;
    let dispose: () => void = () => undefined;
    void listenTauriEvent<unknown>("overlay-visibility-changed", (payload) => {
      if (!active) return;
      const visible = overlayVisibilityFromPayload(payload);
      if (visible !== null) setOverlayVisible(visible);
    })
      .then((off) => {
        if (!active) {
          off();
          return;
        }
        dispose = off;
      })
      .catch(() => undefined);
    return () => {
      active = false;
      dispose();
    };
  }, [inTauri]);

  useEffect(() => {
    if (!inTauri) return;
    let active = true;
    let dispose: () => void = () => undefined;
    void listenOverlayPresentationChanged((snapshot) => {
      if (!active) return;
      const currentSettings = settingsRef.current;
      if (currentSettings) {
        const nextSettings: UserSettings = {
          ...currentSettings,
          overlay_immersive: snapshot.immersive,
          is_overlay_locked: snapshot.locked,
          overlay_click_through: snapshot.clickThrough,
          hud_geometry: geometryFromSnapshot(snapshot),
        };
        rememberSettings(nextSettings);
      }
      setGeometry(geometryFromSnapshot(snapshot));
    })
      .then((off) => {
        if (!active) {
          off();
          return;
        }
        dispose = off;
      })
      .catch(() => undefined);
    return () => {
      active = false;
      dispose();
    };
  }, [inTauri]);

  useEffect(() => {
    if (!inTauri || stream.phase === "open") return;
    let active = true;
    const poll = async () => {
      try {
        const nextConfig = await getConfig();
        if (!active) return;
        const nextSettings = nextConfig.user_settings || nextConfig.settings;
        setSetupComplete(!nextConfig.needs_setup);
        const previous = settingsRef.current;
        const nativeChanged = nativePresentationChanged(
          previous,
          nextSettings,
        );
        rememberSettings(nextSettings);
        if (nativeChanged) syncPresentation(nextSettings);
      } catch {
        // The next lightweight poll retries after Sidecar reconnect.
      }
    };
    const timer = window.setInterval(() => void poll(), 1500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [inTauri, stream.phase]);

  const persistGeometry = (next: HudGeometry) => {
    void saveSettings({ hud_geometry: next }).catch(() => undefined);
  };

  const toggleMode = (nextMode: OverlayMode) => {
    const updated: HudGeometry = { ...geometryRef.current, mode: nextMode };
    setGeometry(updated);
    if (inTauri) {
      void invokeOverlayMode(nextMode);
    } else {
      persistGeometry(updated);
    }
  };

  const applyBrowserResize = (edge: ResizeEdge, dx: number, dy: number) => {
    if (inTauri) return;
    setGeometry((current) => {
      let nextW = current.width;
      let nextH = current.height;
      let nextX = current.x;
      let nextY = current.y;
      if (edge.includes("e")) nextW += dx;
      if (edge.includes("s")) nextH += dy;
      if (edge.includes("w")) {
        nextW -= dx;
        nextX += dx;
      }
      if (edge.includes("n")) {
        nextH -= dy;
        nextY += dy;
      }
      const clamped = clampExpandedDimensions(nextW, nextH);
      return {
        ...current,
        x: Math.round(nextX),
        y: Math.round(nextY),
        width: clamped.width,
        height: clamped.height,
      };
    });
  };

  if (!settings) {
    return (
      <div
        className={`overlay-stage${isBrowserFallback ? " overlay-stage--browser" : ""}`}
        data-overlay-count={OVERLAY_METRICS.MAX_OVERLAY_WINDOWS}
      />
    );
  }

  const isCollapsed = isBrowserFallback && geometry.mode === "collapsed";
  const displayWidth = isCollapsed
    ? OVERLAY_METRICS.COLLAPSED_WIDTH
    : geometry.width;
  const displayHeight = isCollapsed
    ? OVERLAY_METRICS.COLLAPSED_HEIGHT
    : geometry.height;
  const immersive =
    !isBrowserFallback && settings.overlay_immersive === true && !needsSetup;
  const locked = settings.is_overlay_locked === true;
  return (
    <div
      className={`overlay-stage${isBrowserFallback ? " overlay-stage--browser" : ""}`}
      data-overlay-count={OVERLAY_METRICS.MAX_OVERLAY_WINDOWS}
    >
      <section
        aria-label="单窗悬浮字幕"
        className={`overlay-window${locked ? " overlay-window--locked" : ""}${
          isCollapsed ? " overlay-window--collapsed" : ""
        }${immersive ? " overlay-window--immersive" : ""}`}
        data-presentation={immersive ? "background-blend" : "standard"}
        data-display-mode={settings.subtitle_display_mode}
        style={{
          width: isBrowserFallback ? `${displayWidth}px` : "100%",
          height: isBrowserFallback ? `${displayHeight}px` : "100%",
          background: immersive
            ? "transparent"
            : subtitleBackgroundColor(settings.background_opacity),
          opacity: 1,
        }}
        onWheel={() => {
          if (isCollapsed) toggleMode("expanded");
        }}
      >
        {!immersive ? (
          <ResizeHandles
            disabled={(isBrowserFallback && isCollapsed) || locked}
            onResizeDelta={applyBrowserResize}
            onResizeCommit={() => {
              if (inTauri) {
                void invokeOverlayCommitGeometry();
              } else {
                persistGeometry(geometryRef.current);
              }
            }}
          />
        ) : null}

        <OverlayDragArea
          disabled={immersive || locked}
          onBrowserDragDelta={
            isBrowserFallback
              ? (dx, dy) => {
                  setGeometry((current) => ({
                    ...current,
                    x: current.x + dx,
                    y: current.y + dy,
                  }));
                }
              : undefined
          }
        >
          <>
            {isBrowserFallback ? (
              <header className="overlay-capsule">
                <span className="overlay-capsule__status">
                  <i
                    className={`overlay-capsule__dot${
                      stream.engineState === "ready" ? " is-ready" : ""
                    }`}
                  />
                  {needsSetup ? "请先在设置页填写 API Key" : "浏览器预览"}
                </span>
              </header>
            ) : null}

            {isCollapsed ? (
              <div className="overlay-minibar" aria-label="迷你字幕条">
                <span className="overlay-capsule__status">
                  <i
                    className={`overlay-capsule__dot${
                      stream.engineState === "ready" ? " is-ready" : ""
                    }`}
                  />
                  <span className="overlay-minibar__label">
                    {stream.target || "HARRY Translate · 迷你条"}
                  </span>
                </span>
              </div>
            ) : (
              <div
                className={`overlay-body${immersive ? " overlay-body--immersive" : ""}`}
              >
                <Timeline
                  pipeline={pipeline}
                  settings={settings}
                  immersive={immersive}
                  browserFallback={isBrowserFallback}
                  immersiveTextShadow={IMMERSIVE_TEXT_SHADOW}
                />
              </div>
            )}
          </>
        </OverlayDragArea>
      </section>

      {isBrowserFallback ? (
        <SummaryWidget
          enabled={settings.summary_enabled === true}
          liveSnapshot={summarySnapshot}
          onDisabled={handleSummaryDisabled}
        />
      ) : null}

      {isBrowserFallback ? (
        <div className="overlay-debug-bar">
          <button
            type="button"
            aria-label={isCollapsed ? "展开字幕悬浮窗" : "折叠为迷你条"}
            onClick={() => toggleMode(isCollapsed ? "expanded" : "collapsed")}
          >
            {isCollapsed ? <Maximize2 size={14} /> : <Minimize2 size={14} />}
            {isCollapsed ? "展开" : "折叠"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
