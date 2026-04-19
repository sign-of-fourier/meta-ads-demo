# Quick Start — AdStac.kr

## 1 — Sign up

Go to the app URL and click **Sign Up**. Enter your email and a password. You'll be logged in automatically.

---

## 2 — Connect your Meta Ads account

1. Click **Settings** in the top nav
2. Click **Connect Meta Account**
3. Complete the Meta login and permissions flow
4. You'll be redirected back — you should see your account listed as connected

---

## 3 — Import your campaigns

1. Click **Campaigns** in the nav
2. Click **Preview and Ingest** — this shows your campaigns and their last 7 days of metrics
3. Click **Confirm Ingest** to save the snapshot

Campaigns that haven't run ads recently will show blank metrics — that's normal.

---

## 4 — Import creative components

For each campaign you want to optimise:

1. Find the campaign row and click **Creatives**
2. Click **Ingest Structure**

This imports all the headlines, body texts, descriptions, and images from your dynamic ad into AdStac.kr. It also generates text embeddings for every possible combination of your copy in the background (this takes a few seconds).

---

## 5 — Verify your creatives were imported

After ingesting, expand the **Creatives** panel for your campaign. You should see your ad listed with its component slots — headlines, primary texts, descriptions, and images.

---

## 6 — Get AI-recommended combinations (BO)

Once your creatives are imported, AdStac.kr can recommend which headline + body text + description combinations are most likely to perform well, using Bayesian Optimisation over your ad's embedding space.

This improves as more real performance data is added. On first run it returns two combinations to test — one selected by Expected Improvement, one by a diversity step.

*(This feature is currently accessed via the API — UI coming soon.)*
