# ADR-019: R18 quiet start and realtime session gate

- **Status**: Accepted; user manual acceptance passed on 2026-11-24
- **Date**: 2026-11-24
- **Decision owner**: user-authorized R18-01 execution
- **Pre-fix checkpoint**: `pre-fix-r18-20261124` / `604cd26`
- **Closeout checkpoint**: `checkpoint-r18-01-ok` / `839f4c9`
- **Scope**: quiet native startup, native visibility registration, realtime
  enable/disable gate, and server-side stop on hide
- **Related**: `ADR-008`, `ADR-009`, `ADR-014`, `ADR-015`

## Background

The released Tauri host unconditionally showed the subtitle overlay during
`setup()` and reported the control service as visible before the user asked to
start translation. The Sidecar also registered that synthetic visible state and
only relied on the hidden WebView to close `/ws/main`. A delayed or stuck
WebView could therefore leave the microphone or upstream Qwen session running.

## Decision

1. Tauri startup configures the existing `HarrySubtitlePanel` geometry,
   collection behavior, and presentation, but does not display it. The control
   server and `overlay_get_visibility` both begin at `visible=false`.
2. Initial Sidecar control registration is attempted synchronously for at most
   three bounded 75 ms attempts (225 ms total) and carries the real native
   `visible` value. Connect, write, and read phases share the remaining attempt
   deadline, so one attempt cannot multiply the timeout. The background retry
   remains only as a fallback.
3. Browser-only paths remain compatible while no native control service is
   registered. Registration activates the native gate; until
   `POST /api/overlay/launch` succeeds, `/ws/main` must be rejected without
   activating the session controller, opening audio, or connecting upstream.
4. `POST /api/overlay/launch` enables the gate before requesting native show so
   the visibility event cannot race a still-paused Sidecar. A failed show
   returns to paused and disabled state.
5. `POST /api/overlay/hide` is fail-closed. It sets `realtime_enabled=false`,
   sets `SessionManager.paused=true`, closes every active `main` client with
   code `1000`, calls `SessionController.stop()` (including the existing
   bounded `session.finish` path), and stops `AudioRuntime`. This happens even
   if the native hide request fails. Controller or audio stop failures are
   surfaced as `stop_failed` with HTTP 503 rather than a false success.
6. Native control `/health.visible` is authoritative for
   `GET /api/overlay/status`. Missing or unhealthy control health fails closed
   for an already registered native overlay. Launch, hide, and status state
   transitions share the same serialization lock so a status poll cannot
   re-enable the gate while hide is in flight.
7. Success and failure transitions broadcast an `overlay_status` event over the
   passive `/ws/config` channel. This channel remains non-billing and never
   joins the subtitle session.
8. A repeated registration from the same control URL is idempotent for
   visibility. It may retry a pending stop but cannot overwrite a newer
   launch-enabled state with a stale retry payload.
9. Hiding does not clear `SubtitlePipelineState`; the existing React test that
   closes `/ws/main` and reconnects without losing history remains the UI
   contract.
10. Native settings-window creation, `RunEvent::Reopen`, and replacement of the
   duplicate-instance overlay reveal belong to R18-03. This ADR does not bring
   those later window-lifecycle changes into R18-01.

## Authorized Existing-Test Evolution

`tests/smoke/native_overlay_lifecycle.py` is intentionally updated because its
old first assertion encoded the superseded startup behavior. The authorized
change is limited to:

1. Assert the initial overlay diagnostics report `visible=false`.
2. Invoke the backend `POST /api/overlay/launch` path and then require
   `visible=true` before the existing full-screen checks continue.
3. Invoke `POST /api/overlay/hide` instead of the loopback control `/hide` so
   the smoke also exercises the server-side realtime gate.

The duplicate-instance assertion remains unchanged in R18-01 and is expected to
move to the native settings window under R18-03.

## Must Remain Unchanged

- `MAX_OVERLAY_WINDOWS=1` and one active upstream Qwen WebSocket.
- The overlay remains a button-free, link-free, non-activating `NSPanel`.
- No changes to `app/audio/**`, `app/engines/**`, `app/glossary/**`,
  `app/bypass/**`, `app/transcript_cleaner.py`, or `app/session_manager.py`.
- Summary remains a normal `NSWindow`; no runtime `NSPanel` conversion,
  `destroy()`, or `to_window().close()`.
- Zero Token/usage/cost fields, zero subtitle red highlighting, zero anomaly
  badges, and zero UI sound.

## Verification

- `tests/unit/test_repro_r18_01_quiet_launch_and_stop.py`
- `tests/core_regression/`
- `tests/smoke/native_overlay_lifecycle.py`
- `cargo test --offline --manifest-path src-tauri/Cargo.toml`
- `cargo build --offline --features custom-protocol --manifest-path src-tauri/Cargo.toml`
- `npm --prefix frontend run typecheck && npm --prefix frontend test && npm --prefix frontend run build`
- `.venv-v2/bin/python scripts/smoke_e2e.py --mock`
- `ALLOW_TEST_EDIT=1 ./scripts/watchdog_gate.sh tests/unit/test_repro_r18_01_quiet_launch_and_stop.py pre-fix-r18-20261124`

`ALLOW_TEST_EDIT=1` is required only for the test evolution authorized above.
All other watchdog gates remain active.

## Acceptance

The user completed the quiet-start, no-background-microphone, realtime on/off,
history-retention, and packaged-app control checks on 2026-11-24. R18-01 is
accepted as part of the final `post-fix-R18` closeout.
