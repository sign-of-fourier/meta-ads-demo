const PLATFORM_LABELS = { meta: "Meta", google: "Google" };

const SLOT_LABELS = {
  headline: "Headline",
  description: "Description",
  primary_text: "Primary text",
  final_url: "Final URL",
};

function imageNameFromUrl(url) {
  if (!url) return null;
  try {
    return new URL(url, "http://x").pathname.split("/").pop() || url;
  } catch {
    return url.split("/").pop() || url;
  }
}

function CrossPlatformResults({ data }) {
  const { picks, group_stats } = data;
  return (
    <div className="cross-platform-results">
      <div className="cross-platform-stats">
        {group_stats.map((stat) => (
          <span key={`${stat.platform}-${stat.seed_ad_id}`} className="cross-platform-stat">
            <span className={`platform-badge platform-badge-${stat.platform}`}>
              {PLATFORM_LABELS[stat.platform] || stat.platform}
            </span>
            <code className="stat-ad-id">…{String(stat.seed_ad_id).slice(-6)}</code>
            {stat.scored_count} scored · {stat.candidate_count} candidates
            {stat.scored_count === 0 && (
              <span className="stat-random-note"> (random — no scored variants yet)</span>
            )}
          </span>
        ))}
      </div>
      {picks.length === 0 ? (
        <p className="history-empty">
          No candidates yet — run per-campaign recommendations first, then try again.
        </p>
      ) : (
        <div className="bo-results">
          {picks.map((pick, i) => (
            <div key={i} className="bo-pick">
              <div className="bo-pick-body">
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
                      : pick.selection_type === "modal_q_ei"
                      ? "Modal q-EI"
                      : "Random"}
                  </span>
                </div>
                {pick.combination.image_url && (
                  <p className="bo-pick-image-name">{imageNameFromUrl(pick.combination.image_url)}</p>
                )}
                <dl className="slot-list">
                  {Object.entries(pick.combination)
                    .filter(([k]) => !["image_url", "image_width", "image_height"].includes(k))
                    .map(([k, v]) => (
                      <div key={k} className="slot-row">
                        <dt>{SLOT_LABELS[k] ?? k}</dt>
                        <dd>
                          <span className="slot-value">{v || <em>—</em>}</span>
                        </dd>
                      </div>
                    ))}
                </dl>
                {pick.gpr_mean != null && (
                  <p className="bo-pick-score">
                    Score: <strong>{pick.gpr_mean.toFixed(2)}</strong>
                    {pick.ei_score != null && (
                      <> · EI: {pick.ei_score.toFixed(4)}</>
                    )}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function BatchPanel({
  selectedAds,
  onRemove,
  topN,
  onTopNChange,
  onRunBO,
  boState,
}) {
  const platformsInBatch = [...new Set(selectedAds.map((a) => a.platform))];
  const canRun = selectedAds.length >= 2;

  return (
    <div className="cross-platform-section">
      <div className="cross-platform-header">
        <div className="cross-platform-title-row">
          <h3 className="cross-platform-title">Cross-Platform Analysis</h3>
          <div className="cross-platform-readiness">
            {platformsInBatch.length === 0 ? (
              <span className="platform-readiness readiness-pending">No ads selected</span>
            ) : (
              platformsInBatch.map((p) => (
                <span key={p} className="platform-readiness readiness-ready">
                  ✓ {PLATFORM_LABELS[p] || p} (
                  {selectedAds.filter((x) => x.platform === p).length})
                </span>
              ))
            )}
          </div>
        </div>

        <p className="cross-platform-desc">
          Runs Bayesian Optimisation across all selected ads using a single unified model.{" "}
          {!canRun && (
            <span className="cross-platform-hint">
              Select at least 2 ads above to unlock this.
            </span>
          )}
        </p>

        {selectedAds.length > 0 && (
          <div className="cross-platform-batch">
            {selectedAds.map(({ platform, seed_ad_id, label }) => (
              <span key={`${platform}-${seed_ad_id}`} className="batch-chip">
                <span className={`platform-badge platform-badge-${platform}`}>
                  {PLATFORM_LABELS[platform] || platform}
                </span>
                <span className="batch-chip-label">{label || seed_ad_id}</span>
                <button
                  className="batch-chip-remove"
                  onClick={() => onRemove(platform, seed_ad_id)}
                  title="Remove from batch"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}

        <div className="cross-platform-controls">
          <label className="topn-label">
            Recommendations
            <input
              type="number"
              min={1}
              max={8}
              value={topN}
              onChange={(e) =>
                onTopNChange(
                  Math.max(1, Math.min(8, parseInt(e.target.value, 10) || 1))
                )
              }
              className="topn-input"
            />
          </label>
          <button
            className="btn-primary"
            onClick={onRunBO}
            disabled={!canRun || boState?.status === "loading"}
          >
            {boState?.status === "loading" ? "Analyzing…" : "Run Cross-Platform Analysis"}
          </button>
        </div>
      </div>

      {boState?.status === "error" && (
        <p className="error" style={{ marginTop: "0.75rem" }}>
          {boState.error}
        </p>
      )}
      {boState?.status === "done" && <CrossPlatformResults data={boState.data} />}
    </div>
  );
}
