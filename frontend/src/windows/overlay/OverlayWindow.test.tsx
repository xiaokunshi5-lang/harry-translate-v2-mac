// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetBackendEndpointCache } from "../../lib/backendBridge";
import { IMMERSIVE_TEXT_SHADOW } from "../../lib/subtitles/subtitleAppearance";
import type { UserSettings } from "../../shared/types";
import { OverlayWindow } from "./OverlayWindow";

class FakeWebSocket extends EventTarget {
  static instances: FakeWebSocket[] = [];
  readonly url: string;
  closed = false;

  constructor(url: string) {
    super();
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.dispatchEvent(new Event("close"));
  }
}

type TauriEventCallback = (message: {
  event: string;
  id: number;
  payload: unknown;
}) => void;

const BASE_SETTINGS: UserSettings = {
  active_provider: "qwen",
  active_model: "qwen3.8-livetranslate-flash-realtime",
  provider: "qwen",
  model: "qwen3.8-livetranslate-flash-realtime",
  source_lang: "auto",
  target_lang: "zh",
  audio_input: "both",
  input_device: null,
  audio_device: null,
  workspace_id: "",
  font_size: 18,
  source_font_size: 15,
  target_font_size: 18,
  subtitle_emphasis: "translation_primary",
  bilingual_layout_order: "source_top",
  glossary_enabled: true,
  glossary_wire_budget_bytes: 4096,
  overlay_window_mode: "single",
  overlay_immersive: false,
  overlay_click_through: true,
  hud_geometry: {
    x: 200,
    y: 120,
    width: 580,
    height: 240,
    mode: "expanded",
    locked: false,
  },
  background_opacity: 35,
  subtitle_display_mode: "bilingual",
  subtitle_color: "#FFFFFF",
  mic_subtitle_color: "#FFD54F",
  system_subtitle_color: "#4FD1C5",
  subtitle_alignment: "left",
  line_height_multiplier: 1.32,
  show_subtitle_dividers: true,
  visible_subtitle_count: 3,
  is_overlay_locked: false,
  correction_enabled: true,
  correction_mode: "llm",
  correction_model: "qwen-turbo",
  correction_gap_ms: 1500,
  summary_enabled: false,
  ui_appearance: { density: "comfortable", accent: "teal" },
  windows: [],
};

let root: Root | null = null;
let container: HTMLDivElement | null = null;
let activeInvokeMock: {
  mock: { calls: Array<[string, Record<string, unknown>?]> };
} | null = null;
let nextCallbackId = 1;
let tauriCallbacks = new Map<number, TauriEventCallback>();
let tauriEventIds = new Map<string, number>();

function response(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function snapshot(settings: UserSettings) {
  return {
    maxOverlayWindows: 1,
    activeWindows: ["overlay"],
    mode: settings.hud_geometry?.mode || "expanded",
    locked: settings.is_overlay_locked,
    clickThrough: settings.overlay_click_through,
    hoverUnlocked: false,
    immersive: settings.overlay_immersive,
    userFrame: settings.hud_geometry,
    presentationFrame: settings.hud_geometry,
  };
}

async function renderOverlay(
  settings: UserSettings,
  options: {
    needsSetup?: boolean;
    visible?: boolean;
    visibilityError?: boolean;
  } = {},
) {
  const needsSetup = options.needsSetup === true;
  const visible = options.visible !== false;
  const nativeSnapshot = snapshot(settings);
  const invokeMock = vi.fn(
    async (
      command: string,
      args?: Record<string, unknown>,
    ): Promise<unknown> => {
      if (command === "get_backend_endpoint") {
        return {
          host: "127.0.0.1",
          port: 8123,
          baseUrl: "http://127.0.0.1:8123",
          wsBaseUrl: "ws://127.0.0.1:8123",
          pid: 1,
        };
      }
      if (command === "overlay_get_state") return nativeSnapshot;
      if (command === "overlay_get_visibility") {
        if (options.visibilityError) {
          throw new Error("visibility_query_failed");
        }
        return { visible };
      }
      if (command === "plugin:event|listen") {
        const event = String(args?.event || "");
        const callbackId = Number(args?.handler);
        if (event && Number.isFinite(callbackId)) {
          tauriEventIds.set(event, callbackId);
        }
        return 1;
      }
      if (command === "plugin:event|unlisten") return 1;
      return nativeSnapshot;
    },
  );
  activeInvokeMock = invokeMock;
  window.__TAURI_INTERNALS__ = {
    invoke: invokeMock as unknown as <T>(
      command: string,
      args?: Record<string, unknown>,
    ) => Promise<T>,
    transformCallback: vi.fn((callback: TauriEventCallback) => {
      const callbackId = nextCallbackId;
      nextCallbackId += 1;
      tauriCallbacks.set(callbackId, callback);
      return callbackId;
    }),
    unregisterCallback: vi.fn((callbackId: number) => {
      tauriCallbacks.delete(callbackId);
    }),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/health")) {
        return response({
          status: "ok",
          port: 8123,
          python_version: "3.12",
          audio_backend: "sounddevice",
          audio_devices: [],
          audio_error: null,
          env_exists: true,
          needs_setup: needsSetup,
          key_mask: "sk-****test",
          permission_hint: "",
        });
      }
      return response({
        settings,
        user_settings: settings,
        key_mask: "sk-****test",
        api_key_masked: "sk-****test",
        needs_setup: needsSetup,
      });
    }),
  );
  vi.stubGlobal("WebSocket", FakeWebSocket);

  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<OverlayWindow />);
    await Promise.resolve();
    await Promise.resolve();
  });
  return container;
}

async function emitSubtitle(target: HTMLElement) {
  const socket = FakeWebSocket.instances
    .filter((candidate) => candidate.url.includes("/ws/main"))
    .at(-1);
  expect(socket).toBeDefined();
  await act(async () => {
    socket?.dispatchEvent(new Event("open"));
    socket?.dispatchEvent(
      new MessageEvent("message", {
        data: JSON.stringify({
          type: "subtitle",
          source_text: "原文",
          target_text: "Translation",
          is_final: false,
        }),
      }),
    );
  });
  return target.querySelector(".overlay-subtitle-line") as HTMLElement;
}

function socketFor(channel: string): FakeWebSocket | undefined {
  return FakeWebSocket.instances
    .filter((candidate) => candidate.url.includes(`/ws/${channel}`))
    .at(-1);
}

async function emitTauriEvent(event: string, payload: unknown) {
  const callbackId = tauriEventIds.get(event);
  const callback =
    callbackId === undefined ? undefined : tauriCallbacks.get(callbackId);
  expect(callback).toBeDefined();
  await act(async () => {
    callback?.({ event, id: callbackId ?? 0, payload });
    await Promise.resolve();
  });
}

async function emitConfigUpdate(event: Record<string, unknown>) {
  const configSocket = socketFor("config");
  expect(configSocket).toBeDefined();
  await act(async () => {
    configSocket?.dispatchEvent(
      new MessageEvent("message", {
        data: JSON.stringify(event),
      }),
    );
    await Promise.resolve();
  });
}

describe("OverlayWindow native contract", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    resetBackendEndpointCache();
    activeInvokeMock = null;
    nextCallbackId = 1;
    tauriCallbacks = new Map();
    tauriEventIds = new Map();
  });

  afterEach(async () => {
    await act(async () => root?.unmount());
    container?.remove();
    root = null;
    container = null;
    activeInvokeMock = null;
    tauriCallbacks = new Map();
    tauriEventIds = new Map();
    vi.unstubAllGlobals();
    delete window.__TAURI_INTERNALS__;
  });

  it("renders zero native buttons and anchors with constant standard background", async () => {
    const surface = await renderOverlay(BASE_SETTINGS);
    const windowNode = surface.querySelector(".overlay-window") as HTMLElement;
    expect(surface.querySelectorAll("button")).toHaveLength(0);
    expect(surface.querySelectorAll("a")).toHaveLength(0);
    const before = windowNode.style.background;
    expect(before).toBe("rgba(0, 0, 0, 0.35)");
    await emitSubtitle(surface);
    expect(windowNode.style.background).toBe(before);
  });

  it("uses background-blend and three-layer shadows without resize handles", async () => {
    const surface = await renderOverlay({
      ...BASE_SETTINGS,
      overlay_immersive: true,
    });
    const windowNode = surface.querySelector(".overlay-window") as HTMLElement;
    expect(windowNode.dataset.presentation).toBe("background-blend");
    expect(windowNode.style.background).toBe("transparent");
    expect(surface.querySelectorAll("[data-resize-edge]")).toHaveLength(0);
    const line = await emitSubtitle(surface);
    expect(line.style.textShadow).toBe(IMMERSIVE_TEXT_SHADOW);
  });

  it("keeps only passive config sync before setup and activates automatically", async () => {
    const surface = await renderOverlay(BASE_SETTINGS, { needsSetup: true });
    expect(socketFor("config")).toBeDefined();
    expect(socketFor("main")).toBeUndefined();

    await emitConfigUpdate({
      type: "config_updated",
      settings: BASE_SETTINGS,
      needs_setup: false,
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(socketFor("main")).toBeDefined();
    expect(surface.querySelectorAll("button")).toHaveLength(0);
    expect(surface.querySelectorAll("a")).toHaveLength(0);
  });

  it("stops the main socket while hidden and reconnects without losing history", async () => {
    const surface = await renderOverlay(BASE_SETTINGS);
    await emitSubtitle(surface);
    const initialMain = socketFor("main");
    const configSocket = socketFor("config");
    expect(initialMain).toBeDefined();
    expect(configSocket).toBeDefined();

    await emitTauriEvent("overlay-visibility-changed", { visible: false });
    expect(initialMain?.closed).toBe(true);
    expect(configSocket?.closed).toBe(false);
    expect(surface.textContent).toContain("原文");
    expect(surface.textContent).toContain("Translation");

    await emitTauriEvent("overlay-visibility-changed", { visible: true });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const reconnectedMain = socketFor("main");
    expect(reconnectedMain).toBeDefined();
    expect(reconnectedMain).not.toBe(initialMain);
    expect(surface.textContent).toContain("原文");
    expect(surface.textContent).toContain("Translation");
  });

  it("syncs presentation updates without reverse persistence", async () => {
    await renderOverlay(BASE_SETTINGS);
    const updated = { ...BASE_SETTINGS, overlay_immersive: true };
    await emitConfigUpdate({
      type: "config_updated",
      settings: updated,
      needs_setup: false,
    });

    const calls = activeInvokeMock?.mock.calls.filter(
      ([command]) => command === "overlay_sync_presentation",
    ) ?? [];
    const latest = calls.at(-1);
    expect(latest).toBeDefined();
    expect(latest?.[1]).toMatchObject({
      immersive: true,
      persist: false,
    });
  });

  it("queries native visibility before opening main and starts hidden when false", async () => {
    await renderOverlay(BASE_SETTINGS, { visible: false });

    const visibilityQuery = activeInvokeMock?.mock.calls.find(
      ([command]) => command === "overlay_get_visibility",
    );
    expect(visibilityQuery).toBeDefined();
    expect(socketFor("config")).toBeDefined();
    expect(socketFor("main")).toBeUndefined();

    await emitTauriEvent("overlay-visibility-changed", { visible: true });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      FakeWebSocket.instances.filter((socket) =>
        socket.url.includes("/ws/main"),
      ),
    ).toHaveLength(1);

    await emitTauriEvent("overlay-visibility-changed", { visible: true });
    expect(
      FakeWebSocket.instances.filter((socket) =>
        socket.url.includes("/ws/main"),
      ),
    ).toHaveLength(1);
  });

  it("stays conservatively hidden when the visibility query fails", async () => {
    await renderOverlay(BASE_SETTINGS, { visibilityError: true });

    expect(socketFor("config")).toBeDefined();
    expect(socketFor("main")).toBeUndefined();

    await emitTauriEvent("overlay-visibility-changed", { visible: true });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(socketFor("main")).toBeDefined();
  });
});
