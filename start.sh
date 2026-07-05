#!/bin/bash
# start.sh — prints commands for each service. Starts nothing.
# Usage: ./start.sh [staging]   (default: prod)

ENV=${1:-prod}

if [ "$ENV" = "staging" ]; then
  BACKEND_PORT=8001
  FRONTEND_PORT=5174
  BACKEND_CMD="APP_ENV=staging python main.py"
  FRONTEND_CMD="npm run dev -- --mode staging"
else
  BACKEND_PORT=8000
  FRONTEND_PORT=5173
  BACKEND_CMD="python main.py"
  FRONTEND_CMD="npm run dev"
fi

echo ""
echo "=== $ENV ==="
echo ""
echo "  backend   :$BACKEND_PORT   cd backend && source .venv/bin/activate && $BACKEND_CMD"
if [ "$ENV" = "staging" ]; then
  echo "  frontend  :$FRONTEND_PORT   cd frontend && $FRONTEND_CMD"
else
  echo "  frontend  :80/:443   cd frontend && npm run build   (nginx serves dist/ — no restart needed)"
fi
echo ""
echo "  fake ads  :9000"
echo "    Run one of the two cases below, then enable it in backend/.env by"
echo "    uncommenting FAKE_META_BASE_URL and/or FAKE_GOOGLE_BASE_URL."
echo ""
echo "    Case 1 — pre-warmed (default)"
echo "      Fixture ads return random metrics. BO skips warm-start."
echo "      cd fake_ad_server && uvicorn server:app --port 9000 --reload"
echo ""
echo "    Case 2 — cold start + fast demo loop"
echo "      Fixture ads return zero metrics. Warm-start fires on ingest."
echo "      Pushed clones converge after the next ingest cycle."
echo "      Also requires CONVERGENCE_MARGIN_FRACTION=0.80 in backend/.env."
echo "      cd fake_ad_server && COLD_START=true FAST_RAMP=true uvicorn server:app --port 9000 --reload"
echo ""
echo "    To switch cases: change env vars, restart the fake server, then Sync + Ingest in the UI."
echo ""

# Warn about active fake server env vars
ACTIVE=""
if grep -qE '^FAKE_META_BASE_URL=' backend/.env 2>/dev/null; then
  ACTIVE="${ACTIVE}    [ON] FAKE_META_BASE_URL    — Meta data routed to :9000\n"
fi
if grep -qE '^FAKE_GOOGLE_BASE_URL=' backend/.env 2>/dev/null; then
  ACTIVE="${ACTIVE}    [ON] FAKE_GOOGLE_BASE_URL  — Google data routed to :9000\n"
fi

if [ -n "$ACTIVE" ]; then
  echo "  Active fake-server vars in backend/.env:"
  printf "$ACTIVE"
  echo ""
fi

echo "  Other env:  ./start.sh $([ "$ENV" = "prod" ] && echo staging || echo prod)"
echo ""
