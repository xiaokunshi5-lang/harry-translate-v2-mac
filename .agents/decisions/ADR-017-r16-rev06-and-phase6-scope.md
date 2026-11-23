# ADR-017: R16 REV-06 audit and adapted Phase 6 scope

- **Status**: Accepted; R16 sealed as `checkpoint-r16-ok`; user-facing export remains pending
- **Date**: 2026-11-23
- **Amended**: 2026-11-24
- **Decision owner**: user-authorized R16
- **Pre-fix baseline**: `pre-feature-p6-20261123` / `4b604357de9e`
- **Prior accepted source**: `checkpoint-r156-ok` / `4b60435`
- **Supersedes**: only stale scheduling references in the external R16 book;
  it does not supersede any accepted project ADR

## Scope correction

The Phase 5 handoff is the current source of truth. R16 therefore starts from
the accepted R15.6 commit `4b60435`, not from `v2.0-p5-stable` (`b1215d9`).
The old `pre-feature-p6-20261122` name is treated as a stale date in the
external execution book. The actual L1 tag is `pre-feature-p6-20261123`.

R15.6 dynamic subtitle lanes, R15.5S native panel lifecycle, single-instance
ownership and the current frontend paths remain in place. This round does not
retry dynamic summary `NSPanel` conversion and does not modify the audio
engine, glossary or bypass modules outside the explicit R16 allowance below.

The execution book's `frontend/src/lib/wsClient.ts` and
`frontend/src/components/SettingsView.tsx` paths do not exist. The actual
equivalent paths are `frontend/src/shared/wsClient.ts` and
`frontend/src/screens/SettingsView.tsx`. The execution book's `.venv` command
is also stale; this project uses `.venv-v2`.

Windows-specific testing and handoff language is removed because the project
contract is Apple Silicon macOS only.

## REV-06 source verification

Read-only source audit used the fixed upstream commit
`fc8d9a2195903cef780c8a295183b21a3561db12` from
`Samoyed123-yed/echo-translate` (MIT):

| Item | Verified value |
| :--- | :--- |
| `server.py` SHA-256 | `616ae6dd6cac79a910da1ab2ef1290c8b66c8c4940641ed41be50119be104404` |
| `public/app.js` SHA-256 | `97e20b01d1af5d978b96445440b8da64e1fa32ab75ee9a123d65dab5942fd53d` |
| `LICENSE` SHA-256 | `e3bfd055183780bed394e5b52e56b9f815d4dea144d8230b2c71d08c9039ea1f` |

The upstream browser function is named `saveSession()`, but it does not use a
body object with `sessionId` and `entries`. It posts the complete snapshot to
`POST /api/sessions/<id>`. The body contains `id`, `clientSavedAt`,
`startedAt`, `transcript`, `transcriptArchive`, `paras`, `parasArchive`,
`notes`, `notesArchive`, `questions`, and `qaArchive`.

The upstream backend reads `transcript`, `notes`, and `questions`; it compares
the existing `clientSavedAt` with the incoming value and rejects only when the
stored value is strictly greater. It serializes writes with one
`threading.Lock`, writes a sibling `.tmp` file and calls `os.replace()`. It
does not use `fcntl` and does not call `fsync()`.

This project adapts the useful monotonic timestamp and atomic-replace
semantics, while retaining the planned per-session lock, `fcntl.flock`,
encoding and `fsync()` hardening. The local canonical shape uses `entries` and
also accepts the upstream `transcript` name at the boundary. No `usage_stats`
or Token/cost field is retained.

## R16 file boundary

Allowed runtime/test changes:

- `app/audio/pipeline.py`
- `app/engines/qwen_realtime.py`
- `app/session_manager.py`
- `app/transcript_exporter.py` (new)
- `app/main.py`
- `frontend/src/shared/wsClient.ts`
- `frontend/src/screens/SettingsView.tsx`
- `frontend/src/shared/apiClient.ts` and `frontend/src/shared/types.ts` only
  for the export/session contract
- `tests/core_regression/test_dual_audio_dsp.py` (new)
- `tests/unit/test_exporter.py` (new)
- `tests/unit/test_package_smoke.py` (new)
- `scripts/build_sidecar.py` (new)
- `scripts/verify_package_smoke.py` (new)
- `scripts/package-app.sh`
- `scripts/macos/prepare-macos-release.sh` (new)
- `src-tauri/tauri.macos.conf.json`
- `src-tauri/src/sidecar.rs` and `src-tauri/src/lib.rs` only where required
  for packaged resource location and process cleanup
- `.gitignore`, notices, documentation and ADR files

The native window files and the existing R15.6 timeline implementation are not
part of the R16 change surface.

## AGC and turn commits

`AdaptiveFarFieldAGC` is adapted from the verified local V3 implementation with
the requested constants. It runs only on the physical microphone path; a
system/loopback frame is a byte-for-byte bypass. The engine computes RMS for
each 50ms frame and enqueues a commit command in the same bounded audio queue
as the frame. This preserves append-then-commit ordering and keeps the capture
callback non-blocking.

The automatic commit thresholds are `380.0` RMS, `2.4s` speech plus a
low-energy pause, and `3.8s` continuous speech. `qwen3.8*` session updates
remain free of top-level `turn_detection`. Only the optional non-3.8 branch may
retain the legacy `server_vad` payload.

## Export contract

The local API accepts `POST /api/session/save`, `GET /api/session/latest`, and
`GET /api/export?session_id=...&format=md|srt|json`. Files live under
`exports/session_<id>.json`; the directory and temporary/lock files remain
ignored. Export formats never include `usage_stats`, Token counts, cost or
billing fields. The frontend records finalized `main` events, patches by
`sent_id` plus `source_hash`, debounces saves for 2 seconds, and keeps an
in-memory recovery snapshot while the page is alive. Persistent browser storage
is intentionally not used because the existing frontend privacy contract
forbids it; the pagehide path sends a keepalive save to the local backend. The
recorder, atomic save and backend formatter are retained as reusable foundation
work. During R16 manual review the user confirmed that MD/SRT/JSON cannot yet be
downloaded or opened through the current user-facing flow. Per the user's
2026-11-24 decision, R16 does not continue fixing that flow; it is recorded as
a pending capability, not as delivered functionality. A future task may open
it only after new authorization and end-to-end verification.

## Packaging contract

The sidecar build is a PyInstaller one-file artifact for the active Mac
architecture, with a bundled resource root for the selected glossary and
provider catalog. The package check verifies:

- only `glossary_bank/00_base_emergency.json` and
  `glossary_bank/01_curated_everyday.json` are included;
- the combined glossary count is at most 250;
- `externalBin` has the target-triple suffix expected by Tauri;
- a sidecar can bind `--port 0`, print a `sidecar_ready` line, answer
  `/api/health`, and exit without a child process;
- `Info.plist`, entitlements and `minimumSystemVersion` remain present.

An actual `.dmg` build is recorded as PASS only if the local host has the
public PyInstaller and Tauri build tools and the command completes. Otherwise
the missing build tool is an explicit `尚未验证` item; the structure and
sidecar lifecycle checks may still pass.

## Must remain unchanged

- `MAX_OVERLAY_WINDOWS=1`, one main subtitle session and one active upstream
  Qwen WebSocket.
- `all_connected == 0` means zero upstream audio.
- 50ms / 800 sample / 1600 byte slicing and bounded Drop-Oldest queues.
- `qwen3.8*` has no top-level `turn_detection`.
- 250-term, 4096 B default / 6144 B hard-limit glossary budget.
- Zero Token, `usage`, cost, billing, red subtitle highlighting, warning
  badges, UI audio, or `<audio>`/`AudioContext`.
- No `useSubtitleTail` creation or reference.
- No dynamic summary `NSPanel` conversion, `destroy()` or
  `to_window().close()`.
- Never read, print, copy or commit `.env` or local secrets.

## Verification

- Baseline: `./scripts/capture_baseline.sh checkpoint-r156-ok`
- L1 rollback check: `./scripts/rollback.sh pre-feature-p6-20261123 --dry-run`
- Target tests: `tests/core_regression/test_dual_audio_dsp.py`,
  `tests/unit/test_exporter.py`, `tests/unit/test_package_smoke.py`
- Full evidence: `.venv-v2/bin/python -m pytest -q tests/` plus core, smoke,
  Ruff, compileall, frontend, Rust, Markdown and watchdog gates
- R16 final stop: sealed at `checkpoint-r16-ok`. R17 subsequently completed
  under separate user authorization; the pending user-facing export remains
  outside the delivered scope.
