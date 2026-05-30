import sys
from pathlib import Path

# Make backend/ importable so test files can do `from main import ...`,
# `from bo_pipeline import ...`, etc. without modification.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load backend/.env so env vars (MODAL_BO_API_URL, OPENAI_KEY, etc.) are
# available to all tests without needing to import main.py first.
# Tests that don't need any of these vars are unaffected.
from dotenv import load_dotenv  # noqa: E402
load_dotenv(Path(__file__).parent.parent / ".env")
