# fake_ad_server/server.py
#
# Standalone fake ad server that mimics the Meta Graph API and Google Ads REST API.
# Run with:
#   cd fake_ad_server
#   pip install -r requirements.txt
#   uvicorn server:app --port 9000 --reload
#
# Then in backend/.env uncomment:
#   FAKE_META_BASE_URL=http://localhost:9000/meta/v19.0
#   FAKE_GOOGLE_BASE_URL=http://localhost:9000/google
#
# Auth (OAuth) is NOT handled here — it still goes to real Google/Meta.
# Only data API calls are intercepted.

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.meta import router as meta_router
from routes.google import router as google_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="Fake Ad Server",
    description=(
        "Local stub for Meta Graph API and Google Ads REST API. "
        "Returns fixture data so the real backend provider code can run "
        "end-to-end without touching live platforms."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount under /meta and /google — mirrors what the backend base-URL env vars point at
app.include_router(meta_router, prefix="/meta")
app.include_router(google_router, prefix="/google")


@app.get("/")
async def health():
    return {
        "status": "ok",
        "meta_prefix": "/meta",
        "google_prefix": "/google",
        "note": "Set FAKE_META_BASE_URL=http://localhost:9000/meta/v19.0 and "
                "FAKE_GOOGLE_BASE_URL=http://localhost:9000/google in backend/.env",
    }
