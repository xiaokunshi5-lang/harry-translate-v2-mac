import { create } from "zustand";
import {
  ApiError,
  getConfig,
  getHealth,
  getProviderCatalog,
  saveApiKey,
  saveSettings,
  testProvider,
} from "../shared/apiClient";
import type {
  ConfigResponse,
  HealthResponse,
  ProviderCatalog,
  ProviderTestResult,
  UserSettings,
} from "../shared/types";

type LoadState = "idle" | "loading" | "ready" | "error";
type SaveState = "idle" | "saving" | "saved" | "error";

interface SettingsState {
  status: LoadState;
  saveState: SaveState;
  error: string;
  config: ConfigResponse | null;
  draft: UserSettings | null;
  catalog: ProviderCatalog | null;
  health: HealthResponse | null;
  testState: "idle" | "testing" | "success" | "error";
  testResult: ProviderTestResult | null;
  pendingPatch: Partial<UserSettings>;
  init: () => Promise<void>;
  applyImmediate: (patch: Partial<UserSettings>, debounceMs?: number) => void;
  flush: () => void;
  syncExternalSettings: (
    settings: UserSettings,
    options?: { needsSetup?: boolean; keyMask?: string },
  ) => void;
  saveSecret: (apiKey: string) => Promise<boolean>;
  runConnectionTest: () => Promise<void>;
}

function messageFor(error: unknown): string {
  if (error instanceof ApiError) return error.code;
  if (error instanceof Error) return error.message;
  return "request_failed";
}

function mergePatches(
  base: Partial<UserSettings>,
  patch: Partial<UserSettings>,
): Partial<UserSettings> {
  const merged: Partial<UserSettings> = { ...base, ...patch };
  if (patch.ui_appearance) {
    merged.ui_appearance = {
      ...(base.ui_appearance || {}),
      ...patch.ui_appearance,
    } as UserSettings["ui_appearance"];
  }
  return merged;
}

function mergeDraft(
  current: UserSettings,
  patch: Partial<UserSettings>,
): UserSettings {
  return mergePatches(current, patch) as UserSettings;
}

function pickField<K extends keyof UserSettings>(
  patch: Partial<UserSettings>,
  key: K,
): Pick<UserSettings, K> {
  return { [key]: patch[key] } as Pick<UserSettings, K>;
}

function fieldKeys(patch: Partial<UserSettings>): Array<keyof UserSettings> {
  return Object.keys(patch) as Array<keyof UserSettings>;
}

function hasPatch(patch: Partial<UserSettings>): boolean {
  return fieldKeys(patch).length > 0;
}

let flushTimer: number | null = null;
let saveInFlight = false;
let localRevision = 0;
let inFlightPatch: Partial<UserSettings> = {};
let readyPatch: Partial<UserSettings> = {};
let pendingPatchValues: Partial<UserSettings> = {};
const pendingDeadlines = new Map<keyof UserSettings, number>();

export const useSettingsStore = create<SettingsState>((set, get) => ({
  status: "idle",
  saveState: "idle",
  error: "",
  config: null,
  draft: null,
  catalog: null,
  health: null,
  testState: "idle",
  testResult: null,
  pendingPatch: {},

  init: async () => {
    if (!saveInFlight && !hasPatch(readyPatch) && pendingDeadlines.size === 0) {
      resetCoordinator();
    }
    set({ status: "loading", error: "" });
    try {
      const [config, catalog, health] = await Promise.all([
        getConfig(),
        getProviderCatalog().catch(() => null),
        getHealth(),
      ]);
      set({
        status: "ready",
        config,
        draft: config.user_settings || config.settings,
        catalog,
        health,
        pendingPatch: {},
      });
    } catch (error) {
      set({ status: "error", error: messageFor(error) });
    }
  },

  applyImmediate: (patch, debounceMs = 0) => {
    const current = get().draft;
    if (!current) return;

    localRevision += 1;
    const keys = fieldKeys(patch);
    for (const key of keys) {
      delete readyPatch[key];
      pendingPatchValues = mergePatches(pendingPatchValues, pickField(patch, key));
      pendingDeadlines.set(key, Date.now() + Math.max(0, debounceMs));
    }

    set({
      draft: mergeDraft(current, patch),
      pendingPatch: { ...pendingPatchValues },
      saveState: "saving",
      error: "",
    });
    scheduleNextFlush();
  },

  flush: () => {
    clearFlushTimer();
    promotePending(Array.from(pendingDeadlines.keys()));
    flushReadyPatch();
  },

  syncExternalSettings: (settings, options) => {
    const current = get().draft;
    const dirtyKeys = collectDirtyKeys();
    const merged =
      current && dirtyKeys.size > 0
        ? preserveDirtyFields(settings, current, dirtyKeys)
        : settings;
    const config = get().config;
    localRevision += 1;
    const keyMask = options?.keyMask ?? config?.key_mask;
    set({
      draft: merged,
      config: config
        ? {
            ...config,
            settings: merged,
            user_settings: merged,
            needs_setup: options?.needsSetup ?? config.needs_setup,
            key_mask: keyMask ?? config.key_mask,
            api_key_masked: keyMask ?? config.api_key_masked,
          }
        : config,
    });
  },

  saveSecret: async (apiKey) => {
    set({ saveState: "saving", error: "" });
    try {
      const secret = await saveApiKey(apiKey);
      const config = get().config;
      if (config) {
        set({
          config: {
            ...config,
            key_mask: secret.key_mask,
            api_key_masked: secret.api_key_masked,
            needs_setup: secret.needs_setup,
          },
          saveState: "saved",
        });
      }
      return true;
    } catch (error) {
      set({ saveState: "error", error: messageFor(error) });
      return false;
    }
  },

  runConnectionTest: async () => {
    const draft = get().draft;
    if (!draft) return;
    set({ testState: "testing", testResult: null, error: "" });
    try {
      const result = await testProvider({
        provider: draft.active_provider,
        source_lang: draft.source_lang,
        target_lang: draft.target_lang,
        workspace_id: draft.workspace_id,
      });
      set({
        testState: result.ok ? "success" : "error",
        testResult: result,
      });
    } catch (error) {
      set({ testState: "error", error: messageFor(error) });
    }
  },
}));

function resetCoordinator(): void {
  clearFlushTimer();
  saveInFlight = false;
  localRevision = 0;
  inFlightPatch = {};
  readyPatch = {};
  pendingPatchValues = {};
  pendingDeadlines.clear();
}

function clearFlushTimer(): void {
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
}

function scheduleNextFlush(): void {
  clearFlushTimer();
  if (pendingDeadlines.size === 0) return;
  const nextDeadline = Math.min(...pendingDeadlines.values());
  flushTimer = window.setTimeout(() => {
    flushTimer = null;
    promoteDuePatches();
    flushReadyPatch();
    scheduleNextFlush();
  }, Math.max(0, nextDeadline - Date.now()));
}

function promoteDuePatches(): void {
  const now = Date.now();
  const dueKeys = Array.from(pendingDeadlines.entries())
    .filter(([, deadline]) => deadline <= now)
    .map(([key]) => key);
  promotePending(dueKeys);
}

function promotePending(keys: Array<keyof UserSettings>): void {
  for (const key of keys) {
    if (!(key in pendingPatchValues)) continue;
    readyPatch = mergePatches(readyPatch, pickField(pendingPatchValues, key));
    delete pendingPatchValues[key];
    pendingDeadlines.delete(key);
  }
  useSettingsStore.setState({ pendingPatch: { ...pendingPatchValues } });
}

function flushReadyPatch(): void {
  if (saveInFlight || !hasPatch(readyPatch)) return;
  const patch = readyPatch;
  readyPatch = {};
  inFlightPatch = patch;
  saveInFlight = true;
  const requestRevision = localRevision;
  useSettingsStore.setState({ saveState: "saving", error: "" });

  void saveSettings(patch)
    .then((config) => applySaveResponse(config, requestRevision))
    .catch((error: unknown) => applySaveError(error));
}

function applySaveResponse(
  config: ConfigResponse,
  requestRevision: number,
): void {
  saveInFlight = false;
  inFlightPatch = {};
  const serverSettings = config.user_settings || config.settings;
  const currentDraft = useSettingsStore.getState().draft;
  const dirtyKeys = collectDirtyKeys();
  let nextSettings = serverSettings;

  if (currentDraft && localRevision !== requestRevision) {
    nextSettings = currentDraft;
  } else if (currentDraft && dirtyKeys.size > 0) {
    nextSettings = preserveDirtyFields(serverSettings, currentDraft, dirtyKeys);
  }

  const hasQueuedWork =
    hasPatch(readyPatch) ||
    hasPatch(pendingPatchValues) ||
    pendingDeadlines.size > 0 ||
    saveInFlight;
  useSettingsStore.setState({
    config: {
      ...config,
      settings: nextSettings,
      user_settings: nextSettings,
    },
    draft: nextSettings,
    saveState: hasQueuedWork ? "saving" : "saved",
    error: "",
  });
  if (hasPatch(readyPatch)) flushReadyPatch();
}

function applySaveError(error: unknown): void {
  saveInFlight = false;
  const failedPatch = inFlightPatch;
  inFlightPatch = {};
  const dirtyKeys = collectDirtyKeys();
  for (const key of fieldKeys(failedPatch)) {
    if (dirtyKeys.has(key)) continue;
    readyPatch = mergePatches(readyPatch, pickField(failedPatch, key));
  }
  useSettingsStore.setState({
    saveState: "error",
    error: messageFor(error),
  });
}

function collectDirtyKeys(): Set<keyof UserSettings> {
  return new Set([
    ...fieldKeys(pendingPatchValues),
    ...fieldKeys(readyPatch),
    ...fieldKeys(inFlightPatch),
  ]);
}

function preserveDirtyFields(
  external: UserSettings,
  current: UserSettings,
  dirtyKeys: Set<keyof UserSettings>,
): UserSettings {
  const merged = { ...external };
  for (const key of dirtyKeys) {
    (merged as Record<keyof UserSettings, UserSettings[keyof UserSettings]>)[key] =
      current[key];
  }
  return merged;
}
