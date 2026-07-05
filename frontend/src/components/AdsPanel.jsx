import { useState } from "react";
import { createPortal } from "react-dom";

const UNSUPPORTED_TYPES = new Set(["shopping", "unknown"]);

const CREATIVE_LABELS = {
  rsa: "RSA",
  display: "Display",
  video: "Video",
  pmax: "pMax",
  shopping: "Shopping",
  unknown: "Unknown",
  dynamic: "Dynamic",
  static: "Static",
};

const SLOT_LABELS = {
  headline: "Headline",
  description: "Description",
  primary_text: "Primary text",
  final_url: "Final URL",
  cta: "CTA",
  image: "Image",
};

function imageNameFromUrl(url) {
  if (!url) return url;
  try {
    return new URL(url, "http://x").pathname.split("/").pop() || url;
  } catch {
    return url.split("/").pop() || url;
  }
}

function StatRow({ label, value }) {
  return (
    <div className="slot-row">
      <dt>{label}</dt>
      <dd><span className="slot-value">{value}</span></dd>
    </div>
  );
}

export function AdPreviewModal({ ad, onClose }) {
  const components = ad.components ?? {};
  const slots = Object.entries(components).filter(([, vals]) => vals && vals.length > 0);
  const stats = ad.clone_stats;

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3 className="modal-title">
            <span className={`creative-type-badge ${ad.creative_type}`} style={{ marginRight: "0.5rem" }}>
              {CREATIVE_LABELS[ad.creative_type] ?? ad.creative_type}
            </span>
            {ad.ad_name || ad.ad_id}
          </h3>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <p className="modal-hint" style={{ marginBottom: "0.75rem" }}>
          <code style={{ fontSize: "0.75rem" }}>{ad.ad_id}</code>
          {ad.is_pushed_clone && (
            <span className="push-status-badge push-status-running" style={{ marginLeft: "0.5rem" }}>
              Pushed clone
            </span>
          )}
        </p>

        {stats && (
          <div style={{ marginBottom: "1rem" }}>
            <p style={{ fontSize: "0.8rem", fontWeight: 600, color: "#374151", marginBottom: "0.4rem" }}>
              Performance
              {stats.synthetic && (
                <span style={{ fontWeight: 400, color: "#9ca3af", marginLeft: "0.4rem" }}>(synthetic)</span>
              )}
            </p>
            <dl className="slot-list modal-slot-list">
              {stats.push_status && <StatRow label="Status" value={stats.push_status} />}
              <StatRow label="Impressions" value={(stats.impressions ?? 0).toLocaleString()} />
              {stats.clicks != null && <StatRow label="Clicks" value={stats.clicks.toLocaleString()} />}
              <StatRow
                label="CTR"
                value={stats.ctr != null ? `${(stats.ctr * 100).toFixed(2)}%` : "—"}
              />
              {stats.spend != null && <StatRow label="Spend" value={`$${stats.spend.toFixed(2)}`} />}
              {stats.cpm != null && <StatRow label="CPM" value={`$${stats.cpm.toFixed(2)}`} />}
              {stats.days_running != null && <StatRow label="Days running" value={stats.days_running} />}
              {stats.converged != null && (
                <StatRow label="Converged" value={stats.converged ? "Yes" : "No (need 500 impr.)"} />
              )}
            </dl>
          </div>
        )}

        {slots.length === 0 ? (
          <p className="history-empty">No component data available.</p>
        ) : (
          <>
            <p style={{ fontSize: "0.8rem", fontWeight: 600, color: "#374151", marginBottom: "0.4rem" }}>
              Creative
            </p>
            <dl className="slot-list modal-slot-list">
              {slots.map(([slot, values]) => {
                const isImage = slot === "image";
                const displayValues = values.filter(Boolean);
                return (
                  <div key={slot} className="slot-row" style={{ alignItems: "flex-start" }}>
                    <dt>{SLOT_LABELS[slot] ?? slot}</dt>
                    <dd>
                      {displayValues.length <= 1 ? (
                        <span className="slot-value">
                          {isImage ? imageNameFromUrl(displayValues[0]) : (displayValues[0] || <em>—</em>)}
                        </span>
                      ) : (
                        <ul className="slot-value-list">
                          {displayValues.map((v, i) => (
                            <li key={i} className="slot-value">
                              {isImage ? imageNameFromUrl(v) : v}
                            </li>
                          ))}
                        </ul>
                      )}
                    </dd>
                  </div>
                );
              })}
            </dl>
          </>
        )}
      </div>
    </div>,
    document.body
  );
}

export default function AdsPanel({
  structure,
  ingesting,
  onIngest,
  platform,
  campaignName,
  selectedAdIds,
  onToggleAd,
}) {
  const [previewAd, setPreviewAd] = useState(null);

  if (ingesting) {
    return <p className="ads-panel-status">Ingesting…</p>;
  }

  if (!structure) {
    return (
      <div className="ads-panel-empty">
        <p className="ads-panel-hint">Ingest this campaign to see its ads.</p>
        <button className="btn-primary" onClick={onIngest}>
          Ingest
        </button>
      </div>
    );
  }

  if (structure.length === 0) {
    return (
      <div className="ads-panel-empty">
        <p className="ads-panel-hint">No ads found in this campaign.</p>
        <button className="btn-small" onClick={onIngest}>Re-ingest</button>
      </div>
    );
  }

  return (
    <div className="ads-panel">
      <div className="ads-panel-header">
        <span className="ads-panel-count">
          {structure.length} ad{structure.length !== 1 ? "s" : ""}
        </span>
        <button className="btn-small" onClick={onIngest}>Re-ingest</button>
      </div>
      <div className="ads-panel-list">
        {structure.map((ad) => {
          const disabled = UNSUPPORTED_TYPES.has(ad.creative_type);
          const rowKey = `${platform}:${ad.ad_id}`;
          const isSelected = selectedAdIds.has(rowKey);
          const stats = ad.clone_stats;
          return (
            <div
              key={ad.ad_id}
              className={[
                "ad-checkbox-row",
                disabled ? "ad-checkbox-row-disabled" : "",
                isSelected ? "ad-checkbox-row-selected" : "",
                !disabled ? "ad-checkbox-row-clickable" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={() => !disabled && setPreviewAd(ad)}
            >
              <input
                type="checkbox"
                checked={isSelected}
                disabled={disabled}
                onClick={e => e.stopPropagation()}
                onChange={() =>
                  onToggleAd({
                    platform,
                    seed_ad_id: ad.ad_id,
                    label: `${campaignName} — ${ad.ad_id}`,
                  })
                }
              />
              <span className={`creative-type-badge ${ad.creative_type}`}>
                {CREATIVE_LABELS[ad.creative_type] ?? ad.creative_type}
              </span>
              <span className="ad-id-text">{ad.ad_id}</span>
              {disabled ? (
                <span className="ad-unsupported-note">Not supported for BO</span>
              ) : stats ? (
                <span className="ad-row-stats">
                  {(stats.impressions ?? 0).toLocaleString()} impr.
                  {stats.ctr != null && <> · {(stats.ctr * 100).toFixed(1)}% CTR</>}
                  {stats.spend != null && <> · ${stats.spend.toFixed(0)}</>}
                  {stats.synthetic && <span className="ad-row-stats-synthetic"> ~</span>}
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
      {previewAd && (
        <AdPreviewModal ad={previewAd} onClose={() => setPreviewAd(null)} />
      )}
    </div>
  );
}
