import { AlignCenter, AlignLeft, AlignRight } from "lucide-react";
import type {
  BilingualLayoutOrder,
  SubtitleAlignment,
  SubtitleDisplayMode,
  SubtitleEmphasis,
  UserSettings,
} from "../../shared/types";
import {
  ColorSwatches,
  Segmented,
  SettingsField,
  SettingsSection,
  Slider,
  Toggle,
} from "./Controls";

export function SubtitlePanel({
  draft,
  onImmediate,
}: {
  draft: UserSettings;
  onImmediate: (patch: Partial<UserSettings>, debounceMs?: number) => void;
}) {
  return (
    <div className="settings-panel-stack">
      <SettingsSection
        title="悬浮窗与沉浸模式"
        description="即时同步到原生悬浮窗，无需再点击保存。"
      >
        <SettingsField
          label="沉浸模式（透明穿透）"
          help="隐藏底色、边框与缩放手柄，鼠标可操作下层应用"
        >
          <Toggle
            label="沉浸模式（透明穿透）"
            checked={draft.overlay_immersive}
            onChange={(value) =>
              onImmediate({ overlay_immersive: value })
            }
          />
        </SettingsField>
        <SettingsField
          label="锁定穿透（保留底色）"
          help="锁定标准态交互；可从本页或菜单栏托盘中关闭"
        >
          <Toggle
            label="锁定穿透（保留底色）"
            checked={draft.is_overlay_locked}
            onChange={(value) =>
              onImmediate({ is_overlay_locked: value })
            }
          />
        </SettingsField>
      </SettingsSection>

      <SettingsSection
        title="字幕结构"
        description="显示模式、分句线与行距会同步到顶部预览。"
      >
        <SettingsField label="字幕模式">
          <Segmented<SubtitleDisplayMode>
            label="字幕模式"
            value={draft.subtitle_display_mode}
            onChange={(value) => onImmediate({ subtitle_display_mode: value })}
            options={[
              { value: "translation", label: "译文" },
              { value: "original", label: "原文" },
              { value: "bilingual", label: "双语" },
            ]}
          />
        </SettingsField>
        <SettingsField label="分句线" help="在句与句之间保留清晰的节奏">
          <Toggle
            label="分句线"
            checked={draft.show_subtitle_dividers}
            onChange={(value) => onImmediate({ show_subtitle_dividers: value })}
          />
        </SettingsField>
        <SettingsField label="行距" help="1.00x 至 2.00x">
          <Slider
            ariaLabel="行距"
            value={draft.line_height_multiplier}
            min={1}
            max={2}
            step={0.05}
            suffix="x"
            onChange={(value) =>
              onImmediate({ line_height_multiplier: value }, 60)
            }
          />
        </SettingsField>
        <SettingsField label="对齐">
          <Segmented<SubtitleAlignment>
            label="字幕对齐"
            value={draft.subtitle_alignment}
            onChange={(value) => onImmediate({ subtitle_alignment: value })}
            options={[
              { value: "left", label: "左" },
              { value: "center", label: "中" },
              { value: "right", label: "右" },
            ]}
          />
        </SettingsField>
      </SettingsSection>

      <SettingsSection title="字号与颜色">
        <SettingsField label="原文字号" help="12 至 48 px">
          <Slider
            ariaLabel="原文字号"
            value={draft.source_font_size}
            min={12}
            max={48}
            step={1}
            suffix=" px"
            onChange={(value) => onImmediate({ source_font_size: value }, 60)}
          />
        </SettingsField>
        <SettingsField label="译文字号" help="12 至 48 px">
          <Slider
            ariaLabel="译文字号"
            value={draft.target_font_size}
            min={12}
            max={48}
            step={1}
            suffix=" px"
            onChange={(value) => onImmediate({ target_font_size: value }, 60)}
          />
        </SettingsField>
        <SettingsField
          label="主次显示优先级"
          help="仅通过字重与文字不透明度区分主次"
        >
          <Segmented<SubtitleEmphasis>
            label="主次显示优先级"
            value={draft.subtitle_emphasis}
            onChange={(value) => onImmediate({ subtitle_emphasis: value })}
            options={[
              { value: "translation_primary", label: "译文为主" },
              { value: "source_primary", label: "原文为主" },
              { value: "balanced", label: "均衡对照" },
            ]}
          />
        </SettingsField>
        <SettingsField label="双语排版顺序">
          <Segmented<BilingualLayoutOrder>
            label="双语排版顺序"
            value={draft.bilingual_layout_order}
            onChange={(value) =>
              onImmediate({ bilingual_layout_order: value })
            }
            options={[
              { value: "source_top", label: "原文在上" },
              { value: "target_top", label: "译文在上" },
            ]}
          />
        </SettingsField>
        <SettingsField
          label="背景不透明度"
          help={
            draft.overlay_immersive
              ? "沉浸模式下已去除背景底色"
              : "仅调整背景，不影响文字"
          }
        >
          {draft.overlay_immersive ? (
            <span className="settings-slider settings-slider--disabled">
              <input
                type="range"
                aria-label="背景不透明度"
                value={draft.background_opacity}
                min={1}
                max={100}
                step={1}
                disabled
              />
              <output>{draft.background_opacity}%</output>
            </span>
          ) : (
            <Slider
              ariaLabel="背景不透明度"
              value={draft.background_opacity}
              min={1}
              max={100}
              step={1}
              suffix="%"
              onChange={(value) =>
                onImmediate({ background_opacity: value }, 60)
              }
            />
          )}
        </SettingsField>
        <SettingsField label="译文颜色">
          <ColorSwatches
            value={draft.subtitle_color}
            onChange={(value) => onImmediate({ subtitle_color: value })}
          />
        </SettingsField>
        <SettingsField label="麦克风原文颜色">
          <ColorSwatches
            value={draft.mic_subtitle_color}
            onChange={(value) => onImmediate({ mic_subtitle_color: value })}
          />
        </SettingsField>
        <SettingsField label="系统声音颜色">
          <ColorSwatches
            value={draft.system_subtitle_color}
            onChange={(value) => onImmediate({ system_subtitle_color: value })}
          />
        </SettingsField>
      </SettingsSection>

      <SettingsSection
        title="云端热词"
        description="仅发送预算内的高价值词，关闭后使用无热词极简握手。"
      >
        <SettingsField label="云端热词开关">
          <Toggle
            label="云端热词开关"
            checked={draft.glossary_enabled}
            onChange={(value) => onImmediate({ glossary_enabled: value })}
          />
        </SettingsField>
        <SettingsField label="线预算上限" help="0 至 6144 UTF-8 字节">
          <Slider
            ariaLabel="线预算上限"
            value={draft.glossary_wire_budget_bytes}
            min={0}
            max={6144}
            step={256}
            suffix=" B"
            onChange={(value) =>
              onImmediate({ glossary_wire_budget_bytes: value }, 60)
            }
          />
        </SettingsField>
      </SettingsSection>

      <div className="settings-alignment-key" aria-hidden="true">
        <span>
          <AlignLeft size={15} /> 左对齐
        </span>
        <span>
          <AlignCenter size={15} /> 居中
        </span>
        <span>
          <AlignRight size={15} /> 右对齐
        </span>
      </div>
    </div>
  );
}
