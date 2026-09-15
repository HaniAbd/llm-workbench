"""The contract between the API and the front end has one source.

`api/tracing.py` and the other pydantic models define the shapes. FastAPI
derives `openapi.json` from them; the front end derives its TypeScript from
that file. These tests guard the first link - that the committed schema is
current. The second link is guarded by `npm run check:api` in the web build,
which is where Node lives.
"""

import json
import subprocess
import sys
from pathlib import Path

import tracing

API_DIR = Path(__file__).resolve().parent.parent
OPENAPI = API_DIR / "openapi.json"


def test_openapi_is_committed():
    assert OPENAPI.exists(), "run: python dump_openapi.py"


def test_openapi_is_current():
    """Fails when a model changed and the schema was not regenerated.

    Without this, a rename would reach neither the schema nor the generated
    TypeScript, and every test would still pass.
    """
    result = subprocess.run(
        [sys.executable, str(API_DIR / "dump_openapi.py"), "--check"],
        capture_output=True, text=True, cwd=API_DIR,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_trace_document_is_published_in_the_schema():
    schemas = json.loads(OPENAPI.read_text())["components"]["schemas"]
    assert "TraceDocument" in schemas
    published = set(schemas["TraceDocument"]["properties"])
    assert published == set(tracing.TraceDocument.model_fields)


def test_published_event_stays_open_for_new_kinds():
    """Step 6 adds tool calls and agent steps as new event kinds. They must
    need no schema change, which means the published event has to admit
    fields it does not name."""
    schemas = json.loads(OPENAPI.read_text())["components"]["schemas"]
    assert schemas["TraceEvent"]["additionalProperties"] is True


def test_both_responses_carry_the_typed_trace():
    schemas = json.loads(OPENAPI.read_text())["components"]["schemas"]
    for response in ("AskResponse", "ClassificationResponse"):
        ref = schemas[response]["properties"]["trace"]
        assert "TraceDocument" in json.dumps(ref), f"{response}.trace is untyped"
