export default function LandingPage() {
  return (
    <div
      dangerouslySetInnerHTML={{
        __html: `
<section style="font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color:#0f172a; background:#0b1220; min-height:100vh; padding:60px 20px 80px; box-sizing:border-box;">
  <div style="max-width:1040px; margin:0 auto;">

    <!-- Badge -->
    <div style="display:flex; align-items:center; gap:10px; margin-bottom:16px;">
      <span style="font-size:12px; letter-spacing:0.12em; text-transform:uppercase; color:#38bdf8; background:rgba(56,189,248,0.1); border-radius:999px; padding:4px 10px;">
        Built for performance media buyers
      </span>
    </div>

    <!-- Hero copy -->
    <h1 style="font-size: clamp(32px, 5vw, 44px); line-height:1.1; font-weight:700; color:#e5e7eb; margin:0 0 16px;">
      Self‑learning Meta ads, powered by ad embeddings.
    </h1>

    <p style="max-width:640px; font-size:16px; line-height:1.5; color:#9ca3af; margin:0 0 24px;">
      Not another rules engine. AdStackr builds a memory of every creative, angle, and offer you test, then uses that understanding to shift budget, suggest new ideas, and recover an extra 10–20% performance from the same test spend.
    </p>

    <!-- CTA row -->
    <div style="display:flex; flex-wrap:wrap; gap:12px; margin-bottom:40px;">
      <a href="/app/auth" style="display:inline-block; text-decoration:none; background:#38bdf8; color:#0b1120; font-weight:600; font-size:14px; padding:10px 18px; border-radius:999px;">
        Connect a Meta ad account
      </a>
      <button style="border:1px solid #4b5563; cursor:pointer; background:transparent; color:#e5e7eb; font-size:14px; padding:10px 16px; border-radius:999px;">
        Watch 3‑minute product tour
      </button>
      <span style="font-size:12px; color:#6b7280; align-self:center;">
        No credit card required · Read‑only by default
      </span>
    </div>

    <!-- 3 value props -->
    <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:18px; margin-bottom:40px;">
      <!-- Cold start -->
      <div style="background:#020617; border-radius:16px; padding:18px; border:1px solid #1f2937;">
        <h2 style="font-size:16px; font-weight:600; color:#e5e7eb; margin:0 0 8px;">
          Cold starts that don’t start from zero
        </h2>
        <p style="font-size:14px; color:#9ca3af; margin:0;">
          AdStackr uses ad embeddings and past test history so new campaigns don’t launch blind. It recognizes similar ideas you’ve already run and borrows signal, helping you skip obvious losers and get to workable concepts faster.
        </p>
      </div>

      <!-- Cannibalization -->
      <div style="background:#020617; border-radius:16px; padding:18px; border:1px solid #1f2937;">
        <h2 style="font-size:16px; font-weight:600; color:#e5e7eb; margin:0 0 8px;">
          Stop strong ads from cannibalizing better ideas
        </h2>
        <p style="font-size:14px; color:#9ca3af; margin:0;">
          Meta loves to over‑feed an early “winner,” even when it’s just riding noisy data. AdStackr watches for cannibalization inside your campaigns and protects exploration, so one ad can’t hog most of the spend while only barely beating everything else.
        </p>
      </div>

      <!-- Fatigue -->
      <div style="background:#020617; border-radius:16px; padding:18px; border:1px solid #1f2937;">
        <h2 style="font-size:16px; font-weight:600; color:#e5e7eb; margin:0 0 8px;">
          Catch creative fatigue before results fall off
        </h2>
        <p style="font-size:14px; color:#9ca3af; margin:0;">
          AdStackr tracks performance over time at the creative level, so you can see when an ad is truly wearing out versus just having a bad day. It flags fatigued winners and helps you rotate or refresh them before they quietly drain your ROAS.
        </p>
      </div>
    </div>

    <!-- Dashboard screenshot -->
    <div style="border-radius:24px; overflow:hidden; border:1px solid #1f2937; box-shadow:0 24px 80px rgba(15,23,42,0.8); background:#020617;">
      <img
        src="/adstackr-dashboard.png"
        alt="AdStackr Meta ads dashboard"
        style="display:block; width:100%; height:auto;"
      />
    </div>

  </div>
</section>

`,
      }}
    />
  );
}

