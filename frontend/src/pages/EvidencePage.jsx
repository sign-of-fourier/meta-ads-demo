import BoComparisonChart from "../components/BoComparisonChart.jsx";

const C = {
  bg: "#0b1220",
  surface: "#020617",
  border: "#1e2d45",
  text: "#e2e8f0",
  muted: "#94a3b8",
  dim: "#475569",
  sky: "#38bdf8",
  skyFaint: "rgba(56,189,248,0.07)",
  green: "#4ade80",
  greenFaint: "rgba(74,222,128,0.07)",
  amber: "#fbbf24",
  red: "#f87171",
};

function NavBar() {
  return (
    <div style={{
      position: "sticky", top: 0, zIndex: 10,
      background: `${C.bg}ee`, backdropFilter: "blur(10px)",
      borderBottom: `1px solid ${C.border}`,
      padding: "12px 24px", display: "flex", alignItems: "center", gap: 16,
    }}>
      <a href="/" style={{ display: "flex", alignItems: "center", gap: 8, textDecoration: "none" }}>
        <img src="/logo.svg" alt="AdStackers" style={{ height: 24 }} />
        <span style={{ fontFamily: "'Big Shoulders Display', sans-serif", fontWeight: 700, fontSize: 20, background: "linear-gradient(90deg, #f59e0b, #b45309)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent", backgroundClip: "text" }}>AdStackers</span>
      </a>
      <span style={{ color: C.border }}>›</span>
      <a href="/guide" style={{ fontSize: 14, color: C.dim, textDecoration: "none" }}>Guide</a>
      <span style={{ color: C.border }}>›</span>
      <span style={{ fontSize: 14, color: C.muted }}>The Evidence</span>
      <div style={{ flex: 1 }} />
      <a href="/app/auth" style={{
        fontSize: 13, fontWeight: 600, color: "#0b1120",
        background: C.sky, padding: "6px 16px", borderRadius: 999, textDecoration: "none",
      }}>Get started</a>
    </div>
  );
}

function Stat({ value, label, source }) {
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 12, padding: "20px 22px",
    }}>
      <div style={{ fontSize: 28, fontWeight: 800, color: C.sky, marginBottom: 6, lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: 14, color: C.text, fontWeight: 500, marginBottom: 6, lineHeight: 1.4 }}>{label}</div>
      <div style={{ fontSize: 11, color: C.dim }}>{source}</div>
    </div>
  );
}

function Citation({ children, href }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      style={{ fontSize: 11, color: C.dim, textDecoration: "none", display: "inline-block", marginLeft: 6 }}
    >
      [{children}]
    </a>
  );
}

function Finding({ n, headline, children }) {
  return (
    <div style={{ display: "flex", gap: 20, marginBottom: 36 }}>
      <div style={{
        flexShrink: 0, fontSize: 11, fontWeight: 800, color: C.sky,
        width: 28, height: 28, borderRadius: "50%",
        background: C.skyFaint, border: `1px solid ${C.sky}33`,
        display: "flex", alignItems: "center", justifyContent: "center",
        marginTop: 2,
      }}>{n}</div>
      <div>
        <div style={{ fontSize: 17, fontWeight: 700, color: C.text, marginBottom: 8 }}>{headline}</div>
        <div style={{ fontSize: 14, color: C.muted, lineHeight: 1.8 }}>{children}</div>
      </div>
    </div>
  );
}

function SectionLabel({ children }) {
  return (
    <div style={{
      fontSize: 11, fontWeight: 700, color: C.sky,
      textTransform: "uppercase", letterSpacing: "0.1em",
      marginBottom: 14,
    }}>{children}</div>
  );
}

export default function EvidencePage() {
  return (
    <div style={{
      fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
      background: C.bg, minHeight: "100vh", color: C.text,
    }}>
      <NavBar />

      <div style={{ maxWidth: 820, margin: "0 auto", padding: "52px 24px 80px" }}>

        {/* Header */}
        <SectionLabel>The Evidence</SectionLabel>
        <h1 style={{ fontSize: 34, fontWeight: 800, color: C.text, margin: "0 0 16px", lineHeight: 1.15 }}>
          Why Bayesian optimization beats the way most teams test ads
        </h1>
        <p style={{ fontSize: 16, color: C.muted, lineHeight: 1.8, maxWidth: 620, margin: "0 0 52px" }}>
          Three claims, each backed by research. Experimentation outperforms intuition.
          Bayesian optimization outperforms random testing. Doing this at scale requires
          the right infrastructure — which most teams don't have.
        </p>

        {/* Claim 1 */}
        <div style={{ marginBottom: 56 }}>
          <SectionLabel>Claim 1</SectionLabel>
          <h2 style={{ fontSize: 24, fontWeight: 700, color: C.text, margin: "0 0 24px" }}>
            Structured experimentation outperforms intuition
          </h2>

          <Finding n={1} headline="Experimenting firms show higher subsequent ad performance">
            Meta's own research found that firms running structured experiments on the platform
            show higher concurrent and subsequent ad performance — not just in the short term,
            but as a compounding advantage over time. The implication is direct: teams that build
            an experiment-based learning process get better at running ads, and that advantage
            grows with every cycle.
            <Citation href="https://research.facebook.com/publications/experimentation-and-performance-in-advertising-an-observational-survey-of-firm-practices-on-facebook/">
              Meta Research
            </Citation>
          </Finding>

          <Finding n={2} headline="Each test builds organizational knowledge — it compounds">
            The core argument for disciplined experimentation is not that any single test
            produces a big win. It's that each test compounds what your team knows. Teams
            making decisions from accumulated evidence make better decisions faster than teams
            relying on intuition or the highest-paid opinion in the room.
            <Citation href="https://www.appcues.com/blog/guide-to-in-product-experimentation">
              Appcues
            </Citation>
          </Finding>

          <Finding n={3} headline="Experimentation reduces risk and accelerates learning">
            Structured experimentation is valuable because it reduces risk, speeds up learning,
            and pushes teams toward decisions based on what actually moves metrics — not what
            feels right. The alternative is not "no risk." It's "hidden risk."
            <Citation href="https://bridgingproducts.com/experimentation-the-engine-behind-smarter-product-decisions/">
              Bridging Products
            </Citation>
          </Finding>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 14, marginTop: 8 }}>
            <Stat
              value="Higher"
              label="Ad performance for firms that run structured experiments vs those that don't"
              source="Meta Research, observational survey"
            />
            <Stat
              value="Faster"
              label="Decision-making when teams use cumulative evidence instead of intuition"
              source="Appcues, experimentation guide"
            />
            <Stat
              value="Lower"
              label="Risk of misallocating budget when tests, not opinions, drive creative decisions"
              source="Bridging Products"
            />
          </div>
        </div>

        {/* Claim 2 */}
        <div style={{ marginBottom: 56 }}>
          <SectionLabel>Claim 2</SectionLabel>
          <h2 style={{ fontSize: 24, fontWeight: 700, color: C.text, margin: "0 0 16px" }}>
            BO at scale beats BO at half-scale — and no one else runs at this scale
          </h2>

          {/* Three-tier explanation */}
          <p style={{ fontSize: 15, color: C.muted, lineHeight: 1.8, marginBottom: 28 }}>
            There are three tiers on this chart, and they represent three completely different
            levels of capability. Understanding the difference is the whole point.
          </p>

          <div style={{ display: "flex", flexDirection: "column", gap: 14, marginBottom: 36 }}>
            {[
              {
                color: C.red,
                tier: "Tier 1 — Random search",
                desc: "Pick which ads to test randomly. No model, no learning. This is what most teams actually do, even when they call it A/B testing. You rotate creatives, see what sticks, make a gut call on the winner. It's better than nothing, but it doesn't learn.",
              },
              {
                color: C.sky,
                tier: "Tier 2 — Bayesian optimization, batch 512",
                desc: "Instead of random picks, an acquisition function scores 512 candidate ads per round, balancing exploration (untested territory) with exploitation (doubling down on what's working). The model learns from each round and narrows in on the best creative space. This is already computationally hard — most teams can't run it — but it measurably outperforms random search from round 2 onward.",
              },
              {
                color: C.green,
                tier: "Tier 3 — Bayesian optimization, batch 4096",
                desc: "The same acquisition function, but scoring 4,096 candidates per round instead of 512. Eight times more of the creative space evaluated on every cycle. The results are clearly better — and the gap compounds over 10 rounds. The reason almost no one runs at this scale: evaluating a Gaussian Process across 4,096 candidates in parallel, round after round, requires infrastructure most companies don't have and can't build. This is where AdStackers runs.",
              },
            ].map(({ color, tier, desc }) => (
              <div key={tier} style={{
                display: "flex", gap: 16,
                background: C.surface, border: `1px solid ${C.border}`,
                borderLeft: `3px solid ${color}`,
                borderRadius: "0 10px 10px 0",
                padding: "16px 18px",
              }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 14, fontWeight: 700, color, marginBottom: 6 }}>{tier}</div>
                  <div style={{ fontSize: 14, color: C.muted, lineHeight: 1.75 }}>{desc}</div>
                </div>
              </div>
            ))}
          </div>

          {/* The chart */}
          <div style={{
            background: C.surface, border: `1px solid ${C.border}`,
            borderRadius: 16, padding: "28px 24px", marginBottom: 20,
          }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 4 }}>
              Three tiers, measured on real ad creative data
            </div>
            <div style={{ fontSize: 13, color: C.dim, marginBottom: 24 }}>
              CHI-BAD-ADS benchmark · average creative quality score over 10 rounds · shaded area = ±1 std dev
            </div>
            <BoComparisonChart />
          </div>

          {/* What to notice */}
          <div style={{
            background: C.greenFaint, border: `1px solid ${C.green}22`,
            borderRadius: 12, padding: "18px 22px", marginBottom: 28,
          }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: C.green, marginBottom: 12 }}>
              What to notice in this chart
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {[
                ["Round 1", "BO-512 and random are nearly tied. Neither has enough signal yet to differentiate — this is expected."],
                ["Round 2 onward", "BO-512 pulls ahead of random and stays there for all remaining rounds. The model is learning."],
                ["BO-4096 vs BO-512", "The green line (4096) is consistently above the blue (512). More candidates scored per round means a better search — and the gap holds across all 10 rounds."],
                ["Narrowing bands on BO", "The shaded confidence bands around BO-512 shrink over rounds, meaning it gets more consistent over time. Random search stays noisy throughout."],
                ["The green line is not a ceiling", "It's not a theoretical benchmark or an upper bound. It's a result — what actually happens when you score 4,096 candidates with the acquisition function. AdStackers produces this outcome."],
              ].map(([label, desc]) => (
                <div key={label} style={{ display: "flex", gap: 12, fontSize: 13 }}>
                  <span style={{ fontWeight: 600, color: C.text, flexShrink: 0, width: 160 }}>{label}</span>
                  <span style={{ color: C.muted, lineHeight: 1.6 }}>{desc}</span>
                </div>
              ))}
            </div>
          </div>

          <Finding n={4} headline="BO is designed for exactly this problem">
            BO is the standard approach when evaluations are expensive and you can't afford to
            try everything.
            <Citation href="https://en.wikipedia.org/wiki/Bayesian_optimization">Wikipedia</Citation>
            <Citation href="https://www.datacamp.com/tutorial/mastering-bayesian-optimization-in-data-science">DataCamp</Citation>
            {" "}Ad creative testing is exactly this problem: each test costs real budget, real time,
            and real audience exposure. The acquisition function is how you make each test count.
          </Finding>

          <Finding n={5} headline="The bottleneck is compute, not math">
            The math behind 4,096-candidate batch BO is not new. What's new is running it
            efficiently enough to make it practical for marketing teams. AdStackers's infrastructure —
            cloud-hosted GP endpoints with parallel acquisition function evaluation — is what makes
            the green line achievable outside of a research lab.
          </Finding>
        </div>

        {/* Claim 3 */}
        <div style={{ marginBottom: 56 }}>
          <SectionLabel>Claim 3</SectionLabel>
          <h2 style={{ fontSize: 24, fontWeight: 700, color: C.text, margin: "0 0 16px" }}>
            The only remaining barrier is getting creative content into the model
          </h2>
          <p style={{ fontSize: 15, color: C.muted, lineHeight: 1.8, marginBottom: 28 }}>
            Once you can run 4,096-candidate batch BO, the next question is: what are you
            optimizing over? Standard BO takes numerical inputs — knobs and sliders. Ad creative
            is text and images. Bridging that gap is the other hard problem AdStackers solves.
          </p>

          <Finding n={6} headline="Ad content has to be represented mathematically for BO to work">
            AdStackers converts your ad creative — headlines, images, offers, CTAs — into
            embedding vectors that capture semantic meaning. The model understands that
            "limited time offer" and "act now" are similar signals. It knows that a lifestyle
            photo and a product shot are different visual strategies. This is what lets the
            acquisition function reason about creative quality, not just numerical parameters.
          </Finding>

          <Finding n={7} headline="The search space is larger than most teams realize">
            A campaign with 4 headline angles, 4 image types, 3 offer variations, and 2 CTAs
            has 96 combinations. A realistic creative search space is 500–4,000 candidates.
            You cannot afford to test all of them. The 4,096-batch acquisition function is
            what lets the model score the entire space and tell you which one to actually run.
          </Finding>

          <Finding n={8} headline="AdStackers is the only platform that puts all of this together">
            Creative embedding + 4,096-candidate batch BO + cross-platform signal pooling +
            one-click push to your ad accounts. No data science team required. The green line
            on that chart is not a research result — it's what your campaigns can produce.
          </Finding>
        </div>

        {/* Bottom CTA */}
        <div style={{
          background: C.surface, border: `1px solid ${C.border}`,
          borderRadius: 16, padding: "32px 28px", textAlign: "center",
        }}>
          <div style={{ fontSize: 20, fontWeight: 700, color: C.text, marginBottom: 10 }}>
            Your competitors are figuring this out.
          </div>
          <div style={{ fontSize: 14, color: C.muted, marginBottom: 24, maxWidth: 440, margin: "0 auto 24px" }}>
            AdStackers is free during beta. The window to build a compounding advantage before
            this becomes standard practice is now.
          </div>
          <a href="/app/auth" style={{
            display: "inline-block", textDecoration: "none",
            background: C.sky, color: "#0b1120",
            fontWeight: 700, fontSize: 14, padding: "12px 28px", borderRadius: 999,
          }}>
            Get started — it's free
          </a>
          <div style={{ marginTop: 14, fontSize: 13, color: C.dim }}>
            Already have an account?{" "}
            <a href="/app/auth" style={{ color: C.muted, textDecoration: "underline" }}>Log in</a>
          </div>
        </div>

      </div>
    </div>
  );
}
