# Rollback and restore points

## Scope of the guarantee

The project can undo changes **made by this project**. It cannot truthfully promise to undo an independently executed 3x-ui, x-ui-reseller-panel, OS, or provider upgrade. Use a provider/hypervisor snapshot before major unrelated upgrades.

## Bridge install/update transaction

Before an install/update, `install.sh` records whether each managed object existed and stores exact prior copies under `/var/backups/broute-bridge/install-<timestamp>/`, including:

- bridge systemd unit
- bridge environment file
- master key when pre-existing
- SQLite online backup when pre-existing
- previous `current` release target
- CLI/update/setup launchers

A new version is installed into a fresh `/opt/broute-bridge/releases/<timestamp>/` directory. The `current` symlink is switched only after the release is prepared. If startup or `/healthz` fails, the ERR trap restores the previous service/config/key/database/symlinks and removes the failed release.

Manual code release rollback:

```bash
sudo broute-bridge rollback
```

The rollback target must pass a health check; otherwise the CLI restores the release that was active before the rollback attempt.

## Database

`broute-bridge backup` uses SQLite's online backup API instead of blindly copying a live WAL database:

```bash
sudo broute-bridge backup --note before-xui-update
```

## HTTPS/setup wizard

`broute-bridge-setup` creates a separate `setup-<timestamp>` restore point. It backs up only the Nginx site it manages and remembers whether the enabled symlink existed. Nginx is validated with `nginx -t` before reload. If setup fails, it restores the exact prior site/symlink state and removes a newly-created profile. A certificate created by that failed transaction is deleted with Certbot.

The wizard refuses to hijack an existing enabled virtual host for the same domain.

## Mirza source

Before changing Mirza, the patcher stores exact copies plus SHA-256 metadata under `/var/backups/broute-mirza-compat/<timestamp>/`. Replacement files are created separately, run through `php -l`, and atomically moved into place only after validation.

Rollback:

```bash
sudo broute-mirza-compat rollback
```

This command first disables the `.path` and `.timer` watchers and creates `/etc/broute-mirza-compat.disabled`, preventing the restored files from being patched again automatically.

After reviewing/updating compatibility logic:

```bash
sudo broute-mirza-compat watch-enable
```

If an upstream Mirza update changes patch anchors, the patcher exits before writing anything.
