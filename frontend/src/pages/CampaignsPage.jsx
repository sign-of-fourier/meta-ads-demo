import { useEffect, useState } from "react";
import {
  getCampaigns,
  pauseCampaign,
  resumeCampaign,
  getCampaignHistory,
  getIngestPreview,
  runIngest,
} from "../api.js";

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
                  <td>
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
                  </td>
                </tr>

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
