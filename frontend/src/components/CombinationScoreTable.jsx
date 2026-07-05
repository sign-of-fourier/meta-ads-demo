import { useState } from "react";
import { scoreManualCombination } from "../api.js";

function ScoreCell({ adId, combo, onScored }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState("");
  const [metric, setMetric] = useState("ctr");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  if (!editing) {
    return (
      <button className="btn-small" onClick={() => setEditing(true)}>
        + Score
      </button>
    );
  }

  async function handleSave() {
    const num = parseFloat(val);
    if (isNaN(num)) { setErr("Enter a number"); return; }
    setSaving(true);
    setErr(null);
    try {
      await scoreManualCombination(adId, combo.combination_key, combo.combination, num, metric);
      setEditing(false);
      setVal("");
      onScored();
    } catch (e) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <span className="score-entry">
      <input
        type="number"
        step="any"
        value={val}
        onChange={(e) => setVal(e.target.value)}
        placeholder="e.g. 0.032"
        className="score-input"
        style={{ width: 90 }}
        onKeyDown={(e) => e.key === "Enter" && handleSave()}
      />
      <select value={metric} onChange={(e) => setMetric(e.target.value)} className="score-metric-select">
        <option value="ctr">CTR</option>
        <option value="cvr">CVR</option>
        <option value="roas">ROAS</option>
        <option value="custom">Custom</option>
      </select>
      <button className="btn-small btn-activate-clone" disabled={saving} onClick={handleSave}>
        {saving ? "…" : "Save"}
      </button>
      <button className="btn-small" onClick={() => setEditing(false)}>Cancel</button>
      {err && <span className="error-inline">{err}</span>}
    </span>
  );
}

export default function CombinationScoreTable({ adId, combos, slotNames, onRefresh }) {
  if (!combos || combos.length === 0) {
    return (
      <p className="history-empty">
        No combinations yet — embeddings may still be running. Refresh in a moment.
      </p>
    );
  }

  const hasImages = combos.some((c) => c.image_url);

  return (
    <div className="combo-score-table-wrap" style={{ overflowX: "auto" }}>
      <table className="campaigns-table combo-score-table">
        <thead>
          <tr>
            {hasImages && <th>Image</th>}
            {slotNames.map((s) => (
              <th key={s} style={{ textTransform: "capitalize" }}>{s}</th>
            ))}
            <th>Score</th>
            <th>Metric</th>
            <th>BO Pick</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {combos.map((c) => (
            <tr
              key={c.combination_key}
              className={c.is_bo_pick ? "bo-pick-row" : ""}
            >
              {hasImages && (
                <td>
                  {c.image_url ? (
                    <img
                      src={c.image_url}
                      alt=""
                      className="combo-row-thumb"
                      onError={(e) => { e.target.style.display = "none"; }}
                    />
                  ) : "—"}
                </td>
              )}
              {slotNames.map((s) => (
                <td key={s} style={{ maxWidth: 200, whiteSpace: "normal", wordBreak: "break-word" }}>
                  {c.combination[s] ?? "—"}
                </td>
              ))}
              <td>
                {c.score != null ? (
                  <strong>{c.score}</strong>
                ) : (
                  <span style={{ color: "var(--text-muted)" }}>—</span>
                )}
              </td>
              <td>{c.metric ?? "—"}</td>
              <td>
                {c.is_bo_pick ? (
                  <span className="clone-status-badge clone-status-clone-active">
                    #{c.bo_rank}
                  </span>
                ) : "—"}
              </td>
              <td>
                {c.score == null && (
                  <ScoreCell adId={adId} combo={c} onScored={onRefresh} />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
