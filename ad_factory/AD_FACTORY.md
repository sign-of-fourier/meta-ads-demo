# ad_factory

A standalone command-line tool that creates fully-formed fake ads from a plain-English concept description. It uses the same LLM and image-generation stack as the main backend pipeline, and writes the result directly into the fake ad server's fixture files so the ads are immediately visible through all the normal fake server routes — no server restart required.

## What it does

```
concept text  →  LLM (Azure OpenAI)  →  ad copy
                                         ↓
                 image source        →  image URL
                                         ↓
                 fake_ad_server/fixtures/*.json  ←  written here
                                         ↓
                 GET /meta/.../ads   or  GAQL FROM ad_group_ad  ←  visible here
```

Given a JSON config file, `ad_factory`:

1. Resolves (or creates) the target campaign and adset / ad group in the fixture files
2. Calls Azure OpenAI to generate the ad copy from the concept
3. Gets an image URL — either a deterministic picsum placeholder or a FLUX-generated image via deAPI
4. Assembles the platform-correct ad structure and appends it to the fixture JSON
5. Prints a summary with the ad ID and the fake server URL that will return it

---

## Running

Run from the **project root**:

```bash
python -m ad_factory ad_factory/examples/meta_coffee_shop.json
python -m ad_factory ad_factory/examples/google_existing_campaign.json
```

Or call the script directly:

```bash
python ad_factory/create_ad.py my_config.json
```

The tool auto-loads `backend/.env` if it exists, so no environment setup is needed beyond that file being populated.

---

## Config JSON reference

Every config file must have `concept` and `platform`. All other keys are optional.

```json
{
  "concept":        "Specialty pour-over coffee subscription — single-origin beans, roasted fresh weekly",
  "platform":       "meta",

  "campaign_id":    "120210001",
  "campaign_name":  "Coffee Launch — Q3",

  "adset_id":       "120211001",
  "adset_name":     "Coffee Drinkers",

  "ad_name":        "Coffee Sub — AI Ad",
  "final_url":      "https://example-coffee.com/subscribe",
  "status":         "PAUSED",

  "generate_image": false,
  "image_url":      null,

  "customer_id":    "1234567890",
  "fake_server_dir": "fake_ad_server"
}
```

| Key | Required | Default | Notes |
|---|---|---|---|
| `concept` | yes | — | Plain-English description of the product or offer. This is the primary LLM prompt input. Be specific — more detail produces better copy. |
| `platform` | yes | — | `"meta"` or `"google"` |
| `campaign_id` | no | first fixture campaign | ID of an existing fixture campaign. If the ID is not in the fixtures, a new campaign is created with that ID. If omitted entirely, the first existing campaign in the fixture file is used. |
| `campaign_name` | no | `"{concept[:50]} Campaign"` | Only used when `campaign_id` is absent or points to a new campaign. |
| `adset_id` | no | first adset in campaign | Meta: adset ID. Google: ad group ID. Same resolution logic as `campaign_id`. Also accepted as `ad_group_id` for Google. |
| `adset_name` | no | `"AI Factory Ad Set"` / `"AI Factory Ad Group"` | Only used when creating a new adset / ad group. Also accepted as `ad_group_name` for Google. |
| `ad_name` | no | `"{concept[:50]} — AI Ad"` / `"— AI RSA"` | Display name of the created ad. |
| `final_url` | no | `"https://example.com"` | Landing page URL embedded in the creative. |
| `status` | no | `"PAUSED"` | `"PAUSED"` or `"ACTIVE"`. All factory ads default to PAUSED so they don't accidentally interfere with convergence metrics. |
| `generate_image` | no | `false` | `true` calls deAPI FLUX img2img. Requires `DEAPI_API_KEY` in the environment. Falls back to a placeholder on any error. |
| `image_url` | no | `null` | Explicit image URL. Overrides both `generate_image` and the placeholder. |
| `customer_id` | no | `"1234567890"` | Google only. The fake customer ID embedded in the ad's resource name path. Must match the customer ID the fake server is simulating. |
| `fake_server_dir` | no | `"fake_ad_server"` | Path to the `fake_ad_server/` directory, relative to the project root. Change this if you have a non-standard layout. |

---

## Environment variables

The tool loads these from `backend/.env` automatically. You don't need to set them separately unless you're running outside the project.

| Variable | Used when | Notes |
|---|---|---|
| `AZURE_OPENAI_KEY` | always | Required for text generation |
| `AZURE_OPENAI_ENDPOINT` | always | Your Azure OpenAI resource endpoint |
| `AZURE_OPENAI_API_VERSION` | always | Defaults to `2024-12-01-preview` |
| `AZURE_TEXT_GEN_DEPLOYMENT` | always | Defaults to `gpt-4.1-nano` |
| `DEAPI_API_KEY` | `generate_image: true` | FLUX img2img API key |
| `IMAGES_SERVE_BASE_URL` | `generate_image: true` | Base URL for the served image. Defaults to `http://localhost:8000/images`. Affects the URL stored in the ad creative. |

---

## How text is generated

`text_gen.py` makes a single Azure OpenAI chat completion call per ad. No streaming; uses `response_format: json_object` for reliable structured output.

### Meta ads

The prompt instructs the model to act as a copywriter and return four fields:

| Field | Constraint | Maps to creative field |
|---|---|---|
| `headline` | max 40 chars | `creative.title` and `link_data.name` |
| `primary_text` | 1-2 sentences | `creative.body` and `link_data.message` |
| `description` | max 30 chars | `creative.description` and `link_data.description` |
| `cta_type` | enum | `link_data.call_to_action.type` |

Valid CTA types: `SHOP_NOW`, `LEARN_MORE`, `SIGN_UP`, `GET_OFFER`, `SUBSCRIBE`, `BOOK_NOW`.

Character limits are re-enforced in Python after parsing (defensive truncation), so an over-eager model can't produce invalid output.

### Google RSA ads

The prompt instructs the model to return:

| Field | Constraint | Notes |
|---|---|---|
| `headlines` | 8-15 strings, each ≤ 30 chars | Enforced with post-parse truncation and minimum padding |
| `descriptions` | 3-4 strings, each ≤ 90 chars | Same enforcement |

Temperature is 0.8 for both platforms — high enough to produce varied copy, low enough to stay on-brand.

---

## How images are handled

`image_gen.py` provides two modes:

### Placeholder (default, `generate_image: false`)

```
https://picsum.photos/seed/<md5[:10]>/600/315
```

The seed is the first 10 hex chars of the MD5 of the lowercased concept string. This means the same concept always produces the same placeholder image — useful for reproducing a specific ad without re-running. No API call is made.

### FLUX generation (`generate_image: true`)

Uses the same deAPI img2img pipeline as the main backend:

1. Downloads the concept's placeholder image as a reference (`seed_image_url`)
2. Submits an img2img job to deAPI FLUX with the concept reworded as a visual prompt
3. Polls until complete (up to 120 seconds)
4. Downloads the result and saves it to `backend/generated_images/`
5. Returns a `localhost:8000/images/<filename>` URL

The backend server must be running on `:8000` for the image URL to resolve when ingest or embedding tasks access it. If `IMAGES_SERVE_BASE_URL` is set in `.env`, that base URL is used instead.

On any deAPI failure the tool falls back to the placeholder and continues — the ad is still written to the fixtures.

---

## How fixture injection works

The fake ad server reads all fixture files **fresh on every HTTP request** — there is no startup cache. This means writing to a fixture file while the server is running takes effect immediately for the next request.

`fixtures.py` wraps every write as a load → mutate → save cycle:

```
_load(fs_dir, "meta_ads.json")     # read current file contents
  → append the new ad dict
_save(fs_dir, "meta_ads.json", …)  # write back as formatted JSON
```

### Files modified

| Fixture file | Modified when | What is added |
|---|---|---|
| `meta_campaigns.json` | creating a new Meta campaign | One campaign object `{id, name, status, daily_budget}` |
| `meta_insights.json` | creating a new Meta campaign | One insights baseline row (zeroed) so the campaign appears in the insights feed |
| `meta_adsets.json` | creating a new Meta adset | One adset object under the campaign's key |
| `meta_ads.json` | always (Meta) | One ad object under the campaign's key |
| `google_campaigns.json` | creating a new Google campaign | One campaign row `{campaign, campaignBudget, metrics}` |
| `google_adgroups.json` | creating a new Google ad group | One ad group row under the campaign's key |
| `google_ads.json` | always (Google) | One ad row under the campaign's key |

New campaigns and ad groups inherit sensible defaults: `daily_budget: "5000"` for Meta, `amountMicros: "5000000000"` for Google, all statuses `ACTIVE` / `ENABLED`.

### ID generation for new entities

When an adset or ad group is created without a supplied ID, the tool generates one from the current timestamp in milliseconds (`int(time.time() * 1_000) % 10^12`). This avoids collisions with the hand-crafted fixture IDs (which are in the `120210XXX` / `876543XXXX` range) and is human-readable in log output.

Ad IDs are always UUID-derived: `adgen_<10 hex chars>`. Google ad resource names follow the canonical pattern: `customers/{customer_id}/adGroupAds/{numeric_id}`.

---

## How the fake server serves factory ads

The fake server has two sources of ads: fixture files and in-memory pushed state. Factory ads live in the **fixture files**, not in-memory state. Here is how each platform route serves them:

### Meta — `GET /meta/v19.0/act_<act_id>/ads`

```python
# routes/meta.py — _get_ads()
if campaign_id and campaign_id in all_fixture_ads:
    data = list(all_fixture_ads[campaign_id])   # ← factory ads appear here
elif campaign_id:
    data = []
else:
    data = [ad for ads in all_fixture_ads.values() for ad in ads]

pushed = state.get_meta_ads(act_id, campaign_id=campaign_id)   # pushed clones
data = data + pushed
```

Factory ads slot into the first `data` list, alongside the original hand-crafted fixtures. The pushed-clone list is appended after, so both sources are always combined.

### Google — GAQL `FROM ad_group_ad`

```python
# routes/google.py — _handle_ads()
rows = list(all_fixture_ads[campaign_id])   # ← factory ads appear here

pushed = state.get_google_ads(customer_id, campaign_id=campaign_id)
rows = rows + pushed
```

Same pattern. Factory ads are returned in the `results` array of the `searchStream` response alongside fixture and pushed ads.

---

## How factory ads differ from other ad types

There are four kinds of ads in the system. Knowing the difference matters for understanding what each type can and can't do.

| Kind | Where stored | How created | Convergence tracked? | BO-eligible? |
|---|---|---|---|---|
| **Fixture ad** | `fake_ad_server/fixtures/*.json` | Hand-written JSON | No | Yes (after ingest) |
| **Factory ad** | `fake_ad_server/fixtures/*.json` | `ad_factory` tool (LLM + optional image) | No | Yes (after ingest) |
| **Pushed clone** | `fake_ad_server/state.py` in-memory | Backend `POST /api/push` or `/api/push/pick` | Yes — `_check_*_convergence` fires at ingest | Yes |
| **Generated ad** | Backend SQLite (`generated_ads` table) | Backend dynamic/text generation pipeline | No | Yes (the source of BO candidates) |

The key distinction between **factory ads** and **pushed clones**:

- Pushed clones are created through the normal push flow: backend calls the fake server's POST endpoint, the fake server stores them in its in-memory `state.py` dictionaries, and their IDs are recorded in the backend's `pushed_ad_combos` table. This is what enables convergence tracking.
- Factory ads skip that entire flow. They are written directly to fixture files by an external tool. The backend has no record of them in `pushed_ad_combos`, so convergence checking never fires for them.

The system does not distinguish factory ads from original fixture ads at runtime. Both are returned as plain ad objects from the same endpoint. There is no special field, flag, or ID prefix that marks an ad as factory-created at the API level.

What does distinguish them in practice:

- **ID prefix**: factory ad IDs start with `adgen_` (`adgen_609ac9a7a7`). Original fixture IDs use short numeric strings (`120212001`). This is a convention, not enforced by the fake server.
- **`object_story_spec` completeness**: factory ads always include a full `object_story_spec` with `page_id`, `link_data`, and `call_to_action`. Some original fixture ads use a flatter creative structure.
- **Persistence**: fixture file writes survive a fake server restart. Pushed clone state (in-memory) is lost on restart.

---

## How the backend ingests factory ads

When you run `POST /api/ingest/structure/{campaign_id}` (Meta) or `POST /api/google/ingest/structure/{campaign_id}` (Google) against a campaign that contains factory ads, the backend processes them identically to any other ad in the campaign. Specifically:

- The ad's creative is parsed by `_normalize_creative` / `google_provider.normalize_creative`, which detects `asset_feed_spec` (dynamic) vs. flat creative fields (static). Factory Meta ads are static — they have `title`/`body`/`description` at the top level of `creative`, not an `asset_feed_spec`. Factory Google ads are RSAs.
- The ingested structure is written to `ad_creative_structures` in the backend's SQLite database with `platform='meta'` or `platform='google'`.
- Clone detection runs: the backend checks if the ad's ID is in `pushed_ad_combos.platform_ad_id`. For factory ads it won't be, so they are treated as native/seed ads, not pushed clones.
- Embedding tasks fire for each ingested ad (`embed_ad`, `embed_images`, `embed_all_combinations`).
- After embedding, the ad becomes eligible for BO as a seed ad.

Factory ads are therefore a fast way to populate the fake server with seeded ads that are ready to run through the full ingest → generate → BO → push flow.

---

## Module structure

```
ad_factory/
  __init__.py           package marker + one-line docstring
  __main__.py           enables `python -m ad_factory`; delegates to create_ad.main()
  create_ad.py          CLI entry point; orchestrates the three steps per platform
  text_gen.py           Azure OpenAI calls; platform-specific prompts and enforcement
  image_gen.py          placeholder URL or deAPI FLUX img2img with polling
  fixtures.py           load/mutate/save cycle for all six fixture JSON files
  requirements.txt      minimal deps (openai, python-dotenv, requests)
  examples/
    meta_coffee_shop.json        inject into an existing campaign
    meta_new_campaign.json       create a brand-new campaign + adset
    google_existing_campaign.json
    google_new_campaign.json
```

`ad_factory` has no dependency on the FastAPI app, the backend database, or the fake server process. It only needs:

1. The `fake_ad_server/fixtures/` directory (to write into)
2. `backend/.env` (for credentials — auto-loaded)
3. The `openai` and `python-dotenv` packages (already in the backend's Python environment)

For `generate_image: true` it also imports from `backend/ad_generation/` at runtime by adding `backend/` to `sys.path`. This import happens lazily inside `image_gen.generate_with_deapi()`, so the package loads fine without it.

---

## Examples

### Inject a static Meta ad into an existing campaign

```json
{
  "concept": "Specialty pour-over coffee subscription — single-origin beans roasted fresh weekly, delivered to your door",
  "platform": "meta",
  "campaign_id": "120210001",
  "final_url": "https://example-coffee.com/subscribe",
  "ad_name": "Coffee Sub — AI Ad",
  "status": "PAUSED"
}
```

```bash
python -m ad_factory ad_factory/examples/meta_coffee_shop.json
```

Output:
```
ad_factory: platform=meta  concept='Specialty pour-over coffee subscription …'
Generating Meta ad copy …
Resolving image …

Meta ad written to fixtures:
  ID:           adgen_3f2a1b4c9d
  Name:         Coffee Sub — AI Ad
  Campaign:     120210001
  Adset:        120211001
  Status:       PAUSED
  Headline:     Freshness in Every Cup
  Body:         Hand-selected single-origin beans roasted the day your box ships.
  Description:  First bag free
  CTA:          SUBSCRIBE
  Image:        https://picsum.photos/seed/0d504847d1/600/315

Visible at (fake server running on :9000):
  GET /meta/v19.0/act_<your_act_id>/ads?filtering=[{"field":"campaign.id","operator":"EQUAL","value":"120210001"}]
```

### Create a new Google campaign with an RSA

```json
{
  "concept": "Electric cargo bike for urban deliveries — 100km range, 200kg payload, zero emissions",
  "platform": "google",
  "campaign_name": "EcoCargo — Search Campaign",
  "adset_name": "Urban Logistics",
  "final_url": "https://ecocargo.example.com/fleet",
  "customer_id": "1234567890"
}
```

The tool will create a new entry in `google_campaigns.json` and `google_adgroups.json` before writing the ad.

### Use a FLUX-generated image

```json
{
  "concept": "Minimalist leather wallet with RFID blocking — slim, premium, functional",
  "platform": "meta",
  "campaign_id": "120210002",
  "final_url": "https://wallet.example.com",
  "generate_image": true
}
```

Requires `DEAPI_API_KEY` to be set in `backend/.env`. Takes up to 2 minutes for the image job. Falls back to a picsum placeholder automatically if the job fails or times out.

---

## Limitations and known constraints

- **Ephemeral vs. persistent**: Fixture file writes persist across fake server restarts. The fake server's in-memory pushed-clone state (`state.py`) does not. If you need factory ads to survive after clearing and restarting the server, they do — because they live in files, not memory.

- **`meta_insights.json` baseline row**: When a new Meta campaign is created, a zeroed insights row is added to `meta_insights.json`. The `_get_insights` route only returns metrics for campaigns listed in that file. The `campaign_metrics()` function generates live evolving numbers from an MD5 seed, so new campaigns show real-looking metrics immediately after the row is added.

- **Google campaign metrics**: New Google campaigns are added to `google_campaigns.json` with `"impressions": "0"` in the initial metrics object. The `_handle_campaigns` route replaces these with live evolving numbers from `campaign_metrics()` keyed by campaign ID, so they will show non-zero numbers immediately.

- **No BO training data**: Factory ads created without `generate_image: true` will have `scored_count=0` when BO is run after ingest. This is the same as any fresh native ad — picks will be random. Run `POST /api/bo/seed-scored-variants` after ingest to inject synthetic scores, or run the generation pipeline to get Qwen2-VL scores.

- **No adset mapping in fake server state**: `state.py` builds an `_adset_to_campaign` lookup at server startup from the fixture files. New adsets created by `ad_factory` after the server starts will not be in this lookup. This only matters for pushed clones — `store_meta_ad()` uses this lookup to set the clone's `campaign_id`. It does not affect factory ads (which are written directly to `meta_ads.json` with `campaign_id` already set) or for reading fixture ads.

- **No deduplication**: Running the same config twice creates two separate ads with different UUIDs. The fixtures accumulate entries. If you want to reset to a clean state, restore the fixture files from git: `git checkout fake_ad_server/fixtures/`.
