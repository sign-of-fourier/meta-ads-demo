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
  createGenerator,
  runBOForGenerator,
} from "../api.js";

const META_LAST_SYNCED_KEY = "meta_last_synced";
const GOOGLE_LAST_SYNCED_KEY = "google_last_synced";
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
  const [googleLastSynced, setGoogleLastSynced] = useState(
    () => localStorage.getItem(GOOGLE_LAST_SYNCED_KEY)
  );
  const [metaPushNote, setMetaPushNote] = useState(null);
  const [googleSyncNote, setGoogleSyncNote] = useState(null);

  // "platform:campaignId" — only one open at a time
  const [expandedKey, setExpandedKey] = useState(null);
  // structureByKey: { "platform:id": AdStructure[] }
  const [structureByKey, setStructureByKey] = useState({});
  const [ingestingKey, setIngestingKey] = useState(null);
  const [ingestedKeys, setIngestedKeys] = useState(loadIngestedKeys);

  // parent_should_pause flags: { "platform:campaignId": bool }
  const [pauseWarningByKey, setPauseWarningByKey] = useState({});

  // campaigns synced but not yet re-ingested: Set of "platform:campaignId"
  const [staleCampaigns, setStaleCampaigns] = useState(new Set());

  // Batch selection: [{ platform, seed_ad_id, label }]
  const [selectedAds, setSelectedAds] = useState(loadSelectedAds);
  const [topN, setTopN] = useState(4);
  const [targetMetric, setTargetMetric] = useState(null);
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
      setStaleCampaigns((prev) => {
        const next = new Set(prev);
        for (const key of ingestedKeys) {
          if (key.startsWith("meta:")) next.add(key);
        }
        return next;
      });
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
      const now = new Date().toLocaleString();
      localStorage.setItem(GOOGLE_LAST_SYNCED_KEY, now);
      setGoogleLastSynced(now);
      const updated = await getGoogleCampaigns();
      setGoogleCampaigns(updated);
      setStaleCampaigns((prev) => {
        const next = new Set(prev);
        for (const key of ingestedKeys) {
          if (key.startsWith("google:")) next.add(key);
        }
        return next;
      });
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
        const ingestResult = await ingestCampaignStructure(campaignId);
        const ads = await getCampaignStructure(campaignId);
        setStructureByKey((prev) => ({ ...prev, [key]: ads }));
        if (ingestResult?.parent_should_pause) {
          setPauseWarningByKey((prev) => ({ ...prev, [key]: true }));
        }
      } else {
        const ingestResult = await ingestGoogleStructure(campaignId);
        const ads = await getGoogleStructure(campaignId);
        setStructureByKey((prev) => ({ ...prev, [key]: ads }));
        if (ingestResult?.parent_should_pause) {
          setPauseWarningByKey((prev) => ({ ...prev, [key]: true }));
        }
      }
      setIngestedKeys((prev) => {
        const next = new Set(prev);
        next.add(key);
        saveIngestedKeys(next);
        return next;
      });
      setStaleCampaigns((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    } catch (err) {
      alert(`Ingest failed: ${err.message}`);
    } finally {
      setIngestingKey(null);
    }
  }

  // ── Batch selection ────────────────────────────────────────────────────────
  function handleToggleAd({ platform, seed_ad_id, label, adType }) {
    setSelectedAds((prev) => {
      const already = prev.some(
        (a) => a.platform === platform && a.seed_ad_id === seed_ad_id
      );
      const next = already
        ? prev.filter(
            (a) => !(a.platform === platform && a.seed_ad_id === seed_ad_id)
          )
        : [...prev, { platform, seed_ad_id, label, adType }];
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
      const platforms = [...new Set(selectedAds.map((a) => a.platform))];

      if (platforms.length === 1) {
        // Same platform — merge into a generator and run BO on the combined pool
        const platform = platforms[0];
        const name = `Auto ${platform} ${new Date().toISOString().slice(0, 10)}`;
        const members = selectedAds.map((a) => ({
          ad_id: a.seed_ad_id,
          contribution_mode: a.adType === "original_static" ? "static" : "dynamic",
        }));
        const { id: generatorId } = await createGenerator(name, members);
        const result = await runBOForGenerator(generatorId, platform, targetMetric);
        // Enrich each pick with platform + seed_ad_id so BatchPushFooter needs no external context
        const enrichedPicks = (result.picks || []).map((p) => ({
          ...p,
          platform,
          seed_ad_id: p.seed_ad_id ?? generatorId,
        }));
        setBoState({
          status: "done",
          data: { type: "generator", platform, seed_ad_id: generatorId, members: selectedAds, ...result, picks: enrichedPicks },
        });
      } else {
        // Mixed platforms — unified cross-platform GP (existing behaviour)
        const pairs = selectedAds.map(({ platform, seed_ad_id, text_source_id }) => ({
          platform,
          seed_ad_id,
          text_source_id: text_source_id ?? seed_ad_id,
        }));
        const result = await runUnifiedCrossPlatformBO(pairs, topN, targetMetric);
        // Enrich each pick with seed_ad_id from group_stats so BatchPushFooter is self-contained
        const seedByPlatform = Object.fromEntries(
          (result.group_stats || []).map((s) => [s.platform, s.seed_ad_id])
        );
        const enrichedPicks = (result.picks || []).map((p) => ({
          ...p,
          seed_ad_id: p.seed_ad_id ?? seedByPlatform[p.platform],
        }));
        setBoState({ status: "done", data: { type: "cross-platform", ...result, picks: enrichedPicks } });
      }
    } catch (err) {
      setBoState({ status: "error", error: err.message });
    }
  }

  // ── Post-push: re-ingest campaigns that contained selected ads ────────────
  async function handlePushDone() {
    const toReingest = [];
    for (const [key, ads] of Object.entries(structureByKey)) {
      const colonIdx = key.indexOf(":");
      const plat = key.slice(0, colonIdx);
      if (ads.some((ad) => selectedAds.some((s) => s.platform === plat && s.seed_ad_id === ad.ad_id))) {
        toReingest.push(key);
      }
    }
    for (const key of toReingest) {
      const colonIdx = key.indexOf(":");
      const plat = key.slice(0, colonIdx);
      const campaignId = key.slice(colonIdx + 1);
      await handleIngest(campaignId, plat);
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

      {/* ── Step 1: Sync ─────────────────────────────────────────────────────── */}
      <section className="dash-section">
        <div className="dash-step-hd">
          <span className="dash-step-num s1">1</span>
          <div>
            <h3 className="dash-step-title">Sync campaigns</h3>
            <p className="dash-step-desc">
              Pull the latest campaign list from Meta and Google. Expand a campaign row, then click <strong>Re-ingest</strong> to load its ads.
            </p>
          </div>
        </div>
        <div className="dash-section-body">
          <SyncBar
            metaSyncing={metaLoading}
            googleSyncing={googleLoading}
            onMetaSync={handleMetaSync}
            onGoogleSync={handleGoogleSync}
            metaLastSynced={metaLastSynced}
            googleLastSynced={googleLastSynced}
            metaError={metaError}
            googleError={googleError}
            metaPushNote={metaPushNote}
            googleSyncNote={googleSyncNote}
          />
        </div>
      </section>

      {/* ── Step 2: Select ads ───────────────────────────────────────────────── */}
      <section className="dash-section">
        <div className="dash-step-hd">
          <span className="dash-step-num s2">2</span>
          <div>
            <h3 className="dash-step-title">Select ads to analyze</h3>
            <div className="dash-select-legend">
              <div className="dash-legend-row">
                <span className="creative-type-badge dynamic">Template</span>
                <span>Check a Dynamic or RSA ad — Adstac.kr will explore all its headline, description, and image combinations.</span>
              </div>
              <div className="dash-legend-row">
                <span className="creative-type-badge static">Static</span>
                <span>Check a static ad to add it to the candidate pool. Its past performance (if any) will also inform the recommendations.</span>
              </div>
            </div>
          </div>
        </div>
        <div className="dash-section-body" style={{ padding: 0 }}>
          <UnifiedCampaignsTable
            campaigns={allCampaigns}
            expandedKey={expandedKey}
            onToggle={handleToggle}
            structureByKey={structureByKey}
            ingestingKey={ingestingKey}
            onIngest={handleIngest}
            selectedAdIds={selectedAdIds}
            onToggleAd={handleToggleAd}
            pauseWarningByKey={pauseWarningByKey}
            staleCampaigns={staleCampaigns}
          />
        </div>
      </section>

      {/* ── Step 3: Adstac.kr ────────────────────────────────────────────────── */}
      <section className="dash-section">
        <div className="dash-step-hd">
          <span className="dash-step-num s3">3</span>
          <div>
            <h3 className="dash-step-title">Run Adstac.kr</h3>
            <p className="dash-step-desc">
              Review the ads you've selected, then run Adstac.kr to get ranked combination recommendations. Check any recommendation to push it as a new static ad.
            </p>
          </div>
        </div>
        <div className="dash-section-body">
          <BatchPanel
            selectedAds={selectedAds}
            onRemove={handleRemoveAd}
            topN={topN}
            onTopNChange={setTopN}
            targetMetric={targetMetric}
            onTargetMetricChange={setTargetMetric}
            onRunBO={handleRunBO}
            boState={boState}
            onPushDone={handlePushDone}
          />
        </div>
      </section>

    </div>
  );
}
