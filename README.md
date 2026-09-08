# Broute Mirza Reseller Bridge

Update-resistant integration layer between **MirzaBot**, **x-ui-reseller-panel**, and **3x-ui Central**.

The bridge is intentionally a standalone service. It does **not** embed reseller logic into 3x-ui and does **not** require x-ui-reseller-panel to be permanently patched. MirzaBot keeps using its existing `mirza_agent` panel type; only a small reversible compatibility overlay is required to fix two gaps in Mirza's current Agent adapter: real usage reset and preservation of `Methodextend` semantics.

## Architecture

```text
MirzaBot / Hong Kong
        |
        | Authorization: Bearer br_live_...
        v
Broute Mirza Reseller Bridge / Germany
        |
        | Authorization: Bearer xui_live_...
        v
x-ui-reseller-panel
        |
        v
3x-ui Central -> Nodes
```

The reseller API key stays on the Germany server and is encrypted at rest. Mirza only receives a bridge-scoped token.

## Safety model

This project treats production changes as transactions:

- versioned releases under `/opt/broute-bridge/releases`
- stable `/opt/broute-bridge/current` symlink
- pre-change restore points under `/var/backups/broute-bridge`
- SQLite online backups before upgrades
- health check before a deployment is committed
- automatic release rollback on failed install/update
- manual rollback command
- idempotent Mirza patcher with per-change backups
- PHP lint before Mirza source replacement
- fail-closed patching if upstream source anchors change
- replay protection for duplicate Mirza mutations after transport timeouts
- persistent, self-healing `username -> reseller client_id` mapping

## Germany installation

```bash
curl -fsSL https://raw.githubusercontent.com/xbroute/broute-mirza-reseller-bridge/main/install.sh | sudo bash
```

Then create one profile per reseller/Mirza instance:

```bash
sudo broute-bridge profile-add \
  --name seller1 \
  --reseller-url https://reseller.example.com \
  --reseller-api-key 'xui_live_...' \
  --inbounds '1,2,3'
```

The command prints a `br_live_...` token **once**. Store that token in Mirza as the Mirza-Agent panel password/API key.

Useful commands:

```bash
sudo broute-bridge doctor
sudo broute-bridge doctor --live
sudo broute-bridge backup --note before-xui-update
sudo broute-bridge profile-list
sudo broute-bridge rollback
sudo broute-bridge-update
```

## Hong Kong / Mirza compatibility installation

Set `MIRZA_ROOT` if Mirza is not installed in `/var/www/html`:

```bash
curl -fsSL https://raw.githubusercontent.com/xbroute/broute-mirza-reseller-bridge/main/scripts/install-mirza-compat.sh | sudo bash
```

Rollback the compatibility overlay:

```bash
sudo broute-mirza-compat rollback
```

The patcher changes only the Mirza Agent integration surface. It refuses to guess if the upstream source layout changes.

## Mirza panel configuration

Use:

```text
Panel type: mirza_agent
URL: https://bridge.example.com/
Password/API key: br_live_...
```

The bridge implements the Agent actions currently used by Mirza:

- `list_panel`
- `user_create`
- `get_user_data`
- `add_time_service`
- `add_volume_service`
- `extend_service`
- `reset_usage` (compatibility extension)
- `user_delete`
- `change_link`

## Mirza renewal compatibility

The bridge preserves all five current Mirza renewal modes:

- `resetVolumeTime`
- `addTimeVolumeNextMonth`
- `resetTimeAddVolume`
- `resetVolumeAddTime`
- `addTimeConvertVolume`

Mirza's upstream `mirza_agent` currently drops this mode when calling `extend_service`; the compatibility overlay forwards it as `method_extend`.

## Credentials

`br_live_...` tokens are stored only as SHA-256 hashes. `xui_live_...` keys are encrypted with a local Fernet master key stored separately from the database.

Default locations:

```text
/etc/broute-bridge/master.key
/etc/broute-bridge/bridge.env
/var/lib/broute-bridge/bridge.db
```

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
python -m compileall -q broute_bridge scripts
bash -n install.sh scripts/install-mirza-compat.sh
```

See `docs/ARCHITECTURE.md`, `docs/PRODUCTION.md`, `docs/ROLLBACK.md`, and `docs/UPSTREAM-RISKS.md` before production rollout.
