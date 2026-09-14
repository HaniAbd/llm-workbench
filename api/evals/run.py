#!/usr/bin/env python3
"""Score the classifier against evals/cases.json and compare with earlier runs.

    python evals/run.py                      # full set, ~50s
    python evals/run.py --group arguable     # one group, for a fast loop
    python evals/run.py --api http://localhost:8001
    python evals/run.py --history            # past runs, no calls made

Produces one number per run plus a breakdown by field and by group. A run is
appended to evals/runs.jsonl, so comparing against last time needs no
bookkeeping.

Three things decide whether two runs mean the same thing:

  scorer_digest   how credit is computed. If it differs, the numbers are not
                  comparable and no comparison is offered - this is the whole
                  point of hashing the rules rather than trusting a version
                  number someone has to remember to bump.
  dataset_digest  which cases exist. Adding cases does NOT block a comparison;
                  the delta is computed over the cases both runs share, and
                  the report says how many that was.
  prompt_id       which prompt produced the answers. A difference here is the
                  comparison you actually want, so it is reported loudly
                  rather than refused.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import scoring  # noqa: E402

DATA = json.loads((HERE / "cases.json").read_text())
RUNS_PATH = HERE / "runs.jsonl"

# Notes and prose are excluded so that rewording a comment does not invalidate
# a comparison; only what is actually asked and accepted counts.
DATASET_DIGEST = scoring.digest(
    [
        {"id": c["id"], "text": c["text"], "expect": c["expect"],
         "accepted_failures": sorted(c.get("accepted_failures", []))}
        for c in sorted(DATA["cases"], key=lambda c: c["id"])
    ]
)


def call(api: str, text: str):
    req = urllib.request.Request(
        f"{api}/classify",
        data=json.dumps({"text": text}).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def mean(xs):
    xs = list(xs)
    return round(sum(xs) / len(xs), 4) if xs else None


def run_once(api: str, cases: list) -> dict:
    started = time.time()
    results, prompt_ids = {}, set()

    for i, case in enumerate(cases, 1):
        status, body = call(api, case["text"])
        if isinstance(body, dict) and body.get("prompt_id"):
            prompt_ids.add(body["prompt_id"])

        observed = {"status": status, **(body if isinstance(body, dict) else {})}
        fields = scoring.score_case(case["expect"], observed)

        # A wrong status makes everything downstream meaningless rather than
        # merely wrong, so it is not averaged in alongside a near-miss.
        if fields.get("status") == 0.0:
            fields = {k: 0.0 for k in fields}

        results[case["id"]] = {
            "group": case["group"],
            "fields": fields,
            "score": mean(fields.values()),
            "observed": {
                k: observed.get(k)
                for k in ("status", "is_support_ticket", "category", "urgency",
                          "sentiment", "requires_human")
            },
        }
        print(f"\r  {i}/{len(cases)} {case['id'][:44]:<44}", end="", file=sys.stderr)
    print("\r" + " " * 60 + "\r", end="", file=sys.stderr)

    per_field = {}
    for f in scoring.SCORED_FIELDS:
        vals = [r["fields"][f] for r in results.values() if f in r["fields"]]
        if vals:
            per_field[f] = mean(vals)

    groups = sorted({r["group"] for r in results.values()})
    per_group = {
        g: mean(r["score"] for r in results.values() if r["group"] == g) for g in groups
    }

    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "score": mean(r["score"] for r in results.values()),
        "prompt_id": sorted(prompt_ids)[0] if len(prompt_ids) == 1 else None,
        "prompt_ids_seen": sorted(prompt_ids),
        "scorer_digest": scoring.SCORER_DIGEST,
        "dataset_digest": DATASET_DIGEST,
        "case_count": len(results),
        "elapsed_s": round(time.time() - started, 1),
        "by_field": per_field,
        "by_group": per_group,
        "cases": results,
    }


def classify_misses(run: dict, cases: list, baseline):
    """Sort imperfect fields into buckets that mean different things.

    accepted     already blessed in cases.json - never an alarm
    regression   passing in the baseline and failing now, or failing by more
                 than it did. This is the bucket that should be empty.
    outstanding  failing, not accepted, and no worse than the baseline. Known
                 bad. Reported so it is visible, kept apart so it cannot
                 drown out a regression.
    fixed        an accepted failure that now passes - the acceptance is stale
    """
    by_id = {c["id"]: c for c in cases}
    accepted, regression, outstanding, fixed = [], [], [], []

    for cid, res in run["cases"].items():
        ok_list = by_id[cid].get("accepted_failures", [])
        base = baseline["cases"].get(cid) if baseline else None

        for field, sc in res["fields"].items():
            if sc == 1.0:
                continue
            entry = (cid, field, sc, res["observed"].get(field))
            prev = base["fields"].get(field) if base else None
            if field in ok_list:
                accepted.append(entry)
            elif prev is None:
                outstanding.append(entry)
            elif sc < prev:
                regression.append((*entry, prev))
            elif prev < 1.0:
                outstanding.append(entry)
            else:
                regression.append((*entry, prev))

        for field in ok_list:
            if res["fields"].get(field) == 1.0:
                fixed.append((cid, field))
    return accepted, regression, outstanding, fixed


def find_baseline(run: dict):
    """Most recent earlier run whose score means the same thing."""
    if not RUNS_PATH.exists():
        return None, "no earlier runs"
    prior = [json.loads(l) for l in RUNS_PATH.read_text().splitlines() if l.strip()]
    if not prior:
        return None, "no earlier runs"
    same_scorer = [p for p in prior if p["scorer_digest"] == run["scorer_digest"]]
    if not same_scorer:
        return None, (
            f"scoring changed (digest {prior[-1]['scorer_digest']} -> "
            f"{run['scorer_digest']}); earlier scores are not comparable"
        )
    return same_scorer[-1], None


def report(run: dict, cases: list, baseline, why_not) -> int:
    accepted, regression, outstanding, fixed = classify_misses(run, cases, baseline)

    print(f"\nscore  {run['score']:.3f}   ({run['case_count']} cases, {run['elapsed_s']}s)")
    print(f"prompt {run['prompt_id'] or 'MIXED: ' + ', '.join(run['prompt_ids_seen'])}")
    print(f"scorer {run['scorer_digest']}   dataset {run['dataset_digest']}")

    print("\nby field")
    for f, v in run["by_field"].items():
        print(f"  {f:<20} {v:.3f}")
    print("by group")
    for g, v in run["by_group"].items():
        print(f"  {g:<20} {v:.3f}")

    if baseline:
        shared = sorted(set(run["cases"]) & set(baseline["cases"]))
        a = mean(run["cases"][c]["score"] for c in shared)
        b = mean(baseline["cases"][c]["score"] for c in shared)
        delta = a - b
        print(f"\nvs {baseline['at']}  ({len(shared)} shared cases)")
        if baseline["prompt_id"] != run["prompt_id"]:
            print(f"  ACROSS PROMPTS  {baseline['prompt_id']} -> {run['prompt_id']}")
        else:
            print(f"  same prompt ({run['prompt_id']}) - differences are model noise")
        if len(shared) < baseline["case_count"] or len(shared) < run["case_count"]:
            print(f"  dataset changed: baseline had {baseline['case_count']},"
                  f" this run {run['case_count']}; compared on the overlap only")
        print(f"  {b:.3f} -> {a:.3f}   {delta:+.3f}")

        moved = [
            (c, run["cases"][c]["score"] - baseline["cases"][c]["score"])
            for c in shared
            if abs(run["cases"][c]["score"] - baseline["cases"][c]["score"]) > 1e-9
        ]
        for cid, d in sorted(moved, key=lambda m: m[1]):
            print(f"    {'WORSE' if d < 0 else 'better'} {d:+.3f}  {cid}")
        if not moved:
            print("    no case changed")
    else:
        print(f"\nno comparison: {why_not}")

    if fixed:
        print("\naccepted failures that now pass (consider removing the acceptance)")
        for cid, field in fixed:
            print(f"  {cid}.{field}")

    print(f"\naccepted failures: {len(accepted)}   (blessed in cases.json)")
    for cid, field, sc, got in accepted:
        print(f"  {cid}.{field} = {sc} (got {got!r})")

    if baseline:
        print(f"\nREGRESSIONS: {len(regression)}   (worse than the baseline)")
        for cid, field, sc, got, prev in sorted(regression, key=lambda r: r[2] - r[4]):
            print(f"  {cid}.{field}  {prev} -> {sc} (got {got!r})")
    else:
        print("\nREGRESSIONS: n/a   (no baseline yet - nothing can be new)")

    label = "outstanding" if baseline else "failing, not accepted"
    print(f"\n{label}: {len(outstanding)}")
    for cid, field, sc, got in sorted(outstanding, key=lambda u: u[2]):
        print(f"  {cid}.{field} = {sc} (got {got!r})")

    return 1 if regression else 0


def show_history() -> int:
    if not RUNS_PATH.exists():
        print("no runs yet")
        return 0
    for line in RUNS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        print(f"{r['at']}  {r['score']:.3f}  {r['case_count']:>3} cases  "
              f"scorer {r['scorer_digest']}  {r['prompt_id']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--group", help="only run one group (clean, arguable, junk, injection, rejected)")
    ap.add_argument("--history", action="store_true", help="list past runs and exit")
    ap.add_argument("--no-save", action="store_true", help="do not append to runs.jsonl")
    args = ap.parse_args()

    if args.history:
        return show_history()

    cases = DATA["cases"]
    if args.group:
        cases = [c for c in cases if c["group"] == args.group]
        if not cases:
            print(f"no cases in group {args.group!r}")
            return 2

    run = run_once(args.api, cases)
    baseline, why_not = find_baseline(run)
    # A subset run is not a fair baseline for a later full run, so it is scored
    # and reported but never written to the history.
    if not args.no_save and not args.group:
        with RUNS_PATH.open("a") as fh:
            fh.write(json.dumps(run) + "\n")
    return report(run, cases, baseline, why_not)


if __name__ == "__main__":
    sys.exit(main())
