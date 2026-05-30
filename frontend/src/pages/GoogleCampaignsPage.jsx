import { useEffect, useState } from "react";
import BOPickCard from "../components/BOPickCard.jsx";
import {
  getGoogleCampaigns,
  ingestGoogleStructure,
  getGoogleStructure,
  generateGoogleTextAds,
  runGoogleBO,
  pushGoogleAds,
  seedScoredVariants,
} from "../api.js";

const STORAGE_KEY = "google_ingested_ids";

const SLOT_LABELS = {
  headline: "Headline",
  description: "Description",
  final_url: "Final URL",
};

function fmt(val, decimals = 2) {
  if (val == null) return "—";
  return typeof val === "number" ? val.toFixed(decimals) : val;
}

function fmtInt(val) {
  if (val == null) return "—";
  return Number(val).toLocaleString();
}

/* ── Creative structure panel ─────────────────────────────────────────────── */
const CREATIVE_LABELS = {
  rsa: "Responsive Search",
  display: "Responsive Display",
  video: "Video Responsive",
  pmax: "Performance Max",
  shopping: "Shopping",
  unknown: "Unknown",
};

function StructurePanel({ structure, campaignId, onClose, onReingest, reingesting }) {
  const header = (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
      <h4 style={{ margin: 0 }}>Creative Structure</h4>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
        <button
          className="btn-small"
          onClick={onReingest}
          disabled={reingesting}
          title="Re-read this campaign's ad creatives from Google"
        >
          {reingesting ? "Re-ingesting…" : "Re-ingest"}
        </button>
        <button onClick={onClose} className="btn-small">Close</button>
      </div>
    </div>
  );

  if (!structure || structure.length === 0) {
    return (
      <div className="structure-panel">
        {header}
        <p style={{ marginTop: "0.5rem", color: "#868e96" }}>No structure data found.</p>
      </div>
    );
  }

  return (
    <div className="structure-panel">
      {header}
      {structure.map((ad) => (
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
          <dl className="slot-list">
            {Object.entries(ad.components).map(([slot, values]) => (
              <div key={slot} className="slot-row">
                <dt>{SLOT_LABELS[slot] ?? slot}</dt>
                <dd>
                  {values.map((v, i) => (
                    <span key={i} className="slot-value">{v ?? <em>—</em>}</span>
                  ))}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </div>
  );
}

/* ── Text gen results ─────────────────────────────────────────────────────── */
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
    <div className="gen-results">
      <p className="bo-meta gen-mode-label">Generated RSA Variants</p>
      {slots.filter((s) => bySlot[s]?.length).map((slot) => (
        <div key={slot} className="gen-slot">
          <h5 className="gen-slot-label">{SLOT_LABELS[slot] || slot}</h5>
          <ol className="gen-slot-list">
            {bySlot[slot].map((v, i) => <li key={i}>{v}</li>)}
          </ol>
        </div>
      ))}
    </div>
  );
}

/* ── BO picks panel ───────────────────────────────────────────────────────── */
function BOPicksPanel({ result, campaignName, seedAdId, onPushed }) {
  if (!result.picks || result.picks.length === 0) {
    return (
      <div className="bo-results">
        <p className="history-empty">
          No candidates yet — ingest the campaign and generate RSA text, then run BO again once
          the embedding pipeline finishes.
        </p>
        <p className="bo-meta">
          Scored: <strong>{result.scored_count}</strong> · Candidates:{" "}
          <strong>{result.candidate_count}</strong>
        </p>
      </div>
    );
  }
  return (
    <div className="bo-results">
      {result.warning && <p className="dyn-warning">{result.warning}</p>}
      <p className="bo-meta">
        <strong>{result.scored_count}</strong> scored variant{result.scored_count !== 1 ? "s" : ""} ·{" "}
        <strong>{result.candidate_count}</strong> candidate{result.candidate_count !== 1 ? "s" : ""}
        {result.scored_count === 0 && (
          <span className="stat-random-note"> — no scored data yet, picks are random. Generate RSA Text first to build candidates.</span>
        )}
      </p>
      {result.picks.map((pick, i) => (
        <BOPickCard
          key={pick.combination_key}
          pick={pick}
          index={i}
          platform="google"
          seedAdId={seedAdId}
          campaignName={campaignName}
          onPushed={onPushed}
        />
      ))}
    </div>
  );
}

/* ── Main page ────────────────────────────────────────────────────────────── */
export default function GoogleCampaignsPage({ onIngest = null, batchedAdIds = [] }) {
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [ingestingId, setIngestingId] = useState(null);
  const [openStructureId, setOpenStructureId] = useState(null);

  // structureById[campaignId] = array of ad structure objects
  const [structureById, setStructureById] = useState({});

  // genStateById[campaignId] = null | { status: "loading"|"done"|"error", data, error }
  const [genStateById, setGenStateById] = useState({});

  // boStateById[campaignId] = null | { status: "loading"|"done"|"error", data, error }
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
  // seedStateById[id] = { n, loading, seeded, warning, error }
  const [seedStateById, setSeedStateById] = useState({});

  useEffect(() => {
    getGoogleCampaigns()
      .then(setCampaigns)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  // On mount, silently reload stored structure for any previously-ingested campaigns
  // so getSeedAdId() works without requiring a manual Re-ingest click.
  useEffect(() => {
    if (campaigns.length === 0) return;
    const toReload = campaigns.filter((c) => ingestedIds.includes(c.id) && !structureById[c.id]);
    if (toReload.length === 0) return;
    toReload.forEach(async (c) => {
      try {
        const structure = await getGoogleStructure(c.id);
        if (structure?.length > 0) {
          setStructureById((prev) => ({ ...prev, [c.id]: structure }));
        }
      } catch {
        // silently ignore — user can still click Re-ingest manually
      }
    });
  }, [campaigns]); // eslint-disable-line react-hooks/exhaustive-deps

  function markIngested(id) {
    setIngestedIds((prev) => {
      const next = prev.includes(id) ? prev : [...prev, id];
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }

  function getSeedAdId(campaignId) {
    return structureById[campaignId]?.[0]?.ad_id ?? null;
  }

  // ── Ingest (first time) or Re-ingest ─────────────────────────────────────
  async function handleIngest(campaignId) {
    setIngestingId(campaignId);
    try {
      await ingestGoogleStructure(campaignId);
      markIngested(campaignId);
      const structure = await getGoogleStructure(campaignId);
      setStructureById((prev) => ({ ...prev, [campaignId]: structure }));
      setOpenStructureId(campaignId);

    } catch (err) {
      alert(`Ingest failed: ${err.message}`);
    } finally {
      setIngestingId(null);
    }
  }

  // ── Toggle the detail row for an already-ingested campaign ───────────────
  function toggleDetail(campaignId) {
    if (openStructureId === campaignId) {
      setOpenStructureId(null);
    } else {
      if (!structureById[campaignId]) {
        getGoogleStructure(campaignId).then((s) => {
          setStructureById((prev) => ({ ...prev, [campaignId]: s }));
        });
      }
      setOpenStructureId(campaignId);
    }
  }

  async function handleGenerateText(campaignId) {
    setGenStateById((prev) => ({ ...prev, [campaignId]: { status: "loading", data: null } }));
    try {
      const result = await generateGoogleTextAds(campaignId);
      setGenStateById((prev) => ({ ...prev, [campaignId]: { status: "done", data: result } }));
    } catch (err) {
      setGenStateById((prev) => ({
        ...prev,
        [campaignId]: { status: "error", data: null, error: err.message },
      }));
    }
  }

  async function handleRunBO(campaignId) {
    setBoStateById((prev) => ({ ...prev, [campaignId]: { status: "loading" } }));
    try {
      let seedAdId = getSeedAdId(campaignId);
      if (!seedAdId) {
        const structure = await getGoogleStructure(campaignId);
        setStructureById((prev) => ({ ...prev, [campaignId]: structure }));
        seedAdId = structure?.[0]?.ad_id;
      }
      if (!seedAdId) throw new Error("No ingested ad found for this campaign.");

      const textSourceId = genStateById[campaignId]?.data?.source_ad_id ?? seedAdId;

      const result = await runGoogleBO(seedAdId, textSourceId);
      setBoStateById((prev) => ({ ...prev, [campaignId]: { status: "done", data: result } }));
    } catch (err) {
      setBoStateById((prev) => ({
        ...prev,
        [campaignId]: { status: "error", error: err.message },
      }));
    }
  }

  async function handleSeedTestData(campaignId) {
    const seedAdId = getSeedAdId(campaignId);
    if (!seedAdId) return;
    const n = seedStateById[campaignId]?.n ?? 5;
    setSeedStateById(prev => ({ ...prev, [campaignId]: { ...prev[campaignId], loading: true, seeded: null, error: null } }));
    try {
      const result = await seedScoredVariants(seedAdId, "google", n, seedAdId);
      setSeedStateById(prev => ({ ...prev, [campaignId]: { ...prev[campaignId], loading: false, seeded: result.seeded, warning: result.warning } }));
    } catch (err) {
      setSeedStateById(prev => ({ ...prev, [campaignId]: { ...prev[campaignId], loading: false, error: err.message } }));
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
        setSyncNote({
          type: "success",
          text: `Pushed ${result.pushed} ad${result.pushed !== 1 ? "s" : ""} to Google Ads.`,
        });
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

  if (loading) return <p>Loading Google campaigns…</p>;

  if (error) {
    const notConnected = error.includes("not connected");
    return (
      <div className="campaigns-page">
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
        <p>No campaigns found for this account.</p>
      </div>
    );
  }

  return (
    <div className="campaigns-page">
      {/* Header + Sync */}
      <div className="campaigns-header">
        <h2>Campaigns</h2>
        <div className="sync-controls">
          <button
            className="btn-primary"
            onClick={handleSync}
            disabled={syncing}
            title="Pull latest Google campaigns; also pushes any generated ads"
          >
            {syncing ? "Syncing…" : "Sync"}
          </button>
        </div>
      </div>

      {syncNote && (
        <p
          className={
            syncNote.type === "error"
              ? "error"
              : syncNote.type === "amber"
              ? "sync-push-pending"
              : "sync-push-note"
          }
          style={{ marginBottom: "0.75rem" }}
        >
          {syncNote.text}
        </p>
      )}

      <table className="campaigns-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Daily Budget</th>
            <th>7d Impr.</th>
            <th>7d Clicks</th>
            <th>7d Spend</th>
            <th>CTR</th>
            <th>CPM</th>
            <th>Actions</th>
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
                <td>
                  {c.daily_budget != null ? `$${(c.daily_budget / 100).toFixed(2)}` : "—"}
                </td>
                <td>{fmtInt(c.impressions_7d)}</td>
                <td>{fmtInt(c.clicks_7d)}</td>
                <td>{c.spend_7d != null ? `$${fmt(c.spend_7d)}` : "—"}</td>
                <td>{c.ctr_7d != null ? `${fmt(c.ctr_7d)}%` : "—"}</td>
                <td>{c.cpm_7d != null ? `$${fmt(c.cpm_7d)}` : "—"}</td>
                <td className="actions-cell">
                  {ingestedIds.includes(c.id) ? (
                    /* Already ingested — show View/Hide toggle */
                    <button
                      className="btn-creatives"
                      onClick={() => toggleDetail(c.id)}
                      title={openStructureId === c.id ? "Hide campaign detail" : "View creative structure and recommendations"}
                    >
                      {openStructureId === c.id ? "Hide" : "View"}
                    </button>
                  ) : (
                    /* Not yet ingested — show Ingest button */
                    <button
                      className="btn-creatives"
                      disabled={ingestingId === c.id}
                      onClick={() => handleIngest(c.id)}
                      title="Read this campaign's ad creatives from Google"
                    >
                      {ingestingId === c.id ? "…" : "Ingest"}
                    </button>
                  )}
                </td>
              </tr>

              {/* ── Expanded detail row ──────────────────────────────────── */}
              {openStructureId === c.id && (
                <tr key={`${c.id}-detail`} className="history-row">
                  <td colSpan={9}>
                    {/* Structure panel — Re-ingest button lives inside here */}
                    <StructurePanel
                      structure={structureById[c.id]}
                      campaignId={c.id}
                      onClose={() => setOpenStructureId(null)}
                      onReingest={() => handleIngest(c.id)}
                      reingesting={ingestingId === c.id}
                    />

                    {/* Action buttons + results */}
                    <div className="recommendations-section">
                      <div className="recommendations-header">
                        <button
                          className="btn-primary"
                          onClick={() => handleRunBO(c.id)}
                          disabled={boStateById[c.id]?.status === "loading"}
                          title="Bayesian Optimisation: suggests the best headline and description combinations to test next"
                        >
                          {boStateById[c.id]?.status === "loading"
                            ? "Running…"
                            : "Get Recommendations"}
                        </button>
                        <button
                          className="btn-secondary"
                          onClick={() => handleGenerateText(c.id)}
                          disabled={genStateById[c.id]?.status === "loading"}
                          title="Generate 10 new headline and description variants for this campaign's RSAs"
                        >
                          {genStateById[c.id]?.status === "loading"
                            ? "Generating…"
                            : "Generate RSA Text"}
                        </button>
                        {onIngest && (() => {
                          const seedAdId = structureById[c.id]?.[0]?.ad_id;
                          if (!seedAdId) return null;
                          const inBatch = batchedAdIds.includes(seedAdId);
                          return (
                            <button
                              className={inBatch ? "btn-batch-active" : "btn-batch"}
                              onClick={() => onIngest({ platform: "google", seed_ad_id: seedAdId, text_source_id: seedAdId, label: c.name })}
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

                      {/* Text gen results */}
                      {genStateById[c.id]?.status === "error" && (
                        <p className="error">{genStateById[c.id].error}</p>
                      )}
                      {genStateById[c.id]?.status === "done" && (
                        <TextGenResults data={genStateById[c.id].data} />
                      )}

                      {/* BO results */}
                      {boStateById[c.id]?.status === "error" && (
                        <p className="error">{boStateById[c.id].error}</p>
                      )}
                      {boStateById[c.id]?.status === "done" && (
                        <BOPicksPanel
                          result={boStateById[c.id].data}
                          campaignName={c.name}
                          seedAdId={structureById[c.id]?.[0]?.ad_id}
                          onPushed={(pick, platformAdId, adName) => {
                            setBoStateById(prev => ({
                              ...prev,
                              [c.id]: {
                                ...prev[c.id],
                                data: {
                                  ...prev[c.id].data,
                                  picks: prev[c.id].data.picks.map(pk =>
                                    pk.combination_key === pick.combination_key
                                      ? { ...pk, already_pushed: true, push_status: "paused", ad_name: adName, platform_ad_id: platformAdId }
                                      : pk
                                  ),
                                },
                              },
                            }));
                          }}
                        />
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
