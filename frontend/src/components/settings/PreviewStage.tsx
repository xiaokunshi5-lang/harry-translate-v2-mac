import { AudioLines, Signal } from "lucide-react";
import {
  IMMERSIVE_TEXT_SHADOW,
  subtitleBackgroundColor,
} from "../../lib/subtitles/subtitleAppearance";
import type {
  LanguageCode,
  SubtitleEmphasis,
  UserSettings,
} from "../../shared/types";

const LANGUAGE_LABELS: Record<LanguageCode, string> = {
  auto: "自动识别",
  zh: "中文",
  en: "英语",
  fr: "法语",
  es: "西班牙语",
  it: "意大利语",
  ja: "日语",
  ko: "韩语",
  de: "德语",
};

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

export function PreviewStage({
  draft,
  needsSetup,
}: {
  draft: UserSettings;
  needsSetup: boolean;
}) {
  const showSource = draft.subtitle_display_mode !== "translation";
  const showTarget = draft.subtitle_display_mode !== "original";
  const sourceStyle = emphasisWeight("source", draft.subtitle_emphasis);
  const targetStyle = emphasisWeight("target", draft.subtitle_emphasis);
  const textShadow = draft.overlay_immersive
    ? IMMERSIVE_TEXT_SHADOW
    : undefined;
  const rows = [
    showSource
      ? {
          role: "source" as const,
          style: {
            ...sourceStyle,
            color: draft.mic_subtitle_color,
            fontSize: draft.source_font_size,
            textShadow,
          },
          content: "欢迎参加今天的会议，我们现在开始。",
        }
      : null,
    showTarget
      ? {
          role: "target" as const,
          style: {
            ...targetStyle,
            color: draft.subtitle_color,
            fontSize: draft.target_font_size,
            textShadow,
          },
          content: "Welcome to today's meeting. We will begin now.",
        }
      : null,
  ].filter((row) => row !== null);
  if (draft.bilingual_layout_order === "target_top") rows.reverse();
  return (
    <section
      className="preview-stage"
      data-needs-setup={needsSetup}
      data-presentation={
        draft.overlay_immersive ? "background-blend" : "standard"
      }
      aria-label="实时字幕预览"
    >
      <div className="preview-stage__meta">
        <span className="preview-stage__route">
          {LANGUAGE_LABELS[draft.source_lang]}
          <i />
          {LANGUAGE_LABELS[draft.target_lang]}
        </span>
        <span className="preview-stage__status">
          <Signal size={15} />
          {needsSetup ? "等待密钥" : "等待实时会话"}
        </span>
      </div>

      <div
        className="preview-stage__subtitle"
        style={{
          background: draft.overlay_immersive
            ? "transparent"
            : subtitleBackgroundColor(draft.background_opacity),
          textAlign: draft.subtitle_alignment,
          lineHeight: draft.line_height_multiplier,
        }}
      >
        <AudioLines className="preview-stage__wave" size={18} />
        <div>
          {rows.map((row, index) =>
            row.role === "source" ? (
              <p
                key={row.role}
                className={
                  draft.show_subtitle_dividers && index < rows.length - 1
                    ? "has-divider"
                    : ""
                }
                style={row.style}
              >
                {row.content}
              </p>
            ) : (
              <strong key={row.role} style={row.style}>
                {row.content}
              </strong>
            ),
          )}
        </div>
      </div>

      <div className="preview-stage__footer">
        <span>{draft.active_model}</span>
        <span>
          原文 {draft.source_font_size}px · 译文 {draft.target_font_size}px ·{" "}
          {draft.line_height_multiplier.toFixed(2)}x · {draft.background_opacity}% 背景
        </span>
      </div>
    </section>
  );
}
