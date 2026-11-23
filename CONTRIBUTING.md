# Contributing

## Before Changing Code

Read:

1. `AGENTS.md`
2. `.agents/PROJECT_CONTRACT.md`
3. the relevant ADRs in `.agents/decisions/`
4. `TECHNICAL_ARCHITECTURE.md`

Historical archives are intentionally kept outside this repository. Do not
recreate them in the active tree for ordinary development.

## Invariants

- Keep `MAX_OVERLAY_WINDOWS=1`.
- Keep one main session and one active upstream Qwen WebSocket.
- Do not add Token, `usage`, billing or cost fields.
- Do not add subtitle highlighting, badges or UI sound.
- Do not restore `useSubtitleTail`.
- Do not send top-level `turn_detection` for `qwen3.8*`.
- Do not commit `.env`, real keys, private workspace hostnames, local absolute
  paths, user settings, exports or logs.

## Development

```bash
UV_PROJECT_ENVIRONMENT=.venv-v2 uv sync --frozen --python 3.12
cd frontend && npm ci && npm run typecheck && npm test && npm run build && cd ..
cargo test --offline --manifest-path src-tauri/Cargo.toml
.venv-v2/bin/python -m pytest -q tests/
.venv-v2/bin/python scripts/smoke_e2e.py --mock
```

Run the focused tests for any area you change. Do not remove or weaken an
existing assertion to make a change pass.

## Pull Requests

Include:

- the user-visible or engineering problem;
- the chosen root cause and solution;
- files and invariants affected;
- exact verification commands and results;
- known limitations or unresolved risks;
- confirmation that no secret or local-only artifact is included.

## Licensing

Project-owned code is MIT licensed. New dependencies and reused source must be
publicly available, locally deployable and license-compatible. Update
`THIRD_PARTY_NOTICES.md` when adding a dependency or adapted source.
