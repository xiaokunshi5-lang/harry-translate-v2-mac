# ADR-016: Temporary open-source and local-reuse constraint

- **Status**: Active temporary constraint
- **Date**: 2026-11-23
- **Decision owner**: user instruction

## Decision

All subsequent code, configuration and dependencies should prefer publicly
available, locally deployable open-source solutions. Do not add private
services, commercial-license requirements or unpublished credentials to the
core engineering path.

Configuration must remain portable:

- no absolute personal path is required;
- project root, runtime port and control URL are supplied through environment
  variables or local runtime files;
- business configuration remains UTF-8 JSON;
- secrets remain in local `.env` and are never committed or printed.

Another machine must be able to clone the repository, install public
dependencies, and verify the core engineering path without source changes or a
repository secret. Mock tests, frontend build, Rust tests, Python tests and
native window lifecycle smoke satisfy that requirement.

## Existing Cloud Integration

The current Qwen realtime adapter remains an existing optional runtime
integration. A real DashScope/Qwen key is supplied by the operator only when
live cloud translation is desired. It is not required for local verification,
and this ADR does not authorize adding another proprietary runtime.

## Current Compliance

- `tauri-nspanel 2.1.1`: MIT OR Apache-2.0.
- `tauri-plugin-single-instance 2.4.3`: MIT OR Apache-2.0.
- No new private SDK, paid runtime, hosted credential or remote control plane
  was introduced by R15.5S.
- `THIRD_PARTY_NOTICES.md` records current native/frontend dependencies.

## Consequences

- New integrations must first pass the Ponytail/internal implementation and
  license checks before adding a dependency.
- If a future requirement needs a proprietary service, it must be isolated as
  optional and explicitly approved; it cannot become the only way to build or
  verify the core project.
- Deployment documentation must name the no-key local checks and clearly
  separate optional live cloud behavior from engineering acceptance.
