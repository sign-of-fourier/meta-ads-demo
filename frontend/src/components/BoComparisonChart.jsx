const DATA = [
  { round: 1,  trials: 20, bo: 5.0902, boStd: 0.2355, rand: 5.1235, randStd: 0.231,  bench: 5.175  },
  { round: 2,  trials: 24, bo: 5.1992, boStd: 0.2189, rand: 5.1553, randStd: 0.2095, bench: 5.2667 },
  { round: 3,  trials: 28, bo: 5.2159, boStd: 0.1941, rand: 5.1553, randStd: 0.2095, bench: 5.3    },
  { round: 4,  trials: 32, bo: 5.2576, boStd: 0.1969, rand: 5.2053, randStd: 0.2754, bench: 5.35   },
  { round: 5,  trials: 36, bo: 5.3008, boStd: 0.2067, rand: 5.2447, randStd: 0.2633, bench: 5.4098 },
  { round: 6,  trials: 40, bo: 5.3341, boStd: 0.168,  rand: 5.2795, randStd: 0.2415, bench: 5.4333 },
  { round: 7,  trials: 44, bo: 5.35,   boStd: 0.15,   rand: 5.3053, randStd: 0.2322, bench: 5.4917 },
  { round: 8,  trials: 48, bo: 5.35,   boStd: 0.15,   rand: 5.3242, randStd: 0.238,  bench: 5.4917 },
  { round: 9,  trials: 52, bo: 5.3652, boStd: 0.1201, rand: 5.3258, randStd: 0.2371, bench: 5.5667 },
  { round: 10, trials: 56, bo: 5.3652, boStd: 0.1201, rand: 5.3258, randStd: 0.2371, bench: 5.5667 },
];

const W = 600, H = 310;
const PAD = { top: 50, right: 30, bottom: 54, left: 56 };
const CW = W - PAD.left - PAD.right;
const CH = H - PAD.top - PAD.bottom;
const Y_MIN = 4.98, Y_MAX = 5.68;

const sy = (v) => PAD.top + CH - ((v - Y_MIN) / (Y_MAX - Y_MIN)) * CH;
const sx = (i) => PAD.left + (i / (DATA.length - 1)) * CW;
const clampY = (v) => Math.max(PAD.top, Math.min(PAD.top + CH, sy(v)));

const linePath = (key) =>
  DATA.map((d, i) => `${i === 0 ? "M" : "L"} ${sx(i).toFixed(1)} ${sy(d[key]).toFixed(1)}`).join(" ");

const bandPath = (avgKey, stdKey) => {
  const upper = DATA.map((d, i) =>
    `${i === 0 ? "M" : "L"} ${sx(i).toFixed(1)} ${clampY(d[avgKey] + d[stdKey]).toFixed(1)}`
  ).join(" ");
  const lower = DATA.slice().reverse().map((d, i) =>
    `L ${sx(DATA.length - 1 - i).toFixed(1)} ${clampY(d[avgKey] - d[stdKey]).toFixed(1)}`
  ).join(" ");
  return `${upper} ${lower} Z`;
};

const Y_TICKS = [5.0, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6];

export default function BoComparisonChart() {
  const boLine   = linePath("bo");
  const randLine = linePath("rand");
  const benchLine = linePath("bench");
  const boBand   = bandPath("bo",   "boStd");
  const randBand = bandPath("rand", "randStd");

  const lastI = DATA.length - 1;
  const finalX    = sx(lastI);
  const finalBoY  = sy(DATA[lastI].bo);
  const finalRandY = sy(DATA[lastI].rand);
  const finalBenchY = sy(DATA[lastI].bench);

  // gap bracket between BO-512 and BO-4096 at final round
  const bracketX = finalX + 14;
  const gapMidY = (finalBoY + finalBenchY) / 2;

  return (
    <div style={{ width: "100%" }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: "100%", height: "auto", display: "block" }}
        aria-label="BO batch 512 vs random search vs BO batch 4096 on ad creative benchmark"
      >
        <defs>
          <linearGradient id="boGrad2" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.18" />
            <stop offset="100%" stopColor="#38bdf8" stopOpacity="0.03" />
          </linearGradient>
          <linearGradient id="randGrad2" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f87171" stopOpacity="0.12" />
            <stop offset="100%" stopColor="#f87171" stopOpacity="0.02" />
          </linearGradient>
          {/* Highlight band between BO-512 and BO-4096 */}
          <linearGradient id="gapGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#4ade80" stopOpacity="0.12" />
            <stop offset="100%" stopColor="#4ade80" stopOpacity="0.03" />
          </linearGradient>
        </defs>

        {/* Grid lines */}
        {Y_TICKS.map(v => (
          <line key={v}
            x1={PAD.left} x2={PAD.left + CW}
            y1={sy(v).toFixed(1)} y2={sy(v).toFixed(1)}
            stroke="#1e2d45" strokeWidth="1"
            strokeDasharray="3 4"
          />
        ))}

        {/* Gap fill between BO-512 and BO-4096 — the advantage zone */}
        {(() => {
          const topPath = DATA.map((d, i) =>
            `${i === 0 ? "M" : "L"} ${sx(i).toFixed(1)} ${sy(d.bench).toFixed(1)}`
          ).join(" ");
          const botPath = DATA.slice().reverse().map((d, i) =>
            `L ${sx(DATA.length - 1 - i).toFixed(1)} ${sy(d.bo).toFixed(1)}`
          ).join(" ");
          return <path d={`${topPath} ${botPath} Z`} fill="url(#gapGrad)" />;
        })()}

        {/* Confidence bands */}
        <path d={randBand} fill="url(#randGrad2)" />
        <path d={boBand}   fill="url(#boGrad2)" />

        {/* Lines — drawn in order: random behind, BO-512 middle, BO-4096 front */}
        <path d={randLine}  fill="none" stroke="#f87171" strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
        <path d={boLine}    fill="none" stroke="#38bdf8" strokeWidth="2"   strokeLinejoin="round" strokeLinecap="round" />
        <path d={benchLine} fill="none" stroke="#4ade80" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />

        {/* Data dots */}
        {DATA.map((d, i) => (
          <g key={i}>
            <circle cx={sx(i).toFixed(1)} cy={sy(d.bench).toFixed(1)} r="3.5" fill="#4ade80" />
            <circle cx={sx(i).toFixed(1)} cy={sy(d.bo).toFixed(1)}    r="2.5" fill="#38bdf8" />
          </g>
        ))}

        {/* "AdStackers operates here" callout on the 4096 line */}
        {(() => {
          const labelX = sx(4); // round 5
          const labelY = sy(DATA[4].bench) - 22;
          return (
            <g>
              <line
                x1={sx(4).toFixed(1)} y1={(sy(DATA[4].bench) - 4).toFixed(1)}
                x2={sx(4).toFixed(1)} y2={(labelY + 16).toFixed(1)}
                stroke="#4ade80" strokeWidth="1" strokeDasharray="2 2" strokeOpacity="0.6"
              />
              <rect x={(labelX - 54).toFixed(1)} y={(labelY - 2).toFixed(1)} width="108" height="18" rx="4"
                fill="#0b1e14" stroke="#4ade8044" strokeWidth="1" />
              <text x={labelX.toFixed(1)} y={(labelY + 11).toFixed(1)}
                fill="#4ade80" fontSize="10" fontWeight="700" textAnchor="middle">
                AdStackers operates here
              </text>
            </g>
          );
        })()}

        {/* Gap bracket at right edge */}
        <line
          x1={bracketX.toFixed(1)} x2={bracketX.toFixed(1)}
          y1={finalBoY.toFixed(1)} y2={finalBenchY.toFixed(1)}
          stroke="#4ade8066" strokeWidth="1"
        />
        <line x1={(bracketX - 4).toFixed(1)} x2={(bracketX + 4).toFixed(1)}
          y1={finalBoY.toFixed(1)} y2={finalBoY.toFixed(1)} stroke="#4ade8066" strokeWidth="1" />
        <line x1={(bracketX - 4).toFixed(1)} x2={(bracketX + 4).toFixed(1)}
          y1={finalBenchY.toFixed(1)} y2={finalBenchY.toFixed(1)} stroke="#4ade8066" strokeWidth="1" />
        <text x={(bracketX + 7).toFixed(1)} y={(gapMidY + 4).toFixed(1)}
          fill="#4ade80" fontSize="9" fontWeight="600">4096 edge</text>

        {/* Y axis ticks + labels */}
        {Y_TICKS.map(v => (
          <text key={v}
            x={(PAD.left - 8).toFixed(1)} y={(sy(v) + 4).toFixed(1)}
            fill="#475569" fontSize="10" textAnchor="end" fontVariantNumeric="tabular-nums"
          >{v.toFixed(1)}</text>
        ))}

        {/* X axis labels */}
        {DATA.map((d, i) => (
          <text key={i}
            x={sx(i).toFixed(1)} y={(PAD.top + CH + 16).toFixed(1)}
            fill="#475569" fontSize="10" textAnchor="middle"
          >{d.trials}</text>
        ))}

        {/* Axis labels */}
        <text
          x={(PAD.left + CW / 2).toFixed(1)} y={(H - 6).toFixed(1)}
          fill="#64748b" fontSize="11" textAnchor="middle"
        >Ads evaluated (trials)</text>
        <text
          x="13" y={(PAD.top + CH / 2).toFixed(1)}
          fill="#64748b" fontSize="11" textAnchor="middle"
          transform={`rotate(-90, 13, ${(PAD.top + CH / 2).toFixed(1)})`}
        >Avg. creative score</text>

        {/* Legend — top, three rows */}
        {[
          { color: "#f87171", label: "Random search", sub: "no learning — what most teams do" },
          { color: "#38bdf8", label: "BO · batch 512",  sub: "learns each round — already hard to run" },
          { color: "#4ade80", label: "BO · batch 4096", sub: "AdStackers — 8× more candidates scored" },
        ].map(({ color, label, sub }, i) => (
          <g key={i} transform={`translate(${PAD.left + i * 185}, 8)`}>
            <rect x="0" y="4" width="12" height="3" rx="1.5" fill={color} />
            <text x="17" y="11" fill="#e2e8f0" fontSize="11" fontWeight="600">{label}</text>
            <text x="17" y="22" fill="#475569" fontSize="9">{sub}</text>
          </g>
        ))}
      </svg>

      <p style={{ fontSize: 11, color: "#334155", marginTop: 6, textAlign: "right" }}>
        CHI-BAD-ADS dataset · 10 rounds
      </p>
    </div>
  );
}
