# Security Policy

## Supported Release

Security fixes target the latest `v2.0-r18-ready` source tree and the public
release mirror.

## Reporting

Do not open a public issue containing an API key, token, credential, private
workspace hostname, local absolute path, user configuration or unredacted log.
Report the affected file and a minimal redacted reproduction to the repository
owner through a private GitHub security advisory or another private channel
already controlled by the owner.

## Secret Handling

- Real secrets belong only in local `.env`.
- `.env` must use mode `0600`.
- Repositories must not contain `.env`, environment dumps, browser storage,
  private certificates, signing assets, exported sessions or runtime logs.
- Example values must be obvious placeholders.
- Before any push, run:

```bash
gitleaks git --redact --no-banner --log-opts=--all .
git status --short
git diff --cached --name-only
```

If a secret was committed, stop the release, revoke or rotate it first, then
remove it from the affected history before publishing.

## Application Boundary

The local HTTP and WebSocket services bind to loopback and are not designed
for exposure to a LAN or the public Internet. The optional Qwen integration
requires TLS and an operator-supplied API key. Mock tests, builds and native
window verification do not require credentials.
