# Hardening guide

Humon is secure by default, but a production deployment should tighten further.

## Run under systemd as a non-root user

Use [`deploy/humon.service`](../deploy/humon.service). It sets `User=humon`,
`NoNewPrivileges=true`, `ProtectSystem=strict`, `PrivateTmp=true`, `MemoryMax=2G`, and an
explicit `ReadWritePaths` allowlist. Every writable path a tool needs (e.g. a `files` jail)
must be added to `ReadWritePaths` — nothing else is writable.

## Secrets

Store tokens as systemd credentials (`LoadCredential=`) so they never appear in the
process environment of other users or in `systemctl show`. `config.yaml` must contain
only `*_env` references; the loader refuses to start if it finds an inline secret.

## Tool posture

- **shell**: keep `allowed_binaries` minimal and read-only; leave
  `allow_shell_metachars: false`. Every binary you add is attack surface.
- **files**: make `jail_paths` as narrow as possible; writes/deletes stay approval-gated.
- **lan**: keep `allowed_cidrs` to the subnets you actually probe; it never reaches the
  public internet.
- **sysinfo**: read-only, but journal access can leak information — cap `journal_max_lines`.

## Policy

Review `policy.rules`. The safe posture: `require_approval` for anything that writes,
deletes, or mutates network state; `deny` as the global default. Set the approval
`timeout_s` to a value your operators can realistically meet — a timeout is a deny.

## Slack

Keep `allowed_users` and `allowed_channels` tight. Humon ignores everyone not listed.

## Backups & audit

State is one SQLite file (WAL). Back it up regularly — it holds the append-only audit
log. Export with `humon audit export -o audit.json`.

## Updates

Watch releases and the changelog. Re-run `humon doctor` after upgrades.
