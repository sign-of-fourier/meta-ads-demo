 1) Bugs & test failures

  Test suite: 349 passed / 5 skipped / 0 failures when run cleanly. The 5 skips are all intentional (RUN_LIVE_MODAL_TESTS and one Google live-smoke test not enabled). I did hit test_generation_pipeline.py failing
  (5 failed, 5 errors) when run as part of the full suite — but I verified it passes standalone (1 passed in 183s). It's a real-API integration test (Azure OpenAI/deAPI/Modal) that appears to choke when run
  back-to-back with other real-API tests earlier in the suite (rate limiting/timeout, not a logic bug). Not something introduced by recent work — worth knowing it's flaky in full-suite runs, though.

  One doc bug I found while checking: backend/TEST.md claims TestGoogleMaskingProvider (6 tests) are skipped — they're not, all 26 tests in that file pass now. Stale doc line, not a real problem.

  Known/tracked gaps (TECHNICAL_DEBT.md, 15 items total):
  - BO-1 (P0): cross-platform BO doesn't model cross-platform covariance yet — biggest architectural gap, has a written plan.
  - T1, T2, T4, T5, T6, T9: clone-lifecycle edge cases (role defaults, deleted-clone handling, cross-user observation sharing, budget config, convergence-days not enforced).
  - T10: BatchSampler adoption — decided against, documented only.
  - BO-1 (P0): cross-platform BO doesn't model cross-platform covariance yet — biggest architectural gap, has a written plan.
  - T1, T2, T4, T5, T6, T9: clone-lifecycle edge cases (role defaults, deleted-clone handling, cross-user observation sharing, budget config, convergence-days not enforced).
  - T10: BatchSampler adoption — decided against, documented only.
  - T11: no Stripe webhook (tier changes are admin-only for now).
  - T12: the confidence/PCA-warning work from earlier this session — drafted, awaiting your sign-off, not a bug.
  - T13: silent failure modes in background pipelines (image gen fallback, fire-and-forget embeddings, convergence-write failures) — things fail silently instead of surfacing.
  - T14 (new, found during today's doc audit — I initially wrote it up wrong and corrected it below in point 2): Google ads display with an incorrect "Meta" badge in the ad library.
  - Plus a feature backlog (Studio text-gen, context embeddings, Google Display) and one UI nit (duplicate auto-named generators).

  Nothing on that list is "the system is broken" — it's all scoped, known, and non-blocking. The two live-user-facing bugs are T14 (below) and the recommendation-history gap in Q3.

  2) Ad Library — what it is, and the actual bug

  There are three separate things that could be called "the ad library," and I think that's the root of the confusion:

  1. AdsPage.jsx at /app/ads ("Local Ad Library") — a read-only viewer over everything in ad_creative_structures: ads ingested from Meta/Google, plus AI-generated ads. Click a card to see all slots/images; delete
  removes it locally (Meta/Google ads reappear on next sync). This is the one your "we removed the link" note refers to — it's still fully functional at that route, just unlinked from the navbar since the
  rebranding pass (CHANGES.md, "Navbar cleanup"). I found no evidence in the (squashed) git history of it ever being "a way to seed ads" specifically — that's a different, unrelated feature: POST
  /api/bo/seed-scored-variants, a button on the campaign row that writes synthetic scored observations for BO testing. If that's what you're remembering, it's still there too, just a different mechanism entirely
  from the Ads page.
  2. ExplorerPage.jsx at /app/explore — raw Meta API passthrough for debugging. Also unlinked from nav, page preserved.
  3. The Studio/manual platform (StudioPage.jsx → ManualCampaignView.jsx) — "the library of ads created manually" you're describing. This is a genuinely separate, newer system: manual_campaigns table + manual ad
  creation, its own UI, not shown in AdsPage.jsx at all currently.

  The actual bug, corrected from what I told you earlier: I initially told you ad_creative_structures had no platform column — that was wrong, I only grepped the CREATE TABLE statement and missed that it's added
  via a later ALTER TABLE migration. The column exists and every writer sets it correctly ('meta' / 'google' / 'manual'). The real, narrower bug: GET /api/ads/local's SQL SELECT doesn't include platform, and the
  LocalAd response model has no platform field — so it never reaches the frontend. AdsPage.jsx falls back to data_source (which is 'real' for both Meta and Google ingest) to pick a label, so any Google ad you've
  ingested shows an incorrect "Meta" badge. Small, contained fix (no migration needed) — I've corrected TECHNICAL_DEBT.md T14 and the other docs to reflect this.

  Separately, whether Studio's manually-created ads should ever show up in this same library is the undecided UX question already flagged in CLAUDE.md — worth deciding once, since it affects both this fix and any
  future unification.

  3) Recommendations disappear on navigation — confirmed, and it's a pure frontend gap

  You're right, and I traced exactly why. The backend already does the hard part: every BO run (/api/bo/run, /api/bo/cross-platform, /api/bo/cross-platform/unified) calls save_bo_run(), which persists picks to
  bo_selections. There's even a reader, get_latest_bo_run(), wired to GET /api/bo/results/{ad_id} and GET /api/google/bo/results/{ad_id}.

  The problem: nothing in the frontend ever calls those "get latest" endpoints. DashboardPage.jsx's boState and CampaignsPage.jsx's boStateById are plain useState(null) — populated only as the direct response of
  clicking "Get Recommendations" / "Run AdStackers," never fetched on mount. Navigate away and the component unmounts; come back and it's a blank slate, even though the exact same picks are sitting in
  bo_selections and are one GET call away. The single-ad endpoints already exist and just need to be called on page load. The cross-platform/batch case (Dashboard's main flow) is a bigger gap — there's no "get my
  last batch run" endpoint at all yet, since bo_selections is keyed per (seed_ad_id, text_source_id), not per batch. That'd need a small new query grouping by whatever set of pairs was last run for a user.

  So: single-ad "history" is a frontend-only fix; batch/cross-platform history needs a small backend addition too.

  4) Fake ad server's post-push stats loop — confirmed dead in current workflows, by design

  This one's real but I want to separate two things. The feature you're describing exists: fake_ad_server/routes/meta.py's _post_ads handler registers a pushed clone, and pushed_meta_ad_metrics()/_ramp_metrics()
  in state.py generates synthetic ramping CTR for it on subsequent /insights calls — optionally Qwen-derived. This is exactly the "Case 2 (cold start + fast demo loop)" flow documented in CLAUDE.md.

  It only activates when FAKE_META_BASE_URL/FAKE_GOOGLE_BASE_URL are set — both are commented out by default in .env.example, so out of the box the app talks to live Meta/Google. Given that, your read is correct:
  if you're avoiding real pushes to customer accounts for trust reasons, this feedback loop simply never fires in that workflow — not because it's broken, but because it was built as a local dev/demo
  convenience, not as a substitute for real customer data. The deeper implication worth naming: since new ads always push as PAUSED (never auto-activated), pushing itself is inert until someone manually activates
  the clone — but if the actual policy is "don't write anything to the customer's account at all," then for real customers today there's currently no path to real CTR ever entering scored_observations — BO stays
  on Qwen/synthetic warm-start scores indefinitely. That's not tracked anywhere in TECHNICAL_DEBT.md right now. 
