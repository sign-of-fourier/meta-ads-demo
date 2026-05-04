import sys
from pathlib import Path

# Make backend/ importable so test files can do `from main import ...`,
# `from bo_pipeline import ...`, etc. without modification.
sys.path.insert(0, str(Path(__file__).parent.parent))
