const C = {
  bg: "#020617",
  surface: "#0b1220",
  panel: "#0d1829",
  border: "#1e2d45",
  borderFaint: "#152033",
  text: "#e2e8f0",
  muted: "#94a3b8",
  dim: "#475569",
  sky: "#38bdf8",
  skyFaint: "rgba(56,189,248,0.08)",
  green: "#4ade80",
  greenFaint: "rgba(74,222,128,0.08)",
  amber: "#fbbf24",
  purple: "#a78bfa",
  red: "#f87171",
};

function PlatformPill({ label, active }) {
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 999,
      border: `1px solid ${active ? C.sky : C.border}`,
      color: active ? C.sky : C.dim,
      background: active ? C.skyFaint : "transparent",
      letterSpacing: "0.06em",
    }}>
      {label}
    </span>
  );
}

function StatusDot({ color }) {
  return <span style={{ display: "inline-block", width: 7, height: 7, borderRadius: "50%", background: color, marginRight: 6 }} />;
}

function ExplainBar({ label, value, color = C.sky }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
        <span style={{ fontSize: 12, color: C.muted }}>{label}</span>
        <span style={{ fontSize: 12, fontWeight: 600, color, fontVariantNumeric: "tabular-nums" }}>{value}%</span>
      </div>
      <div style={{ height: 4, background: C.border, borderRadius: 99, overflow: "hidden" }}>
        <div style={{
          height: "100%", width: `${value}%`, borderRadius: 99,
          background: `linear-gradient(90deg, ${color}, ${color}99)`,
          transition: "width 0.3s",
        }} />
      </div>
    </div>
  );
}

function RecommendationCard({ rank, confidence, headline, image, offer, cta, liftPct, ctr, dimmed }) {
  const confidenceColor = confidence >= 80 ? C.green : confidence >= 60 ? C.sky : C.amber;
  return (
    <div style={{
      padding: "12px 14px", borderRadius: 10, marginBottom: 8,
      background: dimmed ? "transparent" : C.surface,
      border: `1px solid ${dimmed ? C.borderFaint : C.border}`,
      opacity: dimmed ? 0.55 : 1,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
        <span style={{ fontSize: 11, color: C.dim, fontWeight: 600 }}>#{rank}</span>
        <span style={{
          fontSize: 11, fontWeight: 700, color: confidenceColor,
          background: `${confidenceColor}18`, borderRadius: 999, padding: "2px 8px",
        }}>
          {confidence}% match
        </span>
      </div>
      <div style={{ fontSize: 13, color: C.text, fontWeight: 500, marginBottom: 4, lineHeight: 1.4 }}>
        {headline}
      </div>
      <div style={{ fontSize: 11, color: C.dim, marginBottom: 8 }}>
        {image} · {offer} · {cta}
      </div>
      <div style={{ display: "flex", gap: 10 }}>
        <span style={{ fontSize: 11, color: C.green, fontWeight: 600 }}>↑ {liftPct}% vs baseline</span>
        <span style={{ fontSize: 11, color: C.dim }}>Est. CTR {ctr}%</span>
      </div>
    </div>
  );
}

function TrendChart() {
  const W = 260, H = 90;
  const pts = [
    [0, 72], [36, 65], [72, 56], [108, 46], [144, 34], [180, 24], [216, 17], [252, 12],
  ];
  const toPath = (points) =>
    points.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x} ${y}`).join(" ");
  const areaPath = `${toPath(pts)} L ${pts[pts.length - 1][0]} ${H} L 0 ${H} Z`;

  return (
    <div style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>
        <defs>
          <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={C.sky} stopOpacity="0.25" />
            <stop offset="100%" stopColor={C.sky} stopOpacity="0.01" />
          </linearGradient>
        </defs>
        {/* Baseline */}
        <line x1="0" y1="72" x2={W} y2="72" stroke={C.border} strokeWidth="1" strokeDasharray="4 3" />
        <text x="4" y="68" fill={C.dim} fontSize="9">Baseline 2.1%</text>
        {/* Area fill */}
        <path d={areaPath} fill="url(#areaGrad)" />
        {/* Trend line */}
        <path d={toPath(pts)} fill="none" stroke={C.sky} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
        {/* Latest point dot */}
        <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="3.5" fill={C.sky} />
        {/* Convergence label */}
        <rect x="168" y="4" width="80" height="17" rx="4" fill={C.greenFaint} />
        <text x="208" y="16" fill={C.green} fontSize="9" fontWeight="700" textAnchor="middle">● Converging</text>
      </svg>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}>
        {["Wk 1", "Wk 2", "Wk 3", "Wk 4", "Wk 5", "Wk 6", "Wk 7", "Wk 8"].map(l => (
          <span key={l} style={{ fontSize: 10, color: C.dim }}>{l}</span>
        ))}
      </div>
    </div>
  );
}

function BudgetAllocationChart() {
  const bars = [
    { label: "Ad A", value: 52, color: C.sky },
    { label: "Ad B", value: 28, color: C.purple },
    { label: "Ad C", value: 13, color: C.amber },
    { label: "Ad D", value: 7, color: C.dim },
  ];
  const H = 60;
  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: H, marginBottom: 6 }}>
        {bars.map(b => (
          <div key={b.label} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "flex-end" }}>
            <span style={{ fontSize: 10, color: b.color, fontWeight: 600, marginBottom: 3 }}>{b.value}%</span>
            <div style={{
              width: "100%", borderRadius: "3px 3px 0 0",
              height: `${(b.value / 52) * H * 0.85}px`,
              background: b.color === C.dim ? C.border : `linear-gradient(180deg, ${b.color}cc, ${b.color}66)`,
            }} />
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        {bars.map(b => (
          <div key={b.label} style={{ flex: 1, textAlign: "center", fontSize: 10, color: C.dim }}>{b.label}</div>
        ))}
      </div>
    </div>
  );
}

const chipStyle = (status) => {
  const map = {
    converging: { color: C.green, bg: C.greenFaint, dot: C.green },
    testing: { color: C.sky, bg: C.skyFaint, dot: C.sky },
    new: { color: C.amber, bg: "rgba(251,191,36,0.08)", dot: C.amber },
    paused: { color: C.dim, bg: "transparent", dot: C.dim },
  };
  return map[status] || map.paused;
};

function ExperimentChip({ name, status, metric }) {
  const cs = chipStyle(status);
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 8,
      padding: "6px 12px", borderRadius: 8,
      border: `1px solid ${cs.color}33`,
      background: cs.bg,
    }}>
      <StatusDot color={cs.dot} />
      <div>
        <div style={{ fontSize: 11, fontWeight: 600, color: C.text }}>{name}</div>
        <div style={{ fontSize: 10, color: C.dim }}>{metric}</div>
      </div>
      <span style={{
        fontSize: 10, color: cs.color, fontWeight: 600,
        textTransform: "uppercase", letterSpacing: "0.06em",
      }}>{status}</span>
    </div>
  );
}

export default function DashboardMockup() {
  return (
    <div style={{
      background: C.bg, borderRadius: 20, border: `1px solid ${C.border}`,
      boxShadow: "0 32px 100px rgba(0,0,0,0.7)", overflow: "hidden", userSelect: "none",
      fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
    }}>

      {/* Top bar */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12, padding: "12px 20px",
        borderBottom: `1px solid ${C.border}`, background: C.surface,
      }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: C.sky, letterSpacing: "0.04em" }}>
          AdStackers
        </span>
        <div style={{ width: 1, height: 14, background: C.border }} />
        <div style={{ display: "flex", gap: 6 }}>
          <PlatformPill label="Meta" active />
          <PlatformPill label="Google" active />
        </div>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: C.muted }}>Summer Sale 2026</span>
        <span style={{ fontSize: 10, color: C.green, background: C.greenFaint, padding: "3px 9px", borderRadius: 999, fontWeight: 600 }}>
          3 active
        </span>
      </div>

      {/* Main content */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.1fr 0.9fr", gap: 0, minHeight: 340 }}>

        {/* Panel 1 — Recommendations */}
        <div style={{ padding: "16px 18px", borderRight: `1px solid ${C.border}` }}>
          <div style={{ fontSize: 10, fontWeight: 700, color: C.dim, textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 12 }}>
            Next Recommendation
          </div>
          <RecommendationCard
            rank={1} confidence={91}
            headline="Limited time — see why it works"
            image="Lifestyle photo"
            offer="Free trial"
            cta="Learn more"
            liftPct={38} ctr={4.8}
          />
          <RecommendationCard
            rank={2} confidence={74}
            headline="You're not the only one asking"
            image="Product close-up"
            offer="Free trial"
            cta="Shop now"
            liftPct={20} ctr={4.2}
            dimmed
          />
          <RecommendationCard
            rank={3} confidence={58}
            headline="Most teams solve this in week one"
            image="Team lifestyle"
            offer="Book a demo"
            cta="Get started"
            liftPct={11} ctr={3.9}
            dimmed
          />
        </div>

        {/* Panel 2 — Trend + Budget */}
        <div style={{ padding: "16px 18px", borderRight: `1px solid ${C.border}` }}>
          <div style={{ fontSize: 10, fontWeight: 700, color: C.dim, textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 12 }}>
            CTR over experiment
          </div>
          <TrendChart />
          <div style={{ marginTop: 20 }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: C.dim, textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 10 }}>
              Budget allocation
            </div>
            <BudgetAllocationChart />
          </div>
        </div>

        {/* Panel 3 — Explainability */}
        <div style={{ padding: "16px 18px" }}>
          <div style={{ fontSize: 10, fontWeight: 700, color: C.dim, textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 12 }}>
            What's working
          </div>
          <div style={{ fontSize: 11, color: C.dim, marginBottom: 14, lineHeight: 1.5 }}>
            Signals driving the #1 pick
          </div>
          <ExplainBar label="Urgency headlines" value={91} color={C.sky} />
          <ExplainBar label="Lifestyle imagery" value={74} color={C.sky} />
          <ExplainBar label="Free-trial offers" value={62} color={C.purple} />
          <ExplainBar label='"Learn more" CTA' value={49} color={C.purple} />
          <ExplainBar label="Product-only images" value={21} color={C.red} />
          <div style={{ marginTop: 14, paddingTop: 14, borderTop: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 11, color: C.dim, marginBottom: 8 }}>Untested space remaining</div>
            <div style={{ height: 6, background: C.border, borderRadius: 99, overflow: "hidden" }}>
              <div style={{ height: "100%", width: "38%", background: `linear-gradient(90deg, ${C.amber}, ${C.amber}88)`, borderRadius: 99 }} />
            </div>
            <div style={{ fontSize: 10, color: C.amber, marginTop: 5 }}>38% of space explored</div>
          </div>
        </div>
      </div>

      {/* Footer — experiment status */}
      <div style={{
        display: "flex", gap: 10, padding: "12px 18px", flexWrap: "wrap",
        borderTop: `1px solid ${C.border}`, background: C.surface,
      }}>
        <ExperimentChip name="Summer Sale" status="converging" metric="CTR 4.8% ↑" />
        <ExperimentChip name="Brand Awareness" status="testing" metric="CTR 2.9% →" />
        <ExperimentChip name="Retargeting" status="new" metric="Starting" />
        <ExperimentChip name="Holiday Push" status="paused" metric="Paused" />
      </div>
    </div>
  );
}
