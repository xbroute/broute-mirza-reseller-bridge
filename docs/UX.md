# User-facing guidance policy

Any option, prompt, field, warning, or workflow added by this project must explain the action at the point of use. A user must not need to guess a value by reading source code.

For every interactive step we provide:

1. **What is this?** — what the value controls and which component owns it.
2. **What should I enter?** — accepted form plus a realistic example.
3. **What changes?** — files/services/remote objects that may be affected.
4. **How is it validated?** — preflight check before committing a write whenever possible.
5. **What if it fails?** — actionable cause and the next diagnostic command.
6. **How do I recover?** — restore point or rollback command when our project made a change.

## Secret labels

The operator must always be told which credential is expected:

- `br_live_...`: Bridge token. This is the only integration token placed in MirzaBot.
- `xui_live_...`: reseller-scoped x-ui-reseller-panel API key. It remains on the Germany Bridge host and is encrypted at rest.
- 3x-ui admin/API credential: never put this in MirzaBot or the Bridge profile.
- Mirza management API token and Telegram bot token: never print or persist these in project logs.

## Inbounds

The setup wizard lists reseller-visible inbound IDs with their labels/protocol/network/port before asking for defaults. It rejects IDs that are not visible to the reseller. Mirza's sample-user `set_inbounds` flow is not required for this integration.

## Failure messages

Errors should identify the failing hop when possible, for example:

- `Mirza -> Bridge authentication`
- `Bridge -> Reseller authentication`
- `Reseller -> 3x-ui operation`
- `Nginx/TLS publishing`

Do not replace a useful upstream message with a generic `failed` unless exposing the original would reveal a secret.
