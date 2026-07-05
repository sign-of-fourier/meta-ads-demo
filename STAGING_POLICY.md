# AdStac.kr Reality Matrix

This document defines which actions are real, simulated, local-only, archived, or resettable across backend modes. It is intended to remove ambiguity during development, demos, QA, and internal testing. The current backend includes real Meta reads, local persistence flows, and some write-capable paths, so this matrix distinguishes those behaviors explicitly.

## Core terms

Use these terms consistently in code, UI copy, and internal discussion.

- **External read** — Sends a real request to Meta and reads data without intentionally changing Meta state.
- **External write** — Sends a real request to Meta that can change object state or create assets.
- **Local write** — Writes only to AdStac.kr storage such as SQLite tables like `ad_insights`, `ad_creative_structures`, `suggested_configurations`, or connection records.
- **Simulated** — Returns fabricated or overridden values via demo provider behavior or masking logic rather than representing the underlying live truth exactly.
- **Soft delete / archive** — Keeps a record but marks it non-current, inactive, missing, rejected, replaced, or otherwise no longer primary.
- **Hard reset / wipe** — Intentionally removes local development or test state so the environment can be re-created cleanly.

## Modes

The backend currently supports at least two broad behavior families: `APP_MODE=live|demo` and a masking layer controlled by `MASK_MODE` plus individual masking flags. These should be treated as user-visible operating modes even if the implementation combines them from multiple environment variables.

### Live mode

`APP_MODE=live` means the backend uses real Meta credentials from `meta_connections` and calls the real Meta APIs for reads and any enabled write actions. If masking is disabled, the responses are pass-through and pause/resume writes can reach Meta directly.

### Demo mode

`APP_MODE=demo` means the backend can return demo credentials (`demo-token`, `demo-account`) and use a demo provider path rather than real account credentials. Demo mode should be treated as simulated-by-default unless a specific action is explicitly documented as still real.

### Masked live mode

Masked live mode is the most important hybrid: reads may still hit live Meta, but selected fields are overridden on the way out, such as campaign status, ad status, daily budgets, or metrics. In this mode, the system may be using real upstream data transport while presenting simulated business truth to the app and user. Masked live mode can override campaign status, ad status, budgets, and metrics independently. In particular, `MASK_AD_STATUSES=true` forces ad statuses returned by `fetch_ads()` to `ACTIVE`, even when campaign-level masking is configured separately.

## Configuring masked live mode

Masking is controlled by `MASK_MODE` and a set of individual `MASK_*` flags in `backend/.env`. You do not need to set `MASK_MODE` — setting any individual flag is sufficient to activate masking.

### `MASK_MODE` values

| Value | Effect |
|---|---|
| `off` (default) | No masking. All data is passed through from Meta as-is. |
| `selective` | Masking is off unless individual `MASK_*` flags are explicitly enabled. |
| `full` | All mask flags are enabled automatically. |

### Individual flags

| Flag | Effect |
|---|---|
| `MASK_STATUS=true` | Forces all campaign statuses → `ACTIVE` |
| `MASK_BUDGETS=true` | Replaces `daily_budget` with a deterministic demo value |
| `MASK_METRICS=true` | Synthesizes insights for campaigns with no/low real delivery (impressions < 100); preserves real metrics otherwise |
| `MASK_PAUSE_RESUME=true` | Makes pause/resume calls a no-op — returns success without hitting Meta |
| `MASK_AD_STATUSES=true` | Forces all ad statuses → `ACTIVE` (also implied by `MASK_STATUS`) |

### `METRIC_PROFILE`

When `MASK_METRICS=true`, synthetic metrics are generated deterministically per campaign ID (same campaign always gets the same numbers across restarts). `METRIC_PROFILE` controls their magnitude:

| Value | Effect |
|---|---|
| `healthy` (default) | High impressions, good CTR and CPC |
| `stable` | Moderate delivery |
| `weak` | Low impressions, poor CTR — useful for testing low-delivery stories |

### Typical demo setup

```env
MASK_MODE=selective
MASK_STATUS=true
MASK_BUDGETS=true
MASK_METRICS=true
MASK_PAUSE_RESUME=true
MASK_AD_STATUSES=true
METRIC_PROFILE=healthy
```

## Reality matrix

| Action | Live mode | Demo mode | Masked live mode | Mutation type | Risk level | Notes |
|---|---|---|---|---|---|---|
| `GET /me/meta-status` | Local read from `meta_connections`. | Local/demo semantics only. | Same as live. | Local read | Low | No Meta request is required. |
| `GET /auth/meta/login-url` | Real OAuth entrypoint generation. | Usually not needed in pure demo mode. | Same as live. | Local write/read flow | Medium | Starts connection flow but does not itself spend money. |
| `GET /auth/meta/callback` | Real OAuth token exchange and local connection persistence. | Usually bypassed in demo setups. | Same as live. | External read + local write | Medium | Stores access token and selected ad account locally. |
| `GET /api/campaigns` | Real Meta read for campaigns and metrics. | Simulated/demo output if demo provider is used. | Real read with possible status, budget, and metric overrides. | External read or simulated | Low | Safe from spend by itself, but not necessarily truthful in masked mode. |
| `GET /api/ads` | Real Meta read for ads and creatives. | Simulated/demo output if demo provider is used. | Real read with possible forced ad statuses. | External read or simulated | Low | Does not create spend by itself. |
| `GET /api/ingest/preview` | Real read of campaigns and ads; no persistence. | Simulated or demo-backed preview. | Real read with masked values possible; still no persistence. | External read or simulated | Low | Best inspection endpoint for safe verification. |
| `POST /api/ingest` | Real Meta read, then local insert into `ad_insights`. | Simulated/demo read, then local insert into `ad_insights`. | Real Meta read with masked metrics possible, then local insert. | External read + local write | Low | No external Meta state change; may persist synthetic metrics locally in masked/demo mode. |
| `POST /api/ingest/structure/{campaign_id}` | Real Meta read, then local upsert into `ad_creative_structures`. | Simulated/demo-backed structure ingest if supported by provider. | Real read with statuses potentially overridden before persistence. | External read + local write | Low | Missing previously seen ads are marked `missing`, not deleted. |
| `GET /api/structure/{campaign_id}` | Local read of persisted structure. | Local read of persisted structure. | Same as live. | Local read | Low | Returns the current local truth, which may itself have been built from simulated inputs. |
| `GET /api/explore` | Real raw Meta read of campaigns, adsets, and ads. | Simulated/demo-backed raw explorer if provider supports it. | Real read with masking depending on provider path. | External read or simulated | Low | No DB persistence. |
| `GET /api/campaigns/{campaign_id}/history` | Local read from `ad_insights`. | Local read from demo-ingested history if present. | Same as live. | Local read | Low | History reflects whatever was previously saved, including masked/demo values. |
| `POST /api/campaigns/{campaign_id}/pause` | Real Meta write unless intercepted. | Should be treated as simulated or disabled in demo contexts. | No-op only if `MASK_PAUSE_RESUME=true`; otherwise still real. | External write or simulated | High | State-changing operation with direct platform impact. |
| `POST /api/campaigns/{campaign_id}/resume` | Real Meta write unless intercepted. | Should be treated as simulated or disabled in demo contexts. | No-op only if `MASK_PAUSE_RESUME=true`; otherwise still real. | External write or simulated | High | Resuming delivery can create real cost exposure depending on the campaign. |
| `GET /api/suggestions` | Local read from `suggested_configurations`. | Same. | Same. | Local read | Low | No Meta side effects. |
| `POST /api/suggestions` | Local insert into `suggested_configurations`. | Same. | Same. | Local write | Low | Stores suggested configurations only. |
| Suggestion confirmation that only updates deployment status | Local-only mutation if implemented as DB status transition. | Same. | Same. | Local write | Low | Safe unless it invokes asset creation logic. |
| `_clone_dynamic_to_static_ad(...)` and any route that calls it | Real Meta creative creation and ad creation. | Should be considered simulated or blocked unless explicitly documented otherwise. | Potentially still real unless separately blocked; masking shown does not guarantee asset-creation no-op. | External write | High | Creates ad creative and ad objects in Meta, even though created ads are initialized as `PAUSED`. |

## Delete and archive semantics

Delete behavior should be documented separately from resets because the product intent is archival where possible rather than destructive erasure. The current backend already follows this pattern for some ingested structure data by marking records as `missing` when an ad no longer appears in a new fetch.

### Implemented archival behavior

The clearest implemented archive-like behavior today is in `ad_creative_structures`: during structure ingest, previously ingested `ad_id` values that are absent from the current fetch are updated to `lifecycle_status = 'missing'` instead of being removed. The same table also carries `active` and `inactive` lifecycle states, which means the local model already supports “kept but no longer current” semantics.

`suggested_configurations` also includes archival-like deployment states such as `rejected`, `replaced_static`, `created_static`, and `active_static`, which are status transitions rather than destructive deletes. Those should be treated as soft lifecycle changes, not as data removal.

### Recommended delete vocabulary

Use the following terms consistently in documentation and eventually in the product UI.

| Term | Meaning | Storage expectation |
|---|---|---|
| Archive | Hide from active workflows but retain full record. | Row remains; status changes only. |
| Missing | Previously ingested object is no longer returned by current upstream fetch. | Row remains; `lifecycle_status='missing'`. |
| Inactive | Object still exists but is not currently live/serving. | Row remains; status/lifecycle marks inactive. |
| Rejected | Human or system declined to use a suggestion. | Row remains in `suggested_configurations`. |
| Replaced | Older suggestion or derived object has been superseded. | Row remains with replacement status. |
| Hard delete | Physically remove data. | Only use for explicit reset/wipe operations. |

### Recommended delete rules

- Ingested Meta-derived records should default to archive semantics rather than hard delete.
- “No longer returned by Meta” should map to `missing`, not deletion.
- “User no longer wants this suggestion” should map to `rejected` or archived, not deletion.
- Hard delete should be reserved for dev/test reset actions, privacy-required removal, or deliberate administrative cleanup.

## Reset semantics

Reset behavior should be documented by scope. The user preference is for throwaway but thoughtfully structured dev data with easy resets, while still avoiding accidental loss of useful business history in normal flows.

### Reset levels

| Reset type | What it does | Meta impact | Data impact | Recommended use |
|---|---|---|---|---|
| Soft reset | Clears derived local artifacts only, such as snapshots, normalized structures, and suggestions. | None | Removes or archives local working data only. | Routine QA reruns, ingest retesting, demo cleanup. |
| Connection reset | Removes local Meta connection state such as `meta_connections` and OAuth state. | None directly | Requires reconnect before further live reads. | Account switching, broken token recovery, dev cleanup. |
| User workspace reset | Clears one user’s local AdStac.kr workspace, including insights, structures, suggestions, and optional connection state. | None directly | Recreates “fresh install” experience for that user. | Integration testing and onboarding rehearsals. |
| Full local wipe | Clears all local dev/test tables for the environment. | None directly | Destroys local environment state for all users in that environment. | Rebuild from scratch in dev only. |
| External reset | Changes Meta objects directly, such as pausing campaigns or replacing ads. | Real Meta effect | External platform state changes. | Should never be part of a generic “reset” unless explicitly named and confirmed. |

### Planned reset targets

The following local entities are good candidates for reset operations or admin scripts because they are local artifacts or connection state already represented in the current schema.

- `ad_insights` — stored metric snapshots from ingest.
- `ad_creative_structures` — normalized creative structure derived from Meta reads.
- `suggested_configurations` — stored suggestions and deployment lifecycle statuses.
- `meta_connections` — local storage for access token, ad account id, and connection metadata.
- `oauth_states` — transient connection-flow state.

### Reset rules

- Resets should be local-only by default.
- Anything that can mutate Meta should never be labeled simply “reset”; it should be called out as an external write.
- Reset endpoints or scripts should be documented by scope: one campaign, one user, one account, or full environment.
- A full wipe should only exist in development or explicitly non-production environments.

## Safe defaults by mode

These defaults reduce ambiguity and cost risk while keeping the product useful for demos and integration testing.

### Live mode defaults

- Allow real reads.
- Allow local ingest and structure persistence.
- Require explicit confirmation for any Meta write.
- Label pause/resume and asset creation paths as **real external writes** in UI and docs.

### Demo mode defaults

- Treat all campaign/ad/metric truth as simulated unless explicitly noted otherwise.
- Allow local persistence for testing downstream flows.
- Disable or stub all external writes by default.

### Masked live mode defaults

- Treat campaign status, ad status, budgets, and metrics as potentially simulated even when upstream reads are real.
- Mark any persisted outputs derived from masked responses as “locally stored from masked/live source” in future provenance documentation or metadata.
- Keep pause/resume blocked with `MASK_PAUSE_RESUME=true` unless an operator intentionally wants real control-plane behavior.

## Recommended implementation notes

This document can remain documentation-only at first, but it will be more durable if eventually mirrored in code-level metadata. A future version should expose mode and action semantics through a small internal policy object or admin endpoint so the frontend, backend, and docs all share the same definitions.

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

That would turn the current documentation into enforceable runtime truth over time.

## Current practical summary

At present, the backend already supports a meaningful distinction between safe read flows, local-only persistence, simulated masking, and real external writes. What is still missing is not the concept, but the single explicit source of truth that tells operators and developers which category each action falls into in each mode.

Until that source is implemented in code, this document should be treated as the canonical internal reference.

---

## Legacy Masking Env Vars

These env vars activate the masking layer described above. They live in `backend/.env`.

Rule of thumb: **fake what costs money, keep everything else real.**

Masking activates when `MASK_MODE=selective|full` **or** when any individual `MASK_*` flag is `true`.

### Meta masking

| Variable | Values | Description |
|---|---|---|
| `MASK_MODE` | `off` \| `selective` \| `full` | Coarse switch. `full` enables all flags; `selective` leaves them off unless individually set. |
| `MASK_STATUS` | `true\|false` | Force campaign status → `ACTIVE` |
| `MASK_BUDGETS` | `true\|false` | Replace `daily_budget` with a deterministic demo value |
| `MASK_METRICS` | `true\|false` | Synthesize insights for campaigns with impressions < 100; preserve real metrics otherwise |
| `MASK_PAUSE_RESUME` | `true\|false` | pause/resume → no-op |
| `MASK_AD_STATUSES` | `true\|false` | Force ad status → `ACTIVE` (also implied by `MASK_STATUS`) |
| `REAL_ASSET_CREATION` | `true\|false` | Reserved for future use — not consulted by any route yet |
| `METRIC_PROFILE` | `healthy` \| `stable` \| `weak` | Synthetic metric profile (default `healthy`) |

Synthetic metrics are deterministic per campaign ID. Values are internally consistent (`ctr = clicks/impressions`, `cpm = spend/impressions*1000`, `cpc = spend/clicks`).

Routes that always hit Meta directly (never masked): `/auth/meta/callback`, `/api/explore`, structural ingest (`_fetch_campaign_structure`), static ad creation (`_clone_dynamic_to_static_ad`).

### Google masking

| Variable | Values | Description |
|---|---|---|
| `GOOGLE_APP_MODE` | `demo` \| _(unset)_ | Force `GoogleDemoProvider` regardless of `APP_MODE` |
| `GOOGLE_MASK_MODE` | `off` \| `selective` \| `full` | Coarse switch; same semantics as Meta |
| `GOOGLE_MASK_STATUS` | `true\|false` | Force campaign status → `ACTIVE` |
| `GOOGLE_MASK_BUDGETS` | `true\|false` | Replace `daily_budget` with a deterministic synthetic value |
| `GOOGLE_MASK_METRICS` | `true\|false` | Synthesize insights for campaigns with impressions < 100 |
| `GOOGLE_MASK_PAUSE_RESUME` | `true\|false` | pause/resume → no-op |
| `GOOGLE_METRIC_PROFILE` | `healthy` \| `stable` \| `weak` | Synthetic metric profile (default `healthy`) |

