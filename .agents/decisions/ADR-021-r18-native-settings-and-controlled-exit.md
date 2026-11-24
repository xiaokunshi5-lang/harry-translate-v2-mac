# ADR-021: R18 native settings window and controlled application exit

- **Status**: Accepted; user manual acceptance passed on 2026-11-24
- **Date**: 2026-11-24
- **Decision owner**: user-authorized R18-03 execution
- **Baseline**: `checkpoint-r18-02-candidate` / `bc9cdb5`
- **Closeout checkpoint**: `checkpoint-r18-03-ok` / `f1cdc38`
- **Scope**: native settings window, Dock/Finder reopen, application menu,
  auxiliary browser isolation, controlled quit, and Sidecar startup retry
- **Related**: `ADR-009`, `ADR-014`, `ADR-015`, `ADR-019`, `ADR-020`

## Background

R18-01 and R18-02 made native startup quiet and made the settings realtime
switch authoritative, but the Tauri host still had no real settings window.
Cold startup opened `/settings` in the system browser, closing the browser lost
the control surface, and a secondary launch incorrectly tried to reveal the
hidden subtitle overlay. The application also lacked a single explicit quit
path that preserved the distinction between an ordinary window close and a
user request to stop the whole application.

## Decision

1. Create a dynamic `settings` `WebviewWindow` only after the Sidecar endpoint
   exists. It is a regular, resizable, decorated, non-transparent macOS window
   with a minimum inner size of `820x580`.
2. Keep `tauri.conf.json` limited to the single declared `overlay` window.
   `MAX_OVERLAY_WINDOWS=1` and the overlay's real non-activating `NSPanel`
   conversion remain unchanged.
3. Closing the settings window prevents native destruction and hides it.
   `show_and_focus_settings_window` unminimizes when necessary, shows, focuses,
   and activates the regular application window.
4. Cold startup, Dock/Finder `RunEvent::Reopen`, the application menu, the tray,
   and a secondary process all route to the settings window rather than the
   hidden subtitle overlay.
5. `HARRY_NO_OPEN_SETTINGS=1` and `--no-open-settings` still suppress automatic
   initial settings display for automated smoke runs. The desktop launcher no
   longer injects that flag unless the user supplied it.
6. The native application menu provides `打开设置窗口` (`Cmd+,`) and
   `退出 HARRY Translate` (`Cmd+Q`). The same menu retains standard edit,
   window, hide, and close actions.
7. `app_quit` and all explicit user quit surfaces run cleanup in a background
   thread: request `POST /api/overlay/hide`, stop the owned or verified attached
   backend, then call `app.exit(0)`. A `RunEvent::ExitRequested` with no code is
   prevented once, routed through the same cleanup, and then allowed after the
   programmatic exit.
8. This explicit whole-application quit is the narrow exception to ADR-014's
   attached-backend rule. Ordinary settings-window close, backend-loss exit,
   and the loopback control `/shutdown` path must not request
   `/api/system/shutdown` for an attached backend.
9. The native settings WebView opens `浏览器大屏（辅助）` through the Tauri
   opener command. It must not navigate its own WebView to `/` or establish a
   second subtitle session.
10. `getBackendEndpoint()` retries only `sidecar_not_ready` for at most 40
    attempts at 50 ms intervals. Other errors fail immediately, and the cached
    promise prevents concurrent callers from multiplying the retry loop.
11. The settings window continues to use only `/ws/config`. It must never
    connect `/ws/main`.

## Authorized Existing-Test Evolution

1. `tests/unit/test_repro_r105_pure_overlay.py` changes its tray labels from
   `打开配置页（浏览器）` / `退出悬浮窗` to `打开设置窗口` /
   `退出 HARRY Translate`.
2. `tests/smoke/native_overlay_lifecycle.py` no longer expects a secondary
   launch to reveal the hidden overlay. It asserts the duplicate exits while
   the overlay remains hidden, then separately verifies that a host allowed to
   open settings presents a layer-0 settings window without showing the
   overlay.
3. The single-instance and full-screen lifecycle checks remain present; this
   evolution changes the expected control surface, not the requirement to
   prove single-instance and zero-residual behavior.

## Must Remain Unchanged

- One `overlay` declaration and one active overlay/upstream session.
- The overlay remains a button-free, link-free non-activating `NSPanel`.
- Summary remains an ordinary closable `NSWindow`; no dynamic `NSPanel`
  conversion, `destroy()`, or `to_window().close()`.
- Settings must not connect `/ws/main`.
- No Token/usage/cost fields, subtitle red highlighting, anomaly badges,
  sound effects, or `AudioContext` nodes.
- No new dependency or private service.

## Verification

- `tests/unit/test_repro_r18_03_native_settings_lifecycle.py`
- `frontend/src/screens/SettingsView.test.tsx`
- `frontend/src/lib/backendBridge.test.ts`
- `tests/smoke/native_overlay_lifecycle.py`
- `tests/unit/test_repro_r105_pure_overlay.py`
- `tests/unit/test_repro_r155s_native_lifecycle.py`
- `cargo test --offline --manifest-path src-tauri/Cargo.toml`
- `cargo build --offline --features custom-protocol --manifest-path src-tauri/Cargo.toml`
- `npm --prefix frontend run typecheck && npm --prefix frontend test && npm --prefix frontend run build`
- User manual acceptance of cold start, Dock/Finder reopen, auxiliary browser,
  and controlled quit with zero residual process.

## Acceptance

The user completed the native cold start, full-screen Space, close-and-reopen,
external-browser isolation, controlled quit, and zero-residual checks on
2026-11-24. Both the application and Sidecar exited cleanly.
