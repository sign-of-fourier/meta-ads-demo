/**
 * Thin wrapper around fetch that handles auth headers and JSON.
 */

const API = "";

function authHeaders() {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request(path, opts = {}) {
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(opts.headers || {}),
    },
  });

  if (!res.ok) {
    if (res.status === 401) {
      localStorage.removeItem("token");
      window.location.href = "/app/auth";
      return;
    }
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    // Some errors (e.g. the write-access gate) return a structured detail object
    // instead of a plain string — surface its message, not "[object Object]".
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || `API error ${res.status}`;
    const err = new Error(message);
    err.status = res.status;
    if (detail && typeof detail === "object") {
      err.code = detail.error;
      err.tier = detail.tier;
    }
    throw err;
  }

  // 204 No Content or empty body
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

// ── Auth ────────────────────────────────────────────────────────────────────

export async function signup(email, password) {
  const data = await request("/auth/signup", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  localStorage.setItem("token", data.token);
  return data;
}

export async function login(email, password) {
  const data = await request("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  localStorage.setItem("token", data.token);
  return data;
}

export function logout() {
  localStorage.removeItem("token");
}

export function isLoggedIn() {
  return !!localStorage.getItem("token");
}

// ── User ─────────────────────────────────────────────────────────────────────

export async function getMe() {
  return request("/me");
}

// ── Meta connection ─────────────────────────────────────────────────────────

export async function getMetaStatus() {
  return request("/me/meta-status");
}

export async function getMetaLoginUrl() {
  return request("/auth/meta/login-url");
}

// ── Google connection ────────────────────────────────────────────────────────

export async function getGoogleStatus() {
  return request("/me/google-status");
}

export async function getGoogleLoginUrl() {
  return request("/auth/google/login-url");
}

export async function getGoogleCampaigns() {
  return request("/api/google/campaigns");
}

export async function ingestGoogleStructure(campaignId) {
  return request(`/api/google/ingest/structure/${campaignId}`, { method: "POST" });
}

export async function getGoogleStructure(campaignId) {
  return request(`/api/google/structure/${campaignId}`);
}

export async function getGooglePendingAccounts(key) {
  return request(`/auth/google/pending/${key}`);
}

export async function selectGoogleAccount(key, customerId, loginCustomerId) {
  return request("/auth/google/select-account", {
    method: "POST",
    body: JSON.stringify({ key, customer_id: customerId, login_customer_id: loginCustomerId || null }),
  });
}

// ── Campaigns ───────────────────────────────────────────────────────────────

export async function getCampaigns() {
  return request("/api/campaigns");
}

export async function pauseCampaign(campaignId) {
  return request(`/api/campaigns/${campaignId}/pause`, { method: "POST" });
}

export async function resumeCampaign(campaignId) {
  return request(`/api/campaigns/${campaignId}/resume`, { method: "POST" });
}

// ── Ads / creatives ─────────────────────────────────────────────────────────

export async function getAds() {
  return request("/api/ads");
}

export async function getLocalAds() {
  return request("/api/ads/local");
}

export async function deleteLocalAd(adId) {
  return request(`/api/ads/local/${adId}`, { method: "DELETE" });
}

// ── Ingest ──────────────────────────────────────────────────────────────────

export async function getIngestPreview() {
  return request("/api/ingest/preview");
}

export async function runIngest() {
  return request("/api/ingest", { method: "POST" });
}

export async function pushGeneratedAds() {
  return request("/api/push", { method: "POST" });
}

export async function getExplore() {
  return request("/api/explore");
}

// ── Dashboard / history ─────────────────────────────────────────────────────

export async function getCampaignHistory(campaignId, days = 30) {
  return request(`/api/campaigns/${campaignId}/history?days=${days}`);
}

// ── Structural ingest ────────────────────────────────────────────────────────

export async function ingestCampaignStructure(campaignId) {
  return request(`/api/ingest/structure/${campaignId}`, { method: "POST" });
}

export async function getCampaignStructure(campaignId) {
  return request(`/api/structure/${campaignId}`);
}

// ── Suggestions ──────────────────────────────────────────────────────────────

export async function getCampaignSuggestions(campaignId) {
  return request(`/api/suggestions?campaign_id=${campaignId}`);
}

export async function confirmSuggestion(id, action) {
  return request(`/api/suggestions/${id}/confirm`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });
}

// ── Bayesian Optimisation ────────────────────────────────────────────────────

export async function runBO(seedAdId, textSourceId) {
  return request("/api/bo/run", {
    method: "POST",
    body: JSON.stringify({ seed_ad_id: seedAdId, text_source_id: textSourceId }),
  });
}

// ── Ad text generation ────────────────────────────────────────────────────────

export async function generateTextAds(campaignId, seedAdId) {
  const qs = seedAdId ? `?seed_ad_id=${encodeURIComponent(seedAdId)}` : "";
  return request(`/api/generate/text/${campaignId}${qs}`, { method: "POST" });
}

export async function generateGoogleTextAds(campaignId, seedAdId) {
  const qs = seedAdId ? `?seed_ad_id=${encodeURIComponent(seedAdId)}` : "";
  return request(`/api/google/generate/text/${campaignId}${qs}`, { method: "POST" });
}

export async function runGoogleBO(seedAdId, textSourceId) {
  return request("/api/google/bo/run", {
    method: "POST",
    body: JSON.stringify({ seed_ad_id: seedAdId, text_source_id: textSourceId }),
  });
}

export async function getGoogleBOResults(adId) {
  return request(`/api/google/bo/results/${encodeURIComponent(adId)}`);
}

export async function pushGoogleAds() {
  return request("/api/google/push", { method: "POST" });
}

export async function pushPick({ platform, seedAdId, combinationKey, combination, name }) {
  return request("/api/push/pick", {
    method: "POST",
    body: JSON.stringify({
      platform,
      seed_ad_id: seedAdId,
      combination_key: combinationKey,
      combination,
      name,
    }),
  });
}

export async function activatePick({ platform, platformAdId }) {
  return request("/api/activate", {
    method: "POST",
    body: JSON.stringify({ platform, platform_ad_id: platformAdId }),
  });
}

export async function pauseAd({ platform, platformAdId }) {
  return request("/api/pause-ad", {
    method: "POST",
    body: JSON.stringify({ platform, platform_ad_id: platformAdId }),
  });
}

export async function retainPick(comboId) {
  return request(`/api/push/retain/${comboId}`, { method: "POST" });
}

export async function pushMatch({ platform, seedAdId, combinationKey, combination, existingAdId }) {
  return request("/api/push/match", {
    method: "POST",
    body: JSON.stringify({
      platform,
      seed_ad_id: seedAdId,
      combination_key: combinationKey,
      combination,
      existing_ad_id: existingAdId,
    }),
  });
}

export async function createGenerator(name, members) {
  return request("/api/generators", {
    method: "POST",
    body: JSON.stringify({ name, members }),
  });
}

export async function runBOForGenerator(generatorId, platform, targetMetric) {
  const endpoint = platform === "google" ? "/api/google/bo/run" : "/api/bo/run";
  return request(endpoint, {
    method: "POST",
    body: JSON.stringify({
      seed_ad_id: generatorId,
      text_source_id: generatorId,
      generator_id: generatorId,
      ...(targetMetric ? { target_metric: targetMetric } : {}),
    }),
  });
}

export async function startDynamicGeneration(campaignId, seedAdId) {
  const qs = seedAdId ? `?seed_ad_id=${encodeURIComponent(seedAdId)}` : "";
  return request(`/api/generate/dynamic/${campaignId}${qs}`, { method: "POST" });
}

export async function storeSuggestion(data) {
  return request("/api/suggestions", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function getDynamicGenStatus(jobId) {
  return request(`/api/generate/dynamic/status/${jobId}`);
}

// ── Cross-platform BO ─────────────────────────────────────────────────────────

/**
 * pairs: array of { platform, seed_ad_id, text_source_id }
 * Returns { picks, group_stats }
 */
export async function runCrossPlatformBO(pairs) {
  return request("/api/bo/cross-platform", {
    method: "POST",
    body: JSON.stringify({ pairs }),
  });
}

/**
 * Unified cross-platform BO: per-group PCA to same K-dim, single GP call.
 * pairs: array of { platform, seed_ad_id, text_source_id }
 * topN: number of recommendations to return (default 4)
 * Returns { picks, group_stats }
 */
export async function runUnifiedCrossPlatformBO(pairs, topN = 4, targetMetric = null) {
  return request("/api/bo/cross-platform/unified", {
    method: "POST",
    body: JSON.stringify({
      pairs,
      top_n: topN,
      ...(targetMetric ? { target_metric: targetMetric } : {}),
    }),
  });
}

// ── Manual platform ───────────────────────────────────────────────────────────

export async function createManualCampaign(name) {
  return request("/api/manual/campaigns", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function listManualCampaigns() {
  return request("/api/manual/campaigns");
}

export async function renameManualCampaign(id, name) {
  return request(`/api/manual/campaigns/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export async function deleteManualCampaign(id) {
  return request(`/api/manual/campaigns/${id}`, { method: "DELETE" });
}

export async function createManualAd(campaignId, data) {
  return request(`/api/manual/campaigns/${campaignId}/ads`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function listManualAds(campaignId) {
  return request(`/api/manual/campaigns/${campaignId}/ads`);
}

export async function deleteManualAd(adId) {
  return request(`/api/manual/ads/${adId}`, { method: "DELETE" });
}

export async function listManualCombinations(adId) {
  return request(`/api/manual/ads/${adId}/combinations`);
}

export async function scoreManualCombination(adId, combinationKey, combination, score, metric) {
  return request(`/api/manual/ads/${adId}/score`, {
    method: "POST",
    body: JSON.stringify({ combination_key: combinationKey, combination, score, metric }),
  });
}

export async function uploadManualImage(file) {
  const formData = new FormData();
  formData.append("file", file);
  const token = localStorage.getItem("token");
  const res = await fetch("/api/manual/upload-image", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Upload error ${res.status}`);
  }
  return res.json(); // { url: "/ad-images/..." }
}

export async function seedScoredVariants(seedAdId, platform, n, textSourceId) {
  return request("/api/bo/seed-scored-variants", {
    method: "POST",
    body: JSON.stringify({
      seed_ad_id: seedAdId,
      platform,
      n,
      text_source_id: textSourceId || null,
    }),
  });
}

// ── Admin (internal — separate auth from regular user JWT) ─────────────────
// Deliberately does not reuse request(): a wrong/missing admin key must not
// clear the caller's own login token or redirect them out of their session.
async function adminRequest(path, adminKey, opts = {}) {
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Key": adminKey,
      ...(opts.headers || {}),
    },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = body.detail;
    const message =
      typeof detail === "string" ? detail : detail?.message || `API error ${res.status}`;
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return body;
}

export async function adminListUsers(adminKey) {
  const data = await adminRequest("/api/admin/users", adminKey);
  return data.users;
}

export async function adminSetUserTier(adminKey, userId, { tier, tierExpiresAt } = {}) {
  const body = {};
  if (tier !== undefined) body.tier = tier;
  if (tierExpiresAt !== undefined) body.tier_expires_at = tierExpiresAt;
  return adminRequest(`/api/admin/users/${userId}/tier`, adminKey, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
