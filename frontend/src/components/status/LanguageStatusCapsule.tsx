export interface LanguageStatusCapsuleProps {
  engineState: string;
  healthStatus?: string;
  reason?: string;
  backoffSeconds?: number;
  reconnectAttempt?: number;
  needsSetup?: boolean;
  switching?: boolean;
  compact?: boolean;
}

function resolveTone(
  engineState: string,
  healthStatus: string | undefined,
  needsSetup: boolean,
): string {
  if (needsSetup) return "is-busy";
  if (healthStatus === "connected" || engineState === "ready") {
    return healthStatus === "degraded_minimal_corpus" ? "is-busy" : "is-ready";
  }
  if (["auth_error", "quota_error", "closed"].includes(engineState)) {
    return "is-error";
  }
  if (
    healthStatus === "reconnecting(backoff)" ||
    ["connecting", "reconnecting"].includes(engineState)
  ) {
    return "is-busy";
  }
  return "";
}

function resolveLabel(props: LanguageStatusCapsuleProps): string {
  if (props.switching) return "正在切换目标语言";
  if (props.needsSetup || props.reason === "needs_setup") return "未配置 API Key";
  if (props.healthStatus === "degraded_minimal_corpus") {
    return "已降级无热词连接";
  }
  if (props.healthStatus === "connected" || props.engineState === "ready") {
    return "引擎已就绪";
  }
  if (props.healthStatus === "reconnecting(backoff)") {
    const backoff =
      typeof props.backoffSeconds === "number" && props.backoffSeconds > 0
        ? ` (${props.backoffSeconds.toFixed(1)}s)`
        : "";
    return `退避重连中${backoff}`;
  }
  const labels: Record<string, string> = {
    idle: "等待连接",
    connecting: "正在连接",
    reconnecting: "正在重连",
    auth_error: "鉴权失败",
    quota_error: "限流或额度异常",
    closed: "连接已关闭",
  };
  return labels[props.engineState] || props.engineState || "等待连接";
}

export function LanguageStatusCapsule(props: LanguageStatusCapsuleProps) {
  const tone = resolveTone(
    props.engineState,
    props.healthStatus,
    Boolean(props.needsSetup),
  );
  const label = resolveLabel(props);
  const showReason =
    !props.compact &&
    Boolean(props.reason) &&
    props.reason !== "needs_setup" &&
    props.reason !== "waiting_for_client";

  return (
    <span
      className={`language-status-capsule monitor-state ${tone}${
        props.compact ? " is-compact" : ""
      }`}
      data-health-status={props.healthStatus || props.engineState}
    >
      <i />
      <strong>{label}</strong>
      {showReason ? (
        <small className="language-status-capsule__reason">{props.reason}</small>
      ) : null}
    </span>
  );
}
