import { useState } from "react";
import CampaignsPage from "./CampaignsPage.jsx";
import GoogleCampaignsPage from "./GoogleCampaignsPage.jsx";
import { runCrossPlatformBO } from "../api.js";

const SLOT_LABELS = {
  headline: "Headline",
  description: "Description",
  primary_text: "Primary text",
  final_url: "Final URL",
};

const PLATFORM_LABELS = { meta: "Meta", google: "Google" };

/* ── Cross-platform results panel ───────────────────────────────────────── */
function CrossPlatformResults({ data }) {
  const { picks, group_stats } = data;

  return (
    <div className="cross-platform-results">
      {/* Per-platform stats */}
      <div className="cross-platform-stats">
        {group_stats.map((stat) => (
          <span key={`${stat.platform}-${stat.seed_ad_id}`} className="cross-platform-stat">
            <span className={`platform-badge platform-badge-${stat.platform}`}>
              {PLATFORM_LABELS[stat.platform] || stat.platform}
            </span>
            {stat.scored_count} scored · {stat.candidate_count} candidates
          </span>
        ))}
      </div>

      {picks.length === 0 ? (
        <p className="history-empty">
          No candidates found — run per-campaign recommendations on each platform first to build
          up scored data, then try cross-platform analysis again.
        </p>
      ) : (
        picks.map((pick, i) => (
          <div key={i} className="bo-pick">
            <div className="bo-pick-header">
              <span className="bo-pick-label">Recommendation {i + 1}</span>
              <span className={`platform-badge platform-badge-${pick.platform}`}>
                {PLATFORM_LABELS[pick.platform] || pick.platform}
              </span>
              <span className="bo-pick-type">
                {pick.selection_type === "ei"
                  ? "Best expected"
                  : pick.selection_type === "fantasy"
                  ? "Exploratory"
                  : "Random"}
              </span>
            </div>
            <dl className="slot-list">
              {Object.entries(pick.combination)
                .filter(([k]) => k !== "image_url")
                .map(([k, v]) => (
                  <div key={k} className="slot-row">
                    <dt>{SLOT_LABELS[k] ?? k}</dt>
                    <dd><span className="slot-value">{v || <em>—</em>}</span></dd>
                  </div>
                ))}
            </dl>
            {pick.gpr_mean != null && (
              <p className="bo-pick-score">
                Predicted score: <strong>{pick.gpr_mean.toFixed(2)}</strong>
                {pick.ei_score != null && <> &nbsp;·&nbsp; EI: {pick.ei_score.toFixed(4)}</>}
              </p>
            )}
          </div>
        ))
      )}
    </div>
  );
}

/* ── Main dashboard ─────────────────────────────────────────────────────── */
export default function DashboardPage() {
  const [metaOpen, setMetaOpen] = useState(true);
  const [googleOpen, setGoogleOpen] = useState(true);

  // Seed ad IDs surfaced by child components after ingest
  // Persisted to localStorage so they survive page refresh
  const [metaSeedAdId, setMetaSeedAdId] = useState(
    () => localStorage.getItem("meta_last_seed_ad_id") || null
  );
  const [googleSeedAdId, setGoogleSeedAdId] = useState(
    () => localStorage.getItem("google_last_seed_ad_id") || null
  );

  // Cross-platform BO state
  const [crossBoState, setCrossBoState] = useState(null); // null | { status, data?, error? }

  function handleMetaIngest(seedAdId) {
    localStorage.setItem("meta_last_seed_ad_id", seedAdId);
    setMetaSeedAdId(seedAdId);
  }

  function handleGoogleIngest(seedAdId) {
    localStorage.setItem("google_last_seed_ad_id", seedAdId);
    setGoogleSeedAdId(seedAdId);
  }

  async function handleCrossPlatformBO() {
    setCrossBoState({ status: "loading" });
    try {
      const pairs = [
        metaSeedAdId && { platform: "meta", seed_ad_id: metaSeedAdId, text_source_id: metaSeedAdId },
        googleSeedAdId && { platform: "google", seed_ad_id: googleSeedAdId, text_source_id: googleSeedAdId },
      ].filter(Boolean);

      const result = await runCrossPlatformBO(pairs);
      setCrossBoState({ status: "done", data: result });
    } catch (err) {
      setCrossBoState({ status: "error", error: err.message });
    }
  }

  const bothReady = !!(metaSeedAdId && googleSeedAdId);

  return (
    <div className="dashboard-page">
      <div className="dashboard-header">
        <h2 className="dashboard-title">Ad Ingestion Dashboard</h2>
      </div>

      <div className="page-hint-banner">
        <p style={{ margin: "0 0 0.4rem", fontWeight: 600, color: "#333" }}>How to use this page</p>
        <ol style={{ margin: 0, paddingLeft: "1.4rem", lineHeight: 2, fontSize: "0.88rem" }}>
          <li>
            <strong>Sync</strong> — pulls your latest campaigns and metrics. Also pushes any
            generated ads. Do this first on each platform.
          </li>
          <li>
            <strong>Ingest</strong> a campaign — reads the individual ads inside it so Adstac.kr
            can learn from them. The row expands automatically.
          </li>
          <li>
            <strong>Get Recommendations</strong> (inside the row) — the AI suggests which ad
            combinations to test next, for that specific campaign.
          </li>
          <li>
            <strong>Cross-Platform Analysis</strong> (below) — once you've ingested at least one
            campaign from each platform, run this to find the best opportunities across Meta
            and Google together.
          </li>
        </ol>
      </div>

      {/* ── Cross-Platform Analysis ──────────────────────────────────────────── */}
      <div className="cross-platform-section">
        <div className="cross-platform-header">
          <div className="cross-platform-title-row">
            <h3 className="cross-platform-title">Cross-Platform Analysis</h3>
            <div className="cross-platform-readiness">
              <span className={`platform-readiness ${metaSeedAdId ? "readiness-ready" : "readiness-pending"}`}>
                {metaSeedAdId ? "✓ Meta" : "○ Meta"}
              </span>
              <span className={`platform-readiness ${googleSeedAdId ? "readiness-ready" : "readiness-pending"}`}>
                {googleSeedAdId ? "✓ Google" : "○ Google"}
              </span>
            </div>
          </div>
          <p className="cross-platform-desc">
            Runs Bayesian Optimisation across both platforms using a shared scoring model — so a
            Meta headline and a Google headline are ranked on the same scale. Returns the top
            opportunities from either channel.{" "}
            {!bothReady && (
              <span className="cross-platform-hint">
                Ingest at least one campaign from each platform to unlock this.
              </span>
            )}
          </p>
          <button
            className="btn-primary"
            onClick={handleCrossPlatformBO}
            disabled={!bothReady || crossBoState?.status === "loading"}
            title={
              !bothReady
                ? "Ingest a campaign on both Meta and Google first"
                : "Run cross-platform Bayesian Optimisation"
            }
          >
            {crossBoState?.status === "loading" ? "Analyzing…" : "Run Cross-Platform Analysis"}
          </button>
        </div>

        {crossBoState?.status === "error" && (
          <p className="error" style={{ marginTop: "0.75rem" }}>{crossBoState.error}</p>
        )}
        {crossBoState?.status === "done" && (
          <CrossPlatformResults data={crossBoState.data} />
        )}
      </div>

      {/* ── Meta Ads ─────────────────────────────────────────────────────── */}
      <div className="platform-section">
        <button
          className="platform-section-toggle"
          onClick={() => setMetaOpen((o) => !o)}
          aria-expanded={metaOpen}
        >
          <div className="platform-section-toggle-left">
            <span className="platform-icon platform-icon-meta">f</span>
            <span className="platform-section-title">Meta Ads</span>
          </div>
          <span className="platform-section-chevron">
            {metaOpen ? "▲" : "▼"}
          </span>
        </button>

        {metaOpen && (
          <div className="platform-section-body">
            <CampaignsPage onIngest={handleMetaIngest} />
          </div>
        )}
      </div>

      {/* ── Google Ads ───────────────────────────────────────────────────── */}
      <div className="platform-section">
        <button
          className="platform-section-toggle"
          onClick={() => setGoogleOpen((o) => !o)}
          aria-expanded={googleOpen}
        >
          <div className="platform-section-toggle-left">
            <span className="platform-icon platform-icon-google">G</span>
            <span className="platform-section-title">Google Ads</span>
          </div>
          <span className="platform-section-chevron">
            {googleOpen ? "▲" : "▼"}
          </span>
        </button>

        {googleOpen && (
          <div className="platform-section-body">
            <GoogleCampaignsPage onIngest={handleGoogleIngest} />
          </div>
        )}
      </div>
    </div>
  );
}
