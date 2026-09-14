"""Shared setup for the checks CI runs without a model.

Two things every test module needs:

  sys.path   `api/` so `main`, `classification`, `prompts` and `tracing`
             import, and `api/evals/` so `scoring` does. The service is run as
             `uvicorn main:app` from `api/`, so there is no package to install.

  env vars   `main.py` reads OPENAI_* at import time via os.environ[...]. On a
             developer machine `load_dotenv("../.env")` supplies them; CI has
             no .env, so importing main there raises KeyError. These are
             deliberately fake - nothing here ever opens a connection, and no
             secret is involved.
"""

import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR / "evals"))

os.environ.setdefault("OPENAI_BASE_URL", "http://127.0.0.1:1/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-a-real-key")
os.environ.setdefault("MODEL", "test-model")
