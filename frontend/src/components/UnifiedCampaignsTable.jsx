import CampaignRow from "./CampaignRow.jsx";

export default function UnifiedCampaignsTable({
  campaigns,
  expandedKey,
  onToggle,
  structureByKey,
  ingestingKey,
  onIngest,
  selectedAdIds,
  onToggleAd,
  pauseWarningByKey = {},
  staleCampaigns = new Set(),
}) {
  if (campaigns.length === 0) {
    return (
      <p className="history-empty" style={{ marginTop: "1rem" }}>
        No campaigns loaded — use Sync Meta or Sync Google to fetch campaigns.
      </p>
    );
  }

  return (
    <table className="campaigns-table">
      <thead>
        <tr>
          <th>Name</th>
          <th>Platform</th>
          <th>Status</th>
          <th>Daily Budget</th>
          <th>7d Impr.</th>
          <th>7d Clicks</th>
          <th>7d Spend</th>
          <th>CTR</th>
          <th>CPM</th>
        </tr>
      </thead>
      <tbody>
        {campaigns.map((c) => {
          const key = `${c.platform}:${c.id}`;
          return (
            <CampaignRow
              key={key}
              campaign={c}
              compositeKey={key}
              isExpanded={expandedKey === key}
              onToggle={onToggle}
              structure={structureByKey[key] ?? null}
              ingesting={ingestingKey === key}
              onIngest={onIngest}
              selectedAdIds={selectedAdIds}
              onToggleAd={onToggleAd}
              parentShouldPause={pauseWarningByKey[key] ?? false}
              isStale={staleCampaigns.has(key)}
            />
          );
        })}
      </tbody>
    </table>
  );
}
