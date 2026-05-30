import AdsPanel from "./AdsPanel.jsx";

const PLATFORM_LABELS = { meta: "Meta", google: "Google" };

function fmt(val, prefix = "", suffix = "") {
  if (val == null) return "—";
  return `${prefix}${Number(val).toFixed(2)}${suffix}`;
}

function fmtInt(val) {
  if (val == null) return "—";
  return Number(val).toLocaleString();
}

function formatBudget(cents) {
  if (cents == null) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

export default function CampaignRow({
  campaign,
  compositeKey,
  isExpanded,
  onToggle,
  structure,
  ingesting,
  onIngest,
  selectedAdIds,
  onToggleAd,
}) {
  const {
    id,
    name,
    platform,
    status,
    daily_budget,
    impressions_7d,
    clicks_7d,
    spend_7d,
    ctr_7d,
    cpm_7d,
  } = campaign;

  return (
    <>
      <tr>
        <td>
          <button
            className="btn-link campaign-name"
            onClick={() => onToggle(compositeKey)}
          >
            {name}
          </button>
        </td>
        <td>
          <span className={`platform-badge platform-badge-${platform}`}>
            {PLATFORM_LABELS[platform] ?? platform}
          </span>
        </td>
        <td>
          <span className={`status-badge ${status === "ACTIVE" ? "active" : "paused"}`}>
            {status}
          </span>
        </td>
        <td>{formatBudget(daily_budget)}</td>
        <td>{fmtInt(impressions_7d)}</td>
        <td>{fmtInt(clicks_7d)}</td>
        <td>{fmt(spend_7d, "$")}</td>
        <td>{fmt(ctr_7d, "", "%")}</td>
        <td>{fmt(cpm_7d, "$")}</td>
      </tr>
      {isExpanded && (
        <tr className="history-row">
          <td colSpan={9}>
            <AdsPanel
              structure={structure}
              ingesting={ingesting}
              onIngest={() => onIngest(id, platform)}
              platform={platform}
              campaignName={name}
              selectedAdIds={selectedAdIds}
              onToggleAd={onToggleAd}
            />
          </td>
        </tr>
      )}
    </>
  );
}
