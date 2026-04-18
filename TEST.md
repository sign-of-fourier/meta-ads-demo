Setup vars
Set a base URL and token first:

bash
export API="http://localhost:8000"
export TOKEN="PASTE_YOUR_JWT_HERE"
If you need a token first, sign up or log in:

bash
curl -s -X POST "$API/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"your-password"}'
Then copy the token value into TOKEN.

Campaigns
Check campaign read behavior first, because this is the easiest place to verify masked status, budgets, and metrics.

bash
curl -s "$API/api/campaigns" \
  -H "Authorization: Bearer $TOKEN" | jq
What to expect:

With masking off: real Meta-looking status, budget, and metrics.

With selective masking on: campaign status should be "ACTIVE", daily_budget should look synthetic/plausible, and 7-day metrics should be populated from the synthetic profile.

To verify determinism, run it twice:

bash
curl -s "$API/api/campaigns" -H "Authorization: Bearer $TOKEN" | jq > /tmp/c1.json
curl -s "$API/api/campaigns" -H "Authorization: Bearer $TOKEN" | jq > /tmp/c2.json
diff /tmp/c1.json /tmp/c2.json
If masking is deterministic, the output should be identical for unchanged campaign IDs.

Ingest preview
This shows what the ingest pipeline is about to consume without writing to the DB.

bash
curl -s "$API/api/ingest/preview" \
  -H "Authorization: Bearer $TOKEN" | jq
What to expect:

Campaigns should reflect the same masked campaign read behavior if masking is enabled.

Ads should retain real creative fields but can have status forced to "ACTIVE" if ad-status masking is on.

Ingest write
This is the key check for your selective masking idea, because it proves the app is writing real DB rows from masked source data.

bash
curl -s -X POST "$API/api/ingest" \
  -H "Authorization: Bearer $TOKEN" | jq
What to expect:

campaigns_seen should reflect real fetched campaigns.

campaigns_saved should be nonzero if synthetic metrics are being injected for campaigns.

message should look normal, because the route does not know the values were masked.

Campaign history
After ingest, inspect one campaign’s stored metrics using a real campaign ID returned from /api/campaigns.

bash
export CAMPAIGN_ID="PASTE_CAMPAIGN_ID_HERE"

curl -s "$API/api/campaigns/$CAMPAIGN_ID/history?days=30" \
  -H "Authorization: Bearer $TOKEN" | jq
What to expect:

You should see persisted rows in ad_insights based on whatever the provider returned during ingest.

If masking was active, those history values should match the synthetic metrics you saw during the ingest period.

Ads
Check whether ad statuses are being masked while creative payloads remain real.

bash
curl -s "$API/api/ads" \
  -H "Authorization: Bearer $TOKEN" | jq
What to expect:

status may be forced to "ACTIVE" when status masking is enabled.

body, image_url, thumbnail_url, campaign_id, and adset_id should still look like real underlying payload data.

Pause / resume
These verify whether pause/resume is being no-op masked or truly passed through.

bash
curl -s -X POST "$API/api/campaigns/$CAMPAIGN_ID/pause" \
  -H "Authorization: Bearer $TOKEN" | jq
bash
curl -s -X POST "$API/api/campaigns/$CAMPAIGN_ID/resume" \
  -H "Authorization: Bearer $TOKEN" | jq
What to expect:

With MASK_PAUSE_RESUME=true, both should return success from your API while not actually changing Meta state.

With masking off, these should pass through to real Meta and actually affect the campaign if the account and campaign allow it.

Good env combos
Here are the most useful configurations to test:



1. Real baseline

text
APP_MODE=live
MASK_MODE=off

Expected: everything is real pass-through.

2. Selective lie layer

APP_MODE=live
MASK_MODE=selective
MASK_STATUS=true
MASK_BUDGETS=true
MASK_METRICS=true
MASK_PAUSE_RESUME=true
MASK_AD_STATUSES=true
METRIC_PROFILE=healthy

Expected: reads look healthy and active, writes to DB are real, pause/resume is fake success.

3. Weaker story

APP_MODE=live
MASK_MODE=selective
MASK_STATUS=true
MASK_BUDGETS=true
MASK_METRICS=true
METRIC_PROFILE=weak

Expected: still active, but much weaker delivery numbers.

4. Existing demo mode

APP_MODE=demo

Expected: factory returns DemoMetaProvider() and masking layer is skipped entirely.





