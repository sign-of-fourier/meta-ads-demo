import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getMetaStatus, getMetaLoginUrl } from "../api.js";

export default function SettingsPage() {
  const [searchParams] = useSearchParams();
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState(null);

  const justConnected = searchParams.get("meta_connected") === "true";
  const metaError = searchParams.get("meta_error");

  useEffect(() => {
    getMetaStatus()
      .then(setStatus)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  async function handleConnect() {
    setConnecting(true);
    setError(null);
    try {
      const { url } = await getMetaLoginUrl();
      window.location.href = url;
    } catch (err) {
      setError(err.message);
      setConnecting(false);
    }
  }

  if (loading) return <p>Loading...</p>;

  return (
    <div className="settings-page">
      <h2>Settings</h2>

      {justConnected && (
        <div className="success-banner">Meta account connected successfully.</div>
      )}
      {metaError && (
        <div className="error" style={{ marginBottom: "1rem" }}>{metaError}</div>
      )}

      <section className="card">
        <h3>Meta Ads Connection</h3>

        {error && <p className="error">{error}</p>}

        {status?.connected ? (
          <p className="connected-status">
            Connected to ad account{" "}
            <code>{status.ad_account_id}</code>
          </p>
        ) : (
          <button
            onClick={handleConnect}
            disabled={connecting}
            className="btn-primary"
          >
            {connecting ? "Redirecting..." : "Connect Meta Ads Account"}
          </button>
        )}
      </section>
    </div>
  );
}
