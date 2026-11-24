# ADR-022: R18 DMG signing and notarization dual track

- **Status**: Accepted; implementation and ad-hoc release gate passed, Developer ID clean-Mac acceptance pending external credentials
- **Date**: 2026-11-24
- **Decision owner**: user-authorized R18-04 execution
- **Baseline**: `checkpoint-r18-03-candidate` / `f1cdc38`
- **Closeout checkpoint**: `checkpoint-r18-04-ok` / `47c3480`
- **Scope**: Developer ID signing, optional notarization, honest package
  classification, and quarantine recovery documentation
- **Related**: `ADR-016`, `docs/RELEASE_REPORT_v2.0.md`

## Background

The released arm64 DMG is an ad-hoc build. A browser-downloaded DMG carries
`com.apple.quarantine`, and modern Apple Silicon Gatekeeper can describe an
unnotarized ad-hoc app as "damaged" after it is copied to Applications. The
artifact hash and `hdiutil verify` can both pass, so the message is a signing
and quarantine trust issue rather than evidence of corrupt bytes.

The project must remain buildable without a paid Apple Developer certificate,
while allowing an operator with valid credentials to produce a signed and
notarized release. No certificate, private key, password, or notary credential
may enter the repository or become a requirement for local engineering tests.

## Decision

1. `scripts/package-app.sh` keeps the default no-key path:
   it builds the complete arm64 app/DMG and reports `adhoc_local` without
   attempting keychain, private-key, or Apple service access.
2. When `APPLE_SIGNING_IDENTITY` is set and available in the current
   codesigning keychain, the inner PyInstaller sidecar is signed first with
   Hardened Runtime, secure timestamp, and `Entitlements.plist`, then verified.
3. Tauri receives `APPLE_SIGNING_IDENTITY` and signs the outer app. The package
   script verifies the app bundle's strict/deep signature before notarization.
4. Notarization is optional and is enabled only when the signing identity is
   present plus either `APPLE_NOTARY_KEYCHAIN_PROFILE` or a complete App Store
   Connect API key set.
5. Both API-key variable conventions are accepted:
   - task form: `APPLE_API_KEY=<path>` and `APPLE_API_KEY_ID=<key id>`;
   - Tauri form: `APPLE_API_KEY_PATH=<path>` and `APPLE_API_KEY=<key id>`.
   Before invoking Tauri, the values are normalized/exported to Tauri's
   canonical names. Values are never printed.
6. `notarytool submit --wait --output-format json` must return
   `status=Accepted`. A rejection, timeout, invalid JSON, or nonzero exit is a
   hard failure. No fallback may claim notarization.
7. After acceptance, both app and DMG are stapled. The DMG ticket is validated
   and the app must pass `spctl -a -vvv -t install`.
8. `scripts/verify_package_smoke.py` reports a secret-free structured state:
   `signature_mode`, `notarized`, `has_stapled_ticket`, separate app/DMG staple
   and Gatekeeper fields, and the inspected target kind.
9. App and DMG evidence are not interchangeable. When both are present,
   `has_stapled_ticket`, Gatekeeper acceptance, and notarized classification
   require both sides; one valid ticket cannot make the other side pass.
10. `scripts/macos/prepare-macos-release.sh --check` prints the currently
    selected signing/notarization mode and delegates detailed validation to
    `package-app.sh --check`.
11. If notarization variables are present without a signing identity, the
    package script warns and builds `adhoc_local`; partial API-key configuration
    with a signing identity is a hard error.
12. User documentation must require hash and `hdiutil verify` before
    instructing a single-app quarantine removal. It must never recommend
    disabling the system-wide Gatekeeper.

## Consequences

- Public-source builds and CI remain reproducible without commercial
  credentials.
- A credentialed release remains fully local and uses only Apple/Xcode system
  tools; no third-party signing service is introduced.
- The published R18 asset remains ad-hoc. The existence of an automated
  Developer ID path does not retroactively make that asset notarized.
- Real Developer ID and clean-Mac no-prompt acceptance remain unverified until
  a certificate-backed build is executed and manually tested.

## Verification

- `bash -n scripts/package-app.sh scripts/macos/prepare-macos-release.sh`
- `./scripts/package-app.sh --check`
- `./scripts/macos/prepare-macos-release.sh --check`
- `.venv-v2/bin/python scripts/verify_package_smoke.py --check --lifecycle`
- `.venv-v2/bin/python -m pytest -q tests/unit/test_package_smoke.py tests/unit/test_public_release_hygiene.py`
- `./scripts/package-app.sh` followed by real ad-hoc DMG classification
- Credential-backed Developer ID/notarization manual release and clean-Mac
  quarantine test are pending external commercial credentials.

## Acceptance

The user verified package integrity, honest `adhoc_local` reporting,
Gatekeeper/quarantine recovery, single-application release, and installation
of the accepted candidate on 2026-11-24. The credential-backed
`developer_id_notarized` path remains explicitly unverified.
