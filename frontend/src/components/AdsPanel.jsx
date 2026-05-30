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

export default function AdsPanel({
  structure,
  ingesting,
  onIngest,
  platform,
  campaignName,
  selectedAdIds,
  onToggleAd,
}) {
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
          return (
            <label
              key={ad.ad_id}
              className={[
                "ad-checkbox-row",
                disabled ? "ad-checkbox-row-disabled" : "",
                isSelected ? "ad-checkbox-row-selected" : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              <input
                type="checkbox"
                checked={isSelected}
                disabled={disabled}
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
              {disabled && (
                <span className="ad-unsupported-note">Not supported for BO</span>
              )}
            </label>
          );
        })}
      </div>
    </div>
  );
}
