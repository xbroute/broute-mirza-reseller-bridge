# Production rollout

## Release channel

Production servers follow the controlled `stable` branch. Do not point production at an arbitrary `main` commit. Promote `stable` only after the target `main` commit has passed PR/main CI and review.

## Germany

1. Take a provider/hypervisor snapshot when available. This protects changes made outside this project too.
2. Run the Bridge installer from `stable`. It creates its own exact pre-change restore point before changing service/config/state paths.
3. Run `sudo broute-bridge-setup`.
4. The wizard live-checks the reseller key (`/api/v1/me` + `/api/v1/inbounds`) before storing it.
5. Prefer HTTPS. The wizard creates a dedicated Nginx virtual host, obtains/installs a Let's Encrypt certificate with the Certbot Nginx plugin, and restricts Bridge access to the Hong Kong Mirza IP/CIDR.
6. Record the one-time `br_live_*` token in your password manager.
7. Run `sudo broute-bridge doctor --live`.

Do not pass `xui_live_*` on a command line in normal operation. The CLI prompts securely, or the wizard feeds it through stdin. The Bridge service runs as the unprivileged `broute-bridge` account; root owns the configuration/master key and grants the service group read-only access.

## Hong Kong

1. Confirm the actual Mirza root (commonly `/var/www/html`).
2. Run the `stable` `scripts/install-mirza-compat.sh`, with `MIRZA_ROOT=...` only if automatic discovery cannot find it.
3. When a patch is required, the installer creates an exact source backup immediately before writing, lints patched PHP, and enables a `.path` + periodic `.timer` compatibility check.
4. Add the Bridge as a `mirza_agent` panel using the Germany HTTPS URL and the `br_live_*` token.
5. Start with a dedicated test product and reseller test account before routing existing production products.

The periodic Mirza compatibility command may refresh its patcher only from the configured Bridge source channel. Normal production configuration points that channel at `stable`, so unreviewed development commits on `main` are not pulled onto the Mirza server. If current Mirza source no longer matches known safe anchors, patching fails closed and leaves those upstream source files untouched.

After a Mirza rollback, automatic patching is deliberately disabled. Re-enable it only after review with:

```bash
sudo broute-mirza-compat watch-enable
```

## State protection

A manual Bridge backup contains the SQLite database **and its matching encryption master key**:

```bash
sudo broute-bridge backup --note before-xui-update
```

If an application release rollback also requires pre-change Bridge state (for example after a future database schema change), switch code first and then restore the matching state snapshot:

```bash
sudo broute-bridge rollback
sudo broute-bridge restore-state latest
```

`restore-state` validates SQLite integrity/decryptability before stopping the service, creates a pre-restore safety snapshot, and reverts to that safety snapshot automatically if the restored state does not pass `/healthz`.

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
- `watch-enable` validates compatibility before automatic Mirza re-apply is restored
- Bridge release rollback restores the prior healthy release
- paired DB/master-key restore rejects a mismatched key and keeps a pre-restore safety copy
