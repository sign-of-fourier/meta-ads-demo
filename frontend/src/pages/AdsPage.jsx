import { useEffect, useState } from "react";
import { getAds } from "../api.js";

export default function AdsPage() {
  const [ads, setAds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    getAds()
      .then(setAds)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p>Loading ads...</p>;
  if (error) return <p className="error">{error}</p>;

  return (
    <div className="ads-page">
      <h2>Ads &amp; Creatives</h2>
      <p className="subtitle">
        Ingested from the connected Meta ad account.
      </p>

      {ads.length === 0 ? (
        <p>No ads found in this ad account.</p>
      ) : (
        <div className="ads-grid">
          {ads.map((ad) => (
            <div key={ad.id} className="ad-card">
              {(ad.thumbnail_url || ad.image_url) && (
                <img
                  className="ad-thumb"
                  src={ad.thumbnail_url || ad.image_url}
                  alt={ad.name || "Ad creative"}
                />
              )}
              <div className="ad-info">
                <h4>{ad.name || `Ad ${ad.id}`}</h4>
                <span
                  className={`status-badge ${ad.status === "ACTIVE" ? "active" : "paused"}`}
                >
                  {ad.status || "–"}
                </span>
                {ad.body && <p className="ad-body">{ad.body}</p>}
                <div className="ad-meta">
                  <span>ID: {ad.id}</span>
                  {ad.campaign_id && <span>Campaign: {ad.campaign_id}</span>}
                  {ad.adset_id && <span>Ad set: {ad.adset_id}</span>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
