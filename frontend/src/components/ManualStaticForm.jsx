import { useState } from "react";
import { createManualAd, uploadManualImage } from "../api.js";

function ImageValueInput({ value, onChange }) {
  const [uploading, setUploading] = useState(false);

  async function handleFile(e) {
    const file = e.target.files[0];
    if (!file) return;
    setUploading(true);
    try {
      const { url } = await uploadManualImage(file);
      onChange(url);
    } catch (err) {
      alert(err.message);
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  return (
    <div className="manual-image-value-row" style={{ marginLeft: 8 }}>
      <input
        type="url"
        className="manual-value-input"
        placeholder="https://example.com/image.jpg"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <label className={`btn-small manual-upload-btn${uploading ? " uploading" : ""}`}>
        {uploading ? "…" : "Upload"}
        <input type="file" accept="image/*" style={{ display: "none" }} onChange={handleFile} />
      </label>
      {value && (
        <img
          src={value}
          alt=""
          className="manual-image-thumb-tiny"
          onError={(e) => { e.target.style.display = "none"; }}
        />
      )}
    </div>
  );
}

export default function ManualStaticForm({ campaignId, onCreated, onCancel }) {
  // Each slot: { type: "text"|"image", name: string, value: string }
  const [slots, setSlots] = useState([{ type: "text", name: "", value: "" }]);
  const [mode, setMode] = useState("pool"); // "pool" | "score"
  const [score, setScore] = useState("");
  const [metric, setMetric] = useState("ctr");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  function addSlot() {
    setSlots((prev) => [...prev, { type: "text", name: "", value: "" }]);
  }

  function removeSlot(i) {
    setSlots((prev) => prev.filter((_, idx) => idx !== i));
  }

  function setSlotType(i, type) {
    setSlots((prev) =>
      prev.map((s, idx) =>
        idx === i ? { ...s, type, name: type === "image" ? "image" : "" } : s
      )
    );
  }

  function updateSlot(i, field, v) {
    setSlots((prev) =>
      prev.map((s, idx) => (idx === i ? { ...s, [field]: v } : s))
    );
  }

  async function handleSave() {
    const cleanSlots = slots
      .filter((s) => (s.type === "image" ? s.value.trim() : s.name.trim() && s.value.trim()))
      .map((s) => ({
        name: s.type === "image" ? "image" : s.name.trim(),
        values: [s.value.trim()],
      }));

    const textSlots = cleanSlots.filter((s) => s.name !== "image");
    if (textSlots.length === 0) {
      setErr("Add at least one text slot with a value.");
      return;
    }

    const poolOnly = mode === "pool";
    const scoreNum = poolOnly ? null : parseFloat(score);
    if (!poolOnly && isNaN(scoreNum)) {
      setErr("Enter a valid score.");
      return;
    }

    setSaving(true);
    setErr(null);
    try {
      const ad = await createManualAd(campaignId, {
        creative_type: "static",
        slots: cleanSlots,
        pool_only: poolOnly,
        score: poolOnly ? null : scoreNum,
        metric: poolOnly ? null : metric,
      });
      onCreated(ad);
    } catch (e) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="manual-form">
      <h4 className="manual-form-title">New Static Ad</h4>

      {slots.map((slot, i) => (
        <div key={i} className="manual-value-row" style={{ marginBottom: 8, alignItems: "flex-start" }}>
          <select
            className="manual-slot-type-select"
            value={slot.type}
            onChange={(e) => setSlotType(i, e.target.value)}
          >
            <option value="text">Text</option>
            <option value="image">Image</option>
          </select>
          {slot.type === "image" ? (
            <>
              <span className="manual-slot-name-fixed">image</span>
              <ImageValueInput
                value={slot.value}
                onChange={(v) => updateSlot(i, "value", v)}
              />
            </>
          ) : (
            <>
              <input
                type="text"
                className="manual-slot-name-input"
                placeholder="Slot name (e.g. headline)"
                value={slot.name}
                onChange={(e) => updateSlot(i, "name", e.target.value)}
              />
              <input
                type="text"
                className="manual-value-input"
                placeholder="Value"
                value={slot.value}
                onChange={(e) => updateSlot(i, "value", e.target.value)}
                style={{ marginLeft: 8 }}
              />
            </>
          )}
          {slots.length > 1 && (
            <button className="btn-small" onClick={() => removeSlot(i)} style={{ marginLeft: 4 }}>×</button>
          )}
        </div>
      ))}
      <button className="btn-small" onClick={addSlot}>+ Add slot</button>

      <div className="manual-mode-select" style={{ marginTop: 16 }}>
        <label style={{ marginRight: 16 }}>
          <input
            type="radio"
            value="pool"
            checked={mode === "pool"}
            onChange={() => setMode("pool")}
            style={{ marginRight: 4 }}
          />
          Add to candidate pool
        </label>
        <label>
          <input
            type="radio"
            value="score"
            checked={mode === "score"}
            onChange={() => setMode("score")}
            style={{ marginRight: 4 }}
          />
          Record a result
        </label>
      </div>

      {mode === "score" && (
        <div className="manual-score-row" style={{ marginTop: 12, display: "flex", gap: 8, alignItems: "center" }}>
          <input
            type="number"
            step="any"
            value={score}
            onChange={(e) => setScore(e.target.value)}
            placeholder="Score (e.g. 0.032)"
            className="score-input"
            style={{ width: 120 }}
          />
          <select value={metric} onChange={(e) => setMetric(e.target.value)} className="score-metric-select">
            <option value="ctr">CTR</option>
            <option value="cvr">CVR</option>
            <option value="roas">ROAS</option>
            <option value="custom">Custom</option>
          </select>
        </div>
      )}

      {err && <p className="error-inline" style={{ marginTop: 8 }}>{err}</p>}

      <div className="manual-form-actions">
        <button className="btn-primary" disabled={saving} onClick={handleSave}>
          {saving ? "Creating…" : mode === "pool" ? "Add to pool" : "Record result"}
        </button>
        <button className="btn-small" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
