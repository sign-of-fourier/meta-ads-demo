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
    throw new Error(body.detail || `API error ${res.status}`);
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
