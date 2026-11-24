# ADR-020: R18 authoritative realtime switch

- **Status**: Accepted; user manual acceptance passed on 2026-11-24
- **Date**: 2026-11-24
- **Decision owner**: user-authorized R18-02 execution
- **Baseline**: `fix-attempt/R18-01/1` / `839f4c9`
- **Closeout checkpoint**: `checkpoint-r18-02-ok` / `bc9cdb5`
- **Scope**: one settings-window realtime switch, authoritative backend state,
  passive WebSocket synchronization, and truthful failure recovery
- **Related**: `ADR-008`, `ADR-009`, `ADR-014`, `ADR-019`

## Background

The settings top bar exposed separate launch and hide buttons for one logical
state. The page read `/api/overlay/status` once at mount, so tray, native-window,
and backend changes could drift from the visible button state. The existing
`Toggle` primitive also lacked disabled, busy, and description support for an
asynchronous authoritative action.

R18-02 was authorized before R18-01 had received manual acceptance. The user
later accepted both rounds together on 2026-11-24; the historical execution
order does not change the final closeout state.

## Decision

1. The settings window exposes exactly one switch with
   `role="switch"`, `aria-label="实时翻译"`, and `aria-checked`. No separate
   launch and hide buttons remain in the settings top bar.
2. The checked state is derived only from backend-confirmed state:
   `visible === true && realtime_enabled !== false`. The compatibility rule
   allows older test stubs that omit `realtime_enabled`, but any explicit
   `false` value is authoritative.
3. No optimistic state change is allowed. During a request the switch keeps
   its prior checked value, sets `aria-busy="true"`, and disables repeat input.
4. HTTP errors, network errors, and an explicit `ok=false` response trigger a
   fresh `/api/overlay/status` read. The switch follows that result and an
   accessible `role="alert"` explains the failure. Browser fallback remains
   honest: opening the auxiliary browser HUD does not turn the native switch
   on.
5. `/api/overlay/status` and `/ws/config` share one overlay snapshot containing
   `running`, `visible`, `realtime_enabled`, `registered`, `control_url`,
   `binary_available`, and `fallback_url`. `/ws/config` emits
   `{ "type": "overlay_status", "overlay": <snapshot> }` after registration,
   launch, hide, and its initial handshake.
6. The settings page consumes only the passive `/ws/config` channel. It must
   never connect `/ws/main`; that channel remains the single subtitle and
   upstream-session path.
7. Window focus and visible-page changes trigger one authoritative status read
   so tray or native-window changes converge without a manual refresh.
8. The shared `Toggle` primitive carries optional disabled, busy, id,
   description, and class-name state. Its switch animation remains visual only:
   no sound, no `AudioContext`, and no subtitle-window controls.
9. A missing API key disables launch and explains the reason. The native
   subtitle overlay remains button-free and link-free.

## Authorized Existing-Test Evolution

`tests/unit/test_repro_r155_ux_and_launch.py` previously asserted the literal
text of the retired launch button. R18-02 replaces that assertion with the
single-switch contract and relies on
`tests/unit/test_repro_r18_02_realtime_switch.py` plus
`frontend/src/screens/SettingsView.test.tsx` for behavioral coverage.

## Must Remain Unchanged

- `MAX_OVERLAY_WINDOWS=1`, one main session, and one active upstream Qwen
  WebSocket.
- `/ws/config` is passive and must not call `SessionManager.connect_client()`.
- Native overlay button and anchor counts remain zero.
- Existing settings tabs, routes, preview DOM, local-storage prohibition, and
  audio-sensitive silence rules remain unchanged.
- Native settings-window creation, Dock `Reopen`, app quit UI, and R18-03
  lifecycle behavior remain out of scope.

## Verification

- `tests/unit/test_repro_r18_02_realtime_switch.py`
- `frontend/src/screens/SettingsView.test.tsx`
- `frontend/src/windows/overlay/OverlayWindow.test.tsx`
- `tests/smoke/settings_ui_gate.mjs`
- `npm --prefix frontend run typecheck`
- `npm --prefix frontend test`
- `npm --prefix frontend run build`
- `.venv-v2/bin/python -m pytest -q tests/unit/test_repro_r155_ux_and_launch.py`
- `.venv-v2/bin/python -m pytest -q tests/core_regression/`

## Acceptance

The user verified the single switch, honest busy state, failure rollback,
keyboard operation, passive state synchronization, realtime on/off behavior,
and the absence of UI sound on 2026-11-24.
