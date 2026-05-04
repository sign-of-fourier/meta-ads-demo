import { useEffect, useState } from "react";
import { getLocalAds, deleteLocalAd } from "../api.js";

const SOURCE_LABELS = {
  ingested: "Meta",
  generated: "Generated",
  real: "Meta",
  masked: "Meta (masked)",
  demo: "Demo",
};

const SOURCE_COLORS = {
  ingested: "#1877f2",
  generated: "#7c3aed",
  real: "#1877f2",
  masked: "#f59e0b",
  demo: "#6b7280",
};

const SLOT_ORDER = ["headline", "primary_text", "description", "cta"];
const SLOT_LABELS = {
  headline: "Headlines",
  primary_text: "Primary Text",
  description: "Descriptions",
  cta: "CTAs",
};

function groupSlots(slots) {
  const groups = {};
  const images = [];
  for (const s of slots) {
    if (s.slot === "image") {
      images.push(s);
    } else {
      if (!groups[s.slot]) groups[s.slot] = [];
      groups[s.slot].push(s);
    }
  }
  for (const key of Object.keys(groups)) {
    groups[key].sort((a, b) => a.slot_index - b.slot_index);
  }
  images.sort((a, b) => a.slot_index - b.slot_index);
  return { groups, images };
}

function AdDetailModal({ ad, onClose }) {
  const { groups, images } = groupSlots(ad.slots);
  const slotKeys = [
    ...SLOT_ORDER.filter((k) => groups[k]),
    ...Object.keys(groups).filter((k) => !SLOT_ORDER.includes(k)),
  ];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-row">
            <span
              className="source-badge"
              style={{ background: SOURCE_COLORS[ad.data_source] || "#6b7280" }}
            >
              {SOURCE_LABELS[ad.data_source] || ad.data_source}
            </span>
            <span className={`status-badge ${ad.lifecycle_status === "active" ? "active" : "paused"}`}>
              {ad.lifecycle_status}
            </span>
            <span className="type-badge">{ad.creative_type}</span>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <p className="modal-ad-id">ID: <code>{ad.ad_id}</code></p>

        {images.length > 0 && (
          <div className="modal-section">
            <h4 className="modal-section-title">Images ({images.length})</h4>
            <div className="modal-image-grid">
              {images.map((img) => (
                <div key={img.slot_index} className="modal-image-cell">
                  <img src={img.value} alt={`Image ${img.slot_index + 1}`} />
                  <span className="modal-image-label">{img.slot_index + 1}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {slotKeys.map((key) => (
          <div key={key} className="modal-section">
            <h4 className="modal-section-title">
              {SLOT_LABELS[key] || key} ({groups[key].length})
            </h4>
            <ol className="modal-variants">
              {groups[key].map((s) => (
                <li key={s.slot_index}>{s.value}</li>
              ))}
            </ol>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function AdsPage() {
  const [ads, setAds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    getLocalAds()
      .then(setAds)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  async function handleDelete(e, ad) {
    e.stopPropagation();
    const confirmed = window.confirm(
      `Delete "${ad.headline || ad.ad_id}"?\n\nIf this ad came from Meta, it will reappear on next sync.`
    );
    if (!confirmed) return;

    setDeleting(ad.ad_id);
    try {
      await deleteLocalAd(ad.ad_id);
      setAds((prev) => prev.filter((a) => a.ad_id !== ad.ad_id));
      if (selected?.ad_id === ad.ad_id) setSelected(null);
    } catch (err) {
      alert(`Failed to delete: ${err.message}`);
    } finally {
      setDeleting(null);
    }
  }

  if (loading) return <p style={{ padding: "1.5rem" }}>Loading ads…</p>;
  if (error) return <p className="error" style={{ padding: "1.5rem" }}>{error}</p>;

  return (
    <div className="ads-page">
      <div className="ads-page-header">
        <h2>Local Ad Library</h2>
        <p className="subtitle">
          All ads stored locally — ingested from Meta and AI-generated. Click a card
          to see all components. Deleting a Meta ad removes it locally only; sync will re-pull it.
        </p>
      </div>

      {ads.length === 0 ? (
        <p style={{ padding: "0.5rem 0", color: "#6b7280" }}>
          No ads yet. Ingest a campaign to populate the library.
        </p>
      ) : (
        <div className="local-ads-grid">
          {ads.map((ad) => (
            <div
              key={ad.ad_id}
              className="local-ad-card"
              onClick={() => setSelected(ad)}
              title="Click to view all components"
            >
              {ad.image_url && (
                <div className="local-ad-thumb">
                  <img src={ad.image_url} alt={ad.headline || ad.ad_id} />
                </div>
              )}
              <div className="local-ad-body">
                <div className="local-ad-badges">
                  <span
                    className="source-badge"
                    style={{ background: SOURCE_COLORS[ad.data_source] || "#6b7280" }}
                  >
                    {SOURCE_LABELS[ad.data_source] || ad.data_source}
                  </span>
                  <span className={`status-badge ${ad.lifecycle_status === "active" ? "active" : "paused"}`}>
                    {ad.lifecycle_status}
                  </span>
                  <span className="type-badge">{ad.creative_type}</span>
                </div>

                <p className="local-ad-headline">
                  {ad.headline || <em style={{ color: "#9ca3af" }}>No headline</em>}
                </p>

                <p className="local-ad-meta">
                  <span>ID: <code>{ad.ad_id}</code></span>
                  <span>{ad.slots.length} slot{ad.slots.length !== 1 ? "s" : ""}</span>
                </p>
                <p className="local-ad-timestamp" title={ad.ingested_at}>
                  {new Date(ad.ingested_at + "Z").toLocaleString()}
                </p>

                <button
                  className="btn-delete"
                  onClick={(e) => handleDelete(e, ad)}
                  disabled={deleting === ad.ad_id}
                >
                  {deleting === ad.ad_id ? "Deleting…" : "Delete"}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {selected && (
        <AdDetailModal ad={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
