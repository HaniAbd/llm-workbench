#!/usr/bin/env python3
"""Score the classifier against evals/cases.json and compare with earlier runs.

    python evals/run.py                      # full set, ~50s
    python evals/run.py --group arguable     # one group, for a fast loop
    python evals/run.py --api http://localhost:8001
    python evals/run.py --history            # past runs, no calls made
    python evals/run.py --set-baseline       # pin the latest run as reference
    python evals/run.py --clear-baseline     # unpin

Every run is compared twice, and the two answer different questions:

  vs REFERENCE     a run you pinned, and it stays pinned until you change it.
                   Regressions are judged against this, so restoring a
                   known-good prompt does not read as a regression merely
                   because the run before it happened to score higher.
  vs previous run  whatever ran last. Answers "what did the change I just
                   made do", which is the question during a tight loop.

With nothing pinned, regressions fall back to the previous run - the old
behaviour - and the report says so.

A --group run is a different measurement, not a smaller one. Its score covers
only that group, so it is not comparable to a full-run score, it is never
written to runs.jsonl, and it cannot be pinned.

The machinery for recording, pinning and comparing runs lives in harness.py
and is shared with the retrieval suite.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import harness  # noqa: E402
import scoring  # noqa: E402

DATA = json.loads((HERE / "cases.json").read_text())
STORE = harness.Store(HERE / "runs.jsonl", HERE / "reference.json")

# Notes and prose are excluded so that rewording a comment does not invalidate
# a comparison; only what is actually asked and accepted counts.
DATASET_DIGEST = harness.digest(
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
            "metrics": fields,
            "score": harness.mean(fields.values()),
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
        vals = [r["metrics"][f] for r in results.values() if f in r["metrics"]]
        if vals:
            per_field[f] = harness.mean(vals)

    groups = sorted({r["group"] for r in results.values()})
    per_group = {
        g: harness.mean(r["score"] for r in results.values() if r["group"] == g)
        for g in groups
    }
    overall = harness.mean(r["score"] for r in results.values())

    return {
        "at": harness.now(),
        # `score` is kept alongside `scores` so that runs recorded before the
        # harness existed remain readable by the same code path.
        "score": overall,
        "scores": {"overall": overall},
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


def report(run, cases, reference, ref_why, previous, prev_why) -> int:
    sub = run.get("subset")
    basis = reference if reference else previous
    basis_name = "reference" if reference else "previous run"
    by_id = {c["id"]: c for c in cases}
    accepted, regression, outstanding, fixed = harness.sort_misses(run, by_id, basis)

    if sub:
        print(f"\nSUBSET RUN  group={sub['group']}  {sub['cases']} of {sub['of']} cases"
              f"  ({sub['of'] - sub['cases']} not run)")
        print(f"score  {run['score']:.3f}   <- THIS SUBSET ONLY, not comparable to a"
              f" full-run score ({run['elapsed_s']}s)")
        print("       not recorded in runs.jsonl and cannot be pinned as a reference")
    else:
        print(f"\nscore  {run['score']:.3f}   ({run['case_count']} cases, {run['elapsed_s']}s)")
    print(f"run    {harness.run_id_of(run)}")
    print(f"prompt {run['prompt_id'] or 'MIXED: ' + ', '.join(run['prompt_ids_seen'])}")
    print(f"scorer {run['scorer_digest']}   dataset {run['dataset_digest']}")

    print("\nby field")
    for f, v in run["by_field"].items():
        print(f"  {f:<20} {v:.3f}")
    unexercised = [f for f in scoring.SCORED_FIELDS if f not in run["by_field"]]
    if unexercised:
        print(f"  not exercised: {', '.join(unexercised)}")
    print("by group")
    for g, v in run["by_group"].items():
        print(f"  {g:<20} {v:.3f}")
    if sub:
        skipped = sorted({c["group"] for c in DATA["cases"]} - set(run["by_group"]))
        if skipped:
            print(f"  not run: {', '.join(skipped)}")

    if reference:
        harness.compare(run, reference, "vs REFERENCE (pinned)", sub)
    else:
        print(f"\nvs REFERENCE (pinned): none - {ref_why}")
        print("  pin one with:  python evals/run.py --set-baseline [RUN_ID]")

    if previous and not (reference and harness.run_id_of(previous) == harness.run_id_of(reference)):
        harness.compare(run, previous, "vs previous run", sub)
    elif not previous:
        print(f"\nvs previous run: none - {prev_why}")
    else:
        print("\nvs previous run: same run as the reference")

    if fixed:
        print("\naccepted failures that now pass (consider removing the acceptance)")
        for cid, metric in fixed:
            print(f"  {cid}.{metric}")

    scope = f" within group {sub['group']}" if sub else ""
    print(f"\naccepted failures{scope}: {len(accepted)}   (blessed in cases.json)")
    for cid, metric, value in accepted:
        print(f"  {cid}.{metric} = {value} (got {run['cases'][cid]['observed'].get(metric)!r})")

    if basis:
        print(f"\nREGRESSIONS vs {basis_name} {harness.run_id_of(basis)}{scope}: {len(regression)}")
        if sub:
            print(f"  only {sub['cases']} of {sub['of']} cases ran - this is NOT an"
                  " all-clear for the suite")
        for cid, metric, value, prev in sorted(regression, key=lambda r: r[2] - r[3]):
            print(f"  {cid}.{metric}  {prev} -> {value} "
                  f"(got {run['cases'][cid]['observed'].get(metric)!r})")
    else:
        print("\nREGRESSIONS: n/a   (nothing to compare against)")

    label = "outstanding" if basis else "failing, not accepted"
    print(f"\n{label}{scope}: {len(outstanding)}")
    for cid, metric, value in sorted(outstanding, key=lambda u: u[2]):
        print(f"  {cid}.{metric} = {value} (got {run['cases'][cid]['observed'].get(metric)!r})")

    return 1 if regression else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    harness.add_common_args(ap)
    ap.add_argument("--group", help="only run one group (clean, arguable, junk, injection, rejected)")
    args = ap.parse_args()

    if args.history:
        return harness.show_history(STORE)
    if args.clear_baseline:
        return STORE.clear_reference()
    if args.set_baseline is not None:
        return STORE.set_reference(args.set_baseline or None)

    cases = DATA["cases"]
    if args.group:
        cases = [c for c in cases if c["group"] == args.group]
        if not cases:
            print(f"no cases in group {args.group!r}")
            return 2

    run = run_once(args.api, cases)
    run["subset"] = (
        {"group": args.group, "cases": len(cases), "of": len(DATA["cases"])}
        if args.group else None
    )
    reference, ref_why = STORE.find_reference(run)
    previous, prev_why = STORE.find_previous(run)
    if not args.no_save and not args.group:
        STORE.append(run)
    return report(run, cases, reference, ref_why, previous, prev_why)


if __name__ == "__main__":
    sys.exit(main())
