# Rollback and restore points

## Scope of the guarantee

The project can undo changes **made by this project**. It cannot truthfully promise to undo an independently executed 3x-ui, x-ui-reseller-panel, OS, provider, or unrelated Nginx upgrade. Use a provider/hypervisor snapshot before major unrelated upgrades.

## Bridge install/update transaction

Before an install/update, `install.sh` records whether each managed object existed and stores exact prior copies under `/var/backups/broute-bridge/install-<timestamp>/`, including when present:

- Bridge systemd unit
- Bridge environment file
- master key
- SQLite online backup
- previous `current` release target
- CLI/update/setup launchers

A new version is installed into a fresh `/opt/broute-bridge/releases/<timestamp>/` directory. The `current` symlink is switched only after the release is prepared. If startup or `/healthz` fails during the install/update transaction, the ERR trap restores the previous service/config/key/database/symlinks, normalizes service-readable permissions, disables a newly-created unit when appropriate, and removes the failed release.

Manual code release rollback:

```bash
sudo broute-bridge rollback
```

The rollback target must pass a health check; otherwise the CLI restores the release that was active before the rollback attempt.

## Bridge database + encryption key

`broute-bridge backup` uses SQLite's online backup API instead of blindly copying a live WAL database. The backup always includes the **matching encryption master key** as a pair:

```bash
sudo broute-bridge backup --note before-xui-update
```

State restore:

```bash
sudo broute-bridge restore-state latest
```

Before changing active state, `restore-state`:

1. requires both `bridge.db` and `master.key`;
2. runs SQLite integrity validation;
3. proves the key can decrypt stored reseller credentials when profiles exist;
4. creates a new pre-restore safety snapshot;
5. stops the Bridge, atomically replaces the DB/key pair with service-safe ownership/modes, and starts it again;
6. automatically restores the pre-restore snapshot if `/healthz` fails.

For a future schema-related rollback, normally roll code back first and then restore the matching state snapshot.

## HTTPS/setup wizard

`broute-bridge-setup` creates a separate `setup-<timestamp>` restore point. It backs up only the Nginx site it manages and remembers whether the enabled symlink existed. Nginx is validated with `nginx -t` before reload. If setup fails, it restores the exact prior site/symlink state and removes a newly-created profile. A certificate created by that failed transaction is removed through Certbot when safe.

Completed setup rollback:

```bash
sudo broute-bridge-setup rollback latest
```

This takes a safety copy of the current managed Nginx state first, restores the selected pre-setup state, validates it, and only then removes the Bridge profile created by that setup. If restored Nginx validation fails, the current Nginx state is restored and the profile is left untouched.

## Mirza source

Before changing Mirza source, the compatibility tool stores exact copies plus SHA-256 metadata under `/var/backups/broute-mirza-compat/<timestamp>/`. A new source snapshot is created only when a patch is actually required; a re-run against already-compatible source does not create a misleading duplicate rollback point.

Rollback:

```bash
sudo broute-mirza-compat rollback
```

This command first creates a disabled marker at `/etc/broute-mirza-compat/disabled` and disables the `.path` and `.timer` watchers, then restores the selected source snapshot and PHP-lints the result. If the rollback target fails lint, the pre-rollback safety snapshot is restored.

Keep automatic patching disabled while investigating. Re-enable only after review:

```bash
sudo broute-mirza-compat watch-enable
```

`watch-enable` refreshes the patcher from the configured (normally `stable`) source channel, verifies the current Mirza source is safely patchable, applies missing compatibility changes if needed, and only then re-enables the watcher/timer. If compatibility fails, the disabled marker remains.

You can disable watching without modifying Mirza source with:

```bash
sudo broute-mirza-compat watch-disable
```

If an upstream Mirza update changes patch anchors, the patcher exits before writing anything.
