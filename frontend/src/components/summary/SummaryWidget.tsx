import {
  ChevronDown,
  ChevronUp,
  GripHorizontal,
  Power,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  getSummary,
  refreshSummary,
  saveSettings,
} from "../../shared/apiClient";
import type {
  SummaryMode,
  SummarySnapshot,
} from "../../shared/types";
import {
  isTauriRuntime,
  listenTauriEvent,
  tauriInvoke,
} from "../../lib/backendBridge";
import "../../styles/summary.css";

const EMPTY_SUMMARY: SummarySnapshot = {
  enabled: false,
  pinned: [],
  archived: [],
};

export interface SummaryWidgetProps {
  enabled: boolean;
  liveSnapshot?: SummarySnapshot | null;
  nativeWindow?: boolean;
  onDisabled?: () => void;
}

function modeLabel(mode: SummaryMode | undefined): string {
  return mode === "rules" ? "纯规则" : "百炼模型";
}

export function SummaryWidget({
  enabled,
  liveSnapshot,
  nativeWindow = false,
  onDisabled,
}: SummaryWidgetProps) {
  const [expanded, setExpanded] = useState(false);
  const [snapshot, setSnapshot] = useState<SummarySnapshot>(EMPTY_SUMMARY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!enabled) {
      setExpanded(false);
      setSnapshot(EMPTY_SUMMARY);
      setLoading(false);
      setError("");
      return;
    }
    let active = true;
    setLoading(true);
    void getSummary()
      .then((next) => {
        if (!active) return;
        setSnapshot(next);
        setError("");
        if (!next.enabled) {
          if (nativeWindow) {
            void tauriInvoke<void>("summary_close");
          }
          onDisabled?.();
        }
      })
      .catch(() => {
        if (active) setError("总结服务暂不可用");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [enabled, nativeWindow, onDisabled]);

  useEffect(() => {
    if (!enabled || !liveSnapshot) return;
    setSnapshot(liveSnapshot);
    if (!liveSnapshot.enabled) {
      setExpanded(false);
      onDisabled?.();
    }
  }, [enabled, liveSnapshot, onDisabled]);

  useEffect(() => {
    if (!enabled || !nativeWindow || !expanded) return;
    let active = true;
    const refreshWhenVisible = async () => {
      try {
        const next = await getSummary();
        if (!active) return;
        setSnapshot(next);
        if (!next.enabled) {
          setExpanded(false);
          if (nativeWindow) {
            void tauriInvoke<void>("summary_close");
          }
          onDisabled?.();
        }
      } catch {
        // The next visible-window refresh retries after reconnection.
      }
    };
    const timer = window.setInterval(
      () => void refreshWhenVisible(),
      15_000,
    );
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [enabled, expanded, nativeWindow, onDisabled]);

  if (!enabled) return null;

  const setWindowExpanded = (next: boolean) => {
    setExpanded(next);
    if (nativeWindow) {
      void tauriInvoke<void>("summary_set_expanded", { expanded: next });
    }
  };

  const runRefresh = async () => {
    setLoading(true);
    setError("");
    try {
      setSnapshot(await refreshSummary());
    } catch {
      setError("暂时无法提炼，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  const changeMode = async (mode: SummaryMode) => {
    setSnapshot((current) => ({ ...current, mode }));
    try {
      await saveSettings({ summary_mode: mode });
      setSnapshot(await refreshSummary());
    } catch {
      setError("模式切换失败");
    }
  };

  const stopSummary = async () => {
    setError("");
    try {
      await saveSettings({ summary_enabled: false });
    } catch {
      setError("关闭失败，后台总结仍在运行");
      return;
    }
    setExpanded(false);
    if (nativeWindow) {
      await tauriInvoke<void>("summary_close").catch(() => undefined);
    }
    onDisabled?.();
  };

  const shellClass = nativeWindow
    ? `summary-native summary-native--${expanded ? "expanded" : "icon"}`
    : "summary-browser-dock";

  if (!expanded) {
    const unread = snapshot.pinned.length;
    return (
      <div className={shellClass} data-summary-widget="icon">
        <button
          type="button"
          className="summary-icon"
          aria-label="打开重点总结"
          title="打开重点总结"
          onClick={() => setWindowExpanded(true)}
        >
          <Sparkles size={21} />
          {unread > 0 ? (
            <span className="summary-icon__badge" aria-label={`${unread} 条重点`}>
              {unread > 9 ? "9+" : unread}
            </span>
          ) : null}
        </button>
      </div>
    );
  }

  const engineLabel =
    snapshot.engine_used === "rules_fallback"
      ? "规则兜底"
      : snapshot.engine_used === "rules"
        ? "纯规则"
        : modeLabel(snapshot.mode);

  return (
    <div className={shellClass} data-summary-widget="expanded">
      <section className="summary-panel" aria-label="重点总结">
        <header
          className="summary-panel__header"
          {...(nativeWindow ? { "data-tauri-drag-region": true } : {})}
        >
          <span className="summary-panel__identity">
            <GripHorizontal size={16} className="summary-panel__grip" />
            <span>
              <strong>重点总结</strong>
              <small>{engineLabel}</small>
            </span>
          </span>
          <span className="summary-panel__actions">
            <button
              type="button"
              className="summary-action"
              aria-label="立即总结"
              title="立即总结"
              disabled={loading}
              onClick={() => void runRefresh()}
            >
              <RefreshCw size={16} className={loading ? "spin" : ""} />
            </button>
            <button
              type="button"
              className="summary-action"
              aria-label="收起回小图标"
              title="收起"
              onClick={() => setWindowExpanded(false)}
            >
              <ChevronDown size={17} />
            </button>
            <button
              type="button"
              className="summary-action summary-action--power"
              aria-label="彻底关闭总结"
              title="关闭总结"
              onClick={() => void stopSummary()}
            >
              <Power size={16} />
            </button>
          </span>
        </header>

        <div className="summary-panel__mode" role="group" aria-label="总结模式">
          <button
            type="button"
            className={snapshot.mode !== "rules" ? "is-selected" : ""}
            aria-pressed={snapshot.mode !== "rules"}
            onClick={() => void changeMode("bailian")}
          >
            百炼模型
          </button>
          <button
            type="button"
            className={snapshot.mode === "rules" ? "is-selected" : ""}
            aria-pressed={snapshot.mode === "rules"}
            onClick={() => void changeMode("rules")}
          >
            纯规则
          </button>
        </div>

        <div className="summary-panel__body">
          {error ? <p className="summary-message is-error">{error}</p> : null}
          {loading && snapshot.pinned.length === 0 ? (
            <p className="summary-message">正在整理重点…</p>
          ) : null}
          {!loading && snapshot.pinned.length === 0 ? (
            <p className="summary-message">
              暂无重点，继续同传后会自动整理。
            </p>
          ) : null}

          {snapshot.pinned.length > 0 ? (
            <div className="summary-pinned" aria-label="核心重点">
              {snapshot.pinned.map((item, index) => (
                <article
                  key={`${item.level}-${item.text}-${index}`}
                  className="summary-point summary-point--pinned"
                >
                  <span className="summary-point__level">P{item.level}</span>
                  <span className="summary-point__content">
                    <small>{item.category || "重点"}</small>
                    <strong>{item.text}</strong>
                  </span>
                </article>
              ))}
            </div>
          ) : null}

          {snapshot.archived.length > 0 ? (
            <details className="summary-archive">
              <summary>
                <span>辅助信息 · {snapshot.archived.length}</span>
                <ChevronUp size={15} />
              </summary>
              <div className="summary-archive__list">
                {snapshot.archived.map((item, index) => (
                  <article
                    key={`${item.level}-${item.text}-${index}`}
                    className="summary-point"
                  >
                    <span className="summary-point__level">P{item.level}</span>
                    <span className="summary-point__content">
                      <small>{item.category || "细节"}</small>
                      <strong>{item.text}</strong>
                    </span>
                  </article>
                ))}
              </div>
            </details>
          ) : null}
        </div>
      </section>
    </div>
  );
}

export function SummaryWindow() {
  const nativeWindow = isTauriRuntime();
  const [enabled, setEnabled] = useState(true);
  const [liveSnapshot, setLiveSnapshot] = useState<SummarySnapshot | null>(null);

  useEffect(() => {
    document.documentElement.dataset.window = "summary";
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
    let active = true;
    let dispose: () => void = () => undefined;
    void listenTauriEvent<SummarySnapshot>("summary-update", (payload) => {
      if (active) setLiveSnapshot(payload);
    })
      .then((off) => {
        if (active) dispose = off;
        else off();
      })
      .catch(() => undefined);
    return () => {
      active = false;
      dispose();
      delete document.documentElement.dataset.window;
      document.documentElement.style.background = "";
      document.body.style.background = "";
    };
  }, []);

  const handleDisabled = useCallback(() => setEnabled(false), []);

  return (
    <SummaryWidget
      enabled={enabled}
      nativeWindow={nativeWindow}
      liveSnapshot={liveSnapshot}
      onDisabled={handleDisabled}
    />
  );
}
