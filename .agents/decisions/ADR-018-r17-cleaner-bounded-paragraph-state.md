# ADR-018: R17 bounded paragraph state for long-session cleanup

- **Status**: Accepted for R17 release-gate repair
- **Date**: 2026-11-24
- **Decision owner**: user-authorized R17
- **Baseline**: `checkpoint-r16-ok` / `7583c4d`
- **Scope**: `SingleSessionCleaner` paragraph state only

## Context

`tests/bench/test_soak_benchmark.py` was added as the R17 AC-19 release
soak. Before this repair it measured a tail/head per-sentence latency ratio of
approximately `11.15x` over 1000 final sentences and `20.49x` over 2000
sentences. The same run retained approximately `189 KiB` of additional Python
allocation between sentence 1000 and sentence 2000.

The root cause was bounded, not a timing-noise issue:

- `SingleSessionCleaner` retained every sentence for the whole session;
- every final sentence called `assign_para()` over that complete history;
- the stored list therefore grew without limit and the hotter path became
  increasingly expensive.

## Decision

Keep the R13 cleaning and paragraph semantics, but replace the full-session
sentence list inside `SingleSessionCleaner` with:

- the current unsealed paragraph's bounded sentence window;
- a monotonic paragraph counter.

The public `assign_para()`, `clean_speech()`, `mend_fragment()` functions and
all 6/12 sentence, 520/1000 character, false-period and merge rules remain
unchanged. When the local window produces more than one paragraph, only the
last unsealed group is retained and the paragraph counter advances by the
number of completed groups.

## Required Semantic Proof

The release soak must compare the bounded implementation against a test-local
reference implementation that preserves the former full-history algorithm.
The comparison must cover:

- ordinary six-sentence soft boundaries;
- twelve-sentence hard boundaries;
- a merge immediately after a hard boundary;
- a merge after a sealed boundary followed by a non-merge sentence;
- character-limit boundaries;
- CJK text, protected terms and reset.

The comparison uses the complete tuple:

```text
(source, target, merged_with_prev_id, paragraph_id)
```

The R17 frontend soak additionally appends 1003 finalized cards, applies a
valid `transcript.patch`, adds a draft and verifies that the final history is
still capped at 1000, the first three entries are evicted, and the draft is
rendered separately.

## Consequences

- No full-session sentence collection remains in the cleaner.
- Paragraph IDs remain monotonic within a session and reset to `p1`.
- `SessionManager`, WebSocket event shapes, subtitle cards, glossary and
  persistence do not change.
- The repair does not open or modify the pending MD/SRT/JSON user-facing
  export flow.
- R16 history and its tag are not moved.

## Verification

```bash
.venv-v2/bin/python -m pytest -q -s tests/bench/test_soak_benchmark.py
.venv-v2/bin/python -m pytest -q tests/core_regression/test_cleaner_glide.py
npm --prefix frontend test
```

Release acceptance requires:

- semantic differential equality with the former full-history algorithm;
- tail/head latency ratio `<=1.2`;
- bounded allocation between sentence 1000 and 2000;
- 1000 finalized frontend cards plus at most one separate draft;
- full Python, frontend, Rust, watchdog and package gates.
