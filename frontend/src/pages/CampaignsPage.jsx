import { useEffect, useState } from "react";
import BOPickCard from "../components/BOPickCard.jsx";
import {
  getCampaigns,
  pauseCampaign,
  resumeCampaign,
  getCampaignHistory,
  runIngest,
  pushGeneratedAds,
  ingestCampaignStructure,
  getCampaignStructure,
  getCampaignSuggestions,
  confirmSuggestion,
  storeSuggestion,
  runBO,
  generateTextAds,
  startDynamicGeneration,
  getDynamicGenStatus,
  seedScoredVariants,
} from "../api.js";
import { useUser } from "../UserContext.js";

const SLOT_LABELS = {
  headline: "Headline",
  description: "Description",
  primary_text: "Primary text",
  image: "Image",
  cta: "Call to Action",
};

function PlacementPreview({ imageUrl, imageWidth, imageHeight, placements }) {
  const defaultPlacements = [{ label: "Feed", ratio_w: 1, ratio_h: 1 }];
  const list = placements && placements.length > 0 ? placements : defaultPlacements;
  const [activeIdx, setActiveIdx] = useState(0);
  const active = list[Math.min(activeIdx, list.length - 1)];
  const pct = ((active.ratio_h / active.ratio_w) * 100).toFixed(3);

  return (
    <div className="placement-preview">
      {list.length > 1 && (
        <div className="placement-tabs">
          {list.map((p, i) => (
            <button
              key={i}
              className={"placement-tab" + (i === activeIdx ? " active" : "")}
              onClick={() => setActiveIdx(i)}
            >
              {p.label}
              <span className="placement-ratio">
                {p.ratio_w}:{p.ratio_h}
              </span>
            </button>
          ))}
        </div>
      )}
      <div className="placement-frame" style={{ paddingBottom: `${pct}%` }}>
        <img
          src={imageUrl}
          alt={active.label}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
            objectPosition: "center",
          }}
        />
      </div>
      {list.length === 1 && (
        <p className="placement-label-single">{list[0].label} &nbsp;{list[0].ratio_w}:{list[0].ratio_h}</p>
      )}
    </div>
  );
}

const DEPLOYMENT_LABELS = {
  suggested: "Suggested",
  pending_confirmation: "Pending",
  created_static: "Created",
  active_static: "Active",
  replaced_static: "Replaced",
  rejected: "Rejected",
};

const INGESTED_IDS_KEY = "ingested_campaign_ids";
const LAST_SYNCED_KEY = "campaigns_last_synced";

function loadIngestedIds() {
  try {
    return new Set(JSON.parse(localStorage.getItem(INGESTED_IDS_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveIngestedIds(ids) {
  localStorage.setItem(INGESTED_IDS_KEY, JSON.stringify([...ids]));
}

function SuggestionsSection({ suggestions, onConfirm, confirmingId }) {
  if (!suggestions || suggestions.length === 0) return null;

  return (
    <div className="suggestions-section">
      <h4 className="suggestions-heading">Suggestions</h4>
      {suggestions.map((s) => (
        <div key={s.id} className="suggestion-item">
          <div className="suggestion-header">
            <span className="suggestion-label">From dynamic ad</span>
            <code className="structure-ad-id">{s.source_ad_id}</code>
            <span className={`deployment-badge status-${s.deployment_status}`}>
              {DEPLOYMENT_LABELS[s.deployment_status] ?? s.deployment_status}
            </span>
          </div>
          <dl className="slot-list">
            {Object.entries(s.components).map(([slot, value]) => (
              <div key={slot} className="slot-row">
                <dt>{SLOT_LABELS[slot] ?? slot}</dt>
                <dd>
                  <span className="slot-value">{value || <em>—</em>}</span>
                </dd>
              </div>
            ))}
          </dl>
          {s.deployment_status === "suggested" && (
            <button
              className="btn-success suggestion-confirm-btn"
              onClick={() => onConfirm(s.id)}
              disabled={confirmingId === s.id}
            >
              {confirmingId === s.id ? "Creating…" : "Confirm Create"}
            </button>
          )}
          {s.static_ad_id && (
            <p className="suggestion-static-id">
              Static ad: <code>{s.static_ad_id}</code>
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function StructurePanel({ data, onDismiss, onConfirmSuggestion, confirmingId, selectedSeedAdId, onSelectSeed }) {
  if (data.error) {
    return (
      <div className="structure-panel">
        <p className="error">{data.error}</p>
        <button className="btn-secondary structure-dismiss" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    );
  }

  const { summary, ads, suggestions } = data;
  return (
    <div className="structure-panel">
      <div className="structure-summary">
        <span>
          Ingested <strong>{summary.ads_processed}</strong> ad
          {summary.ads_processed !== 1 ? "s" : ""},{" "}
          <strong>{summary.components_saved}</strong> component
          {summary.components_saved !== 1 ? "s" : ""}
        </span>
        <button className="btn-link structure-dismiss" onClick={onDismiss}>
          ×
        </button>
      </div>

      {ads.length === 0 ? (
        <p className="history-empty">No creative components found for this campaign.</p>
      ) : (
        <div className="structure-ads">
          {ads.length > 1 && (
            <p className="seed-hint">Select an ad to use as the seed for generation:</p>
          )}
          {ads.map((ad) => {
            const isSelected = (selectedSeedAdId || ads[0]?.ad_id) === ad.ad_id;
            return (
              <div key={ad.ad_id} className={`structure-ad${isSelected ? " structure-ad-selected" : ""}`}>
                <div className="structure-ad-header">
                  <code className="structure-ad-id">{ad.ad_id}</code>
                  <span className={`creative-type-badge ${ad.creative_type}`}>
                    {ad.creative_type}
                  </span>
                  {ad.lifecycle_status && (
                    <span className={`lifecycle-badge ${ad.lifecycle_status}`}>
                      {ad.lifecycle_status === "missing" ? "no longer in Meta" : ad.lifecycle_status}
                    </span>
                  )}
                  {ads.length > 1 && (
                    <button
                      className={`btn-seed${isSelected ? " btn-seed-active" : ""}`}
                      onClick={() => onSelectSeed(ad.ad_id)}
                      title="Use this ad as the seed for generation"
                    >
                      {isSelected ? "Seed ✓" : "Use as seed"}
                    </button>
                  )}
                </div>
                <dl className="slot-list">
                  {Object.entries(ad.components).map(([slot, values]) => (
                    <div key={slot} className="slot-row">
                      <dt>{SLOT_LABELS[slot] ?? slot}</dt>
                      <dd>
                        {values.map((v, i) => (
                          <span key={i} className="slot-value">
                            {v ?? <em>—</em>}
                          </span>
                        ))}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            );
          })}
        </div>
      )}

      <SuggestionsSection
        suggestions={suggestions}
        onConfirm={onConfirmSuggestion}
        confirmingId={confirmingId}
      />
    </div>
  );
}

export default function CampaignsPage({ onIngest = null, batchedAdIds = [] }) {
  const { tier } = useUser();
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [actionLoading, setActionLoading] = useState({});

  // expandedId controls the single unified detail row per campaign
  const [expandedId, setExpandedId] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // boStateById[campaignId] = null | "loading" | { picks, scored_count, candidate_count } | { error }
  const [boStateById, setBoStateById] = useState({});
  // genStateById[campaignId] = null | "loading" | { generated_ad_id, source_ad_id, slots } | { error }
  const [genStateById, setGenStateById] = useState({});
  // dynJobById[campaignId] = null | { job_id, status } | { job_id, status, slots, image_urls } | { error }
  const [dynJobById, setDynJobById] = useState({});

  // Sync state
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState(null);
  const [lastSynced, setLastSynced] = useState(() => localStorage.getItem(LAST_SYNCED_KEY));
  const [pushResult, setPushResult] = useState(null);

  // Persisted set of campaign IDs whose creative structure has been ingested
  const [ingestedIds, setIngestedIds] = useState(loadIngestedIds);

  // Per-campaign structural ingest state
  // structureById[campaignId] = null | "loading" | { summary, ads, suggestions } | { error }
  const [structureById, setStructureById] = useState({});
  const [confirmingId, setConfirmingId] = useState(null);
  const [selectedSeedAdById, setSelectedSeedAdById] = useState({});
  const [savingSuggestionKey, setSavingSuggestionKey] = useState({});
  // seedStateById[id] = { n, loading, seeded, error }
  const [seedStateById, setSeedStateById] = useState({});

  useEffect(() => {
    getCampaigns()
      .then(setCampaigns)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  // On mount, silently reload stored structure for any previously-ingested campaigns
  // so getSeedAdId() works without requiring a manual Reingest click.
  useEffect(() => {
    if (campaigns.length === 0) return;
    const toReload = campaigns.filter((c) => ingestedIds.has(c.id));
    if (toReload.length === 0) return;
    toReload.forEach(async (c) => {
      try {
        const [ads, suggestions] = await Promise.all([
          getCampaignStructure(c.id),
          getCampaignSuggestions(c.id),
        ]);
        if (ads?.length > 0) {
          setStructureById((prev) => ({ ...prev, [c.id]: { ads, suggestions } }));
        }
      } catch {
        // silently ignore — user can still click Reingest manually
      }
    });
  }, [campaigns]); // eslint-disable-line react-hooks/exhaustive-deps

  // Poll dynamic generation jobs while any are running
  useEffect(() => {
    const running = Object.entries(dynJobById).filter(
      ([, v]) => v && v.status === "running" && v.job_id
    );
    if (running.length === 0) return;
    const id = setInterval(async () => {
      for (const [campaignId, jobState] of running) {
        try {
          const result = await getDynamicGenStatus(jobState.job_id);
          if (result.status !== "running") {
            setDynJobById((prev) => ({ ...prev, [campaignId]: result }));
          }
        } catch (err) {
          setDynJobById((prev) => ({ ...prev, [campaignId]: { error: err.message } }));
        }
      }
    }, 5000);
    return () => clearInterval(id);
  }, [dynJobById]);

  // ── Load history for a campaign (shared by toggle and auto-expand) ──────────
  function loadHistory(campaignId) {
    setHistoryLoading(true);
    getCampaignHistory(campaignId, 30)
      .then(setHistory)
      .catch(() => setHistory([]))
      .finally(() => setHistoryLoading(false));
  }

  // ── Toggle the detail row (name click) ─────────────────────────────────────
  function toggleExpanded(campaignId) {
    if (expandedId === campaignId) {
      setExpandedId(null);
      return;
    }
    setExpandedId(campaignId);
    loadHistory(campaignId);
  }

  async function handleSyncCampaigns() {
    setSyncError(null);
    setPushResult(null);
    setSyncing(true);
    try {
      let pushSummary = null;
      try {
        pushSummary = await pushGeneratedAds();
      } catch {
        // Push failures are non-fatal
      }

      await runIngest();
      const now = new Date().toLocaleString();
      localStorage.setItem(LAST_SYNCED_KEY, now);
      setLastSynced(now);
      const updated = await getCampaigns();
      setCampaigns(updated);

      if (pushSummary) setPushResult(pushSummary);
    } catch (err) {
      setSyncError(err.message);
    } finally {
      setSyncing(false);
    }
  }

  async function handleToggle(campaign) {
    const id = campaign.id;
    setActionLoading((prev) => ({ ...prev, [id]: true }));
    try {
      let result;
      if (campaign.status === "ACTIVE") {
        result = await pauseCampaign(id);
      } else {
        result = await resumeCampaign(id);
      }
      setCampaigns((prev) =>
        prev.map((c) => (c.id === id ? { ...c, status: result.status } : c))
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setActionLoading((prev) => ({ ...prev, [id]: false }));
    }
  }

  // ── Ingest: reads creative structure, then auto-expands the detail row ──────
  async function handleIngestStructure(campaignId) {
    setStructureById((prev) => ({ ...prev, [campaignId]: "loading" }));

    // Auto-expand and start loading history concurrently
    if (expandedId !== campaignId) {
      setExpandedId(campaignId);
      loadHistory(campaignId);
    }

    try {
      const summary = await ingestCampaignStructure(campaignId);
      const [ads, suggestions] = await Promise.all([
        getCampaignStructure(campaignId),
        getCampaignSuggestions(campaignId),
      ]);
      setStructureById((prev) => ({ ...prev, [campaignId]: { summary, ads, suggestions } }));
      setIngestedIds((prev) => {
        const next = new Set(prev);
        next.add(campaignId);
        saveIngestedIds(next);
        return next;
      });

    } catch (err) {
      setStructureById((prev) => ({ ...prev, [campaignId]: { error: err.message } }));
    }
  }

  function getSeedAdId(campaignId) {
    return selectedSeedAdById[campaignId] || structureById[campaignId]?.ads?.[0]?.ad_id;
  }

  async function handleGetRecommendations(campaignId) {
    setBoStateById((prev) => ({ ...prev, [campaignId]: "loading" }));
    try {
      let adId = getSeedAdId(campaignId);
      if (!adId) {
        const ads = await getCampaignStructure(campaignId);
        adId = ads?.[0]?.ad_id;
      }
      if (!adId) throw new Error("No ingested ad found for this campaign.");
      const result = await runBO(adId, adId);
      setBoStateById((prev) => ({ ...prev, [campaignId]: { ...result, seedAdId: adId } }));
    } catch (err) {
      setBoStateById((prev) => ({ ...prev, [campaignId]: { error: err.message } }));
    }
  }

  async function handleGenerateTextAds(campaignId) {
    setGenStateById((prev) => ({ ...prev, [campaignId]: "loading" }));
    try {
      const result = await generateTextAds(campaignId, getSeedAdId(campaignId));
      setGenStateById((prev) => ({ ...prev, [campaignId]: result }));
    } catch (err) {
      setGenStateById((prev) => ({ ...prev, [campaignId]: { error: err.message } }));
    }
  }

  async function handleGenerateDynamic(campaignId) {
    setDynJobById((prev) => ({ ...prev, [campaignId]: { status: "running" } }));
    try {
      const { job_id } = await startDynamicGeneration(campaignId, getSeedAdId(campaignId));
      setDynJobById((prev) => ({ ...prev, [campaignId]: { job_id, status: "running" } }));
    } catch (err) {
      setDynJobById((prev) => ({ ...prev, [campaignId]: { error: err.message } }));
    }
  }

  async function handleSeedTestData(campaignId) {
    const seedAdId = getSeedAdId(campaignId);
    if (!seedAdId) return;
    const n = seedStateById[campaignId]?.n ?? 5;
    setSeedStateById(prev => ({ ...prev, [campaignId]: { ...prev[campaignId], loading: true, seeded: null, error: null } }));
    try {
      const result = await seedScoredVariants(seedAdId, "meta", n, seedAdId);
      setSeedStateById(prev => ({ ...prev, [campaignId]: { ...prev[campaignId], loading: false, seeded: result.seeded, warning: result.warning } }));
    } catch (err) {
      setSeedStateById(prev => ({ ...prev, [campaignId]: { ...prev[campaignId], loading: false, error: err.message } }));
    }
  }

  async function handleSaveAsSuggestion(campaignId, pick, pickIndex) {
    const key = `${campaignId}-${pickIndex}`;
    setSavingSuggestionKey((prev) => ({ ...prev, [key]: true }));
    try {
      const boState = boStateById[campaignId];
      const seedAdId = boState?.seedAdId || getSeedAdId(campaignId);
      const ads = structureById[campaignId]?.ads || [];
      const seedAd = ads.find((a) => a.ad_id === seedAdId) || ads[0];
      const adsetId = seedAd?.adset_id || null;

      const components = { ...pick.combination };
      const imageUrl = components.image_url;
      delete components.image_url;
      if (imageUrl) components.image = imageUrl;

      await storeSuggestion({
        campaign_id: campaignId,
        adset_id: adsetId,
        source_ad_id: seedAdId,
        components,
      });

      const suggestions = await getCampaignSuggestions(campaignId);
      setStructureById((prev) => {
        const current = prev[campaignId];
        if (!current || current === "loading") return prev;
        return { ...prev, [campaignId]: { ...current, suggestions } };
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setSavingSuggestionKey((prev) => ({ ...prev, [key]: false }));
    }
  }

  async function handleConfirmSuggestion(campaignId, suggestionId) {
    setConfirmingId(suggestionId);
    try {
      const updated = await confirmSuggestion(suggestionId, "create");
      setStructureById((prev) => {
        const current = prev[campaignId];
        if (!current || current === "loading" || !current.suggestions) return prev;
        return {
          ...prev,
          [campaignId]: {
            ...current,
            suggestions: current.suggestions.map((s) =>
              s.id === suggestionId ? updated : s
            ),
          },
        };
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setConfirmingId(null);
    }
  }

  function fmt(val, prefix = "", suffix = "") {
    if (val == null) return "–";
    return `${prefix}${Number(val).toFixed(2)}${suffix}`;
  }

  function fmtInt(val) {
    if (val == null) return "–";
    return Number(val).toLocaleString();
  }

  function formatBudget(cents) {
    if (cents == null) return "–";
    return `$${(cents / 100).toFixed(2)}`;
  }

  if (loading) return <p>Loading campaigns...</p>;
  if (error) return <p className="error">{error}</p>;

  return (
    <div className="campaigns-page">
      <div className="campaigns-header">
        <h2>Campaigns</h2>
        <div className="sync-controls">
          {lastSynced && (
            <span className="sync-timestamp">Last synced: {lastSynced}</span>
          )}
          <button
            className="btn-primary"
            onClick={handleSyncCampaigns}
            disabled={syncing}
            title="Pull latest campaigns from Meta; also pushes any generated ads"
          >
            {syncing ? "Syncing…" : "Sync"}
          </button>
        </div>
      </div>

      {syncError && <p className="error">{syncError}</p>}
      {pushResult && pushResult.pushed > 0 && (
        <p className="sync-push-note">
          Pushed <strong>{pushResult.pushed}</strong> generated ad{pushResult.pushed !== 1 ? "s" : ""} to Meta
          {pushResult.failed > 0 && <> · {pushResult.failed} pending</>}
        </p>
      )}
      {pushResult && pushResult.pushed === 0 && pushResult.failed > 0 && (
        <p className="sync-push-pending">
          {pushResult.failed} generated ad{pushResult.failed !== 1 ? "s" : ""} ready to push — Meta app must be in Live mode
        </p>
      )}

      {campaigns.length === 0 ? (
        <p>No campaigns found in this ad account.</p>
      ) : (
        <table className="campaigns-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Daily Budget</th>
              <th>7d Spend</th>
              <th>7d Impr.</th>
              <th>7d Clicks</th>
              <th>CTR</th>
              <th>CPM</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {campaigns.map((c) => (
              <>
                {/* ── Main row ────────────────────────────────────────────── */}
                <tr key={c.id}>
                  <td>
                    <button
                      className="btn-link campaign-name"
                      onClick={() => toggleExpanded(c.id)}
                      title="Show metric history and recommendations"
                    >
                      {c.name}
                    </button>
                  </td>
                  <td>
                    <span
                      className={`status-badge ${c.status === "ACTIVE" ? "active" : "paused"}`}
                    >
                      {c.status}
                    </span>
                  </td>
                  <td>{formatBudget(c.daily_budget)}</td>
                  <td>{fmt(c.spend_7d, "$")}</td>
                  <td>{fmtInt(c.impressions_7d)}</td>
                  <td>{fmtInt(c.clicks_7d)}</td>
                  <td>{fmt(c.ctr_7d, "", "%")}</td>
                  <td>{fmt(c.cpm_7d, "$")}</td>
                  <td className="actions-cell">
                    {(c.status === "ACTIVE" || c.status === "PAUSED") && (
                      <button
                        onClick={() => handleToggle(c)}
                        disabled={!!actionLoading[c.id]}
                        className={c.status === "ACTIVE" ? "btn-warn" : "btn-success"}
                        title={c.status === "ACTIVE" ? "Pause this campaign in Meta" : "Resume this campaign in Meta"}
                      >
                        {actionLoading[c.id] ? "..." : c.status === "ACTIVE" ? "Pause" : "Resume"}
                      </button>
                    )}
                    <button
                      className="btn-creatives"
                      onClick={() => handleIngestStructure(c.id)}
                      disabled={structureById[c.id] === "loading"}
                      title={ingestedIds.has(c.id) ? "Re-read this campaign's ad creatives from Meta" : "Read this campaign's ad creatives"}
                    >
                      {structureById[c.id] === "loading"
                        ? "…"
                        : ingestedIds.has(c.id)
                          ? "Reingest"
                          : "Ingest"}
                    </button>
                  </td>
                </tr>

                {/* ── Unified detail row (opens on name click OR after Ingest) ── */}
                {expandedId === c.id && (
                  <tr key={`${c.id}-detail`} className="history-row">
                    <td colSpan={9}>

                      {/* 1. Creative structure */}
                      {structureById[c.id] === "loading" && (
                        <p className="history-loading">Loading creative structure…</p>
                      )}
                      {structureById[c.id] && structureById[c.id] !== "loading" && (
                        <StructurePanel
                          data={structureById[c.id]}
                          onDismiss={() =>
                            setStructureById((prev) => ({ ...prev, [c.id]: null }))
                          }
                          onConfirmSuggestion={(suggestionId) =>
                            handleConfirmSuggestion(c.id, suggestionId)
                          }
                          confirmingId={confirmingId}
                          selectedSeedAdId={selectedSeedAdById[c.id]}
                          onSelectSeed={(adId) =>
                            setSelectedSeedAdById((prev) => ({ ...prev, [c.id]: adId }))
                          }
                        />
                      )}

                      {/* 2. Recommendations — shown once ingested */}
                      {ingestedIds.has(c.id) && (
                        <div className="recommendations-section">
                          <div className="recommendations-header">
                            <button
                              className="btn-primary"
                              onClick={() => handleGetRecommendations(c.id)}
                              disabled={boStateById[c.id] === "loading"}
                              title="Bayesian Optimisation: suggests the best headline, copy, and image combinations to test next"
                            >
                              {boStateById[c.id] === "loading" ? "Running…" : "Get Recommendations"}
                            </button>
                            <button
                              className="btn-secondary"
                              onClick={() => handleGenerateTextAds(c.id)}
                              disabled={genStateById[c.id] === "loading"}
                              title="Generate 10 new text variants per slot (headline, primary text, description, CTA)"
                            >
                              {genStateById[c.id] === "loading" ? "Generating…" : "Static Text Ads"}
                            </button>
                            <button
                              className="btn-secondary btn-dynamic"
                              onClick={() => handleGenerateDynamic(c.id)}
                              disabled={dynJobById[c.id]?.status === "running"}
                              title="Generate AI images + 4 text variants per slot — every combination is scored"
                            >
                              {dynJobById[c.id]?.status === "running"
                                ? "Generating…"
                                : "Dynamic Ad (AI Images)"}
                            </button>
                            {onIngest && (() => {
                              const seedAdId = getSeedAdId(c.id);
                              if (!seedAdId) return null;
                              const inBatch = batchedAdIds.includes(seedAdId);
                              return (
                                <button
                                  className={inBatch ? "btn-batch-active" : "btn-batch"}
                                  onClick={() => onIngest({ platform: "meta", seed_ad_id: seedAdId, text_source_id: seedAdId, label: c.name })}
                                  title={inBatch ? "Remove from cross-platform analysis batch" : "Add to cross-platform analysis batch"}
                                >
                                  {inBatch ? "In Batch ✓" : "Add to Analysis"}
                                </button>
                              );
                            })()}
                          </div>

                          {/* Seed test data */}
                          <div className="seed-controls">
                            <span className="seed-controls-label">Seed test data:</span>
                            <input
                              type="number"
                              min={1} max={50}
                              value={seedStateById[c.id]?.n ?? 5}
                              onChange={e => setSeedStateById(prev => ({
                                ...prev,
                                [c.id]: { ...prev[c.id], n: Math.max(1, Math.min(50, parseInt(e.target.value) || 1)) },
                              }))}
                              className="seed-n-input"
                              title="Number of fake scored variants to seed"
                            />
                            <span className="seed-controls-unit">variants</span>
                            <button
                              className="btn-small"
                              onClick={() => handleSeedTestData(c.id)}
                              disabled={seedStateById[c.id]?.loading || !getSeedAdId(c.id)}
                              title="Seed fake scored observations so Get Recommendations has training data"
                            >
                              {seedStateById[c.id]?.loading ? "Seeding…" : "Seed"}
                            </button>
                            {seedStateById[c.id]?.seeded != null && (
                              <span className="seed-success">✓ {seedStateById[c.id].seeded} seeded</span>
                            )}
                            {seedStateById[c.id]?.warning && (
                              <span className="seed-warning">{seedStateById[c.id].warning}</span>
                            )}
                            {seedStateById[c.id]?.error && (
                              <span className="error-inline">{seedStateById[c.id].error}</span>
                            )}
                          </div>

                          {/* Static text generation results */}
                          {genStateById[c.id]?.error && (
                            <p className="error">{genStateById[c.id].error}</p>
                          )}
                          {genStateById[c.id]?.slots && (
                            <div className="gen-results">
                              <p className="bo-meta gen-mode-label">Static — text only</p>
                              <p className="bo-meta">
                                Generated{" "}
                                <strong>
                                  {genStateById[c.id].slots.filter((s) => s.source === "generated").length}
                                </strong>{" "}
                                text variants for ad <code>{genStateById[c.id].source_ad_id}</code>
                              </p>
                              {["headline", "primary_text", "description", "cta"].map((slotName) => {
                                const slotItems = genStateById[c.id].slots.filter(
                                  (s) => s.slot === slotName && s.source === "generated"
                                );
                                if (!slotItems.length) return null;
                                return (
                                  <div key={slotName} className="gen-slot">
                                    <h5 className="gen-slot-label">{SLOT_LABELS[slotName] || slotName}</h5>
                                    <ol className="gen-slot-list">
                                      {slotItems.map((s, i) => (
                                        <li key={i}>{s.value}</li>
                                      ))}
                                    </ol>
                                  </div>
                                );
                              })}
                            </div>
                          )}

                          {/* Dynamic generation results */}
                          {dynJobById[c.id]?.error && (
                            <p className="error">{dynJobById[c.id].error}</p>
                          )}
                          {dynJobById[c.id]?.status === "running" && (
                            <p className="bo-meta dyn-running">
                              Generating dynamic ad — AI images + text…{" "}
                              <span className="dyn-spinner">⏳</span>
                            </p>
                          )}
                          {dynJobById[c.id]?.status === "complete" && (
                            <div className="gen-results">
                              <p className="bo-meta gen-mode-label">Dynamic — 4×4 text + AI images</p>
                              <p className="bo-meta">
                                Ad ID: <code>{dynJobById[c.id].ad_id}</code> · stored in creative
                                structures · embeddings firing in background
                              </p>

                              {dynJobById[c.id].images_generated === 0 && (
                                <p className="dyn-warning">
                                  Image generation failed (DEAPI_API_KEY not configured) — showing
                                  seed image(s) as placeholder.
                                </p>
                              )}

                              {dynJobById[c.id].image_urls?.length > 0 && (
                                <div className="dyn-image-grid">
                                  {dynJobById[c.id].image_urls.map((url, i) => (
                                    <div key={i} className="dyn-image-cell">
                                      <img src={url} alt={`Generated image ${i + 1}`} />
                                      <span className="dyn-image-label">
                                        {dynJobById[c.id].images_generated === 0 ? "Seed image" : `Image ${i + 1}`}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              )}

                              {["headline", "primary_text", "description", "cta"].map((slotName) => {
                                const slotItems = dynJobById[c.id].slots?.filter(
                                  (s) => s.slot === slotName
                                );
                                if (!slotItems?.length) return null;
                                return (
                                  <div key={slotName} className="gen-slot">
                                    <h5 className="gen-slot-label">{SLOT_LABELS[slotName] || slotName}</h5>
                                    <ol className="gen-slot-list">
                                      {slotItems.map((s, i) => (
                                        <li key={i}>{s.value}</li>
                                      ))}
                                    </ol>
                                  </div>
                                );
                              })}
                            </div>
                          )}

                          {/* BO results */}
                          {boStateById[c.id]?.error && (
                            <p className="error">{boStateById[c.id].error}</p>
                          )}

                          {boStateById[c.id]?.picks && (
                            <div className="bo-results">
                              {boStateById[c.id].warning && (
                                <p className="dyn-warning">{boStateById[c.id].warning}</p>
                              )}
                              <p className="bo-meta">
                                <strong>{boStateById[c.id].scored_count}</strong> scored variant{boStateById[c.id].scored_count !== 1 ? "s" : ""} ·{" "}
                                <strong>{boStateById[c.id].candidate_count}</strong> candidate{boStateById[c.id].candidate_count !== 1 ? "s" : ""}
                                {boStateById[c.id].scored_count === 0 && (
                                  <span className="stat-random-note"> — no scored data yet, picks are random. Run Dynamic Ad (AI Images) first to generate scored variants.</span>
                                )}
                              </p>
                              {boStateById[c.id].picks.length === 0 ? (
                                <p className="history-empty">
                                  Not enough scored data yet — generate a Dynamic Ad first, then run BO again.
                                </p>
                              ) : (
                                boStateById[c.id].picks.map((pick, i) => (
                                  <BOPickCard
                                    key={pick.combination_key}
                                    pick={pick}
                                    index={i}
                                    platform="meta"
                                    seedAdId={boStateById[c.id].seedAdId || getSeedAdId(c.id)}
                                    campaignName={c.name}
                                    onPushed={(p, platformAdId, adName) => {
                                      // Optimistically update the pick in state
                                      setBoStateById(prev => ({
                                        ...prev,
                                        [c.id]: {
                                          ...prev[c.id],
                                          picks: prev[c.id].picks.map(pk =>
                                            pk.combination_key === p.combination_key
                                              ? { ...pk, already_pushed: true, push_status: "paused", ad_name: adName, platform_ad_id: platformAdId }
                                              : pk
                                          ),
                                        },
                                      }));
                                    }}
                                  />
                                ))
                              )}

                              {structureById[c.id]?.suggestions?.length > 0 && (
                                <div className="bo-saved-suggestions">
                                  <h5 className="bo-saved-suggestions-heading">Saved Suggestions</h5>
                                  <p className="bo-meta">
                                    {structureById[c.id].suggestions.length} saved — open the structure panel to confirm-create in Meta.
                                  </p>
                                </div>
                              )}
                            </div>
                          )}

                          {tier === "free" && (
                            <p className="upsell-note">
                              Premium plan includes continuous improvement. Adstac.kr will
                              automatically check your stats and periodically create new ads
                              and make new suggestions.
                            </p>
                          )}
                        </div>
                      )}

                      {/* 3. Metric history */}
                      <div className="campaign-history-section">
                        <h4 className="campaign-history-heading">Metric History</h4>
                        {historyLoading ? (
                          <p className="history-loading">Loading history...</p>
                        ) : history.length === 0 ? (
                          <p className="history-empty">
                            No stored snapshots yet — use Sync to save a snapshot.
                          </p>
                        ) : (
                          <table className="history-table">
                            <thead>
                              <tr>
                                <th>Date</th>
                                <th>Impressions</th>
                                <th>Clicks</th>
                                <th>Spend</th>
                                <th>CTR</th>
                                <th>CPM</th>
                                <th>CPC</th>
                              </tr>
                            </thead>
                            <tbody>
                              {history.map((h) => (
                                <tr key={h.date}>
                                  <td>{h.date}</td>
                                  <td>{fmtInt(h.impressions)}</td>
                                  <td>{fmtInt(h.clicks)}</td>
                                  <td>{fmt(h.spend, "$")}</td>
                                  <td>{fmt(h.ctr, "", "%")}</td>
                                  <td>{fmt(h.cpm, "$")}</td>
                                  <td>{fmt(h.cpc, "$")}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        )}
                      </div>

                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
