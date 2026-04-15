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

// ── Meta connection ─────────────────────────────────────────────────────────

export async function getMetaStatus() {
  return request("/me/meta-status");
}

export async function getMetaLoginUrl() {
  return request("/auth/meta/login-url");
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

// ── Ingest ──────────────────────────────────────────────────────────────────

export async function getIngestPreview() {
  return request("/api/ingest/preview");
}

export async function runIngest() {
  return request("/api/ingest", { method: "POST" });
}

export async function getExplore() {
  return request("/api/explore");
}

// ── Dashboard / history ─────────────────────────────────────────────────────

export async function getCampaignHistory(campaignId, days = 30) {
  return request(`/api/campaigns/${campaignId}/history?days=${days}`);
}
