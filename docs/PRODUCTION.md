# Production rollout

## Germany

1. Take a provider/hypervisor snapshot when available. This protects changes made outside this project too.
2. Run the bridge installer. It creates its own exact pre-change restore point before changing service/config/state paths.
3. Run `sudo broute-bridge-setup`.
4. The wizard live-checks the reseller key (`/api/v1/me` + `/api/v1/inbounds`) before storing it.
5. Prefer HTTPS. The wizard can create a dedicated Nginx virtual host, obtain a Let's Encrypt certificate using webroot mode, and restrict bridge access to the Hong Kong Mirza IP/CIDR.
6. Record the one-time `br_live_*` token in your password manager.
7. Run `sudo broute-bridge doctor --live`.

Do not pass `xui_live_*` on a command line in normal operation. The CLI prompts securely, or the wizard feeds it through stdin.

## Hong Kong

1. Confirm the actual Mirza root (default `/var/www/html`).
2. Run `scripts/install-mirza-compat.sh` with `MIRZA_ROOT=...` if required.
3. The installer creates source backups, lints patched PHP, applies atomically, and enables a `.path` + periodic `.timer` compatibility check.
4. Add the Bridge as a `mirza_agent` panel using the Germany HTTPS URL and the `br_live_*` token.
5. Start with a dedicated test product and reseller test account before routing existing production products.

The Mirza watcher never downloads new executable patch logic by itself. It only re-applies the already-reviewed installed patcher if known anchors still match. If upstream changes the contract/layout, it fails closed and leaves the new source untouched.

## Cutover test matrix

Before moving real customers, verify:

- create user on one inbound and multiple inbounds
- fetch subscription and raw links
- add traffic
- add time
- all five `Methodextend` modes
- usage reset
- subscription revoke/change link
- delete
- timeout/retry does not double-apply a mutation
- a duplicate username with different service attributes is rejected rather than adopted
- reseller quota exhaustion fails closed
- disallowed inbound creation fails
- Bridge restart preserves mappings and encrypted credentials
- Reseller restart and 3x-ui restart recover cleanly
- Mirza compatibility rollback restores original PHP and disables re-apply
- Bridge release rollback restores the prior healthy release
