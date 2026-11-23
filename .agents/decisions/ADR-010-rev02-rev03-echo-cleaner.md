# ADR-010: REV-02 / REV-03 EchoTranslate Cleaner Source Verification

- **Status**: Accepted
- **Date**: 2026-11-23
- **Decision owner**: user-authorized R13 implementation
- **Scope**: REV-02 and REV-03 source audit; R13 cleaner implementation boundary
- **Supersedes**: none

## Context

R13 must adapt the synchronous transcript cleanup pipeline from
`Samoyed123-yed/echo-translate` without inventing constants from memory.
The Phase 5 execution book refers to
`.agents/decisions/ADR-008-rev02-echo-cleaner.md`, but this repository already
uses `ADR-008` for the accepted R09.5 single-window and wire-budget decision.
This ADR therefore uses the next free number while preserving the requested
REV-02 / REV-03 mapping. It does not modify or supersede `ADR-008` or `ADR-009`.

## Source Evidence

| Item | Verified value |
| :--- | :--- |
| Repository | `https://github.com/Samoyed123-yed/echo-translate` |
| Fixed commit | `fc8d9a2195903cef780c8a295183b21a3561db12` |
| Commit date | `2026-11-17T08:52:37-05:00` |
| Audited file | `public/app.js`, 1409 lines |
| Audited file SHA-256 | `97e20b01d1af5d978b96445440b8da64e1fa32ab75ee9a123d65dab5942fd53d` |
| License | MIT |
| LICENSE SHA-256 | `e3bfd055183780bed394e5b52e56b9f815d4dea144d8230b2c71d08c9039ea1f` |
| Copyright notice | `Copyright (c) 2026 Echo Translate contributors` |

The audit was read-only and covered `public/app.js:138-215` for
`cleanSpeech()`, `public/app.js:224-264` for paragraph assignment, and
`public/app.js:353-365` for `mendFragment()`.

## REV-02: `cleanSpeech()`

### 1. Filler-word filter

The upstream implementation uses this exact case-insensitive regular
expression:

```regex
\b(?:u+h+m*|u+m+|e+r+m+|h+m+|m{2,})\b[,.]?\s*
```

It removes the matched filler and adjacent comma, period, or whitespace.
It is not a general grammatical or sentiment filter.

### 2. Repeated phrase compression

The Phase 5 execution book calls this a "4->1 regex", but the verified source
does not use a regular expression. It iterates phrase lengths from four tokens
down to one and at each length compares adjacent token windows. For each exact
normalized duplicate, it removes the second copy.

The verified behavior includes:

- Normalization lowercases tokens, strips outer punctuation, and canonicalizes
  curly apostrophes.
- A duplicate after terminal punctuation is protected unless the terminal
  punctuation is a comma.
- One-token repeats are protected by
  `{that, had, is, very, really, so, no, bye, ha}` and by a leading-digit guard.
- Four-token repeats are also eligible for compression; this is not a
  four-to-one-only rule.
- Capitalization of the retained copy is repaired when the retained copy starts
  with a capital letter and the removed copy does not.

### 3. Cut-off fragments and near-identical slips

The source uses two conservative adjacent-word rules:

- A fragment is removed when the first token ends in `-`, has length greater
  than one, and the next token starts with that fragment after removing the
  hyphen.
- A prefix fragment is removed when the first normalized token is at least
  three characters, the second token is at least two characters longer, the
  second starts with the first, and the first is not in `COMMON_WORDS`.
- A near-slip is removed when both normalized tokens are at least four
  characters, neither is in `COMMON_WORDS`, neither is an inflection of the
  other, and Levenshtein distance is at most one, or at most two when either
  token is at least seven characters.
- Tokens following sentence-ending punctuation are protected from this rule.
- Capitalization is transferred to the retained word when appropriate.

This is a near-word and cut-off heuristic, not a phonetic dictionary or a
standalone "near-homophone truncation regex".

### 4. Glossary-term fuzzy correction

The upstream `fixTerms()` stage fuzzy-matches against its own course glossary.
That stage is intentionally not copied into `clean_speech()` because this
project already has a canonical three-table glossary engine with interval
locks and Qwen wire-budget priority. Running a second fuzzy glossary stage from
the cleaner would violate the R09.5 boundary and could alter protected terms.

## REV-03: `mendFragment()`

### 1. Dangling-word set

The exact upstream set is:

```text
of the a an to and or but than between into whose whether because our your their its
```

That is 18 single-token entries. There is no abbreviation set, punctuation
registry, conjunction subset, or preposition subset beyond this flat set.

### 2. False-period removal boundary

The upstream rule only matches a final unquoted word followed directly by a
period:

```regex
([A-Za-z']+)\.$
```

If the captured word is in the dangling set, the final period is removed.
Because quoted endings, multi-character punctuation, and other terminal marks
do not match, they are preserved. Abbreviations such as `Mr.` are also
preserved because `Mr` is not in the set. The upstream source does not attempt
to distinguish abbreviations from real sentence endings.

### 3. Next-fragment joining rule

The next fragment is lowercased only when all of these are true:

- A previous row exists and has English text.
- The previous English does not end in `.`, `?`, `!`, `:`, or `;`, optionally
  followed by a quote or closing bracket.
- The new fragment starts with an ASCII capital followed by a lowercase letter.
- The new fragment does not start with the standalone pronoun `I`.
- The new fragment does not start with a capitalized glossary term.

R13 adds the execution-book `300ms` gap as an adaptation: a known gap greater
than 300ms blocks the merge. `gap_ms=None` keeps the upstream punctuation-only
behavior because no timing signal is available.

## `assignPara()` Boundary

The verified source uses:

- `PARA_MAX_ROWS = 6`
- `PARA_MAX_CHARS = 520`
- `PARA_HARD_ROWS = 12`
- `PARA_HARD_CHARS = 1000`

Soft limits only break a paragraph after a complete sentence ending. Hard
limits are a missing-punctuation safety valve. The sentence-ending check also
rejects a final word in the dangling set. R13 preserves those semantics in a
pure grouping function rather than copying UI state into the Python cleaner.

## Decision

1. Implement `app/transcript_cleaner.py` as a pure, synchronous, precompiled
   Python adaptation of the verified rules.
2. Preserve the verified filler regex, repeat guards, cut-off/slip guards,
   dangling-word set, punctuation conditions, and paragraph limits.
3. Do not add phonetic transformations, an abbreviation dictionary, model
   calls, disk I/O, network I/O, logging side effects, token accounting, or UI
   highlighting to the cleaner.
4. Do not duplicate glossary-term fuzzy correction in the cleaner. The existing
   glossary engine remains the only term-correction authority.
5. Keep the cleaner independent of `window_id`; the session manager owns
   `sent_id`, `source_hash`, final-event publication, and session reset.
6. Add the Echo Translate MIT notice to `THIRD_PARTY_NOTICES.md` before release.

## Consequences

- R13 can reproduce the verified upstream behavior without claiming unsupported
  phonetic or abbreviation intelligence.
- Repeated phrases of one to four tokens can be compressed, subject to the
  verified exceptions.
- The 300ms timing gate is explicitly a local adaptation, not an upstream
  `mendFragment()` behavior.
- The next available ADR number is used to avoid two different accepted
  decisions sharing `ADR-008`.

## Verification

- Source commit and file hashes recorded above.
- R13 tests must cover filler removal, protected repeats, one-to-four-token
  repeats, fragment/slip correction, all 18 dangling words, quoted/abbreviation
  non-matches, 300ms gating, and 6/520 plus 12/1000 paragraph boundaries.
- `THIRD_PARTY_NOTICES.md` must contain the upstream MIT copyright and license
  before R13 is declared green.
