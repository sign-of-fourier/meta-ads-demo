# Frontend Reference

React + Vite app in `frontend/src/`. No test infrastructure (no Jest/Vitest).

## Pages and components

| File | Role |
|---|---|
| `pages/landingPage.jsx` | Original landing page (preserved at `/landing-classic`) |
| `pages/LandingPageV2.jsx` | Current landing page at `/` — hero, punchy how-it-works, research-backed cards, `DashboardMockup` |
| `pages/LandingPageAlt.jsx` | Alternate landing page variant at `/b`, for A/B copy testing against `LandingPageV2` |
| `pages/GuidePage.jsx` | User-facing product guide at `/guide` — sticky sidebar nav, 7 sections, accordion FAQ, layered from "what is this" to step-by-step |
| `pages/EvidencePage.jsx` | Research/evidence page at `/evidence` — three-tier BO explanation, `BoComparisonChart`, 8 findings with citations |
| `pages/DocsPage.jsx` | In-app documentation reader at `/docs` |
| `pages/DashboardMock.jsx` | Static, public, no-auth mockup of the dashboard at top-level `/dashboard` — distinct from the real logged-in dashboard at `/app/dashboard` (`DashboardPage.jsx`) |
| `components/DashboardMockup.jsx` | Aspirational dashboard UI component used in landing page — recommendations panel, CTR sparkline, explainability bars, budget allocation chart, experiment status footer; no image dependency |
| `components/BoComparisonChart.jsx` | SVG chart: Random vs BO-512 vs BO-4096 from CHI-BAD-ADS data; confidence bands, "AdStackers operates here" callout, gap bracket |
| `App.jsx` | Root layout; React Router `<Outlet>`; fetches `GET /me` on load; exposes user via `UserContext`; nav: Settings / Dashboard / Studio. Logo (`/logo.svg`) + Big Shoulders Display wordmark with amber gradient (`.wordmark` CSS class). Tier badge renders "Free Tier" for `tier==="free"`. Ad Library and Explorer links removed (pages preserved). |
| `UserContext.js` | `UserContext` + `useUser()` hook; default tier `"beta"`; exports `tierCanWrite(tier)`, mirroring `backend/permissions.py`'s `WRITE_TIERS` (`premium`/`enterprise` only) |
| `pages/AdminPage.jsx` | Internal user-management screen at `/admin` — not in the app nav, not nested under `App`'s logged-in shell. Gated by pasting `ADMIN_API_KEY` into a one-time prompt (kept in `localStorage`, sent as `X-Admin-Key`). Table of all users with an inline tier dropdown + expiry date input + Save per row, backed by `GET/POST /api/admin/users*` |
| `pages/AuthPage.jsx` | Signup / login |
| `pages/SettingsPage.jsx` | Meta + Google OAuth connect; reads `?meta_connected`, `?google_error`, `?google_pick=<key>` params; on `google_pick` shows account picker (radio + manual customer ID + optional login customer ID) |
| `pages/DashboardPage.jsx` | Three-section unified dashboard: ① Sync (blue) — Meta + Google sync bars; ② Select ads (purple) — `UnifiedCampaignsTable` with checkboxes; ③ Run AdStackers (green) — `BatchPanel`. Selection persisted to localStorage. After push, auto-re-ingests campaigns that contained the selected ads. Enriches every BO pick with `platform` + `seed_ad_id` at `setBoState` time so push is self-contained per pick. |
| `pages/CampaignsPage.jsx` | Meta campaigns table (legacy per-platform view; still accessible) |
| `pages/GoogleCampaignsPage.jsx` | Google campaigns table (legacy per-platform view; still accessible) |
| `pages/AdsPage.jsx` | Local ad library; card grid with source/status badges; click → detail modal (image grid + text slots); delete with confirm |
| `pages/ExplorerPage.jsx` | Raw Meta API explorer (debug) |
| `pages/StudioPage.jsx` | Manual platform hub: campaign list, create/rename/delete campaigns; drills into `ManualCampaignView` |
| `components/ManualCampaignView.jsx` | Per-campaign view: list ads, create Dynamic Template or Static Ad, run BO per template, expand combos |
| `components/ManualTemplateForm.jsx` | Free-form slot builder for dynamic templates; arbitrary slot names + multiple values per slot |
| `components/ManualStaticForm.jsx` | Static ad form; pool-only or record-a-result (score + metric) modes |
| `components/CombinationScoreTable.jsx` | Cartesian combination table with inline score entry cells; BO picks highlighted; sorted: BO picks first, scored, unscored |
| `components/BOPickCard.jsx` | Shared pick card for Meta + Google; 2-per-row grid; click card → Preview modal (portal); lifecycle button states: **Push and Run** → **Activate** → **Running ✓** → **Testing… N impr.** (amber) → **Running ✓ — Tested** (green); `current_impressions` + `converged` from server via `_enrich_pick` |
| `components/BatchPanel.jsx` | Section 3 panel: chip list of selected ads (Optimize / Include in pool sections), top_n + metric dropdowns, Run AdStackers button. `GeneratorResults` (single platform) and `CrossPlatformResults` (mixed) both render `BatchPushFooter`. `BatchPushFooter` reads `pick.platform` + `pick.seed_ad_id` directly — no external platform context needed. Google picks are skipped with a note. |
| `components/UnifiedCampaignsTable.jsx` | Combined Meta + Google + Manual campaign table; Platform badge column; accordion expand → `CampaignRow`; per-ad checkboxes |
| `components/CampaignRow.jsx` | Single campaign row + expanded ad rows. `platform==='manual'` renders a simplified row (no metrics, Studio link). All ad types show `ad_id` as identifier (consistent); Clone badge distinguishes pushed clones. `canSelect`: all non-clone ads get checkboxes (templates + statics). `CloneStatusBadge` labels use "AdStackers" prefix. |

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

`adminListUsers`, `adminSetUserTier` use a separate `adminRequest()` helper (not the shared `request()`) — auth is `X-Admin-Key`, not the user's JWT, and a wrong/missing admin key must never clear the caller's own login token or redirect them to `/app/auth`.
