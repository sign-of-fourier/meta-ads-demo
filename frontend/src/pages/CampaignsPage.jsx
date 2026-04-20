import { useEffect, useState } from "react";
import {
  getCampaigns,
  pauseCampaign,
  resumeCampaign,
  getCampaignHistory,
  getIngestPreview,
  runIngest,
  ingestCampaignStructure,
  getCampaignStructure,
  getCampaignSuggestions,
  confirmSuggestion,
} from "../api.js";

const SLOT_LABELS = {
  headline: "Headline",
  description: "Description",
  primary_text: "Primary text",
  image: "Image",
};

const DEPLOYMENT_LABELS = {
  suggested: "Suggested",
  pending_confirmation: "Pending",
  created_static: "Created",
  active_static: "Active",
  replaced_static: "Replaced",
  rejected: "Rejected",
};

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

function StructurePanel({ data, onDismiss, onConfirmSuggestion, confirmingId }) {
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
          {ads.map((ad) => (
            <div key={ad.ad_id} className="structure-ad">
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
          ))}
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
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [actionLoading, setActionLoading] = useState({});
  const [expandedId, setExpandedId] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Ingest state
  const [ingestPanel, setIngestPanel] = useState(null); // null | "loading" | { preview } | "ingesting" | { result }
  const [ingestError, setIngestError] = useState(null);

  // Per-campaign structural ingest state
  // structureById[campaignId] = null | "loading" | { summary, ads, suggestions } | { error }
  const [structureById, setStructureById] = useState({});
  // Which suggestion id is currently being confirmed (for loading state)
  const [confirmingId, setConfirmingId] = useState(null);

  useEffect(() => {
    getCampaigns()
      .then(setCampaigns)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

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

  async function handlePreviewIngest() {
    setIngestError(null);
    setIngestPanel("loading");
    try {
      const preview = await getIngestPreview();
      setIngestPanel({ preview });
    } catch (err) {
      setIngestError(err.message);
      setIngestPanel(null);
    }
  }

  async function handleConfirmIngest() {
    setIngestError(null);
    setIngestPanel("ingesting");
    try {
      const result = await runIngest();
      setIngestPanel({ result });
    } catch (err) {
      setIngestError(err.message);
      setIngestPanel(null);
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
    } catch (err) {
      setStructureById((prev) => ({ ...prev, [campaignId]: { error: err.message } }));
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
        <button className="btn-primary" onClick={handlePreviewIngest}>
          Preview &amp; Ingest
        </button>
      </div>

      {ingestError && <p className="error">{ingestError}</p>}

      {ingestPanel === "loading" && (
        <div className="ingest-panel">
          <p>Fetching data from Meta...</p>
        </div>
      )}

      {ingestPanel === "ingesting" && (
        <div className="ingest-panel">
          <p>Saving snapshots to database...</p>
        </div>
      )}

      {ingestPanel?.result && (
        <div className="ingest-panel ingest-success">
          <p>
            Ingested <strong>{ingestPanel.result.campaigns_saved}</strong> campaign
            snapshot{ingestPanel.result.campaigns_saved !== 1 ? "s" : ""} for account{" "}
            <code>{ingestPanel.result.ad_account_id}</code>.
          </p>
          <button className="btn-secondary" onClick={() => setIngestPanel(null)}>
            Dismiss
          </button>
        </div>
      )}

      {ingestPanel?.preview && (
        <div className="ingest-panel">
          <div className="ingest-panel-header">
            <h3>Ingest Preview</h3>
            <span className="ingest-panel-subtitle">
              Data pulled live from Meta — not yet saved
            </span>
          </div>

          <h4>Campaigns ({ingestPanel.preview.campaigns.length})</h4>
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
              </tr>
            </thead>
            <tbody>
              {ingestPanel.preview.campaigns.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}</td>
                  <td>
                    <span className={`status-badge ${c.status === "ACTIVE" ? "active" : "paused"}`}>
                      {c.status}
                    </span>
                  </td>
                  <td>{formatBudget(c.daily_budget)}</td>
                  <td>{fmt(c.spend_7d, "$")}</td>
                  <td>{fmtInt(c.impressions_7d)}</td>
                  <td>{fmtInt(c.clicks_7d)}</td>
                  <td>{fmt(c.ctr_7d, "", "%")}</td>
                  <td>{fmt(c.cpm_7d, "$")}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h4 style={{ marginTop: "1rem" }}>Ads ({ingestPanel.preview.ads.length})</h4>
          {ingestPanel.preview.ads.length === 0 ? (
            <p className="history-empty">No ads found in this ad account.</p>
          ) : (
            <table className="campaigns-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Campaign ID</th>
                  <th>Ad Set ID</th>
                  <th>Body</th>
                </tr>
              </thead>
              <tbody>
                {ingestPanel.preview.ads.map((a) => (
                  <tr key={a.id}>
                    <td>{a.name || `Ad ${a.id}`}</td>
                    <td>
                      <span className={`status-badge ${a.status === "ACTIVE" ? "active" : "paused"}`}>
                        {a.status || "–"}
                      </span>
                    </td>
                    <td><code>{a.campaign_id || "–"}</code></td>
                    <td><code>{a.adset_id || "–"}</code></td>
                    <td className="ad-body-preview">{a.body || "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="ingest-actions">
            <button className="btn-primary" onClick={handleConfirmIngest}>
              Confirm Ingest
            </button>
            <button className="btn-secondary" onClick={() => setIngestPanel(null)}>
              Cancel
            </button>
          </div>
        </div>
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
                      title="Ingest and view creative structure"
                    >
                      {structureById[c.id] === "loading" ? "..." : "Creatives"}
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
                          No stored snapshots yet. Use "Preview &amp; Ingest" to save snapshots.
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
