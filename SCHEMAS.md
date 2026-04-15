# Database & API Schemas

SQLite database: `backend/app.db` (auto-created on first run via `init_db()`).

---

## SQLite Tables

### `users`

Local auth accounts. Independent of Meta identity.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `email` | TEXT UNIQUE | |
| `pw_hash` | TEXT | bcrypt |
| `created_at` | TEXT | `datetime('now')` |

---

### `meta_connections`

One row per user — the connected Meta ad account.

| Column | Type | Notes |
|---|---|---|
| `user_id` | INTEGER PK | FK → `users.id` |
| `meta_user_id` | TEXT | Meta's user id |
| `access_token` | TEXT | Short-lived user token; no refresh logic |
| `ad_account_id` | TEXT | e.g. `act_123456789` |
| `connected_at` | TEXT | `datetime('now')` |

---

### `oauth_states`

CSRF state tokens for the Meta OAuth flow. Deleted after successful callback.

| Column | Type | Notes |
|---|---|---|
| `state` | TEXT PK | random `urlsafe` token |
| `user_id` | INTEGER | FK → `users.id` |
| `created_at` | TEXT | |

---

### `ad_insights`

Metric snapshots. One row per campaign per `POST /api/ingest` call. No date deduplication — rows accumulate over time.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER | FK → `users.id` |
| `ad_account_id` | TEXT | |
| `level` | TEXT | `'campaign'` (only value currently written) |
| `object_id` | TEXT | campaign_id / adset_id / ad_id depending on level |
| `date` | TEXT | `YYYY-MM-DD` — date of ingest, not date of impressions |
| `impressions` | INTEGER | 7-day aggregate at time of ingest |
| `clicks` | INTEGER | |
| `spend` | REAL | |
| `ctr` | REAL | computed: `clicks/impressions * 100` |
| `cpm` | REAL | computed: `spend/impressions * 1000` |
| `cpc` | REAL | computed: `spend/clicks` |
| `created_at` | TEXT | |

---

### `ad_creative_structures`

Normalised creative components. One row per `(user, ad, slot, slot_index)`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER | FK → `users.id` |
| `ad_account_id` | TEXT | |
| `campaign_id` | TEXT | |
| `adset_id` | TEXT | |
| `ad_id` | TEXT | Meta ad id |
| `creative_type` | TEXT | `'static'` or `'dynamic'` |
| `slot` | TEXT | `'headline'`, `'description'`, `'primary_text'`, `'image'` |
| `slot_index` | INTEGER | `0` for static; `0, 1, 2…` for dynamic variants |
| `value` | TEXT | The component text or image URL / hash |
| `ingested_at` | TEXT | ISO timestamp of last ingest |
| `lifecycle_status` | TEXT | `'active'`, `'inactive'`, or `'missing'` (see below) |
| UNIQUE | | `(user_id, ad_id, slot, slot_index)` — drives idempotent upsert |

**Lifecycle status rules** (set during `POST /api/ingest/structure/{campaign_id}`):

| Status | Condition |
|---|---|
| `active` | Ad present in latest Meta fetch AND `effective_status == 'ACTIVE'` |
| `inactive` | Ad present in latest Meta fetch BUT `effective_status != 'ACTIVE'` |
| `missing` | Ad has rows in DB but was absent from the latest Meta fetch for this campaign |

**Ingest behaviour:** `ON CONFLICT DO UPDATE` replaces `creative_type`, `value`, `ingested_at`, and `lifecycle_status` in place. Previously ingested ads absent from the current fetch are updated to `lifecycle_status = 'missing'` in a separate `UPDATE` pass.

---

### `suggested_configurations`

One row per suggested exact configuration derived from a dynamic ad template.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER | FK → `users.id` |
| `ad_account_id` | TEXT | From user's Meta connection at time of storage |
| `campaign_id` | TEXT | |
| `adset_id` | TEXT | Target adset for the static clone |
| `source_ad_id` | TEXT | The dynamic template ad this was derived from |
| `components` | TEXT | **JSON blob**: `{"slot": "chosen_value", …}` |
| `deployment_status` | TEXT | See status lifecycle below |
| `static_ad_id` | TEXT nullable | Set after a successful `action="create"` Meta deployment |
| `created_at` | TEXT | |
| `updated_at` | TEXT | Touched on every status transition |

**`components` JSON shape:**

```json
{
  "headline":     "Buy Now — Summer Sale",
  "primary_text": "Up to 50% off selected items",
  "description":  "Limited time offer",
  "image":        "https://example.com/img.jpg"
}
```

All four slots are optional; only slots with chosen values are present.

**Deployment status lifecycle:**

```
suggested
  │
  ├─ action="create" ──▶ [Meta API] ──▶ created_static   (static_ad_id set)
  │                           └── on failure: stays "suggested"
  │
  ├─ action="replace" ──▶ pending_confirmation            (Meta call not yet implemented)
  │
  └─ reject ──▶ rejected                                  (endpoint not yet implemented)

created_static ──▶ active_static    (future: user activates in Meta Ads Manager)
created_static ──▶ replaced_static  (future: replace flow)
```

---

## Pydantic / API Response Models

### `SuggestionResponse`

Returned by `POST /api/suggestions`, `POST /api/suggestions/{id}/confirm`, and `GET /api/suggestions`.

```python
{
  "id":                int,
  "source_ad_id":      str,
  "campaign_id":       str,
  "adset_id":          str,
  "components":        { slot: value },   # dict[str, str]
  "deployment_status": str,
  "static_ad_id":      str | None,
  "created_at":        str                # ISO datetime string
}
```

### `AdStructure`

Returned by `GET /api/structure/{campaign_id}` — one item per ad.

```python
{
  "ad_id":            str,
  "adset_id":         str,
  "campaign_id":      str,
  "creative_type":    "static" | "dynamic",
  "lifecycle_status": "active" | "inactive" | "missing" | None,
  "components":       { slot: [value, …] }  # dict[str, list[str | None]]
}
```

`components` values are ordered lists: single-element for static ads, multi-element for dynamic variants.

### `StructureIngestResult`

Returned by `POST /api/ingest/structure/{campaign_id}`.

```python
{
  "campaign_id":      str,
  "ads_processed":    int,
  "components_saved": int
}
```

### `Campaign`

Returned by `GET /api/campaigns`.

```python
{
  "id":              str,
  "name":            str,
  "status":          str,          # e.g. "ACTIVE", "PAUSED"
  "daily_budget":    int | None,   # in cents
  "spend_7d":        float | None,
  "impressions_7d":  int | None,
  "clicks_7d":       int | None,
  "ctr_7d":          float | None,
  "cpm_7d":          float | None
}
```

---

## Meta API Fields Used

### Campaigns (`/{ad_account_id}/campaigns`)
`id, name, status, daily_budget`

### Insights (`/{ad_account_id}/insights`)
`campaign_id, impressions, clicks, spend, ctr, cpm, cpc` — `level=campaign`, `date_preset=last_7d`

### Ads for structural ingest (`/{ad_account_id}/ads`)
`id, name, status, effective_status, campaign_id, adset_id, creative{id, name, body, title, image_url, thumbnail_url, asset_feed_spec, object_story_spec}`

**`effective_status`** is preferred over `status` for lifecycle classification because it reflects aggregate delivery state (e.g. an ad with `status=ACTIVE` whose parent campaign is paused will have `effective_status=CAMPAIGN_PAUSED`).

### Source ad fetch for clone (`/{source_ad_id}`)
`creative{page_id, object_story_spec}` — used to inherit `page_id` and destination `link` URL when creating a static clone.
