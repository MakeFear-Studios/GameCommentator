import sys
from pathlib import Path

# Make `import commentator` work when running plain `pytest` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
