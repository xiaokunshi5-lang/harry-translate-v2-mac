# ADR-013: R15 Default-Off Glass Summary Boundary

- **Status**: Accepted; Phase 5 closeout
- **Date**: 2026-11-23
- **Decision owner**: user-authorized R15 implementation
- **Scope**: default-off summary worker, importance levels, API and native UI
- **Supersedes**: none

## Source Evidence

R15 reuses the summary narration-cleaning rules from the same MIT source already
audited in ADR-011.

| Item | Verified value |
| :--- | :--- |
| Repository | `https://github.com/Samoyed123-yed/echo-translate` |
| Fixed commit | `fc8d9a2195903cef780c8a295183b21a3561db12` |
| `server.py` SHA-256 | `616ae6dd6cac79a910da1ab2ef1290c8b66c8c4940641ed41be50119be104404` |
| `LICENSE` SHA-256 | `e3bfd055183780bed394e5b52e56b9f815d4dea144d8230b2c71d08c9039ea1f` |
| License | MIT |
| Copyright notice | `Copyright (c) 2026 Echo Translate contributors` |

The upstream source uses `importance: 1-5`, where 5 is a core concept and 4 is
important. It strips only pure reporting phrases; it does not rewrite a statement
that still contains lecture narration. The exact upstream regexes are:

```python
_EN_NARRATION = re.compile(
    r"^\s*(?:in (?:this|the) (?:lecture|class|session),?\s*)?(?:the\s+)?"
    r"(?:lecturer|professor|instructor|teacher|speaker)"
    r"\s+(?:\w+(?:ly)?\s+){0,2}?"
    r"(?:explains?|explained|says?|said|notes?|noted|points? out|pointed out|"
    r"emphasi[sz]es|emphasi[sz]ed|stresses|stressed|clarifies|clarified|"
    r"mentions?|mentioned|highlights?|highlighted|argues?|argued|shows?|"
    r"showed|reminds? (?:students|us|the class))\s*(?:that\s+|:\s*)",
    re.I,
)
_ZH_NARRATION = re.compile(
    r"^\s*(?:课上|本节课|这节课)?[，,]?\s*"
    r"(?:老师|讲师|教授|讲者|主讲人)[^，。：:；]{0,6}?"
    r"(?:指出|强调|提到|说明|解释|讲到|认为|表示|提醒|说)(?:了|道)?"
    r"\s*[，,：:]\s*"
)
```

The execution book's `httpx` instruction is stale. `httpx` is not installed or
locked in this repository. R15 therefore reuses the R14 standard-library HTTPS
client with `certifi.where()` and a hard timeout.

## Decision

1. `summary_enabled=false` is the non-overridable factory default. It creates no
   summary task, performs no enqueue, no model request and no summary DOM or
   Tauri window.
2. When enabled, one bounded `asyncio.Queue(maxsize=64)` accepts finalized
   `main` subtitle copies. A full queue drops the oldest waiting item. The
   accumulated summary buffer is also capped at 64 utterances and 16,000
   characters so gated or manual-refresh periods cannot grow without bound. The
   main subtitle path never awaits summary work.
3. Summary extraction is gated by both `summary_min_words=120` and
   `summary_min_interval_sec=30`. The interval clock starts when summary is
   enabled. Manual refresh bypasses the gate.
4. `summary_mode` is `bailian` or `rules`. `bailian` uses `qwen-turbo` or
   `qwen-flash`; a missing key, an 8-second timeout or invalid JSON falls back
   to deterministic rules with `engine_used="rules_fallback"`.
5. Items have only `level`, `text`, and `category`. Levels 4-5 are pinned;
   levels 1-3 are archived. Outputs contain no Token, usage, cost or billing
   fields.
6. The native subtitle overlay remains zero buttons and zero links. The R15
   edge icon lives in a separate `summary` WebviewWindow; browser fallback shows
   the same state machine in-page.
7. `GET /api/summary` never wakes a model. When disabled it returns an empty
   disabled snapshot. `POST /api/summary/refresh` returns `409` while disabled.
8. Disabling summary synchronously blocks new ingress, clears buffered finalized
   text, cancels the consumer and invalidates any in-flight result before it can
   update state or broadcast. Session reset or language change increments the
   worker invalidation epoch, clears completed pinned/archived items, and
   publishes an empty summary snapshot. Summary publication and reset/disable
   share the main broadcast lock, so an old response cannot arrive after reset
   or disable completes.
9. `MAX_OVERLAY_WINDOWS=1`, one active upstream session, zero automatic red
   subtitles, zero UI audio and no `useSubtitleTail` remain unchanged.
10. Summary window coordinates live in ignored local
    `.agents/summary_geometry.json`, not in Python's business configuration
    file, avoiding concurrent Rust/Python writes.
11. If the backend cannot confirm `summary_enabled=false`, the summary panel
    remains visible with an error instead of hiding while the worker continues.

## Consequences

- Summary history is captured only while enabled; disabled operation is a
  physically inert path rather than a UI-only toggle.
- Rule fallback remains useful without network access and keeps the main
  translation stream isolated from summary model latency.
- The summary window may consume a normal DashScope text-model request when
  explicitly enabled, but the product never exposes or stores metering fields.
- The Web fallback can display the icon inside `/hud`, while the native Tauri
  subtitle overlay remains visually pure.

## Verification

- `tests/core_regression/test_summary_isolation.py`
- `tests/smoke/test_summary_widget_ui.py`
- `frontend/src/components/summary/SummaryWidget.test.tsx`
- `.venv-v2/bin/python -m pytest -q tests/`
- `npm --prefix frontend run typecheck`
- `npm --prefix frontend run test`
- `npm --prefix frontend run build`
- `cargo test --offline --manifest-path src-tauri/Cargo.toml`
- `./scripts/watchdog_gate.sh tests/core_regression/test_summary_isolation.py pre-feature-p5-20261122`
