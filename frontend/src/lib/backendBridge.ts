export interface BackendEndpoint {
  host: string;
  port: number;
  baseUrl: string;
  wsBaseUrl: string;
  pid?: number | null;
}

interface TauriInternals {
  invoke?: <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
  transformCallback?: (
    callback: (event: { event: string; id: number; payload: unknown }) => void,
    once?: boolean,
  ) => number;
  unregisterCallback?: (id: number) => void;
}

declare global {
  interface Window {
    __TAURI_INTERNALS__?: TauriInternals;
  }
}

let cachedEndpointPromise: Promise<BackendEndpoint> | null = null;
const SIDECAR_READY_RETRY_ATTEMPTS = 40;
const SIDECAR_READY_RETRY_DELAY_MS = 50;

export function isTauriRuntime(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.__TAURI_INTERNALS__?.invoke === "function"
  );
}

export async function tauriInvoke<T>(
  command: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  const invoke =
    typeof window !== "undefined" ? window.__TAURI_INTERNALS__?.invoke : undefined;
  if (typeof invoke !== "function") {
    throw new Error(`tauri_runtime_unavailable:${command}`);
  }
  return invoke<T>(command, args);
}

export async function listenTauriEvent<T>(
  event: string,
  handler: (payload: T) => void,
): Promise<() => void> {
  const internals =
    typeof window !== "undefined" ? window.__TAURI_INTERNALS__ : undefined;
  const invoke = internals?.invoke;
  const transformCallback = internals?.transformCallback;
  if (typeof invoke !== "function" || typeof transformCallback !== "function") {
    throw new Error(`tauri_event_runtime_unavailable:${event}`);
  }
  const callbackId = transformCallback((message) => {
    handler(message.payload as T);
  });
  const eventId = await invoke<number>("plugin:event|listen", {
    event,
    target: { kind: "Any" },
    handler: callbackId,
  });
  return () => {
    void invoke("plugin:event|unlisten", { event, eventId }).finally(() => {
      internals?.unregisterCallback?.(callbackId);
    });
  };
}

export function resetBackendEndpointCache(): void {
  cachedEndpointPromise = null;
}

export function isSidecarNotReadyError(error: unknown): boolean {
  const message =
    error instanceof Error
      ? `${error.name}: ${error.message}`
      : typeof error === "string"
        ? error
        : String(error);
  return message.includes("sidecar_not_ready");
}

async function getBackendEndpointWithRetry(): Promise<BackendEndpoint> {
  for (let attempt = 0; attempt < SIDECAR_READY_RETRY_ATTEMPTS; attempt += 1) {
    try {
      return await tauriInvoke<BackendEndpoint>("get_backend_endpoint");
    } catch (error) {
      const isLastAttempt = attempt === SIDECAR_READY_RETRY_ATTEMPTS - 1;
      if (!isSidecarNotReadyError(error) || isLastAttempt) {
        throw error;
      }
      await new Promise<void>((resolve) => {
        window.setTimeout(resolve, SIDECAR_READY_RETRY_DELAY_MS);
      });
    }
  }
  throw new Error("sidecar_not_ready");
}

function browserFallbackEndpoint(): BackendEndpoint {
  const protocol =
    typeof window !== "undefined" && window.location.protocol === "https:"
      ? "https:"
      : "http:";
  const wsProtocol = protocol === "https:" ? "wss:" : "ws:";
  const host =
    typeof window !== "undefined" && window.location.host
      ? window.location.host
      : "127.0.0.1:8000";
  const parsedPort = Number(
    typeof window !== "undefined" ? window.location.port || "8000" : "8000",
  );
  return {
    host: typeof window !== "undefined" ? window.location.hostname || "127.0.0.1" : "127.0.0.1",
    port: Number.isFinite(parsedPort) && parsedPort > 0 ? parsedPort : 8000,
    baseUrl:
      typeof window !== "undefined" && window.location.origin
        ? window.location.origin
        : "http://127.0.0.1:8000",
    wsBaseUrl: `${wsProtocol}//${host}`,
    pid: null,
  };
}

export async function getBackendEndpoint(): Promise<BackendEndpoint> {
  if (!isTauriRuntime()) {
    return browserFallbackEndpoint();
  }
  if (!cachedEndpointPromise) {
    cachedEndpointPromise = getBackendEndpointWithRetry().catch((error) => {
      cachedEndpointPromise = null;
      throw error;
    });
  }
  return cachedEndpointPromise;
}

export async function resolveApiUrl(path: string): Promise<string> {
  if (!isTauriRuntime()) {
    return path;
  }
  const endpoint = await getBackendEndpoint();
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${endpoint.baseUrl}${normalized}`;
}

export async function resolveWsUrl(channel: string): Promise<string> {
  const encoded = encodeURIComponent(channel);
  if (!isTauriRuntime()) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}/ws/${encoded}`;
  }
  const endpoint = await getBackendEndpoint();
  return `${endpoint.wsBaseUrl}/ws/${encoded}`;
}
