#!/usr/bin/env python3
"""POST every sample in tickets.json at a running API and show what came back.

Checks only the hard contract - HTTP status, the ticket/non-ticket verdict, and
that every value is inside its enum. The classifications themselves are
judgement calls, so they are printed for a human to read rather than asserted.

    python samples/run.py                         # against localhost:8000
    python samples/run.py http://localhost:8001
"""

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
SAMPLES = json.loads((Path(__file__).parent / "tickets.json").read_text())

CAT = {"billing", "technical", "account", "feedback", "other"}
URG = {"low", "medium", "high"}
SEN = {"positive", "neutral", "frustrated", "angry"}


def post(text):
    req = urllib.request.Request(
        f"{BASE}/classify",
        data=json.dumps({"text": text}).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def contract_violations(status, body, expect):
    """What the endpoint is not allowed to do, regardless of model judgement."""
    bad = []
    if status != expect["status"]:
        bad.append(f"status {status} != {expect['status']}")
    if status != 200:
        return bad
    if set(body) != {"is_support_ticket", "category", "urgency", "sentiment", "requires_human", "prompt_id"}:
        bad.append(f"field set {sorted(body)}")
    pid = body.get("prompt_id", "")
    if not re.fullmatch(r"[a-z_]+@[0-9a-f]{12}", pid):
        bad.append(f"prompt_id {pid!r} not name@digest")
    if body.get("category") not in CAT:
        bad.append(f"category {body.get('category')!r} outside enum")
    if body.get("urgency") not in URG:
        bad.append(f"urgency {body.get('urgency')!r} outside enum")
    if body.get("sentiment") not in SEN:
        bad.append(f"sentiment {body.get('sentiment')!r} outside enum")
    if not isinstance(body.get("requires_human"), bool):
        bad.append("requires_human not a bool")
    if "is_support_ticket" in expect and body.get("is_support_ticket") != expect["is_support_ticket"]:
        bad.append(f"is_support_ticket {body.get('is_support_ticket')} != {expect['is_support_ticket']}")
    return bad


def main():
    failures = 0
    seen_prompts = set()
    for s in SAMPLES:
        status, body = post(s["text"])
        bad = contract_violations(status, body, s["expect"])
        failures += bool(bad)
        mark = "FAIL" if bad else "ok  "
        print(f"[{mark}] {s['name']}")
        if status == 200:
            seen_prompts.add(body.get("prompt_id"))
            t = "TICKET    " if body.get("is_support_ticket") else "NOT-TICKET"
            print(
                f"        {t} {body.get('category'):<10} {body.get('urgency'):<7}"
                f" {body.get('sentiment'):<11} human={body.get('requires_human')}"
            )
        else:
            detail = body.get("detail")
            if isinstance(detail, list) and detail:
                detail = detail[0].get("msg", detail)
            print(f"        HTTP {status} - {str(detail)[:90]}")
        if bad:
            print(f"        VIOLATION: {'; '.join(bad)}")
        print(f"        {s['note']}")
        print()

    total = len(SAMPLES)
    print(f"contract: {total - failures}/{total} samples OK")
    # A run that spans two prompt ids is not comparable with itself - the
    # prompt file changed underneath it.
    for pid in sorted(p for p in seen_prompts if p):
        print(f"prompt:   {pid}")
    if len(seen_prompts - {None}) > 1:
        print("WARNING: results came from more than one prompt - not comparable")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
