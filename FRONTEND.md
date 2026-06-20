# Frontend Reference

React + Vite app in `frontend/src/`. No test infrastructure (no Jest/Vitest).

## Pages and components

| File | Role |
|---|---|
| `App.jsx` | Root layout; React Router `<Outlet>`; fetches `GET /me` on load; exposes user via `UserContext`; nav: Settings / Dashboard / Ad Library / Explorer |
| `UserContext.js` | `UserContext` + `useUser()` hook; default tier `"free"` |
| `pages/AuthPage.jsx` | Signup / login |
| `pages/SettingsPage.jsx` | Meta + Google OAuth connect; reads `?meta_connected`, `?google_error`, `?google_pick=<key>` params; on `google_pick` shows account picker (radio + manual customer ID + optional login customer ID) |
| `pages/DashboardPage.jsx` | Three-section unified dashboard: ① Sync (blue) — Meta + Google sync bars; ② Select ads (purple) — `UnifiedCampaignsTable` with checkboxes; ③ Run Adstac.kr (green) — `BatchPanel`. Selection persisted to localStorage. After push, auto-re-ingests campaigns that contained the selected ads. Enriches every BO pick with `platform` + `seed_ad_id` at `setBoState` time so push is self-contained per pick. |
| `pages/CampaignsPage.jsx` | Meta campaigns table (legacy per-platform view; still accessible) |
| `pages/GoogleCampaignsPage.jsx` | Google campaigns table (legacy per-platform view; still accessible) |
| `pages/AdsPage.jsx` | Local ad library; card grid with source/status badges; click → detail modal (image grid + text slots); delete with confirm |
| `pages/ExplorerPage.jsx` | Raw Meta API explorer (debug) |
| `components/BOPickCard.jsx` | Shared pick card for Meta + Google; 2-per-row grid; click card → Preview modal (portal); lifecycle button states: **Push and Run** → **Activate** → **Running ✓** → **Testing… N impr.** (amber) → **Running ✓ — Tested** (green); `current_impressions` + `converged` from server via `_enrich_pick` |
| `components/BatchPanel.jsx` | Section 3 panel: chip list of selected ads (Optimize / Include in pool sections), top_n + metric dropdowns, Run Adstac.kr button. `GeneratorResults` (single platform) and `CrossPlatformResults` (mixed) both render `BatchPushFooter`. `BatchPushFooter` reads `pick.platform` + `pick.seed_ad_id` directly — no external platform context needed. Google picks are skipped with a note. |
| `components/UnifiedCampaignsTable.jsx` | Combined Meta + Google campaign table; Platform badge column; accordion expand → `CampaignRow`; per-ad checkboxes |
| `components/CampaignRow.jsx` | Single campaign row + expanded ad rows. All ad types show `ad_id` as identifier (consistent); Clone badge distinguishes pushed clones. `canSelect`: all non-clone ads get checkboxes (templates + statics). `CloneStatusBadge` labels use "Adstac.kr" prefix. |

## Campaign page panels (per-campaign row)

- **Sync button** — `POST /api/push` then `POST /api/ingest`; shows last-synced timestamp; amber note if Meta dev-mode blocks push
- **Ingest / Reingest** — `POST /api/ingest/structure/<id>`; label tracked in localStorage `ingestedIds`
- **Get Recommendations** — triggers BO; amber note when `scored_count=0` (picks are random)
- **Static Text Ads** — `POST /api/generate/text/{campaign_id}`; synchronous; shows generated text per slot
- **Dynamic Ad (AI Images)** — `POST /api/generate/dynamic/{campaign_id}`; async; polls every 5s; shows 4-image grid when complete
- **Seed test data** — `n` input + Seed button → `POST /api/bo/seed-scored-variants`; requires combination embeddings first
- **Structure panel** — ingested creative slots + lifecycle badges
- **Suggestions panel** — pending/confirmed suggestions with Confirm Create button

State keys in `CampaignsPage.jsx`: `boStateById`, `genStateById` (static), `dynJobById` (dynamic). Dynamic polling via `useEffect` watching `dynJobById` — clears interval when no jobs have `status='running'`.

## `api.js` — API call inventory

Single fetch wrapper; JWT in `localStorage`. Key functions:

`getMe`, `runBO`, `generateTextAds`, `startDynamicGeneration`, `getDynamicGenStatus`, `getLocalAds`, `deleteLocalAd`, `getGoogleStatus`, `getGoogleLoginUrl`, `getGoogleCampaigns`, `ingestGoogleStructure`, `getGoogleStructure`, `getGooglePendingAccounts`, `selectGoogleAccount`, `generateGoogleTextAds`, `runGoogleBO`, `getGoogleBOResults`, `pushGoogleAds`, `runCrossPlatformBO` (original two-GPR), `runUnifiedCrossPlatformBO` (unified, wired to Dashboard), `createGenerator`, `runBOForGenerator`, `pushPick`, `pushMatch`, `seedScoredVariants`
