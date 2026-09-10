# Security policy

## Production principles

- Do not expose the bridge's Uvicorn port publicly; bind to loopback and use a hardened HTTPS reverse proxy.
- Restrict the reverse proxy to the Mirza server IP/CIDR where operationally possible.
- Use a valid TLS certificate.
- Keep `BROUTE_VERIFY_RESELLER_TLS=true` in production.
- Never place the central 3x-ui administrator token in Mirza or in reseller profiles.
- Treat `br_live_*` and `xui_live_*` as secrets.
- Keep `/etc/broute-bridge/master.key` mode `0600` and out of source control/backups that leave your infrastructure trust boundary.

## Secret storage

Bridge bearer tokens are hashed. Reseller API keys are encrypted with a separate master key. Logs must never include Authorization headers or decrypted API keys.

## Reporting

For a security issue, use a private contact channel rather than opening a public issue containing credentials, server addresses, or exploit details.
