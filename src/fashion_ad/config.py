import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Minimal .env loader: real environment variables win.
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

DATA_DIR = Path(os.environ.get("FASHION_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "fashion.duckdb"

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "")  # e.g. "none" for local thinking models
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
