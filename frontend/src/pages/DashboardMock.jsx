// src/pages/DashboardMock.jsx
export default function DashboardMock() {
  return (
    <div
      style={{
        fontFamily:
          "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        background: "#020617",
        minHeight: "100vh",
        padding: "32px",
        color: "#e5e7eb",
      }}
    >
      {/* Top bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "24px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: 999,
              background:
                "radial-gradient(circle at 30% 30%, #38bdf8, #6366f1)",
            }}
          />
          <span style={{ fontWeight: 600 }}>AdStackr</span>
        </div>
        <div style={{ display: "flex", gap: "12px", fontSize: 12 }}>
          <span style={{ color: "#9ca3af" }}>Account</span>
          <span
            style={{
              padding: "4px 10px",
              borderRadius: 999,
              backgroundColor: "#111827",
              border: "1px solid #1f2937",
            }}
          >
            Meta · 1234567890
          </span>
          <span style={{ color: "#9ca3af" }}>Last 7 days</span>
        </div>
      </div>

      {/* Summary cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
          gap: "16px",
          marginBottom: "24px",
        }}
      >
        {[
          { label: "Spend", value: "$4,320", delta: "+12%" },
          { label: "Revenue", value: "$18,900", delta: "+19%" },
          { label: "ROAS", value: "4.37x", delta: "+6%" },
          { label: "CPA", value: "$23.40", delta: "-14%" },
        ].map((card) => (
          <div
            key={card.label}
            style={{
              backgroundColor: "#020617",
              borderRadius: 16,
              padding: "14px 16px",
              border: "1px solid #1f2937",
            }}
          >
            <div style={{ fontSize: 11, color: "#9ca3af", marginBottom: 4 }}>
              {card.label}
            </div>
            <div style={{ fontSize: 18, fontWeight: 600 }}>{card.value}</div>
            <div
              style={{
                fontSize: 11,
                color: card.delta.startsWith("-") ? "#f97373" : "#4ade80",
                marginTop: 4,
              }}
            >
              {card.delta} vs. prior period
            </div>
          </div>
        ))}
      </div>

      {/* Campaigns + right rail */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 2.4fr) minmax(260px, 1fr)",
          gap: "20px",
        }}
      >
        {/* Campaigns table */}
        <div
          style={{
            backgroundColor: "#020617",
            borderRadius: 16,
            border: "1px solid #1f2937",
            padding: "12px 0 8px",
          }}
        >
          <div
            style={{
              padding: "0 16px 8px",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600 }}>Campaigns</div>
            <div style={{ fontSize: 11, color: "#9ca3af", display: "flex", gap: 8 }}>
              <span>All campaigns</span>
              <span>·</span>
              <span>Last 7 days</span>
            </div>
          </div>

          <div
            style={{
              fontSize: 11,
              color: "#9ca3af",
              display: "grid",
              gridTemplateColumns: "1.7fr 0.7fr 0.8fr 0.9fr 0.8fr 0.7fr",
              padding: "6px 16px",
              borderTop: "1px solid #111827",
              borderBottom: "1px solid #111827",
            }}
          >
            <div>Name</div>
            <div>Status</div>
            <div>Spend</div>
            <div>Revenue</div>
            <div>ROAS</div>
            <div style={{ textAlign: "right" }}>Action</div>
          </div>

          {[
            {
              name: "New Hook · UGC (US)",
              status: "On",
              spend: "$1,280",
              rev: "$6,540",
              roas: "5.11x",
              tag: "Scaling",
            },
            {
              name: "Retargeting · 30D ATC",
              status: "On",
              spend: "$920",
              rev: "$4,210",
              roas: "4.58x",
              tag: "Stable",
            },
            {
              name: "Prospecting · Broad V2",
              status: "Off",
              spend: "$640",
              rev: "$1,430",
              roas: "2.23x",
              tag: "Fatigued",
            },
            {
              name: "Creative Test · Q2 hooks",
              status: "On",
              spend: "$320",
              rev: "$720",
              roas: "2.25x",
              tag: "Exploring",
            },
          ].map((row, i) => (
            <div
              key={row.name}
              style={{
                display: "grid",
                gridTemplateColumns:
                  "1.7fr 0.7fr 0.8fr 0.9fr 0.8fr 0.7fr",
                padding: "10px 16px",
                fontSize: 12,
                borderBottom:
                  i === 3 ? "none" : "1px solid rgba(15,23,42,0.8)",
                alignItems: "center",
              }}
            >
              <div style={{ display: "flex", flexDirection: "column" }}>
                <span>{row.name}</span>
                <span style={{ fontSize: 10, color: "#6b7280" }}>
                  {row.tag}
                </span>
              </div>
              <div>
                <span
                  style={{
                    fontSize: 11,
                    padding: "2px 8px",
                    borderRadius: 999,
                    backgroundColor:
                      row.status === "On" ? "#022c22" : "#111827",
                    color: row.status === "On" ? "#6ee7b7" : "#f97373",
                  }}
                >
                  {row.status}
                </span>
              </div>
              <div>{row.spend}</div>
              <div>{row.rev}</div>
              <div>{row.roas}</div>
              <div style={{ textAlign: "right" }}>
                <button
                  style={{
                    fontSize: 11,
                    padding: "4px 10px",
                    borderRadius: 999,
                    border: "1px solid #374151",
                    background: "transparent",
                    color: "#e5e7eb",
                    cursor: "pointer",
                  }}
                >
                  {row.status === "On" ? "Pause" : "Resume"}
                </button>
              </div>
            </div>
          ))}
        </div>

        {/* Right rail: “self-learning” explainer */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div
            style={{
              backgroundColor: "#020617",
              borderRadius: 16,
              border: "1px solid #1f2937",
              padding: "14px 16px",
              fontSize: 12,
            }}
          >
            <div style={{ fontSize: 11, color: "#9ca3af", marginBottom: 4 }}>
              Self-learning summary
            </div>
            <div style={{ marginBottom: 8 }}>
              Ad embeddings suggest increasing budget on{" "}
              <strong>New Hook · UGC (US)</strong>. Probability of beating
              account ROAS: <span style={{ color: "#4ade80" }}>82%</span>.
            </div>
            <div style={{ fontSize: 11, color: "#9ca3af" }}>
              Protecting exploration on{" "}
              <span style={{ color: "#e5e7eb" }}>
                Creative Test · Q2 hooks
              </span>{" "}
              to avoid early false winners.
            </div>
          </div>

          <div
            style={{
              backgroundColor: "#020617",
              borderRadius: 16,
              border: "1px solid #1f2937",
              padding: "14px 16px",
              fontSize: 12,
            }}
          >
            <div style={{ fontSize: 11, color: "#9ca3af", marginBottom: 4 }}>
              Fatigue & cannibalization
            </div>
            <ul style={{ paddingLeft: 16, margin: 0, color: "#d1d5db" }}>
              <li style={{ marginBottom: 4 }}>
                <strong>Prospecting · Broad V2</strong> is showing signs of
                creative fatigue. Suggest rotating in 2 fresh variants.
              </li>
              <li>
                <strong>Retargeting · 30D ATC</strong> is cannibalizing spend
                from other retargeting tests with only a small ROAS edge.
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

