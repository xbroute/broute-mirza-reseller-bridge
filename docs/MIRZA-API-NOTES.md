# MirzaBot API notes used by this project

The supplied MirzaBot management API documentation is useful for contract discovery, but this project deliberately avoids secret-bearing automated provisioning through that API.

## Why we do not create the Bridge panel through Mirza's management API

The management API supports `panel_add`/`panel_edit`, but authenticated requests are documented as being logged with headers, body, source IP, and action in `logs_api`. `panel_add` includes `password_panel`. Putting a `br_live_...` token into that request could therefore persist the token in Mirza logs.

The Germany setup wizard prints the exact Mirza UI values instead. The operator enters the one-time Bridge token directly into Mirza.

## Why we do not use sample-user set_inbounds

Mirza's management `set_inbounds` action discovers defaults from a sample user and current upstream does not provide a `mirza_agent` implementation for that action. The Bridge instead obtains the reseller-visible inbound list from `GET /api/v1/inbounds` and validates the selected default IDs there.

## Mirza Agent overlay

Current upstream Mirza Agent requires a small reversible overlay for two semantics:

- forward the selected `Methodextend` value to `extend_service`;
- make `ResetUserDataUsage` call the backend instead of returning success without a reset.

The overlay also enables certificate verification only for Mirza-Agent requests. It does not change TLS behavior of other Mirza adapters.
