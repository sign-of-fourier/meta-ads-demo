import { useEffect, useState } from "react";
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
} from "../api.js";
import { useUser } from "../UserContext.js";

const SLOT_LABELS = {
  headline: "Headline",
  description: "Description",
  primary_text: "Primary text",
  image: "Image",
  cta: "Call to Action",
};

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

export default function CampaignsPage() {
  const { tier } = useUser();
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [actionLoading, setActionLoading] = useState({});
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
  const [pushResult, setPushResult] = useState(null); // { pushed, failed } | null

  // Persisted set of campaign IDs whose creative structure has been ingested
  const [ingestedIds, setIngestedIds] = useState(loadIngestedIds);

  // Per-campaign structural ingest state
  // structureById[campaignId] = null | "loading" | { summary, ads, suggestions } | { error }
  const [structureById, setStructureById] = useState({});
  const [confirmingId, setConfirmingId] = useState(null);
  // selectedSeedAdById[campaignId] = ad_id string | undefined (undefined = use first)
  const [selectedSeedAdById, setSelectedSeedAdById] = useState({});
  // savingSuggestionKey[campaignId+"-"+pickIndex] = true when saving
  const [savingSuggestionKey, setSavingSuggestionKey] = useState({});

  useEffect(() => {
    getCampaigns()
      .then(setCampaigns)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

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

  async function handleSyncCampaigns() {
    setSyncError(null);
    setPushResult(null);
    setSyncing(true);
    try {
      // Push generated ads first (best-effort — never blocks the pull)
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

  async function toggleHistory(campaignId) {
    if (expandedId === campaignId) {
      setExpandedId(null);
      return;
    }
    setExpandedId(campaignId);
    setHistoryLoading(true);
    try {
      const data = await getCampaignHistory(campaignId, 30);
      setHistory(data);
    } catch {
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }

  async function handleIngestStructure(campaignId) {
    setStructureById((prev) => ({ ...prev, [campaignId]: "loading" }));
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

      // Refresh suggestions in structure panel
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
          <button className="btn-primary" onClick={handleSyncCampaigns} disabled={syncing} title="Push generated ads to Meta, then pull latest campaigns">
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
                <tr key={c.id}>
                  <td>
                    <button
                      className="btn-link campaign-name"
                      onClick={() => toggleHistory(c.id)}
                      title="Show metric history"
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
                        className={
                          c.status === "ACTIVE" ? "btn-warn" : "btn-success"
                        }
                      >
                        {actionLoading[c.id]
                          ? "..."
                          : c.status === "ACTIVE"
                            ? "Pause"
                            : "Resume"}
                      </button>
                    )}
                    <button
                      className="btn-creatives"
                      onClick={() => handleIngestStructure(c.id)}
                      disabled={structureById[c.id] === "loading"}
                      title={ingestedIds.has(c.id) ? "Re-ingest creative structure" : "Ingest creative structure"}
                    >
                      {structureById[c.id] === "loading"
                        ? "…"
                        : ingestedIds.has(c.id)
                          ? "Reingest"
                          : "Ingest"}
                    </button>
                  </td>
                </tr>

                {structureById[c.id] && structureById[c.id] !== "loading" && (
                  <tr key={`${c.id}-structure`} className="history-row">
                    <td colSpan={9}>
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
                    </td>
                  </tr>
                )}

                {expandedId === c.id && (
                  <tr key={`${c.id}-history`} className="history-row">
                    <td colSpan={9}>
                      {historyLoading ? (
                        <p className="history-loading">Loading history...</p>
                      ) : history.length === 0 ? (
                        <p className="history-empty">
                          No stored snapshots yet. Use "Sync" to save snapshots.
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

                      {ingestedIds.has(c.id) && (
                        <div className="recommendations-section">
                          <div className="recommendations-header">
                            <button
                              className="btn-primary"
                              onClick={() => handleGetRecommendations(c.id)}
                              disabled={boStateById[c.id] === "loading"}
                            >
                              {boStateById[c.id] === "loading" ? "Running…" : "Get Recommendations"}
                            </button>
                            <button
                              className="btn-secondary"
                              onClick={() => handleGenerateTextAds(c.id)}
                              disabled={genStateById[c.id] === "loading"}
                            >
                              {genStateById[c.id] === "loading" ? "Generating…" : "Static Text Ads"}
                            </button>
                            <button
                              className="btn-secondary btn-dynamic"
                              onClick={() => handleGenerateDynamic(c.id)}
                              disabled={dynJobById[c.id]?.status === "running"}
                            >
                              {dynJobById[c.id]?.status === "running"
                                ? "Generating…"
                                : "Dynamic Ad (AI Images)"}
                            </button>
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

                              {/* Image generation warning */}
                              {dynJobById[c.id].images_generated === 0 && (
                                <p className="dyn-warning">
                                  Image generation failed (DEAPI_API_KEY not configured) — showing
                                  seed image(s) as placeholder. Add the key and regenerate for AI images.
                                </p>
                              )}

                              {/* Image grid */}
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

                              {/* Text slots */}
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

                          {boStateById[c.id]?.error && (
                            <p className="error">{boStateById[c.id].error}</p>
                          )}

                          {boStateById[c.id]?.picks && (
                            <div className="bo-results">
                              <p className="bo-meta">
                                Based on <strong>{boStateById[c.id].scored_count}</strong> scored
                                variant{boStateById[c.id].scored_count !== 1 ? "s" : ""} across{" "}
                                <strong>{boStateById[c.id].candidate_count}</strong> candidate
                                combination{boStateById[c.id].candidate_count !== 1 ? "s" : ""}
                                {boStateById[c.id].seedAdId && (
                                  <> · seed <code>{boStateById[c.id].seedAdId}</code></>
                                )}
                              </p>
                              {boStateById[c.id].picks.length === 0 ? (
                                <p className="history-empty">
                                  Not enough scored data yet — generate a Dynamic Ad first, then run BO again.
                                </p>
                              ) : (
                                boStateById[c.id].picks.map((pick, i) => {
                                  const saveKey = `${c.id}-${i}`;
                                  const isSaving = !!savingSuggestionKey[saveKey];
                                  return (
                                    <div key={i} className="bo-pick">
                                      <div className="bo-pick-header">
                                        <span className="bo-pick-label">
                                          Recommendation {i + 1}
                                        </span>
                                        <span className="bo-pick-type">{pick.selection_type === "ei" ? "Best expected" : "Exploratory"}</span>
                                        <button
                                          className="btn-success btn-save-suggestion"
                                          onClick={() => handleSaveAsSuggestion(c.id, pick, i)}
                                          disabled={isSaving}
                                          title="Save this recommendation as a suggestion you can confirm-create in Meta"
                                        >
                                          {isSaving ? "Saving…" : "Save as Suggestion"}
                                        </button>
                                      </div>
                                      {pick.combination.image_url && (
                                        <img
                                          className="bo-pick-image"
                                          src={pick.combination.image_url}
                                          alt="Recommended ad image"
                                        />
                                      )}
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
                                          {pick.ei_score != null && (
                                            <> &nbsp;·&nbsp; EI: {pick.ei_score.toFixed(4)}</>
                                          )}
                                        </p>
                                      )}
                                    </div>
                                  );
                                })
                              )}

                              {/* Saved suggestions from this campaign */}
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
                              Premium plan includes continuous improvement. AdStac.kr will
                              automatically check your stats and periodically create new ads
                              and make new suggestions.
                            </p>
                          )}
                        </div>
                      )}
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
