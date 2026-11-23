import { AlertTriangle, Mic2, Radio } from "lucide-react";
import { useEffect, useState } from "react";
import { getAudioDevices, selectAudioInput } from "../../shared/apiClient";
import type {
  AudioInputMode,
  AudioInputProbe,
  HealthResponse,
  LanguageCode,
  ProviderCatalog,
  UserSettings,
} from "../../shared/types";
import {
  Select,
  SettingsField,
  SettingsSection,
} from "./Controls";

const LANGUAGE_NAMES: Record<LanguageCode, string> = {
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

export function AudioLanguagePanel({
  draft,
  catalog,
  health,
  onImmediate,
  onExternalSettings,
}: {
  draft: UserSettings;
  catalog: ProviderCatalog | null;
  health: HealthResponse | null;
  onImmediate: (patch: Partial<UserSettings>, debounceMs?: number) => void;
  onExternalSettings: (settings: UserSettings) => void;
}) {
  const provider =
    catalog?.providers[draft.active_provider] ||
    Object.values(catalog?.providers || {})[0] ||
    null;
  const sources = provider?.source_languages || [
    "auto",
    "zh",
    "en",
    "fr",
    "es",
    "it",
    "ja",
    "ko",
    "de",
  ];
  const targets = provider?.target_languages || sources.filter((item) => item !== "auto");
  const devices = health?.audio_devices || [];
  const [audioProbe, setAudioProbe] = useState<AudioInputProbe | null>(null);
  const [audioSwitchState, setAudioSwitchState] = useState<
    "idle" | "switching"
  >("idle");
  const [audioFeedback, setAudioFeedback] = useState<{
    status: string;
    hint: string;
  } | null>(null);

  useEffect(() => {
    let active = true;
    getAudioDevices()
      .then((result) => {
        if (active) setAudioProbe(result);
      })
      .catch(() => {
        if (active) setAudioProbe(null);
      });
    return () => {
      active = false;
    };
  }, []);

  const audioStatus = audioFeedback?.status || audioProbe?.status || "unavailable";
  const loopbackMissing = audioStatus === "loopback_missing";
  const loopbackHint =
    audioFeedback?.hint ||
    audioProbe?.hint ||
    "未检测到 BlackHole 2ch。安装后请在音频 MIDI 设置中创建多输出设备。";

  const changeAudioInput = async (mode: AudioInputMode) => {
    setAudioSwitchState("switching");
    setAudioFeedback(null);
    try {
      const result = await selectAudioInput(mode);
      if (result.settings) onExternalSettings(result.settings);
      setAudioFeedback({
        status: result.status,
        hint: result.hint || "",
      });
      setAudioProbe((current) => ({
        backend: current?.backend || "sounddevice",
        status: result.status,
        loopback_available: result.status === "available",
        loopback_devices: current?.loopback_devices || [],
        input_devices: current?.input_devices || [],
        hint: result.hint || current?.hint || "",
      }));
    } catch (error) {
      setAudioFeedback({
        status: "request_failed",
        hint: error instanceof Error ? error.message : "request_failed",
      });
    } finally {
      setAudioSwitchState("idle");
    }
  };

  return (
    <div className="settings-panel-stack">
      <SettingsSection
        title="声音输入"
        description="选择麦克风或系统回环输入；缺少 BlackHole 2ch 时会安全回退麦克风。"
      >
        <SettingsField label="麦克风设备" help={health?.audio_backend || "音频后端尚未探测"}>
          <Select
            ariaLabel="麦克风设备"
            value={draft.input_device || ""}
            onChange={(value) =>
              onImmediate({
                input_device: value || null,
                audio_device: value || null,
              })
            }
          >
            <option value="">系统默认设备</option>
            {devices.map((device, index) => (
              <option key={`${device.name}-${device.index ?? index}`} value={device.name}>
                {device.name}
                {device.channels ? ` · ${device.channels} ch` : ""}
              </option>
            ))}
          </Select>
        </SettingsField>
        <SettingsField label="音频输入模式">
          <Select
            ariaLabel="音频输入模式"
            value={draft.audio_input}
            disabled={audioSwitchState === "switching"}
            onChange={(value) => void changeAudioInput(value as AudioInputMode)}
          >
            <option value="microphone">仅麦克风</option>
            <option value="system">仅系统声音</option>
            <option value="both">麦克风 + 系统声音</option>
          </Select>
        </SettingsField>
        <div className="settings-status-grid">
          <div className="settings-status-tile">
            <Mic2 size={18} />
            <span>
              <strong>麦克风</strong>
              <small>{health?.audio_error ? "需要检查权限" : "可用"}</small>
            </span>
          </div>
          <div className="settings-status-tile">
            <Radio size={18} />
            <span>
              <strong>系统声音</strong>
              <small>
                {audioSwitchState === "switching"
                  ? "正在切换"
                  : audioStatus === "available"
                  ? "可用"
                  : audioStatus === "loopback_missing"
                    ? "缺少回环设备"
                    : audioStatus === "request_failed"
                      ? "切换失败"
                      : "等待探测"}
              </small>
            </span>
          </div>
        </div>
        {health?.audio_error ? (
          <div className="settings-notice is-warning">
            <AlertTriangle size={18} />
            <span>
              <strong>{health.audio_error.code}</strong>
              <small>{health.audio_error.hint}</small>
            </span>
          </div>
        ) : null}
        {loopbackMissing ? (
          <div className="settings-notice is-warning">
            <AlertTriangle size={18} />
            <span>
              <strong>loopback_missing</strong>
              <small>{loopbackHint}</small>
              <ol className="settings-notice-steps">
                <li>安装 BlackHole 2ch（终端可执行：brew install blackhole-2ch）。</li>
                <li>打开 macOS「音频 MIDI 设置」。</li>
                <li>左下角点「+」创建「多输出设备」，勾选 BlackHole 2ch。</li>
                <li>在系统声音输出中切换到该多输出设备。</li>
                <li>返回本页重新探测。</li>
              </ol>
            </span>
          </div>
        ) : null}
        {audioFeedback?.status === "request_failed" ? (
          <div className="settings-notice is-warning">
            <AlertTriangle size={18} />
            <span>
              <strong>音频输入切换失败</strong>
              <small>{audioFeedback.hint}</small>
            </span>
          </div>
        ) : null}
      </SettingsSection>

      <SettingsSection
        title="语言方向"
        description="目录来自已注册的实时引擎能力矩阵。"
      >
        <SettingsField label="源语言" help="Qwen 3.8 由模型自动识别源语种">
          <Select
            ariaLabel="源语言"
            value={draft.source_lang}
            onChange={(value) =>
              onImmediate({ source_lang: value as LanguageCode })
            }
          >
            {sources.map((language) => (
              <option key={language} value={language}>
                {LANGUAGE_NAMES[language]}
                {language === "auto" ? "（自动）" : ` · ${language}`}
              </option>
            ))}
          </Select>
        </SettingsField>
        <SettingsField label="目标语言">
          <Select
            ariaLabel="目标语言"
            value={draft.target_lang}
            onChange={(value) =>
              onImmediate({
                target_lang: value as Exclude<LanguageCode, "auto">,
              })
            }
          >
            {targets.map((language) => (
              <option key={language} value={language}>
                {LANGUAGE_NAMES[language]} · {language}
              </option>
            ))}
          </Select>
        </SettingsField>
        <div className="settings-language-route" aria-label="当前语言方向">
          <span>{LANGUAGE_NAMES[draft.source_lang]}</span>
          <i />
          <strong>{LANGUAGE_NAMES[draft.target_lang]}</strong>
        </div>
      </SettingsSection>
    </div>
  );
}
