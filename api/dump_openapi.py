#!/usr/bin/env python3
"""Write the API's OpenAPI schema to openapi.json.

    python dump_openapi.py            # write it
    python dump_openapi.py --check    # fail if the committed file is stale

This is the hinge of the contract. FastAPI derives the schema from the
pydantic models, the front end derives its TypeScript from this file, so the
models are the one place a shape is written down. The file is committed
because the web build has no Python.

No server and no model are needed: importing the app is enough.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "openapi.json"

# main.py reads OPENAI_* at import. Values are irrelevant - nothing is called.
import os  # noqa: E402

os.environ.setdefault("OPENAI_BASE_URL", "http://127.0.0.1:1/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-a-real-key")
os.environ.setdefault("MODEL", "schema-dump")

sys.path.insert(0, str(HERE))
from main import app  # noqa: E402


def schema_text() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if openapi.json is out of date")
    args = ap.parse_args()

    fresh = schema_text()
    if args.check:
        current = OUT.read_text() if OUT.exists() else ""
        if current != fresh:
            print("openapi.json is out of date. Run:\n"
                  "    cd api && python dump_openapi.py\n"
                  "    cd web && npm run gen:api", file=sys.stderr)
            return 1
        print("openapi.json is current")
        return 0

    OUT.write_text(fresh)
    print(f"wrote {OUT.relative_to(HERE.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
