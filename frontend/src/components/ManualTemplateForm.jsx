import { useState } from "react";
import { createManualAd, uploadManualImage } from "../api.js";

function ImageValueInput({ value, onChange, onRemove, showRemove }) {
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
    <div className="manual-image-value-row">
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
      {showRemove && (
        <button className="btn-small" onClick={onRemove}>×</button>
      )}
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

export default function ManualTemplateForm({ campaignId, onCreated, onCancel }) {
  // Each slot: { type: "text"|"image", name: string, values: string[] }
  const [slots, setSlots] = useState([{ type: "text", name: "", values: [""] }]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  function addSlot() {
    setSlots((prev) => [...prev, { type: "text", name: "", values: [""] }]);
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

  function setSlotName(i, name) {
    setSlots((prev) => prev.map((s, idx) => (idx === i ? { ...s, name } : s)));
  }

  function addValue(i) {
    setSlots((prev) =>
      prev.map((s, idx) => (idx === i ? { ...s, values: [...s.values, ""] } : s))
    );
  }

  function removeValue(i, j) {
    setSlots((prev) =>
      prev.map((s, idx) =>
        idx === i ? { ...s, values: s.values.filter((_, vi) => vi !== j) } : s
      )
    );
  }

  function setValue(i, j, v) {
    setSlots((prev) =>
      prev.map((s, idx) =>
        idx === i
          ? { ...s, values: s.values.map((val, vi) => (vi === j ? v : val)) }
          : s
      )
    );
  }

  async function handleSave() {
    const cleanSlots = slots
      .map((s) => ({
        name: s.type === "image" ? "image" : s.name.trim(),
        values: s.values.map((v) => v.trim()).filter(Boolean),
      }))
      .filter((s) => s.name && s.values.length > 0);

    const textSlots = cleanSlots.filter((s) => s.name !== "image");
    if (textSlots.length === 0) {
      setErr("Add at least one text slot with one value.");
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      const ad = await createManualAd(campaignId, {
        creative_type: "dynamic",
        slots: cleanSlots,
        pool_only: true,
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
      <h4 className="manual-form-title">New Dynamic Template</h4>
      <p className="manual-form-hint">
        Add slots and multiple values per slot. BO explores every combination.
      </p>

      {slots.map((slot, i) => (
        <div key={i} className="manual-slot-block">
          <div className="manual-slot-header">
            <select
              className="manual-slot-type-select"
              value={slot.type}
              onChange={(e) => setSlotType(i, e.target.value)}
            >
              <option value="text">Text</option>
              <option value="image">Image</option>
            </select>
            {slot.type === "image" ? (
              <span className="manual-slot-name-fixed">image</span>
            ) : (
              <input
                className="manual-slot-name-input"
                type="text"
                placeholder="Slot name (e.g. headline)"
                value={slot.name}
                onChange={(e) => setSlotName(i, e.target.value)}
              />
            )}
            {slots.length > 1 && (
              <button className="btn-small btn-danger" onClick={() => removeSlot(i)}>
                Remove
              </button>
            )}
          </div>
          <div className="manual-slot-values">
            {slot.values.map((val, j) =>
              slot.type === "image" ? (
                <ImageValueInput
                  key={j}
                  value={val}
                  onChange={(v) => setValue(i, j, v)}
                  onRemove={() => removeValue(i, j)}
                  showRemove={slot.values.length > 1}
                />
              ) : (
                <div key={j} className="manual-value-row">
                  <input
                    type="text"
                    className="manual-value-input"
                    placeholder={`Value ${j + 1}`}
                    value={val}
                    onChange={(e) => setValue(i, j, e.target.value)}
                  />
                  {slot.values.length > 1 && (
                    <button className="btn-small" onClick={() => removeValue(i, j)}>×</button>
                  )}
                </div>
              )
            )}
            <button className="btn-small" onClick={() => addValue(i)}>+ Value</button>
          </div>
        </div>
      ))}

      <button className="btn-small" onClick={addSlot} style={{ marginTop: 8 }}>+ Add slot</button>

      {err && <p className="error-inline" style={{ marginTop: 8 }}>{err}</p>}

      <div className="manual-form-actions">
        <button className="btn-primary" disabled={saving} onClick={handleSave}>
          {saving ? "Creating…" : "Create template"}
        </button>
        <button className="btn-small" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
