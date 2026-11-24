// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getBackendEndpoint,
  isSidecarNotReadyError,
  resetBackendEndpointCache,
  type BackendEndpoint,
} from "./backendBridge";

const ENDPOINT: BackendEndpoint = {
  host: "127.0.0.1",
  port: 18123,
  baseUrl: "http://127.0.0.1:18123",
  wsBaseUrl: "ws://127.0.0.1:18123",
  pid: 42,
};

function installInvoke(
  implementation: (...args: unknown[]) => Promise<unknown>,
): ReturnType<typeof vi.fn> {
  const invoke = vi.fn(implementation);
  window.__TAURI_INTERNALS__ = {
    invoke: invoke as unknown as <T>(
      command: string,
      args?: Record<string, unknown>,
    ) => Promise<T>,
  };
  return invoke;
}

describe("backend endpoint readiness", () => {
  beforeEach(() => {
    resetBackendEndpointCache();
    vi.useFakeTimers();
  });

  afterEach(() => {
    resetBackendEndpointCache();
    vi.useRealTimers();
    delete window.__TAURI_INTERNALS__;
  });

  it("recognizes the sidecar readiness error without retrying other failures", () => {
    expect(isSidecarNotReadyError("sidecar_not_ready")).toBe(true);
    expect(isSidecarNotReadyError(new Error("Command failed: sidecar_not_ready"))).toBe(
      true,
    );
    expect(isSidecarNotReadyError(new Error("permission_denied"))).toBe(false);
  });

  it("retries sidecar_not_ready for up to the bounded startup window", async () => {
    const invoke = installInvoke(
      vi
        .fn()
        .mockRejectedValueOnce("sidecar_not_ready")
        .mockResolvedValueOnce(ENDPOINT),
    );
    const endpointPromise = getBackendEndpoint();

    await Promise.resolve();
    expect(invoke).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(50);

    await expect(endpointPromise).resolves.toEqual(ENDPOINT);
    expect(invoke).toHaveBeenCalledTimes(2);
  });

  it("fails immediately for a non-readiness invocation error", async () => {
    const invoke = installInvoke(vi.fn().mockRejectedValue("permission_denied"));

    await expect(getBackendEndpoint()).rejects.toBe("permission_denied");
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it("shares one bounded retry loop across concurrent callers", async () => {
    const invoke = installInvoke(
      vi
        .fn()
        .mockRejectedValueOnce("sidecar_not_ready")
        .mockResolvedValueOnce(ENDPOINT),
    );

    const first = getBackendEndpoint();
    const second = getBackendEndpoint();
    await vi.advanceTimersByTimeAsync(50);

    await expect(first).resolves.toEqual(ENDPOINT);
    await expect(second).resolves.toEqual(ENDPOINT);
    expect(invoke).toHaveBeenCalledTimes(2);
  });

  it("stops after 40 readiness attempts and clears the failed cache", async () => {
    const invoke = installInvoke(vi.fn().mockRejectedValue("sidecar_not_ready"));
    const endpointPromise = getBackendEndpoint();
    const rejection = expect(endpointPromise).rejects.toBe("sidecar_not_ready");

    await vi.advanceTimersByTimeAsync(2_000);
    await rejection;
    expect(invoke).toHaveBeenCalledTimes(40);

    invoke.mockResolvedValueOnce(ENDPOINT);
    await expect(getBackendEndpoint()).resolves.toEqual(ENDPOINT);
    expect(invoke).toHaveBeenCalledTimes(41);
  });
});
