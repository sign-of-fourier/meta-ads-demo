import DashboardMockup from "../components/DashboardMockup.jsx";

const s = {
  page: {
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    color: "#0f172a",
    background: "#0b1220",
    minHeight: "100vh",
    padding: "60px 20px 80px",
    boxSizing: "border-box",
  },
  inner: { maxWidth: 1040, margin: "0 auto" },
  badge: {
    display: "inline-block",
    fontSize: 12,
    letterSpacing: "0.12em",
    textTransform: "uppercase",
    color: "#38bdf8",
    background: "rgba(56,189,248,0.1)",
    borderRadius: 999,
    padding: "4px 10px",
    marginBottom: 16,
  },
  h1: {
    fontSize: "clamp(32px, 5vw, 48px)",
    lineHeight: 1.1,
    fontWeight: 700,
    color: "#e5e7eb",
    margin: "0 0 16px",
    maxWidth: 720,
  },
  sub: {
    maxWidth: 600,
    fontSize: 17,
    lineHeight: 1.6,
    color: "#9ca3af",
    margin: "0 0 28px",
  },
  ctaRow: { display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12, marginBottom: 10 },
  btnPrimary: {
    display: "inline-block",
    textDecoration: "none",
    background: "#38bdf8",
    color: "#0b1120",
    fontWeight: 700,
    fontSize: 14,
    padding: "11px 22px",
    borderRadius: 999,
    border: "none",
    cursor: "pointer",
  },
  btnSecondary: {
    display: "inline-block",
    textDecoration: "none",
    border: "1px solid #4b5563",
    background: "transparent",
    color: "#e5e7eb",
    fontSize: 14,
    padding: "11px 18px",
    borderRadius: 999,
    cursor: "pointer",
  },
  trustRow: { fontSize: 13, color: "#6b7280", marginBottom: 52, lineHeight: 2 },
  trustLink: { color: "#9ca3af", textDecoration: "underline", cursor: "pointer" },
  howCard: {
    background: "#020617",
    borderRadius: 16,
    padding: "24px 28px",
    border: "1px solid #1f2937",
    marginBottom: 20,
  },
  howLabel: {
    fontSize: 11,
    fontWeight: 700,
    color: "#38bdf8",
    textTransform: "uppercase",
    letterSpacing: "0.1em",
    marginBottom: 10,
  },
  howH2: { fontSize: 20, fontWeight: 700, color: "#e5e7eb", margin: "0 0 20px" },
  howSteps: { display: "flex", flexWrap: "wrap", gap: 0, marginBottom: 18 },
  step: { flex: "1 1 180px", paddingRight: 24, paddingBottom: 12 },
  stepNum: { fontSize: 11, fontWeight: 700, color: "#38bdf8", marginBottom: 6, letterSpacing: "0.08em" },
  stepLabel: { fontSize: 15, fontWeight: 600, color: "#e5e7eb", marginBottom: 4, lineHeight: 1.3 },
  stepDesc: { fontSize: 13, color: "#6b7280", lineHeight: 1.6 },
  walkLink: {
    display: "inline-block",
    fontSize: 13,
    color: "#38bdf8",
    textDecoration: "none",
    fontWeight: 600,
  },
  grid3: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
    gap: 18,
    marginBottom: 44,
  },
  card: {
    background: "#020617",
    borderRadius: 16,
    padding: "20px 22px",
    border: "1px solid #1f2937",
  },
  cardHead: { fontSize: 15, fontWeight: 600, color: "#e5e7eb", margin: "0 0 8px" },
  cardBody: { fontSize: 14, color: "#9ca3af", margin: 0, lineHeight: 1.6 },
  betaBadge: {
    display: "inline-block",
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: "0.1em",
    textTransform: "uppercase",
    color: "#4ade80",
    background: "rgba(74,222,128,0.1)",
    borderRadius: 999,
    padding: "3px 9px",
    marginLeft: 8,
    verticalAlign: "middle",
  },
  mockupWrap: {
    boxShadow: "0 32px 100px rgba(0,0,0,0.6)",
    borderRadius: 20,
    marginBottom: 14,
  },
  caption: { fontSize: 13, color: "#4b5563", textAlign: "center", marginTop: 14 },
};

export default function LandingPageV2() {
  return (
    <div style={s.page}>
      <div style={s.inner}>

        {/* Wordmark */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 40 }}>
          <img src="/logo.svg" alt="AdStackers" style={{ height: 32 }} />
          <span style={{ fontFamily: "'Big Shoulders Display', sans-serif", fontWeight: 700, fontSize: 26, background: "linear-gradient(90deg, #f59e0b, #b45309)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent", backgroundClip: "text" }}>AdStackers</span>
        </div>

        {/* Badge */}
        <div style={s.badge}>Cross-platform · Free in beta</div>

        {/* Hero */}
        <h1 style={s.h1}>
          Improve creative performance through disciplined experimentation.
        </h1>
        <p style={s.sub}>
          AdStackers analyzes your ads and recommends what to run next — across Meta,
          Google, and any platform you define.
        </p>

        {/* CTAs */}
        <div style={s.ctaRow}>
          <a href="/app/auth" style={s.btnPrimary}>Get started</a>
          <a href="/guide" style={s.btnSecondary}>How it works →</a>
        </div>
        <div style={s.trustRow}>
          No credit card required · Read-only by default ·{" "}
          <a href="/app/auth" style={s.trustLink}>Already have an account? Log in</a>
        </div>

        {/* How it works — short */}
        <div style={s.howCard}>
          <div style={s.howLabel}>How it works</div>
          <h2 style={s.howH2}>This is how the best teams pull ahead — and stay there.</h2>
          <div style={s.howSteps}>
            <div style={s.step}>
              <div style={s.stepNum}>01</div>
              <div style={s.stepLabel}>Set up the possibilities</div>
              <div style={s.stepDesc}>Map the creative space before you spend. Most teams skip this — it's why most tests teach them nothing.</div>
            </div>
            <div style={s.step}>
              <div style={s.stepNum}>02</div>
              <div style={s.stepLabel}>Get recommendations with explanations</div>
              <div style={s.stepDesc}>The model tells you what to run next and why — not gut feel, not the highest-paid opinion. Data.</div>
            </div>
            <div style={s.step}>
              <div style={s.stepNum}>03</div>
              <div style={s.stepLabel}>Compounds as your metrics develop</div>
              <div style={s.stepDesc}>Each result sharpens the next pick. Teams running this loop widen the gap on every budget cycle.</div>
            </div>
          </div>
          <a href="/guide" style={s.walkLink}>Full walkthrough →</a>
          <span style={{ ...s.walkLink, color: "#475569", margin: "0 10px" }}>·</span>
          <a href="/evidence" style={s.walkLink}>See the research →</a>
        </div>

        {/* Feature cards */}
        <div style={s.grid3}>
          <div style={s.card}>
            <h3 style={s.cardHead}>Experimenting firms outperform</h3>
            <p style={s.cardBody}>
              Meta's own research found that firms running structured experiments show
              higher subsequent ad performance — and the advantage compounds over time.
            </p>
          </div>

          <div style={s.card}>
            <h3 style={s.cardHead}>Smarter tests, not more tests</h3>
            <p style={s.cardBody}>
              Random A/B testing leaves performance on the table. Bayesian optimization
              finds winners faster because it learns from each test — not just counts results.
            </p>
          </div>

          <div style={s.card}>
            <h3 style={s.cardHead}>
              Free during beta
              <span style={s.betaBadge}>Beta</span>
            </h3>
            <p style={s.cardBody}>
              Early teams get full access at no cost while we refine the model with
              real campaign data. No limits, no credit card, no catch.
            </p>
          </div>
        </div>

        {/* Dashboard mockup */}
        <div style={s.mockupWrap}>
          <DashboardMockup />
        </div>
        <p style={s.caption}>
          Recommendations, performance trends, and signal breakdowns — all in one view.
        </p>

      </div>
    </div>
  );
}
