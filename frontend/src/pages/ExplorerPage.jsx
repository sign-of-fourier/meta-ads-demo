import { useState } from "react";
import { getExplore } from "../api.js";

function JsonNode({ label, value, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);

  if (value === null || value === undefined) {
    return (
      <div className="json-row">
        <span className="json-key">{label}: </span>
        <span className="json-null">null</span>
      </div>
    );
  }

  if (typeof value !== "object") {
    const cls =
      typeof value === "boolean"
        ? "json-bool"
        : typeof value === "number"
          ? "json-num"
          : "json-str";
    return (
      <div className="json-row">
        {label && <span className="json-key">{label}: </span>}
        <span className={cls}>{JSON.stringify(value)}</span>
      </div>
    );
  }

  const isArray = Array.isArray(value);
  const entries = isArray
    ? value.map((v, i) => [String(i), v])
    : Object.entries(value);
  const preview = isArray
    ? `[ ${value.length} items ]`
    : `{ ${Object.keys(value).slice(0, 3).join(", ")}${Object.keys(value).length > 3 ? "…" : ""} }`;

  return (
    <div className="json-node">
      <button className="json-toggle" onClick={() => setOpen((o) => !o)}>
        <span className="json-caret">{open ? "▾" : "▸"}</span>
        {label && <span className="json-key">{label}</span>}
        {!open && <span className="json-preview">{preview}</span>}
      </button>
      {open && (
        <div className="json-children">
          {entries.map(([k, v]) => (
            <JsonNode key={k} label={k} value={v} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function ExplorerPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [view, setView] = useState("tree"); // "tree" | "raw"

  async function handleFetch() {
    setError(null);
    setLoading(true);
    setData(null);
    try {
      const result = await getExplore();
      setData(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="explorer-page">
      <div className="campaigns-header">
        <div>
          <h2>Meta API Explorer</h2>
          <p className="subtitle">
            Raw data from Meta — campaigns, adsets, and ads with all fields.
            Nothing is saved.
          </p>
        </div>
        <button className="btn-primary" onClick={handleFetch} disabled={loading}>
          {loading ? "Fetching…" : "Fetch from Meta"}
        </button>
      </div>

      {error && <p className="error">{error}</p>}

      {data && (
        <>
          {data._ads_fetch_error && (
            <div className="explorer-fetch-error">
              <strong>Ads fetch failed</strong> — Meta returned an error for the ads
              request. The fields list may include unsupported fields for this account.
              <pre className="explorer-error-detail">
                {JSON.stringify(data._ads_fetch_error, null, 2)}
              </pre>
            </div>
          )}

          <div className="explorer-toolbar">
            <span className="explorer-summary">
              {data.campaigns.length} campaign{data.campaigns.length !== 1 ? "s" : ""},{" "}
              {data._all_adsets.length} adset{data._all_adsets.length !== 1 ? "s" : ""},{" "}
              {data._all_ads.length} ad{data._all_ads.length !== 1 ? "s" : ""}
            </span>
            <div className="view-toggle">
              <button
                className={view === "tree" ? "btn-primary" : "btn-secondary"}
                onClick={() => setView("tree")}
              >
                Tree
              </button>
              <button
                className={view === "raw" ? "btn-primary" : "btn-secondary"}
                onClick={() => setView("raw")}
              >
                Raw JSON
              </button>
            </div>
          </div>

          {view === "raw" ? (
            <pre className="raw-json">{JSON.stringify(data, null, 2)}</pre>
          ) : (
            <div className="json-tree">
              <JsonNode label="response" value={data} defaultOpen={true} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
