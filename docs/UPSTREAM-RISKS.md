# Upstream risks tracked by this project

These are upstream behaviors observed during integration review and are intentionally isolated from the Bridge where possible.

## x-ui-reseller-panel

- Public `/api/v1/users` currently returns the reseller's full user list; no exact username lookup/pagination is available in the reviewed API contract. The Bridge caches mappings and verifies them before writes to reduce scans.
- Renew has idempotency, but generic create does not expose the same generic idempotency contract. The Bridge reconciles duplicate-create responses by exact username.
- Current reseller user modification logic should be tested carefully when removing an inbound; older reviewed code attached desired IDs without symmetrically detaching removed IDs.
- Shrinking a representative's allowed inbound set should reconcile already-attached users.
- Creating a disabled client can disagree with current 3x-ui behavior that has historically coerced create-time enable to true; the Bridge creates enabled users and therefore avoids relying on this edge.
- XUI-success followed by local SQLite failure can create an orphan; upstream reconciliation remains desirable.

## MirzaBot

- Current Agent reset path reports success without calling the backend; the compatibility overlay fixes only this branch.
- Current Agent `extend_service` drops `Methodextend`; the overlay forwards it.
- Mirza's updater can replace local source modifications, so the overlay is designed to be re-applied rather than maintained as a long-lived fork.
- Upstream request TLS verification and API log redaction should be reviewed separately before exposing credentials broadly.

## 3x-ui

- Resellers must never receive the central admin API token.
- Client identity is global while inbound membership is separate; desired membership must be reconciled with attach **and detach** operations.
- The Bridge does not call traffic nodes directly and does not fork 3x-ui.
