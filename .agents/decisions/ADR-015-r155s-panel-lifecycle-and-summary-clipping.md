# ADR-015: R15.5S native panel lifecycle and summary icon clipping

- **Status**: Accepted; automated tests, independent watchdog and user manual
  acceptance passed on 2026-11-23
- **Date**: 2026-11-23
- **Decision owner**: user-authorized R15.5S repair
- **Pre-fix checkpoint**: `pre-fix-r155s-20261123` / `a07de43`
- **Accepted candidate**: `fix-attempt/R155S/2` / `a77c38e`
- **Closeout tags**: `checkpoint-r155s-ok`, `post-fix-R155S`
- **Issue record**: local process archive `ISSUE-R155S.md` (not part of the
  active release tree)
- **Supersedes**: the R10 style-mask-only approximation in `native.rs`

## Background

Three user-visible regressions remained after R15.5:

1. The subtitle and summary windows did not reliably accompany another
   application's macOS full-screen Space.
2. The 48x48 native summary icon clipped its unread badge and outer decoration.
3. When the attached Python backend exited, an independently running Tauri host
   could retain its overlay and summary windows indefinitely.

The first implementation only added `NSWindowStyleMask::NonactivatingPanel` to an
ordinary `NSWindow`, used bare Tauri `show()`/`hide()` calls, and did not reapply
all full-screen collection behavior on active-Space changes.

## Source Evidence

| Item | Verified value |
| :--- | :--- |
| `yuxino/Mimi` | `bce7164fcea4591b912050c298c9b72bdbeb6f61` (`v1.7.6`), MIT |
| Mimi panel conversion | `to_panel`, non-activating panel, screen-saver level, full-screen auxiliary behavior |
| Mimi ordering | visible panels use `orderFrontRegardless`; the application never activates to present subtitles |
| `tauri-nspanel` | `2.1.1`, MIT OR Apache-2.0 |
| `tauri-plugin-single-instance` | `2.4.3`, MIT OR Apache-2.0 |

The implementation adapts Mimi directly instead of recreating panel behavior
with an ordinary `NSWindow`.

## Decision

1. Convert the unique subtitle overlay into a real `NSPanel` with
   `tauri-nspanel` 2.1.1. It lives for the host lifetime and is never
   dynamically destroyed.
2. Configure `NonactivatingPanel`, floating-panel behavior, hidden-on-deactivate
   disabled, screen-saver level `1000`, `CanJoinAllSpaces`,
   `FullScreenAuxiliary`, `Stationary`, `IgnoresCycle`, and
   `CanJoinAllApplications`.
3. Apply window level last. `setFloatingPanel` and style updates can reset the
   level to 3; screen-saver level must remain the final native presentation write.
4. Separate native configuration from display. Configuration never orders a
   window onscreen. Display calls `orderFrontRegardless`; hide calls `orderOut`.
   Active-Space changes reassert only currently visible panels, so a user-hidden
   overlay stays hidden.
5. Keep the transient summary window as a normal Tauri NSWindow. Runtime
   conversion to NSPanel, `destroy()`, and `to_window().close()` were all proven
   to trigger intermittent Tao foreign-exception aborts. Use `window.close()`
   only; no runtime class change and no force destroy.
6. Track summary enablement in Rust. An enable request records
   `requested_enabled=true` and queues reconciliation after the current IPC
   callback. The task rechecks the desired state before creating or showing the
   window, preventing stale true requests from resurrecting a disabled summary.
7. Do not manually destroy product windows during `RunEvent::ExitRequested`.
   The host exits normally and Tauri/AppKit closes the remaining windows. This
   avoids an Objective-C exception crossing the Tao event-loop boundary.
8. Register `tauri-plugin-single-instance` first. A secondary process asks the
   existing host to reveal its overlay and then exits without creating a window,
   Sidecar, or control server.
9. Python launches the attached overlay in the same session and supplies
   `HARRY_OWNER_PID` plus `HARRY_INSTANCE_TOKEN`. Tauri validates the fast health
   response and exits after three consecutive identity or liveness failures at
   500 ms intervals.
10. The loopback control service exposes `POST /shutdown`. It returns before
   Tauri exits and never closes a shared attached backend.
11. The native summary icon remains a 48x48 hit target. Summary-only CSS resets
    the inherited `body min-width: 320px`, moves the badge inside the icon,
    removes hover translation in native mode, and replaces clipped outer shadows
    with inside styling.
12. Add `overlay_native_diagnostics` and the loopback-only
    `GET /diagnostics` evidence endpoint. They report the actual class, level,
    collection behavior, visibility, frame and screen; they contain no content,
    credentials or metering data.

## Consequences

- The macOS shell now depends on two pinned, permissively licensed crates.
  `Cargo.lock` and `THIRD_PARTY_NOTICES.md` are updated.
- No private service, commercial SDK or new credential is introduced by this
  repair. The native smoke uses a local backend and placeholder environment.
  Real Qwen translation remains an optional runtime dependency; local core
  verification requires no repository secret change.
- Exactly one overlay host may exist for this application. The independent
  summary window remains a separate window but not a second subtitle overlay.
- Backend loss terminates the attached native host within roughly 1.5-3 seconds.
- Closing one launcher does not terminate a healthy shared Python backend.
- Full-screen acceptance covers the subtitle overlay in native macOS full-screen
  Spaces used by video, presentation and meeting applications. It does not
  claim Mission Control, login UI, screensaver or exclusive game presentation
  modes.
- The summary icon is not claimed to join another application's full-screen
  Space. A normal NSWindow cannot reliably do so, and retrying the transient
  NSPanel lifecycle violates the P1 no-abort requirement.
- Multi-display movement remains manual. The overlay is not moved to another
  screen when a full-screen window opens there.

## Attempt #1 Real-Machine Evidence

The smoke test `tests/smoke/native_overlay_lifecycle.py` launched an isolated
backend, both product panels, a duplicate host, and a separate Swift AppKit
full-screen fixture. Recorded results:

- overlap className `HarrySubtitlePanel`, summary className `HarrySummaryPanel`;
- both at window level `1000` with collection behavior `262481`;
- summary frame exactly `48x48`;
- both HARRY windows remained onscreen while the fixture used a native
  full-screen Space;
- duplicate host return code `0`;
- control `/shutdown` return code `0`;
- backend `SIGKILL` host return code `0`;
- no residual HARRY process or onscreen window after the test.

The same test later exposed an intermittent `Rust cannot catch foreign
exceptions` abort while destroying and recreating the dynamically converted
summary panel. A second attempt using `to_window().close()` still aborted.
The accepted attempt #2 therefore keeps the transient summary as a normal
NSWindow and passed five consecutive complete native smoke runs with no new
crash report and no residual process.

The user then confirmed the three acceptance checks on 2026-11-23: the subtitle
overlay remained above native full-screen applications, the 48x48 summary badge
was complete, and closing the terminal or backend removed the desktop overlay.
The final tags seal the acceptance-closeout commit; they do not move
`v2.0-p5-stable`.

## Must Remain Unchanged

- `MAX_OVERLAY_WINDOWS=1` and one active subtitle main session.
- One active upstream Qwen WebSocket.
- Native subtitle overlay has zero buttons and zero links.
- Zero Token, `usage`, cost or billing fields.
- Zero subtitle red highlighting, zero anomaly badges, zero sound effects and
  zero `AudioContext`/`<audio>` nodes.
- Do not create or reference `useSubtitleTail`.
- Do not modify `app/audio/**`, `app/engines/**`, `app/glossary/**`,
  `app/bypass/**`, `app/transcript_cleaner.py` or `app/session_manager.py`.

## Verification

- `tests/unit/test_repro_r155s_native_lifecycle.py`
- `tests/smoke/native_overlay_lifecycle.py`
- `cargo test --offline --manifest-path src-tauri/Cargo.toml`
- `cargo build --offline --features custom-protocol --manifest-path src-tauri/Cargo.toml`
- `npm --prefix frontend run typecheck && npm --prefix frontend test && npm --prefix frontend run build`
- `.venv-v2/bin/python -m pytest -q tests/`
- `.venv-v2/bin/python -m pytest -q tests/core_regression/`
- `.venv-v2/bin/python scripts/smoke_e2e.py --mock`
- `./scripts/watchdog_gate.sh tests/unit/test_repro_r155s_native_lifecycle.py pre-fix-r155s-20261123`
