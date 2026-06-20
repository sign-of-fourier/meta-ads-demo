import { useState } from "react";
import { createPortal } from "react-dom";
import PotentialBadge from "./PotentialBadge";
import { pushPick, pushMatch, activatePick } from "../api";

const PLATFORM_LABELS = { meta: "Meta", google: "Google" };

const SLOT_LABELS = {
  headline: "Headline",
  description: "Description",
  primary_text: "Primary text",
  final_url: "Final URL",
};

const METRIC_OPTIONS = [
  { value: "",     label: "Auto (best available)" },
  { value: "ctr",  label: "CTR" },
  { value: "cvr",  label: "CVR" },
  { value: "roas", label: "ROAS" },
];

function imageNameFromUrl(url) {
  if (!url) return null;
  try {
    return new URL(url, "http://x").pathname.split("/").pop() || url;
  } catch {
    return url.split("/").pop() || url;
  }
}

/* ── Preview modal ─────────────────────────────────────────────────────────── */
function PickPreviewModal({ pick, index, onClose }) {
  const [drilldown, setDrilldown] = useState(null);
  const combo = drilldown ? drilldown.combination : pick.combination;
  const isDrilldown = drilldown !== null;

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          {isDrilldown ? (
            <button className="modal-back" onClick={() => setDrilldown(null)}>← Back</button>
          ) : (
            <h3 className="modal-title">
              Recommendation {index + 1}
              <span className={`platform-badge platform-badge-${pick.platform}`} style={{ marginLeft: "0.5rem" }}>
                {PLATFORM_LABELS[pick.platform] || pick.platform}
              </span>
            </h3>
          )}
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        {isDrilldown ? (
          <>
            <div className="modal-drilldown-meta">
              <span className="modal-drilldown-title">Known ad</span>
              <span className="modal-expl-neighbor-stats">
                score {drilldown.score.toFixed(3)} · dist {drilldown.cosine_distance.toFixed(3)}
              </span>
            </div>
            <dl className="slot-list modal-slot-list">
              {combo.image_url && (
                <div className="slot-row">
                  <dt>Image</dt>
                  <dd><span className="slot-value">{imageNameFromUrl(combo.image_url)}</span></dd>
                </div>
              )}
              {Object.entries(combo)
                .filter(([k]) => !["image_url", "image_width", "image_height"].includes(k))
                .map(([k, v]) => (
                  <div key={k} className="slot-row">
                    <dt>{SLOT_LABELS[k] ?? k}</dt>
                    <dd><span className="slot-value">{v || <em>—</em>}</span></dd>
                  </div>
                ))}
            </dl>
          </>
        ) : (
          <>
            {combo.image_url && (
              <img src={combo.image_url} alt="Ad creative" className="modal-preview-image" />
            )}
            <dl className="slot-list modal-slot-list">
              {combo.image_url && (
                <div className="slot-row">
                  <dt>Image</dt>
                  <dd><span className="slot-value">{imageNameFromUrl(combo.image_url)}</span></dd>
                </div>
              )}
              {Object.entries(combo)
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
                  {pick.gpr_mean != null && <div className="modal-expl-row"><span>Relative Score</span><span>{Math.exp(pick.gpr_mean).toFixed(3)}</span></div>}
                  {pick.ei_score != null && <div className="modal-expl-row"><span>Potential (EI)</span><span>{pick.ei_score.toFixed(4)}</span></div>}
                  {pick.gpr_std  != null && <div className="modal-expl-row"><span>Uncertainty (σ)</span><span>{pick.gpr_std.toFixed(3)}</span></div>}
                </div>
                {(pick.nearest_known ?? []).length > 0 && (
                  <div className="modal-expl-neighbors">
                    <div className="modal-expl-neighbors-title">Nearest known ads</div>
                    {pick.nearest_known.map((n, i) => (
                      <div key={i} className="modal-expl-neighbor-row modal-expl-neighbor-clickable"
                        onClick={() => setDrilldown(n)}>
                        <span className="modal-expl-neighbor-label">{n.label}</span>
                        <span className="modal-expl-neighbor-stats">score {n.score.toFixed(3)} · dist {n.cosine_distance.toFixed(3)} →</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>,
    document.body
  );
}

/* ── Per-pick card in results ──────────────────────────────────────────────── */
function PickCard({ pick, index, isSelected, onToggleSelect, onPreview }) {
  const selLabel =
    pick.selection_type === "modal_q_ei" ? "Adstac.kr" :
    pick.selection_type === "ei"         ? "Best expected" :
    pick.selection_type === "fantasy"    ? "Exploratory" : "Random";

  // Already pushed — show lifecycle state (read-only in this view)
  if (pick.already_pushed) {
    const cs = pick.clone_status;
    const lifecycleLabel =
      cs === "clone_paused"      ? "Clone pushed — activate to run test" :
      cs === "clone_active"      ? `Testing… ${(pick.current_impressions || 0).toLocaleString()} impr.` :
      cs === "clone_converged"   ? "Done — converged" :
      cs === "clone_retained"    ? "Running — retained" :
      cs === "clone_invalidated" ? "Invalidated" :
      pick.ad_name || "Pushed";
    return (
      <div className="bo-pick bo-pick-clickable" onClick={() => onPreview(pick, index)}>
        <div className="bo-pick-body">
          <div className="bo-pick-header">
            <span className="bo-pick-label">{pick.ad_name || `Recommendation ${index + 1}`}</span>
            <span className="bo-pick-type">{selLabel}</span>
          </div>
          {(pick.combination.headline || pick.combination.primary_text) && (
            <p className="bo-pick-preview">
              {pick.combination.headline || pick.combination.primary_text}
            </p>
          )}
          <PotentialBadge pick={pick} />
        </div>
        <div className="bo-pick-lifecycle-note">{lifecycleLabel}</div>
      </div>
    );
  }

  // Match against existing native ad — no push needed, just activate
  if (pick.matches_existing_ad) {
    return (
      <MatchCard pick={pick} index={index}
        isSelected={isSelected} onToggleSelect={onToggleSelect} onPreview={onPreview} />
    );
  }

  // New combination — show checkbox for batch push
  return (
    <div
      className={["bo-pick bo-pick-row", isSelected ? "bo-pick-selected" : ""].filter(Boolean).join(" ")}
      onClick={() => onPreview(pick, index)}
    >
      <div className="bo-pick-select" onClick={e => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={isSelected}
          onChange={() => onToggleSelect(index)}
          title="Select to push"
        />
      </div>
      <div className="bo-pick-body">
        <div className="bo-pick-header">
          <span className="bo-pick-label">Recommendation {index + 1}</span>
          <span className="bo-pick-type">{selLabel}</span>
        </div>
        {pick.combination?.image_url && (
          <p className="bo-pick-image-name">{pick.combination.image_url.split("/").pop()}</p>
        )}
        {(pick.combination.headline || pick.combination.primary_text) && (
          <p className="bo-pick-preview">
            {pick.combination.headline || pick.combination.primary_text}
          </p>
        )}
        <PotentialBadge pick={pick} />
      </div>
    </div>
  );
}

/* ── Match card — existing native ad detected ────────────────────────────── */
function MatchCard({ pick, index, isSelected, onToggleSelect, onPreview }) {
  const existingAdId = pick.matches_existing_ad?.ad_id;
  const existingStatus = pick.matches_existing_ad?.effective_status;
  const isActive = ["ACTIVE", "ENABLED"].includes(existingStatus);

  return (
    <div
      className={["bo-pick bo-pick-row bo-pick-match", isSelected ? "bo-pick-selected" : ""].filter(Boolean).join(" ")}
      onClick={() => onPreview(pick, index)}
    >
      <div className="bo-pick-select" onClick={e => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={isSelected}
          onChange={() => onToggleSelect(index)}
          title="Select to record match"
        />
      </div>
      <div className="bo-pick-body">
        <div className="bo-pick-header">
          <span className="bo-pick-label">Recommendation {index + 1}</span>
          <span className="bo-pick-type-match">Matches existing ad</span>
        </div>
        {(pick.combination.headline || pick.combination.primary_text) && (
          <p className="bo-pick-preview">
            {pick.combination.headline || pick.combination.primary_text}
          </p>
        )}
        <PotentialBadge pick={pick} />
        <div className="bo-pick-match-note">
          This combination matches ad <code>{existingAdId}</code>
          {" "}({isActive ? "currently active" : "currently paused"}).
          Selecting it will record this as a test instead of creating a duplicate.
        </div>
      </div>
    </div>
  );
}

/* ── Batch push footer ─────────────────────────────────────────────────────── */
// Each pick must carry pick.platform and pick.seed_ad_id (enriched by DashboardPage at setBoState time).
function BatchPushFooter({ picks, selectedIndices, onDone }) {
  const [pushing, setPushing] = useState(false);
  const [results, setResults] = useState(null); // null | {pushed, failed, details}

  const selected = picks.filter((_, i) => selectedIndices.has(i));

  function defaultName(pick, i) {
    const headline = pick.combination?.headline || pick.combination?.primary_text || "Pick";
    const preview = headline.length > 30 ? headline.slice(0, 30) + "…" : headline;
    return `Adstac.kr ${i + 1} — ${preview}`;
  }

  async function handlePushSelected() {
    setPushing(true);
    const details = [];
    for (const pick of selected) {
      const globalIdx = picks.indexOf(pick);
      const name = defaultName(pick, globalIdx + 1);
      const { platform, seed_ad_id: seedAdId } = pick;

      if (platform === "google") {
        details.push({ ok: false, label: `Google push not yet supported — skipped` });
        continue;
      }

      try {
        if (pick.matches_existing_ad) {
          await pushMatch({
            platform,
            seedAdId,
            combinationKey: pick.combination_key,
            combination: pick.combination,
            existingAdId: pick.matches_existing_ad.ad_id,
          });
          details.push({ ok: true, label: `Match recorded — ${pick.matches_existing_ad.ad_id}` });
        } else {
          const res = await pushPick({
            platform,
            seedAdId,
            combinationKey: pick.combination_key,
            combination: pick.combination,
            name,
          });
          details.push({ ok: true, label: `Pushed — ${res.platform_ad_id}` });
        }
      } catch (err) {
        details.push({ ok: false, label: err.message || "Push failed" });
      }
    }
    setPushing(false);
    setResults({ pushed: details.filter(d => d.ok).length, failed: details.filter(d => !d.ok).length, details });
    onDone?.();
  }

  if (results) {
    return (
      <div className="batch-push-footer">
        <div className="batch-push-result">
          {results.pushed > 0 && <span className="push-success">{results.pushed} pushed</span>}
          {results.failed > 0 && <span className="push-error-text">{results.failed} failed</span>}
          {" · "}
          <span className="batch-push-note">Re-ingest the campaign to see clones in the structure view.</span>
        </div>
        <ul className="batch-push-detail-list">
          {results.details.map((d, i) => (
            <li key={i} className={d.ok ? "push-success" : "push-error-text"}>{d.label}</li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="batch-push-footer">
      <button
        className="btn-primary"
        disabled={pushing || selected.length === 0}
        onClick={handlePushSelected}
      >
        {pushing ? "Pushing…" : selected.length > 0 ? `Push ${selected.length} selected` : "Push selected"}
      </button>
      <span className="batch-push-note">
        {selected.length === 0
          ? "Check recommendations above to select them for push."
          : "Clones will be created PAUSED — activate in Ads Manager after setting budget."}
      </span>
    </div>
  );
}

/* ── Generator results ─────────────────────────────────────────────────────── */
function GeneratorResults({ data, onPushDone }) {
  const { picks, scored_count, candidate_count, warning, members, platform } = data;
  const [previewPick, setPreviewPick] = useState(null);
  const [previewIndex, setPreviewIndex] = useState(null);
  const [selectedIndices, setSelectedIndices] = useState(new Set());

  function toggleSelect(i) {
    setSelectedIndices(prev => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  function handleDone() {
    setSelectedIndices(new Set());
    onPushDone?.();
  }

  return (
    <div className="cross-platform-results">
      <div className="cross-platform-stats">
        <span className="cross-platform-stat">
          <span className={`platform-badge platform-badge-${platform}`}>
            {PLATFORM_LABELS[platform] || platform}
          </span>
          {members.length} ad{members.length !== 1 ? "s" : ""} merged
          {" · "}
          {scored_count} scored · {candidate_count} candidates
          {scored_count === 0 && (
            <span className="stat-random-note"> (random — no scored variants yet)</span>
          )}
        </span>
      </div>
      {warning && <p className="batch-warning">{warning}</p>}
      {picks.length === 0 ? (
        <p className="history-empty">No candidates found — ingest and embed ads first.</p>
      ) : (
        <>
          <BatchPushFooter
            picks={picks}
            selectedIndices={selectedIndices}
            onDone={handleDone}
          />
          <div className="bo-results">
            {picks.map((pick, i) => (
              <PickCard
                key={i}
                pick={pick}
                index={i}
                isSelected={selectedIndices.has(i)}
                onToggleSelect={toggleSelect}
                onPreview={(p, idx) => { setPreviewPick(p); setPreviewIndex(idx); }}
              />
            ))}
          </div>
        </>
      )}
      {previewPick && (
        <PickPreviewModal
          pick={previewPick}
          index={previewIndex}
          onClose={() => { setPreviewPick(null); setPreviewIndex(null); }}
        />
      )}
    </div>
  );
}

/* ── Cross-platform results ────────────────────────────────────────────────── */
function CrossPlatformResults({ data, onPushDone }) {
  const { picks, group_stats } = data;
  const [previewPick, setPreviewPick] = useState(null);
  const [previewIndex, setPreviewIndex] = useState(null);
  const [selectedIndices, setSelectedIndices] = useState(new Set());

  function toggleSelect(i) {
    setSelectedIndices(prev => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  function handleDone() {
    setSelectedIndices(new Set());
    onPushDone?.();
  }

  return (
    <div className="cross-platform-results">
      <div className="cross-platform-stats">
        {group_stats.map((stat) => (
          <span key={`${stat.platform}-${stat.seed_ad_id}`} className="cross-platform-stat">
            <span className={`platform-badge platform-badge-${stat.platform}`}>
              {PLATFORM_LABELS[stat.platform] || stat.platform}
            </span>
            <code className="stat-ad-id">…{String(stat.seed_ad_id).slice(-6)}</code>
            {stat.scored_count} scored · {stat.candidate_count} candidates
            {stat.scored_count === 0 && (
              <span className="stat-random-note"> (random — no scored variants yet)</span>
            )}
          </span>
        ))}
      </div>
      {picks.length === 0 ? (
        <p className="history-empty">
          No candidates yet — run per-campaign recommendations first, then try again.
        </p>
      ) : (
        <>
          <BatchPushFooter
            picks={picks}
            selectedIndices={selectedIndices}
            onDone={handleDone}
          />
          <div className="bo-results">
            {picks.map((pick, i) => (
              <div
                key={i}
                className={["bo-pick bo-pick-row", selectedIndices.has(i) ? "bo-pick-selected" : "", "bo-pick-clickable"].filter(Boolean).join(" ")}
                onClick={() => { setPreviewPick(pick); setPreviewIndex(i); }}
              >
                <div className="bo-pick-select" onClick={e => e.stopPropagation()}>
                  {!pick.already_pushed && (
                    <input
                      type="checkbox"
                      checked={selectedIndices.has(i)}
                      onChange={() => toggleSelect(i)}
                    />
                  )}
                </div>
                <div className="bo-pick-body">
                  <div className="bo-pick-header">
                    <span className="bo-pick-label">Recommendation {i + 1}</span>
                    <span className={`platform-badge platform-badge-${pick.platform}`}>
                      {PLATFORM_LABELS[pick.platform] || pick.platform}
                    </span>
                    <span className="bo-pick-type">
                      {pick.selection_type === "ei"         ? "Best expected" :
                       pick.selection_type === "fantasy"    ? "Exploratory" :
                       pick.selection_type === "modal_q_ei" ? "Adstac.kr" : "Random"}
                    </span>
                  </div>
                  {pick.combination.image_url && (
                    <p className="bo-pick-image-name">{imageNameFromUrl(pick.combination.image_url)}</p>
                  )}
                  <dl className="slot-list">
                    {Object.entries(pick.combination)
                      .filter(([k]) => !["image_url", "image_width", "image_height"].includes(k))
                      .map(([k, v]) => (
                        <div key={k} className="slot-row">
                          <dt>{SLOT_LABELS[k] ?? k}</dt>
                          <dd><span className="slot-value">{v || <em>—</em>}</span></dd>
                        </div>
                      ))}
                  </dl>
                  <PotentialBadge pick={pick} />
                </div>
              </div>
            ))}
          </div>
        </>
      )}
      {previewPick && (
        <PickPreviewModal
          pick={previewPick}
          index={previewIndex}
          onClose={() => { setPreviewPick(null); setPreviewIndex(null); }}
        />
      )}
    </div>
  );
}

/* ── Main BatchPanel ────────────────────────────────────────────────────────── */
export default function BatchPanel({
  selectedAds,
  onRemove,
  topN,
  onTopNChange,
  targetMetric,
  onTargetMetricChange,
  onRunBO,
  boState,
  onPushDone,
}) {
  // Separate templates (dynamic/rsa) from original statics
  const templateAds = selectedAds.filter(a => a.adType !== "original_static");
  const staticAds   = selectedAds.filter(a => a.adType === "original_static");

  const platformsInBatch = [...new Set(selectedAds.map((a) => a.platform))];
  const isSamePlatform = platformsInBatch.length <= 1;
  const canRun = templateAds.length >= 1;

  return (
    <div className="cross-platform-section">
      <div className="cross-platform-header">
        <div className="cross-platform-title-row">
          <div className="cross-platform-readiness">
            {selectedAds.length === 0 ? (
              <span className="platform-readiness readiness-pending">No ads selected</span>
            ) : (
              platformsInBatch.map((p) => (
                <span key={p} className="platform-readiness readiness-ready">
                  {PLATFORM_LABELS[p] || p} ({selectedAds.filter((x) => x.platform === p).length})
                </span>
              ))
            )}
          </div>
        </div>

        {/* Section A — Optimize (templates) */}
        {templateAds.length > 0 && (
          <div className="batch-section">
            <div className="batch-section-title">Optimize</div>
            <div className="cross-platform-batch">
              {templateAds.map(({ platform, seed_ad_id, label }) => (
                <span key={`${platform}-${seed_ad_id}`} className="batch-chip batch-chip-template">
                  <span className={`platform-badge platform-badge-${platform}`}>
                    {PLATFORM_LABELS[platform] || platform}
                  </span>
                  <span className="batch-chip-label">{label || seed_ad_id}</span>
                  <button
                    className="batch-chip-remove"
                    onClick={() => onRemove(platform, seed_ad_id)}
                    title="Remove"
                  >×</button>
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Section B — Include in pool (original statics) */}
        {staticAds.length > 0 && (
          <div className="batch-section">
            <div className="batch-section-title">Include in pool</div>
            <div className="cross-platform-batch">
              {staticAds.map(({ platform, seed_ad_id, label }) => (
                <span key={`${platform}-${seed_ad_id}`} className="batch-chip batch-chip-static">
                  <span className={`platform-badge platform-badge-${platform}`}>
                    {PLATFORM_LABELS[platform] || platform}
                  </span>
                  <span className="batch-chip-label">{label || seed_ad_id}</span>
                  <button
                    className="batch-chip-remove"
                    onClick={() => onRemove(platform, seed_ad_id)}
                    title="Remove"
                  >×</button>
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Section C note */}
        {selectedAds.length > 0 && (
          <p className="batch-section-c-note">
            Combinations already pushed are excluded automatically.
          </p>
        )}

        {!canRun && selectedAds.length === 0 && (
          <p className="cross-platform-hint">
            Select a template ad (Dynamic or RSA) from the campaigns above to get started.
          </p>
        )}
        {!canRun && selectedAds.length > 0 && templateAds.length === 0 && (
          <p className="cross-platform-hint">
            Add at least one template (Dynamic or RSA) — static-only selections have no asset pool to explore.
          </p>
        )}

        <div className="cross-platform-controls">
          <label className="topn-label">
            Recommendations
            <input
              type="number"
              min={1}
              max={8}
              value={topN}
              onChange={(e) =>
                onTopNChange(Math.max(1, Math.min(8, parseInt(e.target.value, 10) || 1)))
              }
              className="topn-input"
            />
          </label>
          <label className="topn-label">
            Optimize for
            <select
              className="metric-select"
              value={targetMetric ?? ""}
              onChange={(e) => onTargetMetricChange(e.target.value || null)}
            >
              {METRIC_OPTIONS.map(({ value, label }) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <button
            className="btn-primary"
            onClick={onRunBO}
            disabled={!canRun || boState?.status === "loading"}
          >
            {boState?.status === "loading" ? "Analyzing…" : "Run Adstac.kr"}
          </button>
        </div>
      </div>

      {boState?.status === "error" && (
        <p className="error" style={{ marginTop: "0.75rem" }}>
          {boState.error}
        </p>
      )}
      {boState?.status === "done" && boState.data?.type === "generator" && (
        <GeneratorResults data={boState.data} onPushDone={onPushDone} />
      )}
      {boState?.status === "done" && boState.data?.type === "cross-platform" && (
        <CrossPlatformResults data={boState.data} onPushDone={onPushDone} />
      )}
    </div>
  );
}
