#!/usr/bin/env python3
"""Score the classifier against evals/cases.json and compare with earlier runs.

    python evals/run.py                      # full set, ~50s
    python evals/run.py --group arguable     # one group, for a fast loop
    python evals/run.py --api http://localhost:8001
    python evals/run.py --history            # past runs, no calls made
    python evals/run.py --set-baseline       # pin the latest run as reference
    python evals/run.py --set-baseline c5f58  # pin a specific run
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
written to runs.jsonl, and it cannot be pinned. The report says all of this,
names the fields and groups that did not run, and scopes every failure count
to the group - a subset reporting "0 regressions" is not an all-clear for the
suite. Comparisons still work, because the other run is recomputed over the
same cases; the report prints that run's full score next to its recomputed one
so the two cannot be confused.

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
REF_PATH = HERE / "reference.json"

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


def run_id_of(run: dict) -> str:
    """Short stable handle for a run.

    Derived from the timestamp rather than stored, so runs written before this
    existed are addressable too and no record had to be rewritten.
    """
    return scoring.digest(run["at"])[:8]


def load_runs() -> list:
    if not RUNS_PATH.exists():
        return []
    return [json.loads(l) for l in RUNS_PATH.read_text().splitlines() if l.strip()]


def load_reference() -> dict | None:
    """The pinned reference, or None if nothing is pinned."""
    if not REF_PATH.exists():
        return None
    return json.loads(REF_PATH.read_text())


def set_reference(run_id: str | None) -> int:
    """Pin a run as the reference. Defaults to the most recent run."""
    runs = load_runs()
    if not runs:
        print("no runs to pin")
        return 2
    if run_id is None:
        target = runs[-1]
    else:
        matches = [r for r in runs if run_id_of(r).startswith(run_id)]
        if not matches:
            print(f"no run matching {run_id!r} (see --history)")
            return 2
        if len(matches) > 1:
            print(f"{run_id!r} matches {len(matches)} runs; use more characters")
            return 2
        target = matches[0]

    REF_PATH.write_text(
        json.dumps(
            {
                "run_id": run_id_of(target),
                "at": target["at"],
                "prompt_id": target["prompt_id"],
                "score": target["score"],
                "scorer_digest": target["scorer_digest"],
                "pinned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"reference pinned: {run_id_of(target)}  {target['at']}  "
          f"score {target['score']:.3f}  {target['prompt_id']}")
    return 0


def clear_reference() -> int:
    if REF_PATH.exists():
        REF_PATH.unlink()
        print("reference cleared; comparisons fall back to the previous run")
    else:
        print("no reference was pinned")
    return 0


def find_previous(run: dict):
    """Most recent earlier run whose score means the same thing."""
    prior = load_runs()
    if not prior:
        return None, "no earlier runs"
    same_scorer = [p for p in prior if p["scorer_digest"] == run["scorer_digest"]]
    if not same_scorer:
        return None, (
            f"scoring changed (digest {prior[-1]['scorer_digest']} -> "
            f"{run['scorer_digest']}); earlier scores are not comparable"
        )
    return same_scorer[-1], None


def find_reference(run: dict):
    """The pinned run, if one is pinned and still comparable."""
    ref = load_reference()
    if ref is None:
        return None, "none pinned"
    match = [r for r in load_runs() if run_id_of(r) == ref["run_id"]]
    if not match:
        return None, f"pinned run {ref['run_id']} is no longer in runs.jsonl"
    if match[0]["scorer_digest"] != run["scorer_digest"]:
        return None, (
            f"pinned run {ref['run_id']} was scored with {match[0]['scorer_digest']}, "
            f"this run with {run['scorer_digest']}; re-pin after a scoring change"
        )
    return match[0], None


def _compare(run: dict, other: dict, heading: str, sub=None) -> None:
    """Print one score comparison against `other`."""
    shared = sorted(set(run["cases"]) & set(other["cases"]))
    a = mean(run["cases"][c]["score"] for c in shared)
    b = mean(other["cases"][c]["score"] for c in shared)
    print(f"\n{heading}  {run_id_of(other)}  {other['at']}  ({len(shared)} shared cases)")
    if other["prompt_id"] != run["prompt_id"]:
        print(f"  ACROSS PROMPTS  {other['prompt_id']} -> {run['prompt_id']}")
    else:
        print(f"  same prompt ({run['prompt_id']}) - differences are model noise")
    if sub:
        # Not a dataset change - this run deliberately ran fewer cases. Showing
        # the other run's headline next to its recomputed value is what stops
        # "1.000 vs 0.911" being read as an improvement.
        print(f"  subset comparison: that run's full score was {other['score']:.3f};"
              f" over these {len(shared)} cases it is {b:.3f}")
    elif len(shared) < other["case_count"] or len(shared) < run["case_count"]:
        print(f"  dataset changed: that run had {other['case_count']},"
              f" this run {run['case_count']}; compared on the overlap only")
    print(f"  {b:.3f} -> {a:.3f}   {a - b:+.3f}")

    moved = [
        (c, run["cases"][c]["score"] - other["cases"][c]["score"])
        for c in shared
        if abs(run["cases"][c]["score"] - other["cases"][c]["score"]) > 1e-9
    ]
    for cid, d in sorted(moved, key=lambda m: m[1]):
        print(f"    {'WORSE' if d < 0 else 'better'} {d:+.3f}  {cid}")
    if not moved:
        print("    no case changed")


def report(run: dict, cases: list, reference, ref_why, previous, prev_why) -> int:
    # Regressions are judged against the reference when one is pinned. That is
    # the whole point: restoring a known-good prompt must not read as a
    # regression just because the run before it happened to score higher.
    sub = run.get("subset")
    basis = reference if reference else previous
    basis_name = "reference" if reference else "previous run"
    accepted, regression, outstanding, fixed = classify_misses(run, cases, basis)

    if sub:
        print(f"\nSUBSET RUN  group={sub['group']}  {sub['cases']} of {sub['of']} cases"
              f"  ({sub['of'] - sub['cases']} not run)")
        print(f"score  {run['score']:.3f}   <- THIS SUBSET ONLY, not comparable to a"
              f" full-run score ({run['elapsed_s']}s)")
        print("       not recorded in runs.jsonl and cannot be pinned as a reference")
    else:
        print(f"\nscore  {run['score']:.3f}   ({run['case_count']} cases, {run['elapsed_s']}s)")
    print(f"run    {run_id_of(run)}")
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
        _compare(run, reference, "vs REFERENCE (pinned)", sub)
    else:
        print(f"\nvs REFERENCE (pinned): none - {ref_why}")
        print("  pin one with:  python evals/run.py --set-baseline [RUN_ID]")

    # Kept alongside the reference, not replaced by it: this is the one that
    # answers "what did the change I just made do".
    if previous and not (reference and run_id_of(previous) == run_id_of(reference)):
        _compare(run, previous, "vs previous run", sub)
    elif not previous:
        print(f"\nvs previous run: none - {prev_why}")
    else:
        print("\nvs previous run: same run as the reference")

    if fixed:
        print("\naccepted failures that now pass (consider removing the acceptance)")
        for cid, field in fixed:
            print(f"  {cid}.{field}")

    scope = f" within group {sub['group']}" if sub else ""
    print(f"\naccepted failures{scope}: {len(accepted)}   (blessed in cases.json)")
    for cid, field, sc, got in accepted:
        print(f"  {cid}.{field} = {sc} (got {got!r})")

    if basis:
        print(f"\nREGRESSIONS vs {basis_name} {run_id_of(basis)}{scope}: {len(regression)}")
        if sub:
            print(f"  only {sub['cases']} of {sub['of']} cases ran - this is NOT an"
                  " all-clear for the suite")
        for cid, field, sc, got, prev in sorted(regression, key=lambda r: r[2] - r[4]):
            print(f"  {cid}.{field}  {prev} -> {sc} (got {got!r})")
    else:
        print("\nREGRESSIONS: n/a   (nothing to compare against)")

    label = "outstanding" if basis else "failing, not accepted"
    print(f"\n{label}{scope}: {len(outstanding)}")
    for cid, field, sc, got in sorted(outstanding, key=lambda u: u[2]):
        print(f"  {cid}.{field} = {sc} (got {got!r})")

    return 1 if regression else 0


def show_history() -> int:
    runs = load_runs()
    if not runs:
        print("no runs yet")
        return 0
    ref = load_reference()
    ref_id = ref["run_id"] if ref else None
    for r in runs:
        rid = run_id_of(r)
        mark = "REF ->" if rid == ref_id else "      "
        print(f"{mark} {rid}  {r['at']}  {r['score']:.3f}  {r['case_count']:>3} cases  "
              f"scorer {r['scorer_digest']}  {r['prompt_id']}")
    if ref_id and not any(run_id_of(r) == ref_id for r in runs):
        print(f"\nwarning: pinned reference {ref_id} is not in runs.jsonl")
    if not ref_id:
        print("\nno reference pinned - comparisons fall back to the previous run")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--group", help="only run one group (clean, arguable, junk, injection, rejected)")
    ap.add_argument("--history", action="store_true", help="list past runs and exit")
    ap.add_argument("--no-save", action="store_true", help="do not append to runs.jsonl")
    ap.add_argument("--set-baseline", nargs="?", const="", metavar="RUN_ID",
                    help="pin a run as the reference (default: the most recent) and exit")
    ap.add_argument("--clear-baseline", action="store_true",
                    help="unpin the reference and exit")
    args = ap.parse_args()

    if args.history:
        return show_history()
    if args.clear_baseline:
        return clear_reference()
    if args.set_baseline is not None:
        return set_reference(args.set_baseline or None)

    cases = DATA["cases"]
    if args.group:
        cases = [c for c in cases if c["group"] == args.group]
        if not cases:
            print(f"no cases in group {args.group!r}")
            return 2

    run = run_once(args.api, cases)
    # A subset run is a different measurement, not a smaller one, so it says so
    # in its own record rather than looking like a full run with fewer cases.
    run["subset"] = (
        {"group": args.group, "cases": len(cases), "of": len(DATA["cases"])}
        if args.group
        else None
    )
    reference, ref_why = find_reference(run)
    previous, prev_why = find_previous(run)
    # A subset run is not a fair baseline for a later full run, so it is scored
    # and reported but never written to the history.
    if not args.no_save and not args.group:
        with RUNS_PATH.open("a") as fh:
            fh.write(json.dumps(run) + "\n")
    return report(run, cases, reference, ref_why, previous, prev_why)


if __name__ == "__main__":
    sys.exit(main())
