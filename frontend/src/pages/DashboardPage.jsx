import { useEffect, useState } from "react";
import SyncBar from "../components/SyncBar.jsx";
import UnifiedCampaignsTable from "../components/UnifiedCampaignsTable.jsx";
import BatchPanel from "../components/BatchPanel.jsx";
import {
  getCampaigns,
  runIngest,
  pushGeneratedAds,
  ingestCampaignStructure,
  getCampaignStructure,
  getGoogleCampaigns,
  ingestGoogleStructure,
  getGoogleStructure,
  pushGoogleAds,
  runUnifiedCrossPlatformBO,
} from "../api.js";

const META_LAST_SYNCED_KEY = "meta_last_synced";
const INGESTED_KEY = "unified_ingested_keys"; // stored as array of "platform:id"
const PAIRS_KEY = "cross_platform_pairs";

function loadIngestedKeys() {
  try {
    return new Set(JSON.parse(localStorage.getItem(INGESTED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveIngestedKeys(keys) {
  localStorage.setItem(INGESTED_KEY, JSON.stringify([...keys]));
}

function loadSelectedAds() {
  try {
    return JSON.parse(localStorage.getItem(PAIRS_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveSelectedAds(ads) {
  localStorage.setItem(PAIRS_KEY, JSON.stringify(ads));
}

export default function DashboardPage() {
  const [metaCampaigns, setMetaCampaigns] = useState([]);
  const [googleCampaigns, setGoogleCampaigns] = useState([]);
  const [metaLoading, setMetaLoading] = useState(true);
  const [googleLoading, setGoogleLoading] = useState(true);
  const [metaError, setMetaError] = useState(null);
  const [googleError, setGoogleError] = useState(null);
  const [metaLastSynced, setMetaLastSynced] = useState(
    () => localStorage.getItem(META_LAST_SYNCED_KEY)
  );
  const [metaPushNote, setMetaPushNote] = useState(null);
  const [googleSyncNote, setGoogleSyncNote] = useState(null);

  // "platform:campaignId" — only one open at a time
  const [expandedKey, setExpandedKey] = useState(null);
  // structureByKey: { "platform:id": AdStructure[] }
  const [structureByKey, setStructureByKey] = useState({});
  const [ingestingKey, setIngestingKey] = useState(null);
  const [ingestedKeys, setIngestedKeys] = useState(loadIngestedKeys);

  // Batch selection: [{ platform, seed_ad_id, label }]
  const [selectedAds, setSelectedAds] = useState(loadSelectedAds);
  const [topN, setTopN] = useState(4);
  const [boState, setBoState] = useState(null);

  // ── Load both platforms on mount ──────────────────────────────────────────
  useEffect(() => {
    getCampaigns()
      .then(setMetaCampaigns)
      .catch((err) => setMetaError(err.message))
      .finally(() => setMetaLoading(false));

    getGoogleCampaigns()
      .then(setGoogleCampaigns)
      .catch((err) => setGoogleError(err.message))
      .finally(() => setGoogleLoading(false));
  }, []);

  // ── Silently restore structure for previously-ingested campaigns ──────────
  useEffect(() => {
    if (metaCampaigns.length === 0 && googleCampaigns.length === 0) return;
    for (const key of ingestedKeys) {
      if (structureByKey[key] !== undefined) continue;
      const colonIdx = key.indexOf(":");
      const platform = key.slice(0, colonIdx);
      const id = key.slice(colonIdx + 1);
      const fetcher =
        platform === "meta" ? getCampaignStructure : getGoogleStructure;
      fetcher(id)
        .then((ads) => {
          if (ads?.length > 0) {
            setStructureByKey((prev) => ({ ...prev, [key]: ads }));
          }
        })
        .catch(() => {});
    }
  }, [metaCampaigns, googleCampaigns]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sync handlers ──────────────────────────────────────────────────────────
  async function handleMetaSync() {
    setMetaLoading(true);
    setMetaPushNote(null);
    setMetaError(null);
    try {
      let pushSummary = null;
      try {
        pushSummary = await pushGeneratedAds();
      } catch {}
      await runIngest();
      const now = new Date().toLocaleString();
      localStorage.setItem(META_LAST_SYNCED_KEY, now);
      setMetaLastSynced(now);
      const updated = await getCampaigns();
      setMetaCampaigns(updated);
      if (pushSummary?.pushed > 0) {
        setMetaPushNote({
          type: "success",
          text: `Pushed ${pushSummary.pushed} ad${pushSummary.pushed !== 1 ? "s" : ""} to Meta`,
        });
      } else if (pushSummary?.failed > 0) {
        setMetaPushNote({
          type: "amber",
          text: `${pushSummary.failed} ads ready to push — Meta app must be in Live mode`,
        });
      }
    } catch (err) {
      setMetaError(err.message);
    } finally {
      setMetaLoading(false);
    }
  }

  async function handleGoogleSync() {
    setGoogleLoading(true);
    setGoogleSyncNote(null);
    setGoogleError(null);
    try {
      const result = await pushGoogleAds();
      if (result?.note) {
        setGoogleSyncNote({ type: "amber", text: result.note });
      } else if (result?.pushed > 0) {
        setGoogleSyncNote({
          type: "success",
          text: `Pushed ${result.pushed} ad${result.pushed !== 1 ? "s" : ""} to Google Ads`,
        });
      }
      const updated = await getGoogleCampaigns();
      setGoogleCampaigns(updated);
    } catch (err) {
      setGoogleError(err.message);
    } finally {
      setGoogleLoading(false);
    }
  }

  // ── Accordion: one row open at a time ─────────────────────────────────────
  function handleToggle(compositeKey) {
    setExpandedKey((prev) => (prev === compositeKey ? null : compositeKey));
  }

  // ── Ingest ─────────────────────────────────────────────────────────────────
  async function handleIngest(campaignId, platform) {
    const key = `${platform}:${campaignId}`;
    setIngestingKey(key);
    try {
      if (platform === "meta") {
        await ingestCampaignStructure(campaignId);
        const ads = await getCampaignStructure(campaignId);
        setStructureByKey((prev) => ({ ...prev, [key]: ads }));
      } else {
        await ingestGoogleStructure(campaignId);
        const ads = await getGoogleStructure(campaignId);
        setStructureByKey((prev) => ({ ...prev, [key]: ads }));
      }
      setIngestedKeys((prev) => {
        const next = new Set(prev);
        next.add(key);
        saveIngestedKeys(next);
        return next;
      });
    } catch (err) {
      alert(`Ingest failed: ${err.message}`);
    } finally {
      setIngestingKey(null);
    }
  }

  // ── Batch selection ────────────────────────────────────────────────────────
  function handleToggleAd({ platform, seed_ad_id, label }) {
    setSelectedAds((prev) => {
      const already = prev.some(
        (a) => a.platform === platform && a.seed_ad_id === seed_ad_id
      );
      const next = already
        ? prev.filter(
            (a) => !(a.platform === platform && a.seed_ad_id === seed_ad_id)
          )
        : [...prev, { platform, seed_ad_id, label }];
      saveSelectedAds(next);
      return next;
    });
  }

  function handleRemoveAd(platform, seed_ad_id) {
    setSelectedAds((prev) => {
      const next = prev.filter(
        (a) => !(a.platform === platform && a.seed_ad_id === seed_ad_id)
      );
      saveSelectedAds(next);
      return next;
    });
  }

  // ── Cross-platform BO ──────────────────────────────────────────────────────
  async function handleRunBO() {
    setBoState({ status: "loading" });
    try {
      const pairs = selectedAds.map(({ platform, seed_ad_id, text_source_id }) => ({
        platform,
        seed_ad_id,
        text_source_id: text_source_id ?? seed_ad_id,
      }));
      const result = await runUnifiedCrossPlatformBO(pairs, topN);
      setBoState({ status: "done", data: result });
    } catch (err) {
      setBoState({ status: "error", error: err.message });
    }
  }

  // ── Derived state ──────────────────────────────────────────────────────────
  const allCampaigns = [
    ...metaCampaigns.map((c) => ({ ...c, platform: "meta" })),
    ...googleCampaigns.map((c) => ({ ...c, platform: "google" })),
  ];

  // Set of "platform:seed_ad_id" for O(1) checkbox lookup in AdsPanel
  const selectedAdIds = new Set(
    selectedAds.map((a) => `${a.platform}:${a.seed_ad_id}`)
  );

  return (
    <div className="dashboard-page">
      <div className="dashboard-header">
        <h2 className="dashboard-title">Ad Ingestion Dashboard</h2>
      </div>

      <SyncBar
        metaSyncing={metaLoading}
        googleSyncing={googleLoading}
        onMetaSync={handleMetaSync}
        onGoogleSync={handleGoogleSync}
        metaLastSynced={metaLastSynced}
        metaError={metaError}
        googleError={googleError}
        metaPushNote={metaPushNote}
        googleSyncNote={googleSyncNote}
      />

      <UnifiedCampaignsTable
        campaigns={allCampaigns}
        expandedKey={expandedKey}
        onToggle={handleToggle}
        structureByKey={structureByKey}
        ingestingKey={ingestingKey}
        onIngest={handleIngest}
        selectedAdIds={selectedAdIds}
        onToggleAd={handleToggleAd}
      />

      <BatchPanel
        selectedAds={selectedAds}
        onRemove={handleRemoveAd}
        topN={topN}
        onTopNChange={setTopN}
        onRunBO={handleRunBO}
        boState={boState}
      />
    </div>
  );
}
