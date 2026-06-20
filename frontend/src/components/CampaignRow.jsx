import { useState } from "react";
import { AdPreviewModal } from "./AdsPanel.jsx";
import { activatePick, pauseAd, retainPick } from "../api.js";

const PLATFORM_LABELS = { meta: "Meta", google: "Google" };

const UNSUPPORTED_TYPES = new Set(["shopping", "unknown"]);

function fmt(val, prefix = "", suffix = "", decimals = 2) {
  if (val == null) return "—";
  return `${prefix}${Number(val).toFixed(decimals)}${suffix}`;
}

function fmtInt(val) {
  if (val == null) return "—";
  return Number(val).toLocaleString();
}

function formatBudget(cents) {
  if (cents == null) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

function StatusBadge({ status }) {
  if (!status || status === "UNKNOWN") return <span className="status-badge paused">—</span>;
  const isActive = ["ACTIVE", "ENABLED"].includes(status);
  return (
    <span className={`status-badge ${isActive ? "active" : "paused"}`}>
      {status}
    </span>
  );
}

function CloneStatusBadge({ cloneStatus }) {
  const cfg = {
    clone_paused:      { label: "Adstac.kr — Paused",      cls: "clone-paused" },
    clone_active:      { label: "Adstac.kr — Testing",     cls: "clone-active" },
    clone_converged:   { label: "Adstac.kr — Done",        cls: "clone-converged" },
    clone_invalidated: { label: "Adstac.kr — Invalidated", cls: "clone-invalidated" },
    clone_retained:    { label: "Adstac.kr — Retained",    cls: "clone-retained" },
  };
  const { label, cls } = cfg[cloneStatus] ?? { label: "Adstac.kr clone", cls: "" };
  return <span className={`clone-status-badge clone-status-${cls}`}>{label}</span>;
}

function classifyAd(ad) {
  if (ad.is_pushed_clone) return "bo_clone";
  if (ad.creative_type === "dynamic" || ad.creative_type === "rsa") return "template";
  return "original_static";
}

function AdRow({ ad, platform, campaignName, isSelected, onToggleAd, onPreview }) {
  const [activating, setActivating] = useState(false);
  const [activateError, setActivateError] = useState(null);
  const [pausing, setPausing] = useState(false);
  const [pauseError, setPauseError] = useState(null);
  const [localPaused, setLocalPaused] = useState(false);
  const [retaining, setRetaining] = useState(false);
  const [localCloneStatus, setLocalCloneStatus] = useState(null);

  const disabled = UNSUPPORTED_TYPES.has(ad.creative_type);
  const adType = classifyAd(ad);
  const stats = ad.clone_stats;
  const cloneStatus = localCloneStatus ?? stats?.clone_status ?? null;
  const effectiveStatus = ad.effective_status;
  const isActive = !localPaused && ["ACTIVE", "ENABLED"].includes(effectiveStatus);

  async function handlePause(platformAdId) {
    setPausing(true);
    setPauseError(null);
    try {
      await pauseAd({ platform, platformAdId });
      setLocalPaused(true);
      if (adType === "bo_clone") setLocalCloneStatus("clone_paused");
    } catch (err) {
      setPauseError(err.message || "Pause failed");
    } finally {
      setPausing(false);
    }
  }

  async function handleActivate(platformAdId) {
    setActivating(true);
    setActivateError(null);
    try {
      await activatePick({ platform, platformAdId });
      if (adType === "bo_clone") setLocalCloneStatus("clone_active");
      if (adType === "template") setLocalPaused(false);
    } catch (err) {
      setActivateError(err.message || "Activate failed");
    } finally {
      setActivating(false);
    }
  }

  async function handleRetain(comboId) {
    setRetaining(true);
    try {
      await retainPick(comboId);
      setLocalCloneStatus("clone_retained");
    } finally {
      setRetaining(false);
    }
  }

  // ── Status cell (col 3) ──────────────────────────────────────────────────────
  let statusCell;
  if (adType === "bo_clone" && cloneStatus) {
    statusCell = <CloneStatusBadge cloneStatus={cloneStatus} />;
  } else if (localPaused) {
    statusCell = <StatusBadge status="PAUSED" />;
  } else {
    statusCell = <StatusBadge status={effectiveStatus} />;
  }

  // ── Action cell (col 4, reuses "Daily Budget" column for ad rows) ────────────
  let actionCell = null;
  if (adType === "template" && isActive) {
    actionCell = (
      <>
        <button
          className="btn-small btn-pause-template"
          disabled={pausing}
          onClick={(e) => { e.stopPropagation(); handlePause(ad.ad_id); }}
          title="Pause this template so clone CTR is isolated"
        >
          {pausing ? "Pausing…" : "Pause Template"}
        </button>
        {pauseError && <span className="error-inline">{pauseError}</span>}
      </>
    );
  } else if (adType === "template" && !isActive) {
    actionCell = (
      <>
        <button
          className="btn-small btn-activate-clone"
          disabled={activating}
          onClick={(e) => { e.stopPropagation(); handleActivate(ad.ad_id); }}
        >
          {activating ? "Activating…" : "Activate"}
        </button>
        {activateError && <span className="error-inline">{activateError}</span>}
      </>
    );
  } else if (adType === "bo_clone") {
    const effectiveCloneStatus = localCloneStatus ?? cloneStatus;
    if (effectiveCloneStatus === "clone_paused") {
      const padId = stats?.platform_ad_id ?? ad.ad_id;
      actionCell = (
        <>
          <button
            className="btn-small btn-activate-clone"
            disabled={activating}
            onClick={(e) => { e.stopPropagation(); handleActivate(padId); }}
          >
            {activating ? "Activating…" : "Activate"}
          </button>
          {activateError && <span className="error-inline">{activateError}</span>}
        </>
      );
    } else if (effectiveCloneStatus === "clone_converged") {
      actionCell = (
        <button
          className="btn-small"
          disabled={retaining}
          onClick={(e) => { e.stopPropagation(); handleRetain(stats?.combo_id); }}
        >
          {retaining ? "Saving…" : "Keep Running"}
        </button>
      );
    }
  } else if (adType === "original_static" && !isActive && !disabled) {
    actionCell = (
      <>
        <button
          className="btn-small btn-activate-clone"
          disabled={activating}
          onClick={(e) => { e.stopPropagation(); handleActivate(ad.ad_id); }}
        >
          {activating ? "Activating…" : "Activate"}
        </button>
        {activateError && <span className="error-inline">{activateError}</span>}
      </>
    );
  }

  // ── Type badge in col 1 ──────────────────────────────────────────────────────
  const typeBadge = (() => {
    if (adType === "template") {
      return (
        <span className={`creative-type-badge ${ad.creative_type}`}>
          {ad.creative_type === "rsa" ? "RSA" : "Template"}
        </span>
      );
    }
    if (adType === "bo_clone") {
      return <span className="creative-type-badge bo-clone">Clone</span>;
    }
    // original_static
    return <span className="creative-type-badge static">Static</span>;
  })();

  const canSelect = adType !== "bo_clone" && !disabled;

  return (
    <tr
      className={[
        "ad-data-row",
        isSelected ? "ad-data-row-selected" : "",
        "ad-data-row-clickable",
      ].filter(Boolean).join(" ")}
      onClick={() => onPreview(ad)}
    >
      {/* Col 1: checkbox + type badge + id */}
      <td className="ad-data-cell-name">
        {canSelect ? (
          <input
            type="checkbox"
            checked={isSelected}
            onClick={(e) => e.stopPropagation()}
            onChange={() => onToggleAd({
              platform,
              seed_ad_id: ad.ad_id,
              label: `${campaignName} — ${ad.ad_id}`,
              adType,
            })}
          />
        ) : (
          <span className="checkbox-spacer" />
        )}
        {typeBadge}
        <span className="ad-id-text">{ad.ad_id}</span>
        {disabled && <span className="ad-unsupported-note">Not supported</span>}
      </td>

      {/* Col 2: Platform — blank (implied by campaign) */}
      <td />

      {/* Col 3: Status */}
      <td>{statusCell}</td>

      {/* Col 4: Action (reuses Daily Budget column for ad rows) */}
      <td className="ad-action-cell" onClick={(e) => e.stopPropagation()}>
        {actionCell}
      </td>

      {/* Col 5-9: metrics */}
      <td>{stats ? fmtInt(stats.impressions) : "—"}</td>
      <td>{stats ? fmtInt(stats.clicks) : "—"}</td>
      <td>{stats ? fmt(stats.spend, "$") : "—"}</td>
      <td>
        {stats?.ctr != null
          ? `${(stats.ctr * 100).toFixed(2)}%`
          : "—"}
        {stats?.synthetic && <span className="ad-stat-synthetic"> ~</span>}
      </td>
      <td>{stats ? fmt(stats.cpm, "$") : "—"}</td>
    </tr>
  );
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
  parentShouldPause = false,
  isStale = false,
}) {
  const [previewAd, setPreviewAd] = useState(null);

  const {
    id, name, platform, status, daily_budget,
    impressions_7d, clicks_7d, spend_7d, ctr_7d, cpm_7d,
  } = campaign;

  return (
    <>
      {/* ── Campaign row ── */}
      <tr>
        <td>
          <button className="btn-link campaign-name" onClick={() => onToggle(compositeKey)}>
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

      {/* ── Expanded: sub-header then one <tr> per ad ── */}
      {isExpanded && (
        <>
          <tr className="ads-subheader-row">
            <td colSpan={9}>
              {ingesting ? (
                <span className="ads-panel-status">Ingesting…</span>
              ) : !structure ? (
                <span className="ads-subheader-hint">
                  Ingest this campaign to see its ads.{" "}
                  <button className="btn-small" onClick={() => onIngest(id, platform)}>Ingest</button>
                </span>
              ) : structure.length === 0 ? (
                <span className="ads-subheader-hint">
                  No ads found.{" "}
                  <button className="btn-small" onClick={() => onIngest(id, platform)}>Re-ingest</button>
                </span>
              ) : (
                <span className="ads-subheader-hint">
                  {structure.length} ad{structure.length !== 1 ? "s" : ""}
                  {" · "}
                  <button className="btn-small" onClick={() => onIngest(id, platform)}>Re-ingest</button>
                </span>
              )}
            </td>
          </tr>

          {isStale && structure && (
            <tr className="stale-metrics-banner-row">
              <td colSpan={9}>
                <div className="stale-metrics-banner">
                  Synced — re-ingest to refresh metrics
                </div>
              </td>
            </tr>
          )}

          {parentShouldPause && (
            <tr className="parent-pause-banner-row">
              <td colSpan={9}>
                <div className="parent-pause-banner">
                  Pause this campaign's parent ads in{" "}
                  {platform === "google" ? "Google Ads Manager" : "Meta Ads Manager"}{" "}
                  to isolate test results — active clones are collecting CTR data.
                </div>
              </td>
            </tr>
          )}

          {structure?.map((ad) => (
            <AdRow
              key={ad.ad_id}
              ad={ad}
              platform={platform}
              campaignName={name}
              isSelected={selectedAdIds.has(`${platform}:${ad.ad_id}`)}
              onToggleAd={onToggleAd}
              onPreview={setPreviewAd}
            />
          ))}
        </>
      )}

      {previewAd && (
        <AdPreviewModal ad={previewAd} onClose={() => setPreviewAd(null)} />
      )}
    </>
  );
}
