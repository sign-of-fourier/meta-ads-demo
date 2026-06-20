/**
 * BOPickCard — renders a single BO recommendation with:
 *   - Name-first display (ad_name if already pushed, else "Recommendation N")
 *   - Preview modal showing full slot content + image
 *   - Push and Run modal with name input
 *   - Lifecycle action button: Push / Activate / Testing / Running
 *
 * Props:
 *   pick          — BOPick object from the API
 *   index         — 0-based rank index (for display label)
 *   platform      — 'meta' | 'google'
 *   seedAdId      — seed ad ID (needed for push)
 *   campaignName  — used to suggest a default ad name
 *   onPushed(pick, platformAdId, adName) — called after a successful push
 *   onActivated(pick) — called after a successful activate
 */

import { useState } from "react";
import { createPortal } from "react-dom";
import { pushPick, activatePick, retainPick } from "../api.js";
import PotentialBadge from "./PotentialBadge.jsx";

const SLOT_LABELS = {
  headline: "Headline",
  description: "Description",
  primary_text: "Primary text",
  final_url: "Final URL",
  cta: "CTA",
};

function imageNameFromUrl(url) {
  if (!url) return null;
  try {
    return new URL(url, "http://x").pathname.split("/").pop() || url;
  } catch {
    return url.split("/").pop() || url;
  }
}

function defaultAdName(campaignName, combination) {
  const headline = combination?.headline || "";
  const preview = headline.length > 25 ? headline.slice(0, 25) + "…" : headline;
  const date = new Date().toISOString().slice(0, 10);
  return `${campaignName || "Ad"} — ${preview || "Adstac.kr pick"} (${date})`;
}

/* ── Preview modal ────────────────────────────────────────────────────────── */
function PreviewModal({ pick, onClose }) {
  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3 className="modal-title">{pick.ad_name || "Ad Preview"}</h3>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        {pick.combination.image_url && (
          <img
            src={pick.combination.image_url}
            alt="Ad creative"
            className="modal-preview-image"
          />
        )}
        <dl className="slot-list modal-slot-list">
          {pick.combination.image_url && (
            <div className="slot-row">
              <dt>Image</dt>
              <dd><span className="slot-value">{imageNameFromUrl(pick.combination.image_url)}</span></dd>
            </div>
          )}
          {Object.entries(pick.combination)
            .filter(([k]) => !["image_url", "image_width", "image_height"].includes(k))
            .map(([k, v]) => (
              <div key={k} className="slot-row">
                <dt>{SLOT_LABELS[k] ?? k}</dt>
                <dd><span className="slot-value">{v || <em>—</em>}</span></dd>
              </div>
            ))}
        </dl>
        {(pick.gpr_mean != null || pick.ei_score != null || pick.gpr_std != null || (pick.nearest_known ?? []).length > 0) && (
          <div className="modal-expl-block">
            <div className="modal-expl-title">GP model estimates</div>
            <div className="modal-expl-note">
              Surrogate model predictions — not observed metrics. Relative Score &gt; 1 means
              above-median predicted performance; 1 = median. Not comparable to real CTR/ROAS.
            </div>
            <div className="modal-expl-rows">
              {pick.gpr_mean  != null && <div className="modal-expl-row"><span>Relative Score</span><span>{Math.exp(pick.gpr_mean).toFixed(3)}</span></div>}
              {pick.ei_score  != null && <div className="modal-expl-row"><span>Potential (EI)</span><span>{pick.ei_score.toFixed(4)}</span></div>}
              {pick.gpr_std   != null && <div className="modal-expl-row"><span>Uncertainty (σ)</span><span>{pick.gpr_std.toFixed(3)}</span></div>}
            </div>
            {(pick.nearest_known ?? []).length > 0 && (
              <div className="modal-expl-neighbors">
                <div className="modal-expl-neighbors-title">Nearest known ads</div>
                {pick.nearest_known.map((n, i) => (
                  <div key={i} className="modal-expl-neighbor-row">
                    <span className="modal-expl-neighbor-label">{n.label}</span>
                    <span className="modal-expl-neighbor-stats">score {n.score.toFixed(3)} · dist {n.cosine_distance.toFixed(3)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}

/* ── Push modal ───────────────────────────────────────────────────────────── */
function PushModal({ pick, campaignName, platform, seedAdId, onConfirm, onClose }) {
  const [name, setName] = useState(defaultAdName(campaignName, pick.combination));
  const [pushing, setPushing] = useState(false);
  const [error, setError] = useState(null);

  async function handleConfirm() {
    if (!name.trim()) return;
    setPushing(true);
    setError(null);
    try {
      const result = await pushPick({
        platform,
        seedAdId,
        combinationKey: pick.combination_key,
        combination: pick.combination,
        name: name.trim(),
      });
      onConfirm(result.platform_ad_id, result.ad_name);
    } catch (err) {
      setError(err.message || "Push failed");
      setPushing(false);
    }
  }

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3 className="modal-title">Name and push this ad</h3>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <p className="modal-hint">
          This name will appear in {platform === "google" ? "Google Ads" : "Meta Ads Manager"}.
          You can edit the ad there after it's pushed.
        </p>
        <dl className="slot-list modal-slot-list modal-slot-list-compact">
          {Object.entries(pick.combination)
            .filter(([k]) => !["image_url", "image_width", "image_height"].includes(k))
            .slice(0, 3)
            .map(([k, v]) => (
              <div key={k} className="slot-row">
                <dt>{SLOT_LABELS[k] ?? k}</dt>
                <dd><span className="slot-value slot-value-truncate">{v || <em>—</em>}</span></dd>
              </div>
            ))}
        </dl>
        <label className="push-name-label">
          Ad name
          <input
            className="push-name-input"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="e.g. Summer Sale — Adstac.kr 2026-05-28"
            autoFocus
          />
        </label>
        {error && <p className="error">{error}</p>}
        <div className="modal-actions">
          <button className="btn-secondary" onClick={onClose} disabled={pushing}>Cancel</button>
          <button
            className="btn-primary"
            onClick={handleConfirm}
            disabled={pushing || !name.trim()}
          >
            {pushing ? "Pushing…" : "Push and Run"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

/* ── Main card ────────────────────────────────────────────────────────────── */
export default function BOPickCard({
  pick,
  index,
  platform,
  seedAdId,
  campaignName,
  onPushed,
  onActivated,
}) {
  const [showPreview, setShowPreview] = useState(false);
  const [showPushModal, setShowPushModal] = useState(false);
  const [activating, setActivating] = useState(false);
  const [activateError, setActivateError] = useState(null);
  const [retaining, setRetaining] = useState(false);

  // Local override for lifecycle state after a push in this session
  const [localState, setLocalState] = useState(null);

  const alreadyPushed       = localState?.already_pushed ?? pick.already_pushed;
  const pushStatus          = localState?.push_status    ?? pick.push_status;
  const adName              = localState?.ad_name        ?? pick.ad_name;
  const platformAdId        = localState?.platform_ad_id ?? pick.platform_ad_id;
  const converged           = pick.converged ?? false;
  const currentImpressions  = pick.current_impressions ?? 0;
  const cloneStatus         = localState?.clone_status   ?? pick.clone_status ?? null;
  const comboId             = pick.combo_id ?? null;

  const selectionLabel =
    pick.selection_type === "modal_q_ei" ? "Adstac.kr" :
    pick.selection_type === "ei"         ? "Best expected" :
    pick.selection_type === "fantasy"    ? "Exploratory" : "Random";

  function handlePushed(newPlatformAdId, newAdName) {
    setLocalState({ already_pushed: true, push_status: "paused", ad_name: newAdName, platform_ad_id: newPlatformAdId });
    setShowPushModal(false);
    onPushed?.(pick, newPlatformAdId, newAdName);
  }

  async function handleActivate() {
    setActivating(true);
    setActivateError(null);
    try {
      await activatePick({ platform, platformAdId });
      setLocalState(s => ({ ...s, push_status: "active", clone_status: "clone_active" }));
      onActivated?.(pick);
    } catch (err) {
      setActivateError(err.message || "Activate failed");
    } finally {
      setActivating(false);
    }
  }

  async function handleRetain() {
    if (!comboId) return;
    setRetaining(true);
    try {
      await retainPick(comboId);
      setLocalState(s => ({ ...s, clone_status: "clone_retained" }));
    } finally {
      setRetaining(false);
    }
  }

  /* Action button — driven by clone_status when available, push_status as fallback */
  const effectiveStatus = cloneStatus ?? (pushStatus === "paused" ? "clone_paused" : pushStatus === "active" ? "clone_active" : null);

  let actionButton;
  if (!alreadyPushed) {
    actionButton = (
      <button className="btn-primary btn-push-run" onClick={() => setShowPushModal(true)}>
        Push and Run
      </button>
    );
  } else if (effectiveStatus === "clone_invalidated") {
    actionButton = (
      <span className="push-status-badge push-status-invalidated">Invalidated</span>
    );
  } else if (effectiveStatus === "clone_paused") {
    actionButton = (
      <>
        <button className="btn-success" onClick={handleActivate} disabled={activating}>
          {activating ? "Activating…" : "Activate"}
        </button>
        {activateError && <span className="error-inline">{activateError}</span>}
      </>
    );
  } else if (effectiveStatus === "clone_converged") {
    actionButton = (
      <div className="bo-pick-converged-actions">
        <span className="push-status-badge push-status-tested">Tested ✓</span>
        <button className="btn-secondary btn-keep-running" onClick={handleRetain} disabled={retaining}>
          {retaining ? "Saving…" : "Keep Running"}
        </button>
      </div>
    );
  } else if (effectiveStatus === "clone_retained") {
    actionButton = (
      <span className="push-status-badge push-status-running">Running ✓ — Retained</span>
    );
  } else if (effectiveStatus === "clone_active" || pushStatus === "active") {
    actionButton = currentImpressions > 0
      ? (
        <span className="push-status-badge push-status-testing">
          Testing… {currentImpressions.toLocaleString()} impr.
        </span>
      ) : (
        <span className="push-status-badge push-status-running">Running ✓</span>
      );
  } else {
    actionButton = (
      <button className="btn-warn" onClick={() => setShowPushModal(true)}>
        Push failed — retry
      </button>
    );
  }

  return (
    <>
      <div className="bo-pick bo-pick-clickable" onClick={() => setShowPreview(true)}>
        <div className="bo-pick-body">
          <div className="bo-pick-header">
            <span className="bo-pick-label">
              {adName || `Recommendation ${index + 1}`}
            </span>
            <span className="bo-pick-type">{selectionLabel}</span>
          </div>
          {pick.combination.image_url && (
            <div className="bo-pick-image-row">
              <img
                src={pick.combination.image_url}
                alt="Ad creative"
                className="bo-pick-thumbnail"
              />
              <span className="bo-pick-image-name">{imageNameFromUrl(pick.combination.image_url)}</span>
            </div>
          )}
          {(pick.combination.headline || pick.combination.primary_text) && (
            <p className="bo-pick-preview">
              {pick.combination.headline || pick.combination.primary_text}
            </p>
          )}
          <PotentialBadge pick={pick} />
        </div>
        <div className="bo-pick-actions" onClick={e => e.stopPropagation()}>
          {actionButton}
        </div>
      </div>

      {showPreview && (
        <PreviewModal pick={{ ...pick, ad_name: adName }} onClose={() => setShowPreview(false)} />
      )}
      {showPushModal && (
        <PushModal
          pick={pick}
          campaignName={campaignName}
          platform={platform}
          seedAdId={seedAdId}
          onConfirm={handlePushed}
          onClose={() => setShowPushModal(false)}
        />
      )}
    </>
  );
}
