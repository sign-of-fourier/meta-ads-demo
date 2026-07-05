import { useState, useEffect } from "react";

const C = {
  bg: "#0b1220",
  surface: "#020617",
  border: "#1e2d45",
  text: "#e2e8f0",
  muted: "#94a3b8",
  dim: "#475569",
  sky: "#38bdf8",
  skyFaint: "rgba(56,189,248,0.08)",
  green: "#4ade80",
  greenFaint: "rgba(74,222,128,0.08)",
  amber: "#fbbf24",
};

const sections = [
  { id: "what", label: "What is AdStackers?" },
  { id: "idea", label: "Three things to believe" },
  { id: "setup", label: "Setting up an experiment" },
  { id: "recommendations", label: "Reading recommendations" },
  { id: "signals", label: "Understanding the signals" },
  { id: "crossplatform", label: "Cross-platform" },
  { id: "faq", label: "FAQ" },
];

function Callout({ children, color = C.sky }) {
  return (
    <div style={{
      background: `${color}10`, border: `1px solid ${color}33`,
      borderRadius: 10, padding: "14px 18px", margin: "20px 0",
      fontSize: 14, color: C.muted, lineHeight: 1.7,
    }}>
      {children}
    </div>
  );
}

function SectionHead({ id, children }) {
  return (
    <h2 id={id} style={{
      fontSize: 22, fontWeight: 700, color: C.text,
      margin: "0 0 16px", paddingTop: 48, scrollMarginTop: 80,
    }}>
      {children}
    </h2>
  );
}

function SubHead({ children }) {
  return (
    <h3 style={{ fontSize: 16, fontWeight: 600, color: C.text, margin: "28px 0 10px" }}>
      {children}
    </h3>
  );
}

function P({ children }) {
  return (
    <p style={{ fontSize: 15, color: C.muted, lineHeight: 1.8, margin: "0 0 16px" }}>
      {children}
    </p>
  );
}

function Step({ n, title, children }) {
  return (
    <div style={{ display: "flex", gap: 18, marginBottom: 24 }}>
      <div style={{
        flexShrink: 0, width: 28, height: 28, borderRadius: "50%",
        background: C.skyFaint, border: `1px solid ${C.sky}44`,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 12, fontWeight: 700, color: C.sky, marginTop: 1,
      }}>
        {n}
      </div>
      <div>
        <div style={{ fontSize: 15, fontWeight: 600, color: C.text, marginBottom: 6 }}>{title}</div>
        <div style={{ fontSize: 14, color: C.muted, lineHeight: 1.7 }}>{children}</div>
      </div>
    </div>
  );
}

function SignalRow({ label, description, strong }) {
  return (
    <div style={{
      display: "flex", gap: 14, padding: "14px 0",
      borderBottom: `1px solid ${C.border}`,
    }}>
      <div style={{ width: 140, flexShrink: 0, fontSize: 14, fontWeight: 600, color: strong ? C.sky : C.text }}>
        {label}
      </div>
      <div style={{ fontSize: 14, color: C.muted, lineHeight: 1.6 }}>{description}</div>
    </div>
  );
}

function FAQ({ q, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ borderBottom: `1px solid ${C.border}` }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          width: "100%", textAlign: "left", background: "none", border: "none",
          padding: "16px 0", cursor: "pointer", display: "flex", justifyContent: "space-between",
          alignItems: "center", fontSize: 15, fontWeight: 600, color: C.text,
        }}
      >
        {q}
        <span style={{ color: C.dim, fontSize: 18, flexShrink: 0, marginLeft: 12 }}>{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div style={{ fontSize: 14, color: C.muted, lineHeight: 1.8, paddingBottom: 16 }}>
          {children}
        </div>
      )}
    </div>
  );
}

export default function GuidePage() {
  useEffect(() => {
    if (window.location.hash) {
      const el = document.getElementById(window.location.hash.slice(1));
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, []);

  return (
    <div style={{
      fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
      background: C.bg, minHeight: "100vh", color: C.text,
    }}>

      {/* Top bar */}
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
        <span style={{ fontSize: 14, color: C.dim }}>Guide</span>
        <div style={{ flex: 1 }} />
        <a href="/app/auth" style={{
          fontSize: 13, fontWeight: 600, color: "#0b1120",
          background: C.sky, padding: "6px 16px", borderRadius: 999, textDecoration: "none",
        }}>
          Get started
        </a>
      </div>

      <div style={{ maxWidth: 1000, margin: "0 auto", display: "flex", gap: 48, padding: "0 24px 80px" }}>

        {/* Sidebar nav */}
        <div style={{ flexShrink: 0, width: 200, paddingTop: 48 }}>
          <div style={{ position: "sticky", top: 72 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: C.dim, textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 14 }}>
              On this page
            </div>
            {sections.map(sec => (
              <a
                key={sec.id}
                href={`#${sec.id}`}
                style={{
                  display: "block", fontSize: 13, color: C.dim, textDecoration: "none",
                  padding: "5px 0", lineHeight: 1.4,
                  transition: "color 0.15s",
                }}
                onMouseEnter={e => e.target.style.color = C.sky}
                onMouseLeave={e => e.target.style.color = C.dim}
              >
                {sec.label}
              </a>
            ))}
          </div>
        </div>

        {/* Main content */}
        <div style={{ flex: 1, minWidth: 0 }}>

          {/* Page header */}
          <div style={{ paddingTop: 48, marginBottom: 8 }}>
            <div style={{
              fontSize: 11, fontWeight: 700, color: C.sky, textTransform: "uppercase",
              letterSpacing: "0.1em", marginBottom: 12,
            }}>
              Guide
            </div>
            <h1 style={{ fontSize: 32, fontWeight: 700, color: C.text, margin: "0 0 14px", lineHeight: 1.15 }}>
              How AdStackers works
            </h1>
            <p style={{ fontSize: 16, color: C.muted, lineHeight: 1.7, maxWidth: 560, margin: "0 0 24px" }}>
              A plain-language walkthrough for performance marketers — no engineering degree required.
            </p>
            <a href="/evidence" style={{
              fontSize: 13, color: C.sky, textDecoration: "none", fontWeight: 600,
              display: "inline-flex", alignItems: "center", gap: 6,
            }}>
              Want the research backing first? Read the evidence →
            </a>
          </div>

          {/* --- What is it --- */}
          <SectionHead id="what">What is AdStackers?</SectionHead>
          <P>
            AdStackers is an ad experimentation platform. It helps performance marketers run more
            efficient creative tests by telling you what to run next — not just logging what already
            happened.
          </P>
          <P>
            Most teams test ads by feel: pick a few variations, run them, look at the numbers, make
            a call. That works sometimes. But it's slow, noisy, and it scales poorly. You spend
            budget ruling out things that obviously wouldn't work, you miss non-obvious combinations
            that would have, and you're never quite sure if a "winner" is real or just caught a good
            week.
          </P>
          <P>
            AdStackers replaces that with a systematic loop. You define the creative elements you
            want to explore. The platform figures out which combination is most worth testing next —
            based on your actual performance data and the semantic content of your ads. Each test
            teaches the model. Each recommendation gets sharper. The advantage compounds.
          </P>
          <Callout>
            <strong style={{ color: C.text }}>The short version:</strong> You tell it what to test.
            It tells you what to run and why. It learns from what happens. Repeat.
          </Callout>

          {/* --- Core idea --- */}
          <SectionHead id="idea">The three things you need to believe</SectionHead>
          <P>
            AdStackers is built on three claims. Each one is independently well-supported, and
            together they explain why this approach produces better results than what most teams do.
          </P>

          <SubHead>1. Testing beats gut instinct</SubHead>
          <P>
            Meta's own research found that firms running structured experiments on the platform
            show higher subsequent ad performance — not just in that campaign, but as a compounding
            advantage over time. Teams that build a learning system get better at running ads with
            every cycle. Teams that rely on intuition don't.
          </P>
          <P>
            The basic argument: each experiment adds to what your team knows. Decisions made from
            accumulated evidence beat decisions made from the highest-paid opinion in the room. That
            advantage widens over time.
          </P>

          <SubHead>2. Bayesian optimization beats random testing</SubHead>
          <P>
            Standard A/B testing treats every ad as equally worth testing. Run two versions, wait
            for significance, pick the winner. That's not wrong — but it's inefficient. You spend
            as much budget on a bad ad as a good one, and you learn slowly.
          </P>
          <P>
            AdStackers uses Bayesian optimization instead of random selection. Rather than picking
            the next test at random, it builds a model of your creative space — what's been tested,
            what's worked, what the content of each ad looks like — and uses that model to predict
            which candidate is most likely to outperform. In head-to-head benchmarks on real ad
            creative data, BO consistently beats random search and its advantage grows over rounds.
          </P>
          <Callout color={C.green}>
            <strong style={{ color: C.text }}>In plain terms:</strong> Random testing is trying
            combinations until something works. BO is learning what tends to work and using that
            knowledge to pick what to try next.
          </Callout>

          <SubHead>3. Doing this at scale requires the right infrastructure</SubHead>
          <P>
            The research case is clear. The operational problem is that running BO on ad creative
            is not simple. The inputs are text and images, not numbers — so you need a way to
            represent creative content mathematically. You need a model that can generalize across
            your campaign history. And you need it to plug into your actual ad accounts.
          </P>
          <P>
            AdStackers is that infrastructure. It handles creative embedding, candidate ranking,
            cross-platform signal pooling, and ad push — without requiring a data science team.
            The{" "}
            <a href="/evidence" style={{ color: C.sky, textDecoration: "none", fontWeight: 500 }}>
              evidence page
            </a>
            {" "}goes deeper on how each of these claims is backed.
          </P>

          <P>
            It also separates real signal from noise. Early in a campaign, any ad might look like
            a winner on a good day. AdStackers tracks confidence — how much data backs each result
            — and waits before updating its recommendations too aggressively.
          </P>

          {/* --- Setup --- */}
          <SectionHead id="setup">Setting up an experiment</SectionHead>
          <P>
            Experiments in AdStackers map to your existing campaign structure. You don't need to
            restructure anything.
          </P>
          <Step n={1} title="Connect your ad account">
            Link Meta, Google, or define your own platform. AdStackers reads your existing
            campaigns and performance history in read-only mode — nothing changes until you
            explicitly push an ad.
          </Step>
          <Step n={2} title="Pick the campaign to experiment on">
            Select an active campaign. AdStackers ingests its current ads and performance data
            to build an initial model.
          </Step>
          <Step n={3} title="Define what you want to test">
            Specify the creative dimensions: headline styles, image types, offers, calls to
            action. These become the axes of your search space. You can be broad ("any image
            type") or narrow ("lifestyle vs product-only").
          </Step>

          <Callout color={C.amber}>
            <strong id="dynamic-ads" style={{ color: C.text, scrollMarginTop: 80, display: "inline-block" }}>
              Pro tip: use a dynamic ad to define your search space.
            </strong>{" "}
            A dynamic ad (Meta's multi-asset format, or a Google RSA with several headlines and
            descriptions) is really a template — every headline, image, and body text attached to
            it becomes an axis AdStackers can recombine. The richer that asset library, the wider
            the space it has to search. Once you're using a dynamic ad this way, turn it off (pause
            it) in your campaign — you want AdStackers testing static ads pulled from that template,
            not the platform's own auto-mixing competing for delivery in the background.
          </Callout>

          <Step n={4} title="Get your first recommendation">
            AdStackers scores every combination in your defined space and surfaces the one most
            likely to improve your target metric — CTR, ROAS, or conversion rate. If you're
            starting fresh with no history, it bootstraps from similar campaigns you've already run.
          </Step>
          <Step n={5} title="Push and run">
            Review the recommended ad. Push it to your account when you're ready. It goes live
            paused — you activate it. One click to push; you stay in control.
          </Step>
          <Callout color={C.green}>
            <strong style={{ color: C.text }}>Nothing runs automatically.</strong> AdStackers
            recommends and generates. You approve and activate. Every push requires your explicit action.
          </Callout>

          {/* --- Recommendations --- */}
          <SectionHead id="recommendations">Reading recommendations</SectionHead>
          <P>
            Each recommendation comes with three things: a ranked position, a confidence score,
            and a brief explanation of what's driving the pick.
          </P>
          <SubHead>The ranked list</SubHead>
          <P>
            The platform surfaces your top three candidates. The #1 pick is what the model
            believes will move your target metric the most given current data. The #2 and #3
            picks are worth watching — they often become the recommendation after more signal
            comes in.
          </P>
          <SubHead>Confidence score</SubHead>
          <P>
            Confidence reflects how much data backs the recommendation. A 90% match means the
            model has seen strong signal across multiple similar combinations and is high-conviction.
            A 55% match means it's an educated guess — still worth testing, but expect it to
            update as results come in.
          </P>
          <SubHead>Expected lift</SubHead>
          <P>
            The estimated CTR and percentage lift over your baseline are predictions, not
            guarantees. Early in an experiment they'll be rougher; they sharpen as more results
            accumulate. Use them directionally, not as hard targets.
          </P>
          <SubHead>Convergence</SubHead>
          <P>
            When a test has run long enough that the results are stable — the model isn't
            changing its mind based on new data — the experiment is marked "converging." This
            is the signal to trust the result and move to the next recommendation.
          </P>

          {/* --- Signals --- */}
          <SectionHead id="signals">Understanding the signals</SectionHead>
          <P>
            The "What's working" panel breaks down which creative elements are contributing most
            to strong performance. These are relative scores — they tell you which dimensions
            matter most within your current search space.
          </P>
          <div style={{ marginBottom: 24 }}>
            <SignalRow
              label="High score (80–100%)"
              description="This element is a strong positive signal. The model is actively using it to differentiate winners from losers. Double down: more tests in this dimension will be informative."
              strong
            />
            <SignalRow
              label="Mid score (40–79%)"
              description="This element matters, but the model hasn't seen enough variation to be confident. Worth including in the next round of tests."
            />
            <SignalRow
              label="Low score (0–39%)"
              description="This element isn't moving the needle in your current space. That might mean it genuinely doesn't matter, or that you haven't tested enough variation. Don't write it off — revisit later with different execution."
            />
          </div>
          <SubHead>Untested space</SubHead>
          <P>
            The "untested space" bar shows how much of your defined search space the model has
            explored. At 30% explored, you're still in early-signal territory — recommendations
            will shift more as new results come in. At 80%+, the model has seen most of the space
            and recommendations will be more stable.
          </P>
          <Callout color={C.amber}>
            <strong style={{ color: C.text }}>Important:</strong> A low score for a creative
            element doesn't mean "stop testing it." It means the model doesn't have enough signal
            yet. Some of the most valuable discoveries come from elements that looked flat early on.
          </Callout>

          {/* --- Cross-platform --- */}
          <SectionHead id="crossplatform">Cross-platform</SectionHead>
          <P>
            AdStackers runs the same experimentation loop across Meta, Google, and any platform
            you connect. The model can draw on signal from across platforms — if a creative angle
            is working on Google, that information can inform Meta recommendations, and vice versa.
          </P>
          <P>
            Each platform has its own ad formats and creative requirements. AdStackers handles
            the format differences automatically — you define what you want to test, and it
            generates platform-appropriate versions.
          </P>
          <P>
            The dashboard shows all your experiments in one view, with platform labels on each
            campaign. You can filter by platform or look across everything at once.
          </P>

          {/* --- FAQ --- */}
          <SectionHead id="faq">FAQ</SectionHead>
          <FAQ q="Do I need to restructure my campaigns?">
            No. AdStackers works with your existing campaign structure. You connect an account,
            pick a campaign, and start from there.
          </FAQ>
          <FAQ q="Will it automatically pause or change my ads?">
            No. AdStackers is read-only unless you explicitly push a recommended ad. Nothing
            in your ad account changes without your action.
          </FAQ>
          <FAQ q="How much historical data do I need?">
            The model can start with as little as one active campaign. It bootstraps from
            similar creative you've already run. More history means sharper early recommendations,
            but it's not a prerequisite.
          </FAQ>
          <FAQ q="What's the difference between confidence and expected lift?">
            Confidence is about how certain the model is. Expected lift is what it thinks
            will happen if the recommendation is right. A high-confidence, low-lift pick is
            a safe test. A low-confidence, high-lift pick is a longer shot worth exploring.
          </FAQ>
          <FAQ q="What does 'converging' mean?">
            It means the experiment has run long enough that results are stable — adding more
            budget to that test won't change the conclusion. It's the signal to trust the
            result and move to the next recommendation.
          </FAQ>
          <FAQ q="Is my data shared between accounts?">
            No. Your campaign data stays within your account. Cross-platform signal only
            flows within your own connected accounts, never across different users.
          </FAQ>
          <FAQ q="Is it really free?">
            Yes, during the beta period. Full access, no credit card, no trial limits.
            We'll give advance notice before any pricing changes.
          </FAQ>

          {/* Bottom CTA */}
          <div style={{
            marginTop: 60, padding: "32px 28px", background: C.surface,
            borderRadius: 16, border: `1px solid ${C.border}`, textAlign: "center",
          }}>
            <div style={{ fontSize: 20, fontWeight: 700, color: C.text, marginBottom: 10 }}>
              Ready to run a real experiment?
            </div>
            <div style={{ fontSize: 14, color: C.muted, marginBottom: 20 }}>
              Free during beta. No credit card required.
            </div>
            <a href="/app/auth" style={{
              display: "inline-block", textDecoration: "none",
              background: C.sky, color: "#0b1120", fontWeight: 700,
              fontSize: 14, padding: "11px 28px", borderRadius: 999,
            }}>
              Get started
            </a>
            <div style={{ marginTop: 14, fontSize: 13, color: C.dim }}>
              Already have an account?{" "}
              <a href="/app/auth" style={{ color: C.muted, textDecoration: "underline" }}>Log in</a>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
