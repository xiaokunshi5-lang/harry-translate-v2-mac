import { Eye, EyeOff, PlugZap, ShieldCheck } from "lucide-react";
import { useState } from "react";
import type {
  ProviderCatalog,
  ProviderTestResult,
  UserSettings,
} from "../../shared/types";
import {
  Select,
  SettingsField,
  SettingsSection,
} from "./Controls";

export function ProvidersPanel({
  draft,
  catalog,
  apiKeyMask,
  needsSetup,
  error,
  testState,
  testResult,
  onImmediate,
  onSaveSecret,
  onTest,
}: {
  draft: UserSettings;
  catalog: ProviderCatalog | null;
  apiKeyMask: string;
  needsSetup: boolean;
  error: string;
  testState: "idle" | "testing" | "success" | "error";
  testResult: ProviderTestResult | null;
  onImmediate: (patch: Partial<UserSettings>, debounceMs?: number) => void;
  onSaveSecret: (apiKey: string) => Promise<boolean>;
  onTest: () => Promise<void>;
}) {
  const [apiKey, setApiKey] = useState("");
  const [revealKey, setRevealKey] = useState(false);
  const [secretMessage, setSecretMessage] = useState("");
  const providers = Object.values(catalog?.providers || {});
  const activeProvider =
    catalog?.providers[draft.active_provider] || providers[0] || null;

  const submitSecret = async () => {
    if (!apiKey.trim()) {
      setSecretMessage("输入新密钥后才能更新");
      return;
    }
    const saved = await onSaveSecret(apiKey.trim());
    setSecretMessage(saved ? "密钥已安全写入本机 .env" : "密钥保存失败");
    if (saved) setApiKey("");
  };

  return (
    <div className="settings-panel-stack">
      {needsSetup ? (
        <div className="settings-notice" role="status">
          <ShieldCheck size={18} />
          <span>
            <strong>尚未配置可用密钥</strong>
            <small>填写后先更新密钥，再运行连通性测验。</small>
          </span>
        </div>
      ) : null}

      <SettingsSection
        title="实时翻译服务"
        description="选择当前运行引擎；密钥只写入本机环境文件。"
      >
        <SettingsField label="服务提供商" help="R06 能力目录中的当前默认服务">
          <Select
            ariaLabel="服务提供商"
            value={draft.active_provider}
            onChange={(providerId) => {
              const provider = catalog?.providers[providerId];
              onImmediate({
                active_provider: providerId,
                provider: providerId,
                active_model: provider?.model || draft.active_model,
                model: provider?.model || draft.model,
              });
            }}
          >
            {(providers.length ? providers : [{ id: "qwen", label: "Qwen LiveTranslate" }]).map(
              (provider) => (
                <option
                  key={provider.id}
                  value={provider.id}
                  disabled={
                    "limitations" in provider &&
                    provider.limitations.includes("skeleton_only_until_provider_is_implemented")
                  }
                >
                  {provider.label}
                </option>
              ),
            )}
          </Select>
        </SettingsField>

        <SettingsField label="模型" help={activeProvider?.source_detection === "automatic" ? "源语种由模型自动识别" : undefined}>
          <Select
            ariaLabel="模型"
            value={draft.active_model}
            onChange={(model) =>
              onImmediate({
                active_model: model,
                model,
              })
            }
          >
            <option value={activeProvider?.model || draft.active_model}>
              {activeProvider?.model || draft.active_model}
            </option>
          </Select>
        </SettingsField>

        <SettingsField label="API Key" help={apiKeyMask ? `当前：${apiKeyMask}` : "当前未配置"}>
          <span className="settings-secret-row">
            <span className="settings-secret-input">
              <input
                aria-label="API Key"
                autoComplete="off"
                spellCheck={false}
                type={revealKey ? "text" : "password"}
                placeholder={apiKeyMask || "sk-..."}
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
              />
              <button
                type="button"
                className="settings-icon-button"
                title={revealKey ? "隐藏密钥" : "显示输入"}
                aria-label={revealKey ? "隐藏密钥" : "显示输入"}
                onClick={() => setRevealKey((value) => !value)}
              >
                {revealKey ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </span>
            <button type="button" className="settings-button" onClick={() => void submitSecret()}>
              更新密钥
            </button>
          </span>
        </SettingsField>

        <SettingsField
          label="Workspace ID"
          help="仅私有 MaaS 地址使用，作为域名的一部分"
        >
          <input
            className="settings-input"
            aria-label="Workspace ID"
            autoComplete="off"
            spellCheck={false}
            value={draft.workspace_id}
            placeholder="业务空间 ID"
            onChange={(event) =>
              onImmediate({ workspace_id: event.target.value }, 300)
            }
          />
        </SettingsField>
      </SettingsSection>

      <SettingsSection
        title="连接诊断"
        description="使用 certifi 校验 TLS；测验不会发起翻译请求。"
      >
        <div className="settings-action-row">
          <button
            type="button"
            className="settings-button settings-button--primary"
            disabled={testState === "testing" || needsSetup}
            onClick={() => void onTest()}
          >
            <PlugZap size={17} />
            {testState === "testing" ? "正在测验" : "连通性测验"}
          </button>
          <div className="settings-test-result" aria-live="polite">
            {testResult ? (
              <>
                <strong>{testResult.ok ? "TLS 路由可用" : "测验未通过"}</strong>
                <small>
                  {testResult.routed_endpoint}
                  {testResult.latency_ms !== null ? ` · ${testResult.latency_ms} ms` : ""}
                </small>
              </>
            ) : (
              <small>尚未运行</small>
            )}
          </div>
        </div>
        {secretMessage ? <p className="settings-inline-message">{secretMessage}</p> : null}
        {error ? <p className="settings-inline-message is-error">{error}</p> : null}
      </SettingsSection>
    </div>
  );
}
