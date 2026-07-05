import { useState, useEffect } from "react";
import {
  listManualCampaigns,
  createManualCampaign,
  renameManualCampaign,
  deleteManualCampaign,
} from "../api.js";
import ManualCampaignView from "../components/ManualCampaignView.jsx";

function CampaignCard({ campaign, onSelect, onRename, onDelete }) {
  const [renaming, setRenaming] = useState(false);
  const [nameVal, setNameVal] = useState(campaign.name);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  async function handleRename() {
    if (!nameVal.trim()) return;
    setSaving(true);
    try {
      await renameManualCampaign(campaign.id, nameVal.trim());
      onRename(campaign.id, nameVal.trim());
      setRenaming(false);
    } catch (e) {
      alert(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!confirm(`Delete "${campaign.name}" and all its ads?`)) return;
    setDeleting(true);
    try {
      await deleteManualCampaign(campaign.id);
      onDelete(campaign.id);
    } catch (e) {
      alert(e.message);
      setDeleting(false);
    }
  }

  return (
    <div className="manual-campaign-card">
      <div className="manual-campaign-card-body">
        {renaming ? (
          <span className="manual-campaign-card-name">
            <input
              autoFocus
              value={nameVal}
              onChange={(e) => setNameVal(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleRename(); if (e.key === "Escape") setRenaming(false); }}
              className="manual-rename-input"
            />
            <button className="btn-small btn-activate-clone" disabled={saving} onClick={handleRename}>
              {saving ? "…" : "Save"}
            </button>
            <button className="btn-small" onClick={() => setRenaming(false)}>Cancel</button>
          </span>
        ) : (
          <button className="btn-link manual-campaign-card-name" onClick={() => onSelect(campaign)}>
            {campaign.name}
          </button>
        )}
        <div className="manual-campaign-card-stats">
          <span>{campaign.ad_count} ad{campaign.ad_count !== 1 ? "s" : ""}</span>
          <span>·</span>
          <span>{campaign.obs_count} observation{campaign.obs_count !== 1 ? "s" : ""}</span>
        </div>
      </div>
      <div className="manual-campaign-card-actions">
        <button className="btn-small" onClick={() => setRenaming(true)}>Rename</button>
        <button className="btn-small btn-danger" disabled={deleting} onClick={handleDelete}>
          {deleting ? "…" : "Delete"}
        </button>
      </div>
    </div>
  );
}

export default function StudioPage() {
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    listManualCampaigns()
      .then(setCampaigns)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  async function handleCreate() {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const camp = await createManualCampaign(newName.trim());
      setCampaigns((prev) => [camp, ...prev]);
      setNewName("");
    } catch (e) {
      alert(e.message);
    } finally {
      setCreating(false);
    }
  }

  function handleRename(id, name) {
    setCampaigns((prev) => prev.map((c) => (c.id === id ? { ...c, name } : c)));
  }

  function handleDelete(id) {
    setCampaigns((prev) => prev.filter((c) => c.id !== id));
    if (selected?.id === id) setSelected(null);
  }

  if (selected) {
    return (
      <ManualCampaignView
        campaign={selected}
        onBack={() => setSelected(null)}
      />
    );
  }

  return (
    <div className="studio-page">
      <div className="studio-header">
        <h2>Studio</h2>
        <p className="studio-desc">
          Create manual campaigns to run Bayesian Optimisation on any platform —
          TikTok, LinkedIn, Pinterest, or anywhere you run ads without an API connection.
          Enter your own metrics and let AdStackers recommend the best combinations.
        </p>
      </div>

      <div className="studio-create-bar">
        <input
          type="text"
          placeholder="New campaign name…"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          className="studio-name-input"
        />
        <button className="btn-primary" disabled={creating || !newName.trim()} onClick={handleCreate}>
          {creating ? "Creating…" : "Create campaign"}
        </button>
      </div>

      {loading ? (
        <p className="history-empty">Loading…</p>
      ) : campaigns.length === 0 ? (
        <p className="history-empty">
          No campaigns yet — create one above to get started.
        </p>
      ) : (
        <div className="studio-campaign-list">
          {campaigns.map((c) => (
            <CampaignCard
              key={c.id}
              campaign={c}
              onSelect={setSelected}
              onRename={handleRename}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
    </div>
  );
}
