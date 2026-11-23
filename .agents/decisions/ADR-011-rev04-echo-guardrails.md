# ADR-011: REV-04 EchoTranslate Silent Correction Guardrails

- **Status**: Accepted
- **Date**: 2026-11-23
- **Decision owner**: user-authorized R14 implementation
- **Scope**: REV-04 source audit and R14 bypass correction boundary
- **Supersedes**: none

## Context

R14 must adapt the silent correction scheduling and hallucination guardrails
from `Samoyed123-yed/echo-translate` without presenting local extensions as
upstream behavior. The Phase 5 execution book refers to
`.agents/decisions/ADR-009-rev04-echo-guardrails.md`, but this repository
already uses `ADR-009` for the accepted Phase 4 closeout. This ADR therefore
uses the next free number and does not modify or supersede `ADR-008`,
`ADR-009`, or `ADR-010`.

## Source Evidence

| Item | Verified value |
| :--- | :--- |
| Repository | `https://github.com/Samoyed123-yed/echo-translate` |
| Fixed commit | `fc8d9a2195903cef780c8a295183b21a3561db12` |
| `public/app.js` SHA-256 | `97e20b01d1af5d978b96445440b8da64e1fa32ab75ee9a123d65dab5942fd53d` |
| `server.py` SHA-256 | `616ae6dd6cac79a910da1ab2ef1290c8b66c8c4940641ed41be50119be104404` |
| `LICENSE` SHA-256 | `e3bfd055183780bed394e5b52e56b9f815d4dea144d8230b2c71d08c9039ea1f` |
| License | MIT |
| Copyright notice | `Copyright (c) 2026 Echo Translate contributors` |

The read-only audit used raw files from the fixed commit. The execution book's
line references were stale: `untranslatedWords()` is at
`public/app.js:347-352`, not `L874-885`; `plausible_correction()` is at
`server.py:360-380`, not `L256-320`.

## REV-04: `untranslatedWords()`

The verified upstream implementation is:

```javascript
function untranslatedWords(zh){
  let text=String(zh||'');
  for(const [term] of buildGlossary())text=text.split(new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'gi')).join(' ');
  text=text.replace(/\b[A-Z][A-Za-z]*\b/g,' ');
  return (text.match(/[a-z][a-z'-]{2,}/g)||[]);
}
```

Verified semantics:

1. Glossary terms are removed case-insensitively before counting.
2. Capitalized ASCII words are removed, so names and acronyms are excluded.
3. The remaining match is exactly `/[a-z][a-z'-]{2,}/g`: lowercase ASCII,
   at least three characters, and duplicates count separately.
4. The function returns matches, not a count. Its only upstream call site at
   `public/app.js:480-483` applies `left.length >= 2`.
5. Upstream only recognizes English residue in a Chinese target. It has no
   `target_lang` parameter and no generic French, Spanish, or English target
   detector.
6. Upstream sets `suspect`, `reviewed`, and a 1500ms next-analysis time. It
   does not expose a background `urgent` task.

## REV-04: `plausible_correction()`

The verified upstream implementation is:

```python
def _words(text):
    return re.findall(r"[\w'’-]+", text.lower())


def plausible_correction(original: str, corrected: str) -> bool:
    if not corrected or corrected == original:
        return False
    a, b = _words(original), _words(corrected)
    if not a or not b or len(b) > len(a) + 3 or len(b) < max(1, len(a) * 0.35):
        return False
    if len(corrected) > len(original) * 1.35 + 12:
        return False
    opcodes = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    new_words = sum(j2 - j1 for tag, _i1, _i2, j1, j2 in opcodes if tag in ("replace", "insert"))
    return new_words <= max(3, round(len(b) * 0.3))
```

Verified semantics:

1. Empty or byte-for-byte unchanged output is rejected.
2. Corrected token count may exceed the original by at most 3.
3. Corrected token count must retain at least
   `max(1, len(original_tokens) * 0.35)`.
4. Corrected character count must not exceed
   `len(original) * 1.35 + 12`.
5. The final rule counts replacement and insertion target tokens using
   `difflib.SequenceMatcher(..., autojunk=False)`. It is not Levenshtein word
   distance.
6. The insertion cap is exactly
   `new_words <= max(3, round(len(corrected_tokens) * 0.3))`.
7. Upstream accepts only `(original, corrected)`. `protected_terms` is a local
   R14 extension.

## Local R14 Extensions

The following behaviors are intentionally local and must not be described as
upstream EchoTranslate behavior:

1. `urgent=True` is an internal queue-priority flag. A `>=2` untranslated-word
   result only moves the task to the next 500ms tick. It is never serialized or
   rendered as a warning, red style, underline, badge, or error state.
2. The exact Chinese-target English-residue detector is used for `zh`. Other
   supported target languages use a conservative script-mismatch detector that
   returns source-script runs; this is a local extension, not verified upstream
   logic.
3. P0/P1 glossary terms are protected during model correction. If a protected
   term exists in the original field and disappears from the corrected field,
   the whole patch is rejected.
4. A corrected field must retain a character or token overlap with its original
   field. A model rewrite with no lexical anchor is rejected.
5. The execution book's locked-environment `httpx` assumption is stale: this
   repository does not lock or install `httpx`. R14 uses Python's standard
   library HTTPS client in a worker thread with `certifi.where()` and a 4.0s
   hard timeout. This avoids adding an unlocked runtime dependency.

## R14 Architecture Decision

1. The main subtitle path performs only a synchronous `put_nowait()` into an
   `asyncio.Queue(maxsize=32)`. A full queue removes the oldest waiting task
   before inserting the newest task. It never waits for correction work.
2. One worker owns correction processing. `correction_enabled=false` creates
   no active correction task, performs no enqueue, no model call, and no
   network request.
3. Regular tasks wait `correction_gap_ms=1500`. The worker wakes every 500ms,
   detects untranslated residue, and processes an urgent task first on the next
   tick. At most one correction is processed per tick.
4. Modes are `llm` and `rules`. Missing or invalid API key safely falls back to
   rules. Rules mode uses the existing R09.5 glossary engine and performs zero
   network requests.
5. LLM correction reads the configured `qwen-turbo` or `qwen-flash` model from
   the local config. It does not parse, retain, log, export, or publish token or
   usage metadata.
6. The patch payload is limited to:

   ```json
   {
     "type": "transcript.patch",
     "window_id": "main",
     "sent_id": 42,
     "source_hash": "a1b2c3d4e5f6",
     "patched_source": "corrected source",
     "patched_target": "corrected target",
     "new_source_hash": "123456789abc"
   }
   ```

7. A card is patched only when it is finalized and both `sent_id` and
   `source_hash` match. Draft cards are never touched. Missing or stale matches
   are dropped silently. Each card accepts at most one patch version until a
   later final/merged event replaces its identity, which prevents a second
   target-only patch from reusing an unchanged source hash.
8. Language changes and session resets clear waiting tasks. A captured session
   generation prevents an in-flight result from patching a newly reset session.
9. The frontend replacement uses a 150ms opacity transition and retains the
   existing card height as a minimum. It does not alter user font sizes,
   emphasis, line height, colors, or scroll-follow state. Finalized pipeline
   state is hard-capped at 1000 cards so patch lookup remains bounded. The
   temporary height guard is released when a height-affecting display setting
   changes.
10. `MAX_OVERLAY_WINDOWS=1`, one active upstream session, zero automatic red
    highlighting, zero warning badges, zero UI sound, and zero `useSubtitleTail`
    remain unchanged.

## Consequences

- Queue saturation drops old correction opportunities rather than delaying
  live subtitles.
- A timed-out or malformed model response preserves the original subtitle and
  does not retry automatically.
- LLM mode may consume a normal DashScope text-model request, but no runtime
  token, cost, or usage field is added to the product.
- Rules mode remains deterministic and available without an API key.
- The patch protocol can be reused by later summary work without creating a
  second subtitle or upstream session.
- Disabling correction cancels the worker, clears waiting tasks and blocks all
  later publication. A standard-library HTTPS request already in flight may
  finish in its background thread within the 4.0s hard timeout, but it cannot
  publish a patch after disablement.

## Verification

- `tests/core_regression/test_bypass_isolation.py`
- `frontend/src/lib/subtitles/subtitlePipeline.test.ts`
- `frontend/src/windows/overlay/timeline/Timeline.test.tsx`
- `tests/core_regression/test_cleaner_glide.py`
- `.venv-v2/bin/python -m pytest -q tests/`
- `npm --prefix frontend run typecheck`
- `npm --prefix frontend run test`
- `npm --prefix frontend run build`
- `./scripts/watchdog_gate.sh tests/core_regression/test_bypass_isolation.py pre-feature-p5-20261122`
