# AdStac.kr Reality Matrix

This document defines which actions are real, simulated, local-only, archived, or resettable across backend modes. It is intended to remove ambiguity during development, demos, QA, and internal testing. The current backend includes real Meta reads, local persistence flows, and some write-capable paths, so this matrix distinguishes those behaviors explicitly.[file:109][cite:316]

## Core terms

Use these terms consistently in code, UI copy, and internal discussion.

- **External read** — Sends a real request to Meta and reads data without intentionally changing Meta state.[file:109]
- **External write** — Sends a real request to Meta that can change object state or create assets.[file:109]
- **Local write** — Writes only to AdStac.kr storage such as SQLite tables like `ad_insights`, `ad_creative_structures`, `suggested_configurations`, or connection records.[file:109]
- **Simulated** — Returns fabricated or overridden values via demo provider behavior or masking logic rather than representing the underlying live truth exactly.[file:193][file:194][cite:41]
- **Soft delete / archive** — Keeps a record but marks it non-current, inactive, missing, rejected, replaced, or otherwise no longer primary.[file:109][cite:263]
- **Hard reset / wipe** — Intentionally removes local development or test state so the environment can be re-created cleanly.[cite:316][cite:263]

## Modes

The backend currently supports at least two broad behavior families: `APP_MODE=live|demo` and a masking layer controlled by `MASK_MODE` plus individual masking flags.[file:109][file:193] These should be treated as user-visible operating modes even if the implementation combines them from multiple environment variables.[file:193][file:194]

### Live mode

`APP_MODE=live` means the backend uses real Meta credentials from `meta_connections` and calls the real Meta APIs for reads and any enabled write actions.[file:109] If masking is disabled, the responses are pass-through and pause/resume writes can reach Meta directly.[file:109][file:193][file:194]

### Demo mode

`APP_MODE=demo` means the backend can return demo credentials (`demo-token`, `demo-account`) and use a demo provider path rather than real account credentials.[file:109] Demo mode should be treated as simulated-by-default unless a specific action is explicitly documented as still real.[file:109][cite:41]

### Masked live mode

Masked live mode is the most important hybrid: reads may still hit live Meta, but selected fields are overridden on the way out, such as campaign status, ad status, daily budgets, or metrics.[file:193][file:194] In this mode, the system may be using real upstream data transport while presenting simulated business truth to the app and user.[file:194][cite:41] Masked live mode can override campaign status, ad status, budgets, and metrics independently. In particular, `MASK_AD_STATUSES=true` forces ad statuses returned by `fetch_ads()` to `ACTIVE`, even when campaign-level masking is configured separately.

## Reality matrix

| Action | Live mode | Demo mode | Masked live mode | Mutation type | Risk level | Notes |
|---|---|---|---|---|---|---|
| `GET /me/meta-status` | Local read from `meta_connections`.[file:109] | Local/demo semantics only.[file:109] | Same as live.[file:109] | Local read | Low | No Meta request is required.[file:109] |
| `GET /auth/meta/login-url` | Real OAuth entrypoint generation.[file:109] | Usually not needed in pure demo mode.[file:109] | Same as live.[file:109] | Local write/read flow | Medium | Starts connection flow but does not itself spend money.[file:109] |
| `GET /auth/meta/callback` | Real OAuth token exchange and local connection persistence.[file:109] | Usually bypassed in demo setups.[file:109] | Same as live.[file:109] | External read + local write | Medium | Stores access token and selected ad account locally.[file:109] |
| `GET /api/campaigns` | Real Meta read for campaigns and metrics.[file:109] | Simulated/demo output if demo provider is used.[file:109][cite:41] | Real read with possible status, budget, and metric overrides.[file:194] | External read or simulated | Low | Safe from spend by itself, but not necessarily truthful in masked mode.[file:109][file:194] |
| `GET /api/ads` | Real Meta read for ads and creatives.[file:109] | Simulated/demo output if demo provider is used.[cite:41] | Real read with possible forced ad statuses.[file:194] | External read or simulated | Low | Does not create spend by itself.[file:109] |
| `GET /api/ingest/preview` | Real read of campaigns and ads; no persistence.[file:109] | Simulated or demo-backed preview.[file:109][cite:41] | Real read with masked values possible; still no persistence.[file:109][file:194] | External read or simulated | Low | Best inspection endpoint for safe verification.[file:109] |
| `POST /api/ingest` | Real Meta read, then local insert into `ad_insights`.[file:109] | Simulated/demo read, then local insert into `ad_insights`.[file:109][cite:41] | Real Meta read with masked metrics possible, then local insert.[file:109][file:194] | External read + local write | Low | No external Meta state change; may persist synthetic metrics locally in masked/demo mode.[file:109][file:194] |
| `POST /api/ingest/structure/{campaign_id}` | Real Meta read, then local upsert into `ad_creative_structures`.[file:109] | Simulated/demo-backed structure ingest if supported by provider.[cite:41] | Real read with statuses potentially overridden before persistence.[file:109][file:194] | External read + local write | Low | Missing previously seen ads are marked `missing`, not deleted.[file:109] |
| `GET /api/structure/{campaign_id}` | Local read of persisted structure.[file:109] | Local read of persisted structure.[file:109] | Same as live.[file:109] | Local read | Low | Returns the current local truth, which may itself have been built from simulated inputs.[file:109] |
| `GET /api/explore` | Real raw Meta read of campaigns, adsets, and ads.[file:109] | Simulated/demo-backed raw explorer if provider supports it.[cite:41] | Real read with masking depending on provider path.[file:109][file:194] | External read or simulated | Low | No DB persistence.[file:109] |
| `GET /api/campaigns/{campaign_id}/history` | Local read from `ad_insights`.[file:109] | Local read from demo-ingested history if present.[file:109] | Same as live.[file:109] | Local read | Low | History reflects whatever was previously saved, including masked/demo values.[file:109][file:194] |
| `POST /api/campaigns/{campaign_id}/pause` | Real Meta write unless intercepted.[file:109] | Should be treated as simulated or disabled in demo contexts.[cite:41] | No-op only if `MASK_PAUSE_RESUME=true`; otherwise still real.[file:193][file:194] | External write or simulated | High | State-changing operation with direct platform impact.[file:109][file:194] |
| `POST /api/campaigns/{campaign_id}/resume` | Real Meta write unless intercepted.[file:109] | Should be treated as simulated or disabled in demo contexts.[cite:41] | No-op only if `MASK_PAUSE_RESUME=true`; otherwise still real.[file:193][file:194] | External write or simulated | High | Resuming delivery can create real cost exposure depending on the campaign.[file:109] |
| `GET /api/suggestions` | Local read from `suggested_configurations`.[file:109] | Same.[file:109] | Same.[file:109] | Local read | Low | No Meta side effects.[file:109] |
| `POST /api/suggestions` | Local insert into `suggested_configurations`.[file:109] | Same.[file:109] | Same.[file:109] | Local write | Low | Stores suggested configurations only.[file:109] |
| Suggestion confirmation that only updates deployment status | Local-only mutation if implemented as DB status transition.[file:109] | Same.[file:109] | Same.[file:109] | Local write | Low | Safe unless it invokes asset creation logic.[file:109] |
| `_clone_dynamic_to_static_ad(...)` and any route that calls it | Real Meta creative creation and ad creation.[file:109] | Should be considered simulated or blocked unless explicitly documented otherwise.[cite:41] | Potentially still real unless separately blocked; masking shown does not guarantee asset-creation no-op.[file:109][file:194] | External write | High | Creates ad creative and ad objects in Meta, even though created ads are initialized as `PAUSED`.[file:109] |

## Delete and archive semantics

Delete behavior should be documented separately from resets because the product intent is archival where possible rather than destructive erasure.[cite:263] The current backend already follows this pattern for some ingested structure data by marking records as `missing` when an ad no longer appears in a new fetch.[file:109]

### Implemented archival behavior

The clearest implemented archive-like behavior today is in `ad_creative_structures`: during structure ingest, previously ingested `ad_id` values that are absent from the current fetch are updated to `lifecycle_status = 'missing'` instead of being removed.[file:109] The same table also carries `active` and `inactive` lifecycle states, which means the local model already supports “kept but no longer current” semantics.[file:109]

`suggested_configurations` also includes archival-like deployment states such as `rejected`, `replaced_static`, `created_static`, and `active_static`, which are status transitions rather than destructive deletes.[file:109] Those should be treated as soft lifecycle changes, not as data removal.[file:109]

### Recommended delete vocabulary

Use the following terms consistently in documentation and eventually in the product UI.

| Term | Meaning | Storage expectation |
|---|---|---|
| Archive | Hide from active workflows but retain full record.[cite:263] | Row remains; status changes only.[cite:263] |
| Missing | Previously ingested object is no longer returned by current upstream fetch.[file:109] | Row remains; `lifecycle_status='missing'`.[file:109] |
| Inactive | Object still exists but is not currently live/serving.[file:109] | Row remains; status/lifecycle marks inactive.[file:109] |
| Rejected | Human or system declined to use a suggestion.[file:109] | Row remains in `suggested_configurations`.[file:109] |
| Replaced | Older suggestion or derived object has been superseded.[file:109] | Row remains with replacement status.[file:109] |
| Hard delete | Physically remove data.[cite:263] | Only use for explicit reset/wipe operations.[cite:263][cite:316] |

### Recommended delete rules

- Ingested Meta-derived records should default to archive semantics rather than hard delete.[cite:263]
- “No longer returned by Meta” should map to `missing`, not deletion.[file:109]
- “User no longer wants this suggestion” should map to `rejected` or archived, not deletion.[file:109][cite:263]
- Hard delete should be reserved for dev/test reset actions, privacy-required removal, or deliberate administrative cleanup.[cite:316][cite:263]

## Reset semantics

Reset behavior should be documented by scope. The user preference is for throwaway but thoughtfully structured dev data with easy resets, while still avoiding accidental loss of useful business history in normal flows.[cite:316][cite:263]

### Reset levels

| Reset type | What it does | Meta impact | Data impact | Recommended use |
|---|---|---|---|---|
| Soft reset | Clears derived local artifacts only, such as snapshots, normalized structures, and suggestions.[cite:316][cite:263] | None | Removes or archives local working data only.[cite:316] | Routine QA reruns, ingest retesting, demo cleanup.[cite:316] |
| Connection reset | Removes local Meta connection state such as `meta_connections` and OAuth state.[file:109] | None directly | Requires reconnect before further live reads.[file:109] | Account switching, broken token recovery, dev cleanup.[file:109] |
| User workspace reset | Clears one user’s local AdStac.kr workspace, including insights, structures, suggestions, and optional connection state.[cite:316][cite:263] | None directly | Recreates “fresh install” experience for that user.[cite:316] | Integration testing and onboarding rehearsals.[cite:316] |
| Full local wipe | Clears all local dev/test tables for the environment.[cite:316] | None directly | Destroys local environment state for all users in that environment.[cite:316] | Rebuild from scratch in dev only.[cite:316] |
| External reset | Changes Meta objects directly, such as pausing campaigns or replacing ads.[file:109] | Real Meta effect | External platform state changes.[file:109] | Should never be part of a generic “reset” unless explicitly named and confirmed.[file:109] |

### Planned reset targets

The following local entities are good candidates for reset operations or admin scripts because they are local artifacts or connection state already represented in the current schema.[file:109]

- `ad_insights` — stored metric snapshots from ingest.[file:109]
- `ad_creative_structures` — normalized creative structure derived from Meta reads.[file:109]
- `suggested_configurations` — stored suggestions and deployment lifecycle statuses.[file:109]
- `meta_connections` — local storage for access token, ad account id, and connection metadata.[file:109]
- `oauth_states` — transient connection-flow state.[file:109]

### Reset rules

- Resets should be local-only by default.[cite:316]
- Anything that can mutate Meta should never be labeled simply “reset”; it should be called out as an external write.[file:109]
- Reset endpoints or scripts should be documented by scope: one campaign, one user, one account, or full environment.[cite:316][cite:263]
- A full wipe should only exist in development or explicitly non-production environments.[cite:316]

## Safe defaults by mode

These defaults reduce ambiguity and cost risk while keeping the product useful for demos and integration testing.[cite:316][cite:41]

### Live mode defaults

- Allow real reads.[file:109]
- Allow local ingest and structure persistence.[file:109]
- Require explicit confirmation for any Meta write.[file:109]
- Label pause/resume and asset creation paths as **real external writes** in UI and docs.[file:109]

### Demo mode defaults

- Treat all campaign/ad/metric truth as simulated unless explicitly noted otherwise.[cite:41]
- Allow local persistence for testing downstream flows.[cite:316]
- Disable or stub all external writes by default.[cite:41]

### Masked live mode defaults

- Treat campaign status, ad status, budgets, and metrics as potentially simulated even when upstream reads are real.[file:193][file:194]
- Mark any persisted outputs derived from masked responses as “locally stored from masked/live source” in future provenance documentation or metadata.[file:194]
- Keep pause/resume blocked with `MASK_PAUSE_RESUME=true` unless an operator intentionally wants real control-plane behavior.[file:193][file:194]

## Recommended implementation notes

This document can remain documentation-only at first, but it will be more durable if eventually mirrored in code-level metadata.[cite:316] A future version should expose mode and action semantics through a small internal policy object or admin endpoint so the frontend, backend, and docs all share the same definitions.[cite:41][cite:316]

A minimal future shape would be:

```json
{
  "mode": "masked_live",
  "actions": {
    "read_campaigns": {"reality": "real_read_masked", "writes_local": false, "writes_external": false},
    "ingest_metrics": {"reality": "masked_read", "writes_local": true, "writes_external": false},
    "resume_campaign": {"reality": "simulated_noop", "writes_local": false, "writes_external": false},
    "create_static_ad": {"reality": "disabled"}
  }
}
```

That would turn the current documentation into enforceable runtime truth over time.[cite:316][cite:41]

## Current practical summary

At present, the backend already supports a meaningful distinction between safe read flows, local-only persistence, simulated masking, and real external writes.[file:109][file:194] What is still missing is not the concept, but the single explicit source of truth that tells operators and developers which category each action falls into in each mode.[cite:316]

Until that source is implemented in code, this document should be treated as the canonical internal reference.[cite:316][cite:41]

