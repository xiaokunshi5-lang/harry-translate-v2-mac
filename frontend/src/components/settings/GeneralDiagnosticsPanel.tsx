import {
  Download,
  Gauge,
  LockKeyhole,
  Server,
  TerminalSquare,
} from "lucide-react";
import type { HealthResponse } from "../../shared/types";
import { SettingsSection } from "./Controls";

export function GeneralDiagnosticsPanel({
  health,
  needsSetup,
}: {
  health: HealthResponse | null;
  needsSetup: boolean;
}) {
  return (
    <div className="settings-panel-stack">
      <SettingsSection
        title="运行诊断"
        description="只显示本地 Sidecar 的真实状态，不使用假绿灯。"
      >
        <div className="settings-diagnostic-grid">
          <div className="settings-diagnostic-tile">
            <Server size={19} />
            <span>
              <small>本地端口</small>
              <strong>{health?.port || "—"}</strong>
            </span>
          </div>
          <div className="settings-diagnostic-tile">
            <TerminalSquare size={19} />
            <span>
              <small>Python</small>
              <strong>{health?.python_version || "—"}</strong>
            </span>
          </div>
          <div className="settings-diagnostic-tile">
            <LockKeyhole size={19} />
            <span>
              <small>TLS</small>
              <strong>certifi 校验</strong>
            </span>
          </div>
          <div className="settings-diagnostic-tile">
            <Gauge size={19} />
            <span>
              <small>云端状态</small>
              <strong>{needsSetup ? "未配置" : "由 WS 驱动"}</strong>
            </span>
          </div>
        </div>
        <div className="settings-diagnostic-rows">
          <span>
            <small>音频后端</small>
            <strong>{health?.audio_backend || "unavailable"}</strong>
          </span>
          <span>
            <small>环境文件</small>
            <strong>{health?.env_exists ? ".env · 0600" : "缺失"}</strong>
          </span>
          <span>
            <small>API Key</small>
            <strong>{health?.key_mask || "未配置"}</strong>
          </span>
        </div>
      </SettingsSection>

      <SettingsSection title="会话与导出">
        <div className="settings-feature-card">
          <Download size={21} />
          <span>
            <strong>MD / SRT / JSON 导出</strong>
            <small>按窗口与时间顺序原子写盘，尚未开放。</small>
          </span>
          <em>后续</em>
        </div>
      </SettingsSection>
    </div>
  );
}
