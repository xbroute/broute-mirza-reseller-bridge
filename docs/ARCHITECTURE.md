# Architecture

## Source of truth

The integration deliberately separates responsibilities:

| Component | Source of truth for |
|---|---|
| MirzaBot | products, invoices, payments, customer-facing subscription flow |
| x-ui-reseller-panel | reseller ownership, allowed inbounds, reseller quota/ledger, reseller API authorization |
| 3x-ui Central | actual network client identity, inbound membership, UUID/SubID, runtime and traffic nodes |
| Bridge | translation, short-lived replay protection, credential isolation, username-to-client-id cache |

The bridge never becomes authoritative for traffic or account state. It re-reads reseller state for mutations and self-heals stale mappings.

## Identity mapping

```text
Mirza invoice -> username -> reseller client_id -> 3x-ui client identity
```

3x-ui client email/username is globally unique in current 3x-ui. Production should therefore enforce a reseller/brand prefix in Mirza usernames when multiple sellers share one central panel.

## API boundary

Mirza receives `br_live_*`; it never receives the reseller's `xui_live_*` and never receives a 3x-ui admin API token.

The bridge uses only the reseller public `/api/v1` surface. Traffic nodes are reachable only through 3x-ui Central.
