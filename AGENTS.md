# Project Agent Entry

Before changing code, read:

1. `.agents/PROJECT_CONTRACT.md`
2. `.agents/decisions/ADR-008-r095-single-window-wire-budget.md`
3. `.agents/decisions/ADR-009-r10-r12-phase4-closeout.md`
4. `docs/RELEASE_REPORT_v2.0.md`
5. the current handoff or fix record only when it is supplied in the task
   context

Phase 5 (R13-R15), R15.5, R15.5S and R15.6 were confirmed passed by the user
on 2026-11-23. R16 is explicitly authorized and its automated candidate has
passed the target, full-regression and package checks. Manual review confirmed
that the MD/SRT/JSON user-facing download/open path is not available yet; the
user chose to defer that work while retaining the backend foundation. R16 is
sealed as `checkpoint-r16-ok` (`7583c4d`). R17 then completed the 1000-card,
long-session, WebSocket, glossary, compliance and package gates; the cleaner
paragraph-state repair is recorded in `ADR-018`. Do not describe user-facing
export as delivered or move historical tags.

R18-01 through R18-04 were manually accepted by the user on 2026-11-24.
The code closeout is `post-fix-R18` at candidate `47c3480`; the accepted
checkpoints are `checkpoint-r18-01-ok`, `checkpoint-r18-02-ok`,
`checkpoint-r18-03-ok`, and `checkpoint-r18-04-ok`. The public R18 release
uses `v2.0-r18-ready`; do not move `v2.0-mac-ready`. Read `ADR-019` through
`ADR-022` before touching startup gating, the realtime switch, native settings
or window lifecycle, controlled exit, or package signing.

The current R18 DMG is ad-hoc signed and not notarized. A quarantined download
may still show the macOS "damaged" warning. Keep the hash and `hdiutil verify`
checks, limit quarantine removal to the single HARRY application, and never
recommend disabling system-wide Gatekeeper. Developer ID signing,
notarization, and clean-Mac no-prompt launch remain unverified until a
credential-backed build is executed and manually accepted.

For R16-era behavior, read
`.agents/decisions/ADR-017-r16-rev06-and-phase6-scope.md`. Historical L1
baselines and stage handoffs may exist only in the user's local process
archive; do not recreate them in the active repository unless the user asks.

The post-acceptance R15.5/R15.5S repair is `fix-attempt/R155S/2` (`a77c38e`).
Automated tests, the independent watchdog and user acceptance passed on
2026-11-23. The accepted closeout tags are `checkpoint-r155s-ok` and
`post-fix-R155S`; `v2.0-p5-stable` remains at `b1215d9`. Read
`.agents/decisions/ADR-015-r155s-panel-lifecycle-and-summary-clipping.md`
before touching native window lifecycle. The historical fix log may be kept
in the user's local process archive.
Do not retry dynamic summary `NSPanel` conversion or `destroy()` /
`to_window().close()` on the transient summary window.

Temporary reuse constraint: all new code, configuration and dependencies must
prefer publicly available, locally deployable open-source solutions. Do not add
private services, commercial-license requirements or unpublished credentials.
Keep configuration portable so another machine can clone, deploy and verify
the core behavior without source changes. Real Qwen/DashScope connectivity is
an optional operator-supplied runtime integration; local Mock and engineering
verification must not require a repository secret. See
`.agents/decisions/ADR-016-open-source-local-reuse.md`.

Historical versions, old QA data, stage reports and handoff records are kept
outside this repository in the user's local archive. They are not active
runtime, build, test, or context inputs. Skip them unless the user explicitly
asks for historical provenance, recovery, or audit.

The active repository must contain one final release tree. Do not copy local
archives back into it, and do not add instance-specific workspace hostnames,
personal absolute paths or private process logs to tracked files.

Never read, print, copy, or commit `.env` contents or workspace secrets.
