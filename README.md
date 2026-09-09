# Broute Mirza Reseller Bridge

Update-resistant integration layer between **MirzaBot**, **x-ui-reseller-panel**, and **3x-ui Central**.

```text
MirzaBot / Hong Kong
        |  HTTPS + br_live_...
        v
Nginx -> Broute Bridge / Germany (127.0.0.1:8765)
        |  xui_live_... (encrypted at rest)
        v
x-ui-reseller-panel -> 3x-ui Central -> Nodes
```

## Design goals

- no reseller ever receives a 3x-ui admin credential;
- `xui_live_...` stays on Germany and is encrypted in the Bridge database;
- Mirza stores only a separate `br_live_...` token;
- no permanent source modification to x-ui-reseller-panel or 3x-ui;
- the minimal Mirza-Agent compatibility overlay is versioned, PHP-linted, watched, and reversible;
- upstream contract checks run in CI and every six hours;
- every project-owned production change has a restore or rollback path.

## Germany: install Bridge

After a release is merged to `main`:

```bash
curl -fsSL https://raw.githubusercontent.com/xbroute/broute-mirza-reseller-bridge/main/install.sh | sudo bash
```

The installer creates immutable releases under `/opt/broute-bridge/releases`, snapshots the previous project-owned service/config/key/database state, activates the new release, checks `/healthz`, and restores the old state automatically if activation fails.

Then run the guided production setup:

```bash
sudo broute-bridge-setup
```

The wizard explains every value before asking for it. It validates the `xui_live_...` key with `/api/v1/me`, loads the seller's allowed `/api/v1/inbounds`, displays human-readable inbound labels, configures Nginx + HTTPS + the Hong Kong source allowlist, creates the encrypted Bridge profile, and prints the `br_live_...` token once.

**Never put `xui_live_...` or a 3x-ui admin token into MirzaBot.**

## Hong Kong: Mirza compatibility

```bash
curl -fsSL https://raw.githubusercontent.com/xbroute/broute-mirza-reseller-bridge/main/scripts/install-mirza-compat.sh | sudo bash
```

The installer explains the files it will change and creates a dated backup before writing. It changes Mirza Agent only to:

1. perform a real usage reset;
2. forward `Methodextend` to the Bridge;
3. verify TLS certificates for Mirza-Agent requests only.

Other Mirza adapters keep their existing behavior.

Useful commands:

```bash
sudo broute-mirza-compat status
sudo broute-mirza-compat check
sudo broute-mirza-compat apply
sudo broute-mirza-compat rollback
```

`rollback` disables the automatic watcher first, restores the selected source snapshot, and PHP-lints the restored files before leaving them active.

## Configure the panel in Mirza

Use the normal Mirza panel UI for the secret-bearing step:

```text
Panel type:       Mirza Agent / mirza_agent
URL:              https://bridge.your-domain.example
Password/API key: br_live_... printed by the Germany wizard
```

Do not use Mirza's sample-user `set_inbounds` flow for this integration. Default inbound IDs are already validated against the reseller-visible inbound list by the Germany wizard.

## Health and rollback

Germany:

```bash
sudo broute-bridge doctor --live
sudo broute-bridge backup --note before-xui-update
sudo broute-bridge-setup rollback latest   # restore pre-setup Nginx + remove setup-created profile
sudo broute-bridge rollback                # roll back the Bridge application release
```

The setup rollback takes a safety snapshot of the current Nginx state first, validates the target with `nginx -t`, and refuses to delete the Bridge profile if the restored Nginx configuration is invalid.

Hong Kong:

```bash
sudo broute-mirza-compat status
sudo broute-mirza-compat rollback
```

Non-destructive smoke check from an authorized machine:

```bash
bash scripts/smoke.sh https://bridge.your-domain.example
```

No user is created or modified by the smoke script.

## Update compatibility

`.github/workflows/upstream-watch.yml` checks current MirzaBot, x-ui-reseller-panel, and 3x-ui contracts every six hours. If a required contract changes, the workflow fails and opens/updates an `Upstream compatibility alert` issue. A new upstream version should not be treated as integration-safe until the check is green again.

## Security note about Mirza management API

The Mirza API documentation supplied for this integration documents useful management actions such as `panel_add`, `panel_edit`, `set_inbounds`, and reseller-bot management. It also states that authenticated requests are stored with headers and request bodies in `logs_api`. Because secret-bearing fields such as `password_panel` can be present, this project deliberately does **not** use that API to inject Bridge/Reseller secrets.

See [`docs/MIRZA-API-NOTES.md`](docs/MIRZA-API-NOTES.md).

## User-facing guidance

Any option or workflow added by this project must answer in the same flow:

1. What is this?
2. What should I enter/choose?
3. What changes?
4. How is it validated?
5. What happens if it fails?
6. How do I recover/roll back?

See [`docs/UX.md`](docs/UX.md).

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
python -m compileall -q broute_bridge scripts tests
bash -n install.sh scripts/install-mirza-compat.sh scripts/setup-wizard.sh scripts/smoke.sh
python scripts/upstream_watch.py
```
