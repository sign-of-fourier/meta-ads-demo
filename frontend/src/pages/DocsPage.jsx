import { useState, useEffect, useRef } from "react";

const C = {
  bg: "#0b1220",
  surface: "#020617",
  panel: "#0d1526",
  border: "#1f2937",
  text: "#e5e7eb",
  muted: "#9ca3af",
  dim: "#6b7280",
  sky: "#38bdf8",
  green: "#10b981",
  blue: "#3b82f6",
  red: "#ef4444",
  amber: "#f59e0b",
  purple: "#a78bfa",
};

const METHOD_BG = { GET: "#10b98120", POST: "#3b82f620", DELETE: "#ef444420" };
const METHOD_FG = { GET: C.green, POST: C.blue, DELETE: C.red };

function MethodBadge({ m }) {
  return (
    <span style={{
      display: "inline-block", fontFamily: "monospace", fontSize: 11,
      fontWeight: 700, letterSpacing: "0.06em",
      color: METHOD_FG[m] || C.amber, background: METHOD_BG[m] || "#f59e0b20",
      borderRadius: 4, padding: "2px 7px", marginRight: 8,
    }}>
      {m}
    </span>
  );
}

function AuthNote({ required }) {
  return required
    ? <span style={{ fontSize: 12, color: C.amber, background: "#f59e0b14", borderRadius: 4, padding: "2px 10px" }}>Bearer token required</span>
    : <span style={{ fontSize: 12, color: C.dim, background: "#ffffff08", borderRadius: 4, padding: "2px 10px" }}>No auth</span>;
}

function ParamTable({ rows, title }) {
  if (!rows?.length) return null;
  return (
    <>
      <p style={{ fontSize: 12, fontWeight: 600, color: C.dim, textTransform: "uppercase", letterSpacing: "0.08em", margin: "16px 0 6px" }}>{title}</p>
      <div style={{ overflowX: "auto", marginBottom: 16 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr>
              {["Name", "Type", "Req", "Description"].map(h => (
                <th key={h} style={{ textAlign: "left", color: C.dim, fontWeight: 600, fontSize: 11, padding: "4px 10px", borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td style={{ padding: "6px 10px", fontFamily: "monospace", color: C.sky, fontSize: 12, whiteSpace: "nowrap" }}>{r.name}</td>
                <td style={{ padding: "6px 10px", fontFamily: "monospace", color: C.purple, fontSize: 12, whiteSpace: "nowrap" }}>{r.type}</td>
                <td style={{ padding: "6px 10px", color: r.req ? C.green : C.dim, fontSize: 12, textAlign: "center" }}>{r.req ? "✓" : "—"}</td>
                <td style={{ padding: "6px 10px", color: C.muted, lineHeight: 1.5 }}>{r.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ResponseTable({ rows }) {
  if (!rows?.length) return null;
  return (
    <>
      <p style={{ fontSize: 12, fontWeight: 600, color: C.dim, textTransform: "uppercase", letterSpacing: "0.08em", margin: "16px 0 6px" }}>Response body</p>
      <div style={{ overflowX: "auto", marginBottom: 16 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr>
              {["Field", "Type", "Description"].map(h => (
                <th key={h} style={{ textAlign: "left", color: C.dim, fontWeight: 600, fontSize: 11, padding: "4px 10px", borderBottom: `1px solid ${C.border}` }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td style={{ padding: "6px 10px", fontFamily: "monospace", color: C.sky, fontSize: 12, whiteSpace: "nowrap" }}>{r.field}</td>
                <td style={{ padding: "6px 10px", fontFamily: "monospace", color: C.purple, fontSize: 12, whiteSpace: "nowrap" }}>{r.type}</td>
                <td style={{ padding: "6px 10px", color: C.muted, lineHeight: 1.5 }}>{r.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function CodeBlock({ code }) {
  const [copied, setCopied] = useState(false);
  return (
    <div style={{ position: "relative", marginBottom: 8 }}>
      <button
        onClick={() => { navigator.clipboard?.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1600); }}
        style={{ position: "absolute", top: 8, right: 10, background: "#ffffff10", border: "none", color: C.dim, fontSize: 11, borderRadius: 4, padding: "3px 9px", cursor: "pointer", zIndex: 1 }}
      >
        {copied ? "Copied!" : "Copy"}
      </button>
      <pre style={{
        background: "#060d1a", border: `1px solid ${C.border}`, borderRadius: 8,
        padding: "14px 16px 14px 16px", overflowX: "auto", fontSize: 12,
        lineHeight: 1.65, color: "#a5b4c8", margin: 0, fontFamily: "'Fira Code', 'Cascadia Code', monospace",
      }}>
        {code}
      </pre>
    </div>
  );
}

function Endpoint({ id, method, path, summary, auth, pathParams, queryParams, body, response, notes, example }) {
  return (
    <div id={id} style={{ marginBottom: 48, scrollMarginTop: 24 }}>
      <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
        <MethodBadge m={method} />
        <code style={{ fontFamily: "monospace", fontSize: 14, color: C.text, background: "#0f1829", borderRadius: 4, padding: "2px 10px" }}>{path}</code>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <AuthNote required={auth} />
      </div>
      <p style={{ color: C.muted, fontSize: 14, lineHeight: 1.6, margin: "0 0 12px" }}>{summary}</p>
      {notes && <p style={{ color: C.dim, fontSize: 13, lineHeight: 1.6, background: "#0f182933", border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 12px", margin: "0 0 12px" }}>{notes}</p>}
      <ParamTable rows={pathParams} title="Path parameters" />
      <ParamTable rows={queryParams} title="Query parameters" />
      <ParamTable rows={body} title="Request body (JSON)" />
      <ResponseTable rows={response} />
      {example && (
        <>
          <p style={{ fontSize: 12, fontWeight: 600, color: C.dim, textTransform: "uppercase", letterSpacing: "0.08em", margin: "16px 0 6px" }}>Example</p>
          <CodeBlock code={example} />
        </>
      )}
    </div>
  );
}

function SectionHeading({ id, title, description }) {
  return (
    <div id={id} style={{ marginBottom: 32, scrollMarginTop: 24 }}>
      <h2 style={{ fontSize: 20, fontWeight: 700, color: C.text, margin: "0 0 6px" }}>{title}</h2>
      {description && <p style={{ color: C.muted, fontSize: 14, lineHeight: 1.6, margin: 0 }}>{description}</p>}
      <hr style={{ border: "none", borderTop: `1px solid ${C.border}`, margin: "16px 0 24px" }} />
    </div>
  );
}

// ── Navigation structure ──────────────────────────────────────────────────────

const NAV = [
  { label: "Overview", id: "overview", children: [
    { label: "Base URL", id: "base-url" },
    { label: "Authentication model", id: "auth-model" },
    { label: "Quick start: BO workflow", id: "quickstart-bo" },
  ]},
  { label: "Authentication", id: "section-auth", children: [
    { label: "POST /auth/signup", id: "ep-signup" },
    { label: "POST /auth/login", id: "ep-login" },
  ]},
  { label: "Account", id: "section-account", children: [
    { label: "GET /me", id: "ep-me" },
    { label: "GET /me/meta-status", id: "ep-meta-status" },
  ]},
  { label: "Meta OAuth", id: "section-oauth", children: [
    { label: "GET /auth/meta/login-url", id: "ep-login-url" },
    { label: "GET /auth/meta/callback", id: "ep-callback" },
  ]},
  { label: "Campaigns", id: "section-campaigns", children: [
    { label: "GET /api/campaigns", id: "ep-campaigns-list" },
    { label: "GET …/history", id: "ep-campaign-history" },
    { label: "POST …/pause", id: "ep-campaign-pause" },
    { label: "POST …/resume", id: "ep-campaign-resume" },
  ]},
  { label: "Ingest", id: "section-ingest", children: [
    { label: "GET /api/ingest/preview", id: "ep-ingest-preview" },
    { label: "POST /api/ingest", id: "ep-ingest" },
    { label: "POST …/structure/{id}", id: "ep-ingest-structure" },
    { label: "GET /api/structure/{id}", id: "ep-structure-read" },
  ]},
  { label: "Ads", id: "section-ads", children: [
    { label: "GET /api/ads", id: "ep-ads-meta" },
    { label: "GET /api/ads/local", id: "ep-ads-local" },
    { label: "DELETE /api/ads/local/{id}", id: "ep-ads-delete" },
  ]},
  { label: "Ad Generation", id: "section-generation", children: [
    { label: "POST …/text/{campaign_id}", id: "ep-gen-text" },
    { label: "POST …/dynamic/{campaign_id}", id: "ep-gen-dynamic-start" },
    { label: "GET …/dynamic/status/{id}", id: "ep-gen-dynamic-status" },
  ]},
  { label: "Suggestions", id: "section-suggestions", children: [
    { label: "GET /api/suggestions", id: "ep-suggestions-list" },
    { label: "POST /api/suggestions", id: "ep-suggestions-create" },
    { label: "POST …/{id}/confirm", id: "ep-suggestions-confirm" },
  ]},
  { label: "Bayesian Optimization", id: "section-bo", children: [
    { label: "POST /api/bo/run", id: "ep-bo-run" },
    { label: "GET /api/bo/results/{id}", id: "ep-bo-results" },
  ]},
  { label: "Push to Meta", id: "section-push", children: [
    { label: "POST /api/push", id: "ep-push" },
  ]},
  { label: "Explorer", id: "section-explorer", children: [
    { label: "GET /api/explore", id: "ep-explore" },
  ]},
];

// ── Main component ────────────────────────────────────────────────────────────

export default function DocsPage() {
  const [activeId, setActiveId] = useState("overview");
  const [openSections, setOpenSections] = useState(() => Object.fromEntries(NAV.map(s => [s.id, true])));

  // Track scroll position to highlight active nav item
  useEffect(() => {
    const allIds = NAV.flatMap(s => [s.id, ...(s.children || []).map(c => c.id)]);
    const handler = () => {
      let current = allIds[0];
      for (const id of allIds) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= 80) current = id;
      }
      setActiveId(current);
    };
    window.addEventListener("scroll", handler, { passive: true });
    return () => window.removeEventListener("scroll", handler);
  }, []);

  function scrollTo(id) {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function toggleSection(id) {
    setOpenSections(s => ({ ...s, [id]: !s[id] }));
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" }}>

      {/* ── Sidebar ─────────────────────────────────────────────── */}
      <aside style={{
        width: 248, minWidth: 248, background: C.surface,
        borderRight: `1px solid ${C.border}`,
        position: "sticky", top: 0, height: "100vh", overflowY: "auto",
        padding: "24px 0 40px",
        flexShrink: 0,
      }}>
        <div style={{ padding: "0 16px 16px", borderBottom: `1px solid ${C.border}`, marginBottom: 12 }}>
          <a href="/" style={{ textDecoration: "none", color: C.sky, fontSize: 13, fontWeight: 600 }}>← AdStackr</a>
          <div style={{ fontSize: 16, fontWeight: 700, color: C.text, marginTop: 8 }}>API Reference</div>
          <div style={{ fontSize: 12, color: C.dim, marginTop: 2 }}>v1 · REST · JSON</div>
        </div>

        {NAV.map(section => (
          <div key={section.id} style={{ marginBottom: 4 }}>
            <button
              onClick={() => toggleSection(section.id)}
              style={{
                width: "100%", textAlign: "left", background: "none", border: "none", cursor: "pointer",
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "6px 16px", color: C.text, fontSize: 12, fontWeight: 600,
                textTransform: "uppercase", letterSpacing: "0.07em",
              }}
            >
              {section.label}
              <span style={{ color: C.dim, fontSize: 10 }}>{openSections[section.id] ? "▾" : "▸"}</span>
            </button>

            {openSections[section.id] && section.children?.map(child => (
              <button
                key={child.id}
                onClick={() => scrollTo(child.id)}
                style={{
                  width: "100%", textAlign: "left", background: activeId === child.id ? `${C.sky}14` : "none",
                  border: "none", borderLeft: activeId === child.id ? `2px solid ${C.sky}` : "2px solid transparent",
                  cursor: "pointer", padding: "5px 16px 5px 20px",
                  color: activeId === child.id ? C.sky : C.muted,
                  fontSize: 12, lineHeight: 1.4,
                  fontFamily: child.label.startsWith("GET") || child.label.startsWith("POST") || child.label.startsWith("DELETE") ? "monospace" : "inherit",
                  transition: "color 0.1s",
                }}
              >
                {child.label}
              </button>
            ))}
          </div>
        ))}
      </aside>

      {/* ── Content ─────────────────────────────────────────────── */}
      <main style={{ flex: 1, maxWidth: 860, padding: "40px 48px 80px", overflowX: "hidden" }}>

        {/* ── Overview ── */}
        <div id="overview" style={{ scrollMarginTop: 24, marginBottom: 48 }}>
          <h1 style={{ fontSize: 28, fontWeight: 800, color: C.text, margin: "0 0 8px" }}>API Reference</h1>
          <p style={{ color: C.muted, fontSize: 15, lineHeight: 1.7, margin: "0 0 32px" }}>
            Full HTTP reference for the AdStackr backend. All endpoints accept and return JSON.
            Authenticated endpoints require a <code style={{ fontFamily: "monospace", color: C.sky, fontSize: 13 }}>Authorization: Bearer &lt;token&gt;</code> header.
          </p>

          <div id="base-url" style={{ scrollMarginTop: 24, marginBottom: 32 }}>
            <h3 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: "0 0 8px" }}>Base URL</h3>
            <CodeBlock code={`http://localhost:8000          # local dev (default)\nhttps://your-ngrok-url.ngrok.io  # public tunnel via start.sh`} />
            <p style={{ color: C.muted, fontSize: 13, lineHeight: 1.6, margin: "8px 0 0" }}>
              The backend runs on port <code style={{ fontFamily: "monospace", color: C.sky }}>8000</code> by default. Staging mode uses port{" "}
              <code style={{ fontFamily: "monospace", color: C.sky }}>8001</code>.
            </p>
          </div>

          <div id="auth-model" style={{ scrollMarginTop: 24, marginBottom: 32 }}>
            <h3 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: "0 0 8px" }}>Authentication model</h3>
            <p style={{ color: C.muted, fontSize: 13, lineHeight: 1.6, margin: "0 0 8px" }}>
              The API uses short-lived JWTs. Obtain a token from <code style={{ fontFamily: "monospace", color: C.sky }}>/auth/signup</code> or{" "}
              <code style={{ fontFamily: "monospace", color: C.sky }}>/auth/login</code>, then pass it with every authenticated request:
            </p>
            <CodeBlock code={`Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...`} />
            <p style={{ color: C.muted, fontSize: 13, lineHeight: 1.6, margin: "8px 0 0" }}>
              Tokens expire after <strong style={{ color: C.text }}>24 hours</strong>. Re-authenticate to get a new one.
            </p>
          </div>

          <div id="quickstart-bo" style={{ scrollMarginTop: 24 }}>
            <h3 style={{ fontSize: 15, fontWeight: 700, color: C.text, margin: "0 0 8px" }}>Quick start: Bayesian Optimization workflow</h3>
            <p style={{ color: C.muted, fontSize: 13, lineHeight: 1.6, margin: "0 0 12px" }}>
              The minimum steps to get BO recommendations for an ad:
            </p>
            <CodeBlock code={
`# 1. Create account → get token
curl -s -X POST http://localhost:8000/auth/signup \\
  -H "Content-Type: application/json" \\
  -d '{"email":"you@example.com","password":"secret"}' | jq .token

export TOKEN="<paste token here>"

# 2. Connect Meta account (browser flow — see Meta OAuth section)
#    Or run the backend with APP_MODE=demo to skip this step entirely.

# 3. Ingest campaign metrics (saves snapshot to DB)
curl -s -X POST http://localhost:8000/api/ingest \\
  -H "Authorization: Bearer $TOKEN"

# 4. List campaigns to find a campaign_id
curl -s http://localhost:8000/api/campaigns \\
  -H "Authorization: Bearer $TOKEN" | jq '.[].id'

export CAMPAIGN_ID="<campaign id>"

# 5. Ingest the creative structure for that campaign
#    This populates ad embeddings needed by BO.
curl -s -X POST "http://localhost:8000/api/ingest/structure/$CAMPAIGN_ID" \\
  -H "Authorization: Bearer $TOKEN"

# 6. Read back the structure to find an ad_id
curl -s "http://localhost:8000/api/structure/$CAMPAIGN_ID" \\
  -H "Authorization: Bearer $TOKEN" | jq '.[0].ad_id'

export AD_ID="<ad id>"

# 7. Run Bayesian Optimization → get up to 2 recommended combinations
curl -s -X POST http://localhost:8000/api/bo/run \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d "{\"seed_ad_id\":\"$AD_ID\",\"text_source_id\":\"$AD_ID\"}" | jq .`} />
          </div>
        </div>

        <hr style={{ border: "none", borderTop: `1px solid ${C.border}`, margin: "8px 0 40px" }} />

        {/* ── Authentication ── */}
        <SectionHeading id="section-auth" title="Authentication" description="Register a new account or log in to obtain a Bearer token." />

        <Endpoint
          id="ep-signup"
          method="POST"
          path="/auth/signup"
          auth={false}
          summary="Create a new user account. Returns a JWT that is valid for 24 hours."
          body={[
            { name: "email", type: "string", req: true, desc: "Account email address. Must be unique." },
            { name: "password", type: "string", req: true, desc: "Plaintext password. Stored as a bcrypt hash." },
          ]}
          response={[
            { field: "token", type: "string", desc: "JWT bearer token. Pass in Authorization header for all authenticated requests." },
          ]}
          example={`curl -X POST http://localhost:8000/auth/signup \\
  -H "Content-Type: application/json" \\
  -d '{"email":"dev@example.com","password":"hunter2"}'`}
        />

        <Endpoint
          id="ep-login"
          method="POST"
          path="/auth/login"
          auth={false}
          summary="Authenticate an existing account. Returns a fresh JWT valid for 24 hours."
          body={[
            { name: "email", type: "string", req: true, desc: "Registered email address." },
            { name: "password", type: "string", req: true, desc: "Account password." },
          ]}
          response={[
            { field: "token", type: "string", desc: "JWT bearer token." },
          ]}
          example={`curl -X POST http://localhost:8000/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"email":"dev@example.com","password":"hunter2"}'`}
        />

        {/* ── Account ── */}
        <SectionHeading id="section-account" title="Account" description="Retrieve information about the currently authenticated user." />

        <Endpoint
          id="ep-me"
          method="GET"
          path="/me"
          auth={true}
          summary="Returns the current user's email address and tier."
          response={[
            { field: "email", type: "string", desc: "User's registered email." },
            { field: "tier", type: '"free" | "premium"', desc: "Account tier. Currently informational only — no routes enforce tier limits." },
          ]}
          example={`curl http://localhost:8000/me \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        <Endpoint
          id="ep-meta-status"
          method="GET"
          path="/me/meta-status"
          auth={true}
          summary="Check whether the current user has connected a Meta ad account."
          response={[
            { field: "connected", type: "boolean", desc: "true if a Meta OAuth connection exists." },
            { field: "ad_account_id", type: "string | null", desc: "The connected Meta ad account ID (e.g. act_123456789), or null if not connected." },
          ]}
          example={`curl http://localhost:8000/me/meta-status \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        {/* ── Meta OAuth ── */}
        <SectionHeading id="section-oauth" title="Meta OAuth" description="Connect a Meta ad account via the standard OAuth 2.0 code flow." />

        <Endpoint
          id="ep-login-url"
          method="GET"
          path="/auth/meta/login-url"
          auth={true}
          summary="Generate a Meta OAuth authorization URL. Open this URL in a browser to start the Meta login flow."
          notes="The returned URL points to Facebook's OAuth dialog. After the user approves, Meta redirects back to /auth/meta/callback, which exchanges the code for a token and stores the connection. This is a browser-driven flow — there is no JSON body to POST."
          response={[
            { field: "url", type: "string", desc: "Full Meta OAuth dialog URL. Open in a browser tab." },
          ]}
          example={`curl http://localhost:8000/auth/meta/login-url \\
  -H "Authorization: Bearer $TOKEN"

# → {"url": "https://www.facebook.com/v19.0/dialog/oauth?..."}
# Open that URL in a browser to complete the OAuth flow.`}
        />

        <Endpoint
          id="ep-callback"
          method="GET"
          path="/auth/meta/callback"
          auth={false}
          summary="OAuth redirect target. Meta sends the browser here after the user approves. Not called directly."
          notes="This endpoint is handled automatically by Meta's redirect. On success it redirects the browser to /app/settings?meta_connected=true. On error it redirects to /app/settings?meta_error=<message>. Do not call this directly from your API client."
          queryParams={[
            { name: "code", type: "string", req: false, desc: "Authorization code from Meta (present on success)." },
            { name: "state", type: "string", req: false, desc: "CSRF state token issued by /auth/meta/login-url." },
            { name: "error_code", type: "string", req: false, desc: "Set by Meta when the user denies access." },
            { name: "error_message", type: "string", req: false, desc: "Human-readable error from Meta." },
          ]}
        />

        {/* ── Campaigns ── */}
        <SectionHeading id="section-campaigns" title="Campaigns" description="List campaigns and their 7-day metrics from the connected Meta ad account. Pause or resume campaigns. Read stored metric history." />

        <Endpoint
          id="ep-campaigns-list"
          method="GET"
          path="/api/campaigns"
          auth={true}
          summary="Fetch all campaigns and 7-day aggregate metrics from the connected Meta ad account. Data is read live from Meta on each call — nothing is written to the database."
          response={[
            { field: "id", type: "string", desc: "Meta campaign ID." },
            { field: "name", type: "string", desc: "Campaign name." },
            { field: "status", type: "string", desc: "ACTIVE | PAUSED | ARCHIVED | etc." },
            { field: "daily_budget", type: "integer | null", desc: "Campaign daily budget in account currency minor units (e.g. cents)." },
            { field: "spend_7d", type: "float | null", desc: "Total spend over the last 7 days." },
            { field: "impressions_7d", type: "integer | null", desc: "Total impressions over the last 7 days." },
            { field: "clicks_7d", type: "integer | null", desc: "Total link clicks over the last 7 days." },
            { field: "ctr_7d", type: "float | null", desc: "Click-through rate (clicks / impressions)." },
            { field: "cpm_7d", type: "float | null", desc: "Cost per 1,000 impressions." },
          ]}
          example={`curl http://localhost:8000/api/campaigns \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        <Endpoint
          id="ep-campaign-history"
          method="GET"
          path="/api/campaigns/{campaign_id}/history"
          auth={true}
          summary="Return stored daily metric snapshots for a campaign. Snapshots are written by POST /api/ingest — one row per ingest call."
          pathParams={[
            { name: "campaign_id", type: "string", req: true, desc: "Meta campaign ID." },
          ]}
          queryParams={[
            { name: "days", type: "integer", req: false, desc: "Number of days of history to return (default: 30)." },
          ]}
          response={[
            { field: "date", type: "string", desc: "Snapshot date (YYYY-MM-DD)." },
            { field: "impressions", type: "integer | null", desc: "Impressions on that date." },
            { field: "clicks", type: "integer | null", desc: "Clicks on that date." },
            { field: "spend", type: "float | null", desc: "Spend on that date." },
            { field: "ctr", type: "float | null", desc: "CTR on that date." },
            { field: "cpm", type: "float | null", desc: "CPM on that date." },
            { field: "cpc", type: "float | null", desc: "CPC on that date." },
          ]}
          example={`curl "http://localhost:8000/api/campaigns/120217362579230444/history?days=14" \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        <Endpoint
          id="ep-campaign-pause"
          method="POST"
          path="/api/campaigns/{campaign_id}/pause"
          auth={true}
          summary="Pause a running campaign via the Meta Marketing API."
          pathParams={[
            { name: "campaign_id", type: "string", req: true, desc: "Meta campaign ID." },
          ]}
          response={[
            { field: "id", type: "string", desc: "Campaign ID." },
            { field: "status", type: '"PAUSED"', desc: "Confirmed new status." },
          ]}
          example={`curl -X POST http://localhost:8000/api/campaigns/120217362579230444/pause \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        <Endpoint
          id="ep-campaign-resume"
          method="POST"
          path="/api/campaigns/{campaign_id}/resume"
          auth={true}
          summary="Resume a paused campaign via the Meta Marketing API."
          pathParams={[
            { name: "campaign_id", type: "string", req: true, desc: "Meta campaign ID." },
          ]}
          response={[
            { field: "id", type: "string", desc: "Campaign ID." },
            { field: "status", type: '"ACTIVE"', desc: "Confirmed new status." },
          ]}
          example={`curl -X POST http://localhost:8000/api/campaigns/120217362579230444/resume \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        {/* ── Ingest ── */}
        <SectionHeading id="section-ingest" title="Ingest" description="Pull data from Meta and persist it locally. Metric snapshots and creative structures are stored separately." />

        <Endpoint
          id="ep-ingest-preview"
          method="GET"
          path="/api/ingest/preview"
          auth={true}
          summary="Fetch campaigns and ads from Meta and return them without writing anything to the database. Use this to inspect what a full ingest would save."
          response={[
            { field: "ad_account_id", type: "string", desc: "The connected Meta ad account ID." },
            { field: "campaigns", type: "array", desc: "Array of campaign objects with metrics (same schema as /api/campaigns)." },
            { field: "ads", type: "array", desc: "Array of AdCreative objects (id, name, status, campaign_id, adset_id, body, image_url, thumbnail_url)." },
          ]}
          example={`curl http://localhost:8000/api/ingest/preview \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        <Endpoint
          id="ep-ingest"
          method="POST"
          path="/api/ingest"
          auth={true}
          summary="Fetch campaigns and 7-day insights from Meta and persist a daily metric snapshot to the database. Calling this repeatedly accumulates history — there is no date deduplication."
          response={[
            { field: "campaigns_saved", type: "integer", desc: "Number of campaign snapshots written." },
            { field: "ad_account_id", type: "string", desc: "Connected Meta ad account ID." },
            { field: "campaigns_seen", type: "integer", desc: "Total campaigns returned by Meta." },
            { field: "campaigns_with_metrics", type: "integer", desc: "Campaigns that had delivery data in the last 7 days." },
            { field: "insights_errors", type: "integer", desc: "Number of Insights API failures (0 is healthy)." },
            { field: "message", type: "string", desc: "Human-readable summary of the ingest result." },
          ]}
          example={`curl -X POST http://localhost:8000/api/ingest \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        <Endpoint
          id="ep-ingest-structure"
          method="POST"
          path="/api/ingest/structure/{campaign_id}"
          auth={true}
          summary="Fetch the full creative structure for one campaign (adsets → ads → creatives) and persist all slot values to the database. Idempotent — running it twice on the same campaign replaces rows rather than duplicating them. Also triggers ad embedding generation in the background."
          notes="This is the prerequisite for ad generation and Bayesian Optimization. Run this before calling /api/generate/* or /api/bo/run."
          pathParams={[
            { name: "campaign_id", type: "string", req: true, desc: "Meta campaign ID." },
          ]}
          response={[
            { field: "campaign_id", type: "string", desc: "Campaign ID that was ingested." },
            { field: "ads_processed", type: "integer", desc: "Number of ads whose creative structure was parsed." },
            { field: "components_saved", type: "integer", desc: "Total slot rows written to the database." },
          ]}
          example={`curl -X POST http://localhost:8000/api/ingest/structure/120217362579230444 \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        <Endpoint
          id="ep-structure-read"
          method="GET"
          path="/api/structure/{campaign_id}"
          auth={true}
          summary="Return the persisted creative structure for a campaign, grouped by ad. Each ad includes its slots and their values."
          pathParams={[
            { name: "campaign_id", type: "string", req: true, desc: "Meta campaign ID." },
          ]}
          response={[
            { field: "[].ad_id", type: "string", desc: "Ad identifier (Meta ID for ingested ads; gen_* prefix for generated ads)." },
            { field: "[].adset_id", type: "string", desc: "Ad set this ad belongs to." },
            { field: "[].campaign_id", type: "string", desc: "Campaign this ad belongs to." },
            { field: "[].creative_type", type: '"static" | "dynamic"', desc: "Whether the ad creative uses a single asset or multiple variants per slot." },
            { field: "[].lifecycle_status", type: '"active" | "inactive" | "missing"', desc: "Derived from Meta effective_status. missing = was ingested previously but not found in the latest fetch." },
            { field: "[].components", type: "object", desc: "Map of slot name → array of values ordered by slot_index. Slots: headline, primary_text, description, image." },
          ]}
          example={`curl http://localhost:8000/api/structure/120217362579230444 \\
  -H "Authorization: Bearer $TOKEN"

# Response (truncated):
# [
#   {
#     "ad_id": "120217362579230445",
#     "adset_id": "120217362579230446",
#     "campaign_id": "120217362579230444",
#     "creative_type": "dynamic",
#     "lifecycle_status": "active",
#     "components": {
#       "headline": ["Best price guaranteed", "Save 30% today"],
#       "primary_text": ["Shop our summer sale now."],
#       "image": ["http://localhost:8000/ad-images/abc123.jpg"]
#     }
#   }
# ]`}
        />

        {/* ── Ads ── */}
        <SectionHeading id="section-ads" title="Ads" description="Access ads directly from Meta or from the local database." />

        <Endpoint
          id="ep-ads-meta"
          method="GET"
          path="/api/ads"
          auth={true}
          summary="Fetch all ads and basic creative fields from the connected Meta ad account. Nothing is saved. Use /api/ingest/structure to persist and index creative structure."
          response={[
            { field: "[].id", type: "string", desc: "Meta ad ID." },
            { field: "[].name", type: "string | null", desc: "Ad name." },
            { field: "[].status", type: "string | null", desc: "Ad status from Meta." },
            { field: "[].campaign_id", type: "string | null", desc: "Parent campaign ID." },
            { field: "[].adset_id", type: "string | null", desc: "Parent ad set ID." },
            { field: "[].body", type: "string | null", desc: "Primary text body from the ad creative." },
            { field: "[].image_url", type: "string | null", desc: "Creative image URL." },
            { field: "[].thumbnail_url", type: "string | null", desc: "Creative thumbnail URL." },
          ]}
          example={`curl http://localhost:8000/api/ads \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        <Endpoint
          id="ep-ads-local"
          method="GET"
          path="/api/ads/local"
          auth={true}
          summary="Return all ads stored in the local database — both ingested Meta ads and AI-generated ads. Includes all slot values."
          response={[
            { field: "[].ad_id", type: "string", desc: "Ad identifier." },
            { field: "[].campaign_id", type: "string", desc: "Campaign this ad belongs to." },
            { field: "[].adset_id", type: "string", desc: "Ad set this ad belongs to." },
            { field: "[].creative_type", type: '"static" | "dynamic"', desc: "Creative type." },
            { field: "[].lifecycle_status", type: "string", desc: "active | inactive | missing | generated." },
            { field: "[].data_source", type: '"real" | "masked" | "demo" | "generated"', desc: "Where the data came from." },
            { field: "[].ingested_at", type: "string", desc: "ISO 8601 timestamp when this ad was ingested or generated." },
            { field: "[].headline", type: "string | null", desc: "First headline value (convenience field)." },
            { field: "[].image_url", type: "string | null", desc: "First image value (convenience field)." },
            { field: "[].slots", type: "array", desc: "All slot rows: [{slot, slot_index, value}]. slot is one of: headline, primary_text, description, image, cta." },
          ]}
          example={`curl http://localhost:8000/api/ads/local \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        <Endpoint
          id="ep-ads-delete"
          method="DELETE"
          path="/api/ads/local/{ad_id}"
          auth={true}
          summary="Delete a locally stored ad and all associated embedding rows. Returns 204 No Content on success."
          pathParams={[
            { name: "ad_id", type: "string", req: true, desc: "The ad_id to delete." },
          ]}
          example={`curl -X DELETE http://localhost:8000/api/ads/local/120217362579230445 \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        {/* ── Ad Generation ── */}
        <SectionHeading id="section-generation" title="Ad Generation" description="Generate new ad text variants or full dynamic ads (text + AI images) from a seed ad." />

        <Endpoint
          id="ep-gen-text"
          method="POST"
          path="/api/generate/text/{campaign_id}"
          auth={true}
          summary="Synchronously generate 10 new text variants per slot (headline, primary_text, description, cta) from the first ingested ad in the campaign. The results are stored in the database and returned immediately."
          notes="Requires that POST /api/ingest/structure/{campaign_id} has been called first. This is a synchronous call — it may take 10–30 seconds depending on the LLM."
          pathParams={[
            { name: "campaign_id", type: "string", req: true, desc: "Meta campaign ID to generate text for." },
          ]}
          queryParams={[
            { name: "seed_ad_id", type: "string", req: false, desc: "Specific ad ID to use as the seed. Defaults to the first ingested ad in the campaign." },
          ]}
          response={[
            { field: "generated_ad_id", type: "integer", desc: "Internal ID of the generated ad record." },
            { field: "source_ad_id", type: "string", desc: "The ad ID that was used as the seed." },
            { field: "slots", type: "array", desc: "All generated slot variants: [{slot, slot_index, value, source}]. Includes 10 variants for each of headline, primary_text, description, cta." },
          ]}
          example={`curl -X POST http://localhost:8000/api/generate/text/120217362579230444 \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        <Endpoint
          id="ep-gen-dynamic-start"
          method="POST"
          path="/api/generate/dynamic/{campaign_id}"
          auth={true}
          summary="Start an asynchronous dynamic ad generation job. Generates 4 text variants per slot and 4 AI image variants from a seed ad. Returns immediately with a job_id — poll /api/generate/dynamic/status/{job_id} for completion."
          notes="The job runs in the background. Image generation takes 1–3 minutes. Once complete, the generated ad is stored in the local database and text/image embeddings are computed automatically."
          pathParams={[
            { name: "campaign_id", type: "string", req: true, desc: "Meta campaign ID to generate from." },
          ]}
          queryParams={[
            { name: "seed_ad_id", type: "string", req: false, desc: "Specific ad ID to use as the seed. Defaults to the first ingested ad." },
          ]}
          response={[
            { field: "job_id", type: "integer", desc: "ID to use when polling for status." },
            { field: "status", type: '"running"', desc: "Always running immediately after creation." },
          ]}
          example={`curl -X POST http://localhost:8000/api/generate/dynamic/120217362579230444 \\
  -H "Authorization: Bearer $TOKEN"

# → {"job_id": 42, "status": "running"}`}
        />

        <Endpoint
          id="ep-gen-dynamic-status"
          method="GET"
          path="/api/generate/dynamic/status/{job_id}"
          auth={true}
          summary="Poll the status of a dynamic generation job. When status is complete, the response includes all text slot variants and image URLs."
          pathParams={[
            { name: "job_id", type: "integer", req: true, desc: "Job ID returned by POST /api/generate/dynamic/{campaign_id}." },
          ]}
          response={[
            { field: "job_id", type: "integer", desc: "Job ID." },
            { field: "status", type: '"running" | "complete" | "failed"', desc: "Current job state." },
            { field: "ad_id", type: "string | null", desc: "The generated ad ID (set when complete)." },
            { field: "error", type: "string | null", desc: "Error message (set when failed)." },
            { field: "images_generated", type: "integer | null", desc: "Number of AI images successfully generated." },
            { field: "slots", type: "array", desc: "Generated text variants when complete: [{slot, slot_index, value}]. Excludes image slots." },
            { field: "image_urls", type: "array", desc: "Up to 4 generated image URLs when complete. These are served from the backend's /images/ path." },
          ]}
          example={`# Poll every 5 seconds until complete
curl http://localhost:8000/api/generate/dynamic/status/42 \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        {/* ── Suggestions ── */}
        <SectionHeading id="section-suggestions" title="Suggestions" description="Store and act on suggested ad configurations. A suggestion is a specific combination of slot values chosen for a dynamic ad template. Confirming a suggestion creates a new static ad in Meta." />

        <Endpoint
          id="ep-suggestions-list"
          method="GET"
          path="/api/suggestions"
          auth={true}
          summary="List all stored suggestions for the current user, ordered newest first. Filter by campaign with the campaign_id query parameter."
          queryParams={[
            { name: "campaign_id", type: "string", req: false, desc: "Filter suggestions to a specific campaign." },
          ]}
          response={[
            { field: "[].id", type: "integer", desc: "Suggestion ID." },
            { field: "[].source_ad_id", type: "string", desc: "The dynamic template ad this suggestion was generated from." },
            { field: "[].campaign_id", type: "string", desc: "Campaign ID." },
            { field: "[].adset_id", type: "string", desc: "Ad set ID." },
            { field: "[].components", type: "object", desc: 'Map of slot → chosen value. e.g. {"headline": "Save 30%", "primary_text": "Shop now."}' },
            { field: "[].deployment_status", type: "string", desc: "suggested | pending_confirmation | created_static | active_static | replaced_static | rejected." },
            { field: "[].static_ad_id", type: "string | null", desc: "Meta ad ID of the created static ad (set after a successful confirm with action=create)." },
            { field: "[].created_at", type: "string", desc: "ISO 8601 creation timestamp." },
          ]}
          example={`curl "http://localhost:8000/api/suggestions?campaign_id=120217362579230444" \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        <Endpoint
          id="ep-suggestions-create"
          method="POST"
          path="/api/suggestions"
          auth={true}
          summary="Store a suggested ad configuration. Call this from an external optimization system to queue a combination for review. The suggestion starts in suggested status."
          body={[
            { name: "source_ad_id", type: "string", req: true, desc: "The dynamic template ad ID this suggestion is based on." },
            { name: "campaign_id", type: "string", req: true, desc: "Campaign ID." },
            { name: "adset_id", type: "string", req: true, desc: "Ad set ID where the static clone should be created." },
            { name: "components", type: "object", req: true, desc: 'Map of slot name → single chosen value. e.g. {"headline": "Best deal today", "primary_text": "Shop now.", "image": "http://..."}' },
          ]}
          response={[
            { field: "id", type: "integer", desc: "Newly created suggestion ID." },
            { field: "source_ad_id", type: "string", desc: "Source ad ID." },
            { field: "campaign_id", type: "string", desc: "Campaign ID." },
            { field: "adset_id", type: "string", desc: "Ad set ID." },
            { field: "components", type: "object", desc: "The stored slot→value map." },
            { field: "deployment_status", type: '"suggested"', desc: "Initial status." },
            { field: "static_ad_id", type: "null", desc: "null until confirmed with action=create." },
            { field: "created_at", type: "string", desc: "ISO 8601 creation timestamp." },
          ]}
          example={`curl -X POST http://localhost:8000/api/suggestions \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "source_ad_id": "120217362579230445",
    "campaign_id": "120217362579230444",
    "adset_id": "120217362579230446",
    "components": {
      "headline": "Save 30% this weekend only",
      "primary_text": "Limited time offer. Shop now.",
      "description": "Free shipping on orders over $50."
    }
  }'`}
        />

        <Endpoint
          id="ep-suggestions-confirm"
          method="POST"
          path="/api/suggestions/{suggestion_id}/confirm"
          auth={true}
          summary={`Confirm a suggestion and optionally create it as a static Meta ad. action="create" calls the Meta API to create a new PAUSED static ad. action="replace" transitions to pending_confirmation (Meta deployment not yet implemented).`}
          notes={`action="create" requires the Meta app to be in Live mode. In Development mode the call will fail with a Meta 400 error — the suggestion status is not changed. The new ad is always created as PAUSED.`}
          pathParams={[
            { name: "suggestion_id", type: "integer", req: true, desc: "Suggestion ID to confirm." },
          ]}
          body={[
            { name: "action", type: '"create" | "replace"', req: true, desc: 'create: deploy as a new static ad in Meta. replace: hold at pending_confirmation (not yet deployed to Meta).' },
            { name: "target_static_ad_id", type: "string | null", req: false, desc: 'For action="replace": the existing Meta ad ID to replace (future feature).' },
            { name: "static_ad_id", type: "string | null", req: false, desc: 'Optionally pre-link a known Meta ad ID (testing only).' },
          ]}
          response={[
            { field: "id", type: "integer", desc: "Suggestion ID." },
            { field: "deployment_status", type: "string", desc: 'created_static (action=create success) or pending_confirmation (action=replace).' },
            { field: "static_ad_id", type: "string | null", desc: "The new Meta ad ID when action=create succeeds." },
          ]}
          example={`curl -X POST http://localhost:8000/api/suggestions/7/confirm \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"action": "create"}'`}
        />

        {/* ── Bayesian Optimization ── */}
        <SectionHeading id="section-bo" title="Bayesian Optimization" description="Run Gaussian Process Regression over scored ad variants and retrieve recommended text+image combinations ranked by Expected Improvement." />

        <Endpoint
          id="ep-bo-run"
          method="POST"
          path="/api/bo/run"
          auth={true}
          summary="Run Bayesian Optimization for a seed ad and return up to 2 recommended text+image combinations. Results are saved and retrievable via GET /api/bo/results/{ad_id}."
          notes="seed_ad_id and text_source_id are normally the same value — both should be the ad_id returned by GET /api/structure/{campaign_id}. BO requires: (1) at least one scored image variant in the database (from a dynamic generation job), and (2) text combination embeddings (written automatically after structural ingest). Without scored observations it falls back to random selection from the candidate pool."
          body={[
            { name: "seed_ad_id", type: "string", req: true, desc: "The ad ID to optimize. Must have been ingested via /api/ingest/structure." },
            { name: "text_source_id", type: "string", req: true, desc: "Source ID for text combination embeddings. Use the same value as seed_ad_id." },
          ]}
          response={[
            { field: "seed_ad_id", type: "string", desc: "The ad ID that was optimized." },
            { field: "text_source_id", type: "string", desc: "Text source ID used." },
            { field: "scored_count", type: "integer", desc: "Number of scored image variants found in the database." },
            { field: "candidate_count", type: "integer", desc: "Total text+image combinations evaluated." },
            { field: "picks", type: "array", desc: "Up to 2 recommended combinations (see pick object below)." },
            { field: "picks[].combination_key", type: "string", desc: "Stable key uniquely identifying this text+image combination." },
            { field: "picks[].combination", type: "object", desc: "The chosen slot values: {headline, primary_text, description, image_url, ...}." },
            { field: "picks[].selection_type", type: '"ei" | "fantasy" | "random"', desc: "How this pick was selected. ei = maximum Expected Improvement. fantasy = batch BO second pick. random = fallback when no scored observations exist." },
            { field: "picks[].ei_score", type: "float | null", desc: "Expected Improvement score (higher = more promising)." },
            { field: "picks[].gpr_mean", type: "float | null", desc: "GPR predicted performance mean." },
            { field: "picks[].gpr_std", type: "float | null", desc: "GPR predicted performance uncertainty." },
          ]}
          example={`curl -X POST http://localhost:8000/api/bo/run \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "seed_ad_id": "120217362579230445",
    "text_source_id": "120217362579230445"
  }'

# Example response:
# {
#   "seed_ad_id": "120217362579230445",
#   "text_source_id": "120217362579230445",
#   "scored_count": 5,
#   "candidate_count": 256,
#   "picks": [
#     {
#       "combination_key": "h2_p1_d3_img2",
#       "combination": {
#         "headline": "Save 30% this weekend",
#         "primary_text": "Limited offer — shop now.",
#         "description": "Free shipping on all orders.",
#         "image_url": "http://localhost:8000/images/gen_abc123.png"
#       },
#       "selection_type": "ei",
#       "ei_score": 0.042,
#       "gpr_mean": 0.71,
#       "gpr_std": 0.18
#     }
#   ]
# }`}
        />

        <Endpoint
          id="ep-bo-results"
          method="GET"
          path="/api/bo/results/{ad_id}"
          auth={true}
          summary="Retrieve the most recent BO run results for an ad without recomputing."
          pathParams={[
            { name: "ad_id", type: "string", req: true, desc: "The seed ad ID to retrieve results for." },
          ]}
          response={[
            { field: "[].combination_key", type: "string", desc: "Stable combination key." },
            { field: "[].combination", type: "object", desc: "Slot values for this pick." },
            { field: "[].selection_type", type: "string", desc: "ei | fantasy | random." },
            { field: "[].ei_score", type: "float | null", desc: "Expected Improvement score." },
            { field: "[].gpr_mean", type: "float | null", desc: "GPR predicted mean." },
            { field: "[].gpr_std", type: "float | null", desc: "GPR predicted standard deviation." },
          ]}
          example={`curl http://localhost:8000/api/bo/results/120217362579230445 \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        {/* ── Push ── */}
        <SectionHeading id="section-push" title="Push to Meta" description="Publish locally generated ads to Meta as new PAUSED static ads." />

        <Endpoint
          id="ep-push"
          method="POST"
          path="/api/push"
          auth={true}
          summary="Find all completed dynamic generation jobs that have not yet been pushed to Meta, upload their images to Meta's ad images API, and create a new PAUSED static ad for each one."
          notes="Requires the Meta app to be in Live mode. In Development mode Meta will reject the ad creation with an error. Each job is processed independently — a failure on one does not block others. A job is considered pushable when: status=complete, ad_id is set, meta_ad_id is null, and both seed_ad_id and adset_id are set."
          response={[
            { field: "pushed", type: "integer", desc: "Number of ads successfully pushed to Meta." },
            { field: "failed", type: "integer", desc: "Number of jobs that failed to push." },
            { field: "results", type: "array", desc: "Per-job results." },
            { field: "results[].ad_id", type: "string", desc: "Internal generated ad ID." },
            { field: "results[].meta_ad_id", type: "string | null", desc: "New Meta ad ID (set on success)." },
            { field: "results[].error", type: "string | null", desc: "Error message (set on failure)." },
          ]}
          example={`curl -X POST http://localhost:8000/api/push \\
  -H "Authorization: Bearer $TOKEN"`}
        />

        {/* ── Explorer ── */}
        <SectionHeading id="section-explorer" title="Explorer" description="Debug endpoint — returns raw Meta API data without any transformation or persistence." />

        <Endpoint
          id="ep-explore"
          method="GET"
          path="/api/explore"
          auth={true}
          summary="Fetch raw campaigns, adsets, and ads from Meta and return them in a nested structure. Nothing is written to the database. Useful for inspecting what the Meta API is returning before ingesting."
          notes="This endpoint always calls Meta directly, bypassing the masking layer. Image URLs in creative fields are signed and expire quickly."
          response={[
            { field: "ad_account_id", type: "string", desc: "Connected Meta ad account ID." },
            { field: "campaigns", type: "array", desc: "Raw campaign objects from Meta, each with _adsets (adsets array) and _ads_flat (ads array) attached." },
            { field: "_all_adsets", type: "array", desc: "All adsets across all campaigns, each with _ads attached." },
            { field: "_all_ads", type: "array", desc: "All ads with expanded creative fields." },
            { field: "_ads_fetch_error", type: "object | undefined", desc: "Present only if the Meta ads fetch returned a non-200 status." },
          ]}
          example={`curl http://localhost:8000/api/explore \\
  -H "Authorization: Bearer $TOKEN" | jq '.campaigns[0].name'`}
        />

      </main>
    </div>
  );
}
