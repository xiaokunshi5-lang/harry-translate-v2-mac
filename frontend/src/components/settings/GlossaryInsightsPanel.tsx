import { Cpu, Sparkles } from "lucide-react";
import type {
  CorrectionMode,
  CorrectionModel,
  SummaryMode,
  SummaryModel,
  UserSettings,
} from "../../shared/types";
import { GlossaryPanel } from "../glossary/GlossaryPanel";
import {
  Segmented,
  Select,
  SettingsField,
  SettingsSection,
  Slider,
  Toggle,
} from "./Controls";

export function GlossaryInsightsPanel({
  draft,
  onImmediate,
}: {
  draft: UserSettings;
  onImmediate: (patch: Partial<UserSettings>, debounceMs?: number) => void;
}) {
  const correctionMode = draft.correction_mode ?? "llm";
  const correctionModel = draft.correction_model ?? "qwen-turbo";
  const summaryMode = draft.summary_mode ?? "bailian";
  const summaryModel = draft.summary_model ?? "qwen-turbo";

  return (
    <div className="settings-panel-stack">
      <GlossaryPanel />

      <SettingsSection title="旁路能力">
        <SettingsField
          label="静默口误纠错"
        >
          <Toggle
            label="静默口误纠错"
            checked={draft.correction_enabled}
            onChange={(value) =>
              onImmediate({ correction_enabled: value })
            }
          />
        </SettingsField>
        <SettingsField
          label="纠错模式"
        >
          <Segmented<CorrectionMode>
            label="纠错模式"
            value={correctionMode}
            onChange={(value) =>
              onImmediate({ correction_mode: value })
            }
            options={[
              { value: "llm", label: "轻量模型" },
              { value: "rules", label: "纯规则" },
            ]}
          />
        </SettingsField>
        <SettingsField
          label="轻量模型"
        >
          <Select
            ariaLabel="轻量纠错模型"
            value={correctionModel}
            disabled={
              !draft.correction_enabled || correctionMode !== "llm"
            }
            onChange={(value) =>
              onImmediate({
                correction_model: value as CorrectionModel,
              })
            }
          >
            <option value="qwen-turbo">qwen-turbo</option>
            <option value="qwen-flash">qwen-flash</option>
          </Select>
        </SettingsField>
        <div className="settings-feature-card">
          <Cpu size={21} />
          <span>
            <strong>当前旁路状态</strong>
            <small>
              {draft.correction_enabled
                ? `${correctionMode === "llm" ? correctionModel : "纯规则"} · ${draft.correction_gap_ms ?? 1500} ms 节拍`
                : "已关闭 · 0 请求 · 0 后台执行"}
            </small>
          </span>
          <em>{draft.correction_enabled ? "静默运行" : "已关闭"}</em>
        </div>
        <SettingsField
          label="隐藏式重点总结"
          help="开启后仅在屏幕边缘出现 48px 小图标"
        >
          <Toggle
            label="隐藏式重点总结"
            checked={draft.summary_enabled}
            onChange={(value) =>
              onImmediate({ summary_enabled: value })
            }
          />
        </SettingsField>
        <SettingsField label="总结模式">
          <Segmented<SummaryMode>
            label="总结模式"
            value={summaryMode}
            onChange={(value) =>
              onImmediate({ summary_mode: value })
            }
            options={[
              { value: "bailian", label: "百炼模型" },
              { value: "rules", label: "纯规则" },
            ]}
          />
        </SettingsField>
        <SettingsField label="总结模型">
          <Select
            ariaLabel="总结模型"
            value={summaryModel}
            disabled={!draft.summary_enabled || summaryMode !== "bailian"}
            onChange={(value) =>
              onImmediate({
                summary_model: value as SummaryModel,
              })
            }
          >
            <option value="qwen-turbo">qwen-turbo</option>
            <option value="qwen-flash">qwen-flash</option>
          </Select>
        </SettingsField>
        <SettingsField label="最少词数">
          <Slider
            value={draft.summary_min_words ?? 120}
            min={60}
            max={400}
            step={10}
            suffix=" 词"
            ariaLabel="总结最少词数"
            onChange={(value) =>
              onImmediate({ summary_min_words: value }, 180)
            }
          />
        </SettingsField>
        <SettingsField label="最短间隔">
          <Slider
            value={draft.summary_min_interval_sec ?? 30}
            min={10}
            max={120}
            step={5}
            suffix=" 秒"
            ariaLabel="总结最短间隔"
            onChange={(value) =>
              onImmediate({ summary_min_interval_sec: value }, 180)
            }
          />
        </SettingsField>
        <div className="settings-feature-card">
          <Sparkles size={21} />
          <span>
            <strong>隐藏式重点总结</strong>
            <small>
              {draft.summary_enabled
                ? `${summaryMode === "bailian" ? summaryModel : "纯规则"} · ${draft.summary_min_words ?? 120} 词 / ${draft.summary_min_interval_sec ?? 30} 秒`
                : "出厂默认关闭 · 0 请求 · 0 后台任务"}
            </small>
          </span>
          <em>{draft.summary_enabled ? "已开启" : "已关闭"}</em>
        </div>
      </SettingsSection>
    </div>
  );
}
