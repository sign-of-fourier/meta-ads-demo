import { useState, useEffect, useCallback } from "react";
import {
  listManualAds,
  deleteManualAd,
  listManualCombinations,
  scoreManualCombination,
  runBO,
} from "../api.js";
import ManualTemplateForm from "./ManualTemplateForm.jsx";
import ManualStaticForm from "./ManualStaticForm.jsx";
import CombinationScoreTable from "./CombinationScoreTable.jsx";

function AdPanel({ ad, onDelete, onRefreshAds }) {
  const [combos, setCombos] = useState(null);
  const [loadingCombos, setLoadingCombos] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [boState, setBoState] = useState(null);
  const [deleting, setDeleting] = useState(false);

  // slot names for the text columns (image slot is displayed separately per row)
  const slotNames = ad.slots.map((s) => s.name).filter((n) => n !== "image");

  const loadCombos = useCallback(async () => {
    setLoadingCombos(true);
    try {
      const data = await listManualCombinations(ad.ad_id);
      setCombos(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingCombos(false);
    }
  }, [ad.ad_id]);

  useEffect(() => {
    if (expanded) loadCombos();
  }, [expanded, loadCombos]);

  async function handleDelete() {
    if (!confirm("Delete this ad and all its observations?")) return;
    setDeleting(true);
    try {
      await deleteManualAd(ad.ad_id);
      onDelete(ad.ad_id);
    } catch (e) {
      alert(e.message);
      setDeleting(false);
    }
  }

  async function handleRunBO() {
    setBoState({ status: "loading" });
    try {
      const result = await runBO(ad.ad_id, ad.ad_id);
      setBoState({ status: "done", picks: result.picks || [] });
      // Refresh combos so BO picks show up in the table
      loadCombos();
    } catch (e) {
      setBoState({ status: "error", error: e.message });
    }
  }

  const isDynamic = ad.creative_type === "dynamic";

  return (
    <div className="manual-ad-panel">
      <div className="manual-ad-header">
        <div className="manual-ad-meta">
          {ad.image_urls?.length > 0 && (
            <div className="manual-ad-image-strip">
              {ad.image_urls.map((url, idx) => (
                <img
                  key={idx}
                  src={url}
                  alt=""
                  className="manual-ad-thumb"
                  onError={(e) => { e.target.style.display = "none"; }}
                />
              ))}
            </div>
          )}
          <span className={`creative-type-badge ${isDynamic ? "dynamic" : "static"}`}>
            {isDynamic ? "Template" : "Static"}
          </span>
          <span className="manual-ad-id">{ad.ad_id}</span>
          <span className="manual-ad-slots-summary">
            {ad.slots.map((s) => `${s.name} (${s.values.length})`).join(" × ")}
          </span>
          {ad.obs_count > 0 && (
            <span className="manual-ad-obs-count">{ad.obs_count} obs</span>
          )}
        </div>
        <div className="manual-ad-actions">
          {isDynamic && (
            <button
              className="btn-small"
              onClick={() => setExpanded((p) => !p)}
            >
              {expanded ? "Hide combos" : "View combos"}
            </button>
          )}
          {isDynamic && (
            <button
              className="btn-small btn-activate-clone"
              onClick={handleRunBO}
              disabled={boState?.status === "loading"}
            >
              {boState?.status === "loading" ? "Running…" : "Run BO"}
            </button>
          )}
          <button
            className="btn-small btn-danger"
            onClick={handleDelete}
            disabled={deleting}
          >
            {deleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>

      {boState?.status === "done" && boState.picks?.length > 0 && (
        <div className="bo-result-strip">
          <strong>BO top pick:</strong>{" "}
          {Object.entries(boState.picks[0]?.combination || {})
            .map(([k, v]) => `${k}: ${v}`)
            .join(" · ")}
        </div>
      )}
      {boState?.status === "error" && (
        <p className="error-inline" style={{ paddingLeft: 12 }}>{boState.error}</p>
      )}

      {expanded && isDynamic && (
        <div className="manual-combos-section">
          {loadingCombos ? (
            <p className="history-empty">Loading combinations…</p>
          ) : (
            <CombinationScoreTable
              adId={ad.ad_id}
              combos={combos}
              slotNames={slotNames}
              onRefresh={loadCombos}
            />
          )}
        </div>
      )}
    </div>
  );
}

export default function ManualCampaignView({ campaign, onBack }) {
  const [ads, setAds] = useState(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(null); // null | "dynamic" | "static"

  const loadAds = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listManualAds(campaign.id);
      setAds(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [campaign.id]);

  useEffect(() => {
    loadAds();
  }, [loadAds]);

  function handleAdCreated() {
    setCreating(null);
    loadAds();
  }

  function handleAdDeleted(adId) {
    setAds((prev) => prev.filter((a) => a.ad_id !== adId));
  }

  return (
    <div className="manual-campaign-view">
      <div className="manual-campaign-header">
        <button className="btn-link" onClick={onBack}>← Campaigns</button>
        <h2 className="manual-campaign-name">{campaign.name}</h2>
      </div>

      <div className="manual-add-bar">
        {!creating ? (
          <>
            <button
              className="btn-primary"
              onClick={() => setCreating("dynamic")}
            >
              + Dynamic Template
            </button>
            <button
              className="btn-small"
              onClick={() => setCreating("static")}
              style={{ marginLeft: 8 }}
            >
              + Static Ad
            </button>
          </>
        ) : creating === "dynamic" ? (
          <ManualTemplateForm
            campaignId={campaign.id}
            onCreated={handleAdCreated}
            onCancel={() => setCreating(null)}
          />
        ) : (
          <ManualStaticForm
            campaignId={campaign.id}
            onCreated={handleAdCreated}
            onCancel={() => setCreating(null)}
          />
        )}
      </div>

      {loading ? (
        <p className="history-empty">Loading…</p>
      ) : ads?.length === 0 ? (
        <p className="history-empty">
          No ads yet — create a Dynamic Template to start exploring combinations.
        </p>
      ) : (
        <div className="manual-ads-list">
          {ads.map((ad) => (
            <AdPanel
              key={ad.ad_id}
              ad={ad}
              onDelete={handleAdDeleted}
              onRefreshAds={loadAds}
            />
          ))}
        </div>
      )}
    </div>
  );
}
