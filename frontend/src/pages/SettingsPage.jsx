import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { getMetaStatus, getMetaLoginUrl, getGoogleStatus, getGoogleLoginUrl, getGooglePendingAccounts, selectGoogleAccount } from "../api.js";

export default function SettingsPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const [metaStatus, setMetaStatus] = useState(null);
  const [metaLoading, setMetaLoading] = useState(true);
  const [metaConnecting, setMetaConnecting] = useState(false);
  const [metaError, setMetaError] = useState(null);

  const [googleStatus, setGoogleStatus] = useState(null);
  const [googleLoading, setGoogleLoading] = useState(true);
  const [googleConnecting, setGoogleConnecting] = useState(false);
  const [googleError, setGoogleError] = useState(null);

  const [pickKey, setPickKey] = useState(null);
  const [pickAccounts, setPickAccounts] = useState(null);
  const [pickSelected, setPickSelected] = useState(null);
  const [pickManual, setPickManual] = useState("");
  const [pickLoginId, setPickLoginId] = useState("");
  const [pickSaving, setPickSaving] = useState(false);

  const justConnectedMeta = searchParams.get("meta_connected") === "true";
  const justConnectedGoogle = searchParams.get("google_connected") === "true";
  const metaErrorParam = searchParams.get("meta_error");
  const googleErrorParam = searchParams.get("google_error");
  const googlePickParam = searchParams.get("google_pick");

  useEffect(() => {
    getMetaStatus()
      .then(setMetaStatus)
      .catch((err) => setMetaError(err.message))
      .finally(() => setMetaLoading(false));

    getGoogleStatus()
      .then(setGoogleStatus)
      .catch((err) => setGoogleError(err.message))
      .finally(() => setGoogleLoading(false));
  }, []);

  useEffect(() => {
    if (!googlePickParam) return;
    setPickKey(googlePickParam);
    getGooglePendingAccounts(googlePickParam)
      .then((data) => {
        setPickAccounts(data.accounts);
        setPickSelected(data.accounts[0]?.customer_id ?? null);
      })
      .catch((err) => setGoogleError(err.message));
  }, [googlePickParam]);

  async function handlePickConfirm() {
    const customerId = pickManual.trim() || pickSelected;
    const loginId = pickLoginId.trim() || (pickManual.trim() ? pickSelected : null);
    if (!customerId || !pickKey) return;
    setPickSaving(true);
    setGoogleError(null);
    try {
      await selectGoogleAccount(pickKey, customerId, loginId);
      const status = await getGoogleStatus();
      setGoogleStatus(status);
      setPickKey(null);
      setPickAccounts(null);
      navigate("/app/settings?google_connected=true", { replace: true });
    } catch (err) {
      setGoogleError(err.message);
    } finally {
      setPickSaving(false);
    }
  }

  async function handleMetaConnect() {
    setMetaConnecting(true);
    setMetaError(null);
    try {
      const { url } = await getMetaLoginUrl();
      window.location.href = url;
    } catch (err) {
      setMetaError(err.message);
      setMetaConnecting(false);
    }
  }

  async function handleGoogleConnect() {
    setGoogleConnecting(true);
    setGoogleError(null);
    try {
      const { url } = await getGoogleLoginUrl();
      window.location.href = url;
    } catch (err) {
      setGoogleError(err.message);
      setGoogleConnecting(false);
    }
  }

  return (
    <div className="settings-page">
      <h2>Settings</h2>

      {justConnectedMeta && (
        <div className="success-banner">Meta account connected successfully.</div>
      )}
      {justConnectedGoogle && (
        <div className="success-banner">Google Ads account connected successfully.</div>
      )}
      {metaErrorParam && (
        <div className="error" style={{ marginBottom: "1rem" }}>{metaErrorParam}</div>
      )}
      {googleErrorParam && (
        <div className="error" style={{ marginBottom: "1rem" }}>{googleErrorParam}</div>
      )}

      <section className="settings-connect-banner">
        <p className="settings-connect-headline">
          👋 Here, you authenticate and connect your ad accounts.
        </p>
        <p className="settings-connect-sub">
          Connect as many accounts as you need, then head to the{" "}
          <strong>Dashboard</strong> to ingest your campaigns and get
          AI-powered recommendations.
        </p>
      </section>

      <section className="card" style={{ background: "#f8f9ff", border: "1px solid #dde3f5" }}>
        <p style={{ margin: "0 0 0.5rem", fontWeight: 600, color: "#333" }}>How it works</p>
        <ol style={{ margin: 0, paddingLeft: "1.4rem", lineHeight: 1.9, color: "#444", fontSize: "0.9rem" }}>
          <li>Connect your Meta and/or Google Ads account below</li>
          <li>Go to the <strong>Dashboard</strong> in the nav</li>
          <li>Click <strong>Sync</strong> to load your campaigns</li>
          <li>Click <strong>Ingest</strong> on any campaign row to read its ads</li>
          <li>Click <strong>Get Recommendations</strong> — the AI suggests exactly what to test next</li>
        </ol>
      </section>

      <section className="card">
        <h3>Meta Ads Connection</h3>
        {metaLoading ? (
          <p>Loading...</p>
        ) : (
          <>
            {metaError && <p className="error">{metaError}</p>}
            {metaStatus?.connected ? (
              <div className="connected-row">
                <p className="connected-status">
                  Connected to ad account <code>{metaStatus.ad_account_id}</code>
                </p>
                <button onClick={handleMetaConnect} disabled={metaConnecting} className="btn-secondary">
                  {metaConnecting ? "Redirecting..." : "Reconnect"}
                </button>
              </div>
            ) : (
              <button onClick={handleMetaConnect} disabled={metaConnecting} className="btn-primary">
                {metaConnecting ? "Redirecting..." : "Connect Meta Ads Account"}
              </button>
            )}
          </>
        )}
      </section>

      <section className="card">
        <h3>Google Ads Connection</h3>
        {googleLoading ? (
          <p>Loading...</p>
        ) : (
          <>
            {googleError && <p className="error">{googleError}</p>}
            {pickAccounts ? (
              <div>
                <p style={{ marginBottom: "0.75rem" }}>Select a Google Ads account:</p>
                <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", marginBottom: "1rem" }}>
                  {pickAccounts.map((acct) => (
                    <label key={acct.customer_id} style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer" }}>
                      <input
                        type="radio"
                        name="google_account"
                        value={acct.customer_id}
                        checked={pickSelected === acct.customer_id}
                        onChange={() => setPickSelected(acct.customer_id)}
                      />
                      <span>{acct.name}</span>
                      <code style={{ fontSize: "0.8em", color: "#666" }}>{acct.customer_id}</code>
                    </label>
                  ))}
                </div>
                <div style={{ marginBottom: "1rem", borderTop: "1px solid #eee", paddingTop: "0.75rem" }}>
                  <label style={{ display: "block", fontSize: "0.85em", color: "#666", marginBottom: "0.35rem" }}>
                    Or enter a customer ID manually:
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. 302-153-6317"
                    value={pickManual}
                    onChange={(e) => setPickManual(e.target.value)}
                    style={{ padding: "0.4rem 0.6rem", border: "1px solid #ccc", borderRadius: 4, fontSize: "0.9em", width: "100%", boxSizing: "border-box", marginBottom: "0.5rem" }}
                  />
                  <label style={{ display: "block", fontSize: "0.85em", color: "#666", marginBottom: "0.35rem" }}>
                    Manager / login customer ID{pickManual.trim() && pickSelected ? ` (auto: ${pickSelected})` : " (if required):"}
                  </label>
                  <input
                    type="text"
                    placeholder={pickManual.trim() && pickSelected ? pickSelected : "e.g. manager account ID"}
                    value={pickLoginId}
                    onChange={(e) => setPickLoginId(e.target.value)}
                    style={{ padding: "0.4rem 0.6rem", border: "1px solid #ccc", borderRadius: 4, fontSize: "0.9em", width: "100%", boxSizing: "border-box" }}
                  />
                </div>
                <button onClick={handlePickConfirm} disabled={pickSaving || (!pickSelected && !pickManual.trim())} className="btn-primary">
                  {pickSaving ? "Connecting..." : "Connect"}
                </button>
              </div>
            ) : googleStatus?.connected ? (
              <div className="connected-row">
                <p className="connected-status">
                  Connected{googleStatus.customer_name ? ` as ${googleStatus.customer_name}` : ""}{" "}
                  <code>{googleStatus.customer_id}</code>
                </p>
                <button onClick={handleGoogleConnect} disabled={googleConnecting} className="btn-secondary">
                  {googleConnecting ? "Redirecting..." : "Reconnect"}
                </button>
              </div>
            ) : (
              <button onClick={handleGoogleConnect} disabled={googleConnecting} className="btn-primary">
                {googleConnecting ? "Redirecting..." : "Connect Google Ads Account"}
              </button>
            )}
          </>
        )}
      </section>
    </div>
  );
}
