import { useEffect, useState } from "react";
import { getGoogleCampaigns, ingestGoogleStructure, getGoogleStructure, generateGoogleTextAds, runGoogleBO, pushGoogleAds } from "../api.js";

const STORAGE_KEY = "google_ingested_ids";

function fmt(val, decimals = 2) {
  if (val == null) return "—";
  return typeof val === "number" ? val.toFixed(decimals) : val;
}

function fmtInt(val) {
  if (val == null) return "—";
  return Number(val).toLocaleString();
}

function TextGenResults({ data }) {
  const slots = ["headline", "description"];
  const bySlot = {};
  for (const s of data.slots) {
    if (slots.includes(s.slot)) {
      bySlot[s.slot] = bySlot[s.slot] || [];
      bySlot[s.slot].push(s.value);
    }
  }
  return (
    <div className="text-gen-results">
      <h4>Generated RSA Variants</h4>
      {slots.filter((s) => bySlot[s]?.length).map((slot) => (
        <div key={slot} className="slot-group">
          <span className="slot-label">{slot}</span>
          <div className="slot-values">
            {bySlot[slot].map((v, i) => <div key={i} className="slot-value">{v}</div>)}
          </div>
        </div>
      ))}
    </div>
  );
}

function BOPicksPanel({ result }) {
  if (!result.picks || result.picks.length === 0) {
    return (
      <div className="bo-results">
        <p className="history-empty">
          No candidates yet — ingest the campaign, generate RSA text, and run BO again once the embedding pipeline finishes.
        </p>
        <p className="bo-meta">
          Scored: <strong>{result.scored_count}</strong> · Candidates: <strong>{result.candidate_count}</strong>
        </p>
      </div>
    );
  }
  return (
    <div className="bo-results">
      <p className="bo-meta">
        Based on <strong>{result.scored_count}</strong> scored variant{result.scored_count !== 1 ? "s" : ""} across{" "}
        <strong>{result.candidate_count}</strong> candidate combination{result.candidate_count !== 1 ? "s" : ""}
      </p>
      {result.picks.map((pick, i) => (
        <div key={i} className="bo-pick">
          <div className="bo-pick-header">
            <span className="bo-pick-label">Recommendation {i + 1}</span>
            <span className="bo-pick-type">
              {pick.selection_type === "ei" ? "Best expected" : pick.selection_type === "fantasy" ? "Exploratory" : "Random"}
            </span>
          </div>
          <dl className="slot-list">
            {Object.entries(pick.combination)
              .filter(([k]) => k !== "image_url")
              .map(([k, v]) => (
                <div key={k} className="slot-row">
                  <dt>{k}</dt>
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
      ))}
    </div>
  );
}

function StructurePanel({ campaignId, onClose }) {
  const [structure, setStructure] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getGoogleStructure(campaignId)
      .then(setStructure)
      .finally(() => setLoading(false));
  }, [campaignId]);

  if (loading) return <div className="structure-panel"><p>Loading structure…</p></div>;

  const CREATIVE_LABELS = {
    rsa: "Responsive Search",
    display: "Responsive Display",
    video: "Video Responsive",
    pmax: "Performance Max",
    shopping: "Shopping",
    unknown: "Unknown",
  };

  return (
    <div className="structure-panel">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h4>Creative Structure</h4>
        <button onClick={onClose} className="btn-small">Close</button>
      </div>
      {(!structure || structure.length === 0) ? (
        <p>No structure data found.</p>
      ) : (
        structure.map((ad) => (
          <div key={ad.ad_id} className="ad-structure-card">
            <div className="ad-structure-header">
              <span className="creative-type-badge">
                {CREATIVE_LABELS[ad.creative_type] || ad.creative_type}
              </span>
              <span className={`status-badge status-${ad.lifecycle_status}`}>
                {ad.lifecycle_status}
              </span>
              <span className="ad-id-label">Ad {ad.ad_id}</span>
            </div>
            {Object.entries(ad.components).map(([slot, values]) => (
              <div key={slot} className="slot-group">
                <span className="slot-label">{slot}</span>
                <div className="slot-values">
                  {values.map((v, i) => (
                    <div key={i} className="slot-value">{v}</div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ))
      )}
    </div>
  );
}

export default function GoogleCampaignsPage() {
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [ingestingId, setIngestingId] = useState(null);
  const [openStructureId, setOpenStructureId] = useState(null);
  const [genStateById, setGenStateById] = useState({});
  const [boStateById, setBoStateById] = useState({});

  const [ingestedIds, setIngestedIds] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    } catch {
      return [];
    }
  });
  const [syncing, setSyncing] = useState(false);
  const [syncNote, setSyncNote] = useState(null);

  useEffect(() => {
    getGoogleCampaigns()
      .then(setCampaigns)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  function markIngested(id) {
    setIngestedIds((prev) => {
      const next = prev.includes(id) ? prev : [...prev, id];
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }

  async function handleGenerateText(campaignId) {
    setGenStateById((prev) => ({ ...prev, [campaignId]: { status: "loading", data: null } }));
    try {
      const result = await generateGoogleTextAds(campaignId);
      setGenStateById((prev) => ({ ...prev, [campaignId]: { status: "done", data: result } }));
    } catch (err) {
      setGenStateById((prev) => ({ ...prev, [campaignId]: { status: "error", data: null, error: err.message } }));
    }
  }

  async function handleRunBO(campaignId, seedAdId) {
    setBoStateById((prev) => ({ ...prev, [campaignId]: { status: "loading" } }));
    try {
      const result = await runGoogleBO(seedAdId, seedAdId);
      setBoStateById((prev) => ({ ...prev, [campaignId]: { status: "done", data: result } }));
    } catch (err) {
      setBoStateById((prev) => ({ ...prev, [campaignId]: { status: "error", error: err.message } }));
    }
  }

  async function handleSync() {
    setSyncing(true);
    setSyncNote(null);
    try {
      const result = await pushGoogleAds();
      if (result?.note) {
        setSyncNote({ type: "amber", text: result.note });
      } else if (result?.pushed > 0) {
        setSyncNote({ type: "success", text: `Pushed ${result.pushed} ad${result.pushed !== 1 ? "s" : ""} to Google Ads.` });
      } else {
        setSyncNote({ type: "info", text: "No new ads to push." });
      }
      const refreshed = await getGoogleCampaigns();
      setCampaigns(refreshed);
    } catch (err) {
      setSyncNote({ type: "error", text: `Sync failed: ${err.message}` });
    } finally {
      setSyncing(false);
    }
  }

  async function handleIngest(campaignId) {
    setIngestingId(campaignId);
    try {
      await ingestGoogleStructure(campaignId);
      markIngested(campaignId);
      setOpenStructureId(campaignId);
    } catch (err) {
      alert(`Ingest failed: ${err.message}`);
    } finally {
      setIngestingId(null);
    }
  }

  if (loading) return <p>Loading Google campaigns…</p>;

  if (error) {
    const notConnected = error.includes("not connected");
    return (
      <div className="campaigns-page">
        <h2>Google Ads Campaigns</h2>
        <p className="error">
          {notConnected
            ? "Google Ads account not connected. Go to Settings to connect."
            : error}
        </p>
      </div>
    );
  }

  if (campaigns.length === 0) {
    return (
      <div className="campaigns-page">
        <h2>Google Ads Campaigns</h2>
        <p>No campaigns found for this account.</p>
      </div>
    );
  }

  return (
    <div className="campaigns-page">
      <div className="page-hint-banner">
        <p style={{ margin: "0 0 0.4rem", fontWeight: 600, color: "#333" }}>How to use this page</p>
        <ol style={{ margin: 0, paddingLeft: "1.4rem", lineHeight: 1.9, fontSize: "0.88rem" }}>
          <li>Your campaigns load automatically — click <strong>Sync</strong> to refresh or push ads</li>
          <li>Click <strong>Ingest</strong> on any campaign row to read its ad creatives</li>
          <li>Click <strong>Generate RSA Text</strong> to create AI-powered headline and description variants</li>
          <li>Click <strong>Get Recommendations</strong> — the AI suggests the best combinations to test next</li>
          <li>Click <strong>Sync</strong> to push recommendations to Google Ads as new paused ads</li>
        </ol>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "12px" }}>
        <h2 style={{ margin: 0 }}>Google Ads Campaigns</h2>
        <button
          className="btn-small"
          onClick={handleSync}
          disabled={syncing}
          title="Push generated ads to Google Ads, then refresh campaign data"
        >
          {syncing ? "Syncing…" : "Sync"}
        </button>
        {syncNote && (
          <span className={syncNote.type === "error" ? "error" : syncNote.type === "amber" ? "push-note" : ""}>
            {syncNote.text}
          </span>
        )}
      </div>
      <table className="campaigns-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Daily Budget</th>
            <th>Impressions (7d)</th>
            <th>Clicks (7d)</th>
            <th>Spend (7d)</th>
            <th>CTR (7d)</th>
            <th>CPM (7d)</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {campaigns.map((c) => (
            <>
              <tr key={c.id}>
                <td>{c.name}</td>
                <td>
                  <span className={`status-badge status-${c.status.toLowerCase()}`}>
                    {c.status}
                  </span>
                </td>
                <td>{c.daily_budget != null ? `$${(c.daily_budget / 100).toFixed(2)}` : "—"}</td>
                <td>{fmtInt(c.impressions_7d)}</td>
                <td>{fmtInt(c.clicks_7d)}</td>
                <td>{c.spend_7d != null ? `$${fmt(c.spend_7d)}` : "—"}</td>
                <td>{c.ctr_7d != null ? `${fmt(c.ctr_7d)}%` : "—"}</td>
                <td>{c.cpm_7d != null ? `$${fmt(c.cpm_7d)}` : "—"}</td>
                <td>
                  <button
                    className="btn-small"
                    disabled={ingestingId === c.id}
                    onClick={() =>
                      openStructureId === c.id
                        ? setOpenStructureId(null)
                        : ingestedIds.includes(c.id)
                        ? setOpenStructureId(c.id)
                        : handleIngest(c.id)
                    }
                    title={
                      openStructureId === c.id
                        ? "Hide the creative structure panel"
                        : ingestedIds.includes(c.id)
                        ? "View or re-read this campaign's ad creatives"
                        : "Read this campaign's ad creatives so Adstac.kr can analyse them"
                    }
                  >
                    {ingestingId === c.id
                      ? "Ingesting…"
                      : openStructureId === c.id
                      ? "Hide"
                      : ingestedIds.includes(c.id)
                      ? "Reingest / View"
                      : "Ingest"}
                  </button>
                </td>
              </tr>
              {openStructureId === c.id && (
                <tr key={`${c.id}-structure`}>
                  <td colSpan={9}>
                    <StructurePanel
                      campaignId={c.id}
                      onClose={() => setOpenStructureId(null)}
                    />
                    <div style={{ padding: "8px 0" }}>
                      {genStateById[c.id]?.status === "loading" && (
                        <p>Generating RSA variants…</p>
                      )}
                      {genStateById[c.id]?.status === "done" && (
                        <>
                          <TextGenResults data={genStateById[c.id].data} />
                          <div style={{ marginTop: "8px" }}>
                            {boStateById[c.id]?.status === "loading" && <p>Running BO…</p>}
                            {boStateById[c.id]?.status === "done" && (
                              <BOPicksPanel result={boStateById[c.id].data} />
                            )}
                            {boStateById[c.id]?.status === "error" && (
                              <p className="error">BO failed: {boStateById[c.id].error}</p>
                            )}
                            {(!boStateById[c.id] || boStateById[c.id]?.status === "error") && (
                              <button
                                className="btn-small"
                                onClick={() => handleRunBO(c.id, genStateById[c.id].data.source_ad_id)}
                                title="Run Bayesian Optimisation to suggest the best headline and description combinations to test next"
                              >
                                Get Recommendations
                              </button>
                            )}
                          </div>
                        </>
                      )}
                      {genStateById[c.id]?.status === "error" && (
                        <p className="error">Generation failed: {genStateById[c.id].error}</p>
                      )}
                      {(!genStateById[c.id] || genStateById[c.id]?.status === "error") && (
                        <button
                          className="btn-small"
                          onClick={() => handleGenerateText(c.id)}
                          title="Generate 10 new headline and description variants for this campaign's Responsive Search Ads using AI"
                        >
                          Generate RSA Text
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}
