#!/usr/bin/env python3
"""Score retrieval against evals/retrieval_cases.json.

    python evals/run_retrieval.py                  # full set, ~75s
    python evals/run_retrieval.py --group unanswerable
    python evals/run_retrieval.py --history
    python evals/run_retrieval.py --set-baseline
    python evals/run_retrieval.py --clear-baseline

Two scores, never blended, because the two halves fail independently:

  retrieval   did the right passage come back at all (recall, order ignored)
  answer      was the answer any good given what came back

A third figure, `answer|found`, is printed but not stored: the answer score
restricted to cases where retrieval found everything it should. It is the one
that says whether a bad answer is the index's fault or the model's.

Recording, pinning and comparing runs is shared with the classifier suite in
harness.py - the two measure different things but keep a measurement the same
way.
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
import retrieval_scoring as scoring  # noqa: E402

DATA = json.loads((HERE / "retrieval_cases.json").read_text())

# The /ask prompt is rendered with the retrieved passages, so the prompt_id the
# API returns is different for every question - it identifies the request, not
# the prompt. For attributing a whole run to a prompt version, the stable
# identity is the template before substitution, which is what this hashes.
PROMPT_PATH = HERE.parent / "prompts" / "answer_question.md"


def prompt_template_id() -> str:
    text = PROMPT_PATH.read_text(encoding="utf-8").strip()
    return f"{PROMPT_PATH.stem}@{harness.digest(text)}~template"
STORE = harness.Store(HERE / "retrieval_runs.jsonl", HERE / "retrieval_reference.json")

# Differences here are reported, never grounds for refusing a comparison -
# see harness.compare for why.
FLAGS = (("retrieval_digest", "retrieval config"), ("index_digest", "index"))

DATASET_DIGEST = harness.digest(
    [
        {"id": c["id"], "question": c["question"], "expect": c["expect"],
         "accepted_failures": sorted(c.get("accepted_failures", []))}
        for c in sorted(DATA["cases"], key=lambda c: c["id"])
    ]
)


def fetch_config(api: str) -> dict:
    """How the server was configured, from the server.

    Read here rather than by importing `answering`, because the eval scores
    what this process actually did: a module on disk can be ahead of a server
    that has not been restarted, and a config record that is quietly wrong is
    worse than none.
    """
    try:
        with urllib.request.urlopen(f"{api}/retrieval/config", timeout=30) as r:
            return json.loads(r.read())
    except Exception as exc:  # an older server has no such endpoint
        print(f"  warning: could not read retrieval config ({type(exc).__name__});"
              " this run will compare as 'not recorded'", file=sys.stderr)
        return {}


def call(api: str, question: str):
    req = urllib.request.Request(
        f"{api}/ask",
        data=json.dumps({"question": question}).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def run_once(api: str, cases: list) -> dict:
    started = time.time()
    server = fetch_config(api)
    config, index = server.get("config"), server.get("index")
    results, prompt_ids, ks = {}, set(), set()

    for i, case in enumerate(cases, 1):
        status, body = call(api, case["question"])
        # The passage text is only in the trace; `sources` omits it.
        retrieved = (body.get("trace") or {}).get("retrieved") or []
        answer = body.get("answer", "")
        if body.get("prompt_id"):
            prompt_ids.add(body["prompt_id"])
        ks.add(len(retrieved))

        if status != 200:
            metrics = {"retrieval": None, "answer": 0.0}
            detail = {"status": status, "error": str(body.get("detail"))[:120]}
        else:
            metrics = scoring.score_case(case, retrieved, answer)
            expected = case["expect"].get("passages", [])
            detail = {
                "status": status,
                "top_score": retrieved[0]["score"] if retrieved else None,
                "refused": scoring.refused(answer),
                "found": scoring.matched_passages(expected, retrieved),
                "expected": [e["contains"][:40] for e in expected],
                "answer": " ".join(answer.split())[:200],
            }

        results[case["id"]] = {"group": case["group"], "metrics": metrics, "detail": detail}
        print(f"\r  {i}/{len(cases)} {case['id'][:44]:<44}", end="", file=sys.stderr)
    print("\r" + " " * 60 + "\r", end="", file=sys.stderr)

    def metric_mean(name, subset=None):
        pool = subset if subset is not None else results.values()
        return harness.mean(r["metrics"][name] for r in pool)

    groups = sorted({r["group"] for r in results.values()})
    by_group = {
        g: {m: metric_mean(m, [r for r in results.values() if r["group"] == g])
            for m in scoring.METRICS}
        for g in groups
    }

    return {
        "at": harness.now(),
        "scores": {m: metric_mean(m) for m in scoring.METRICS},
        "prompt_id": prompt_template_id(),
        # One rendered id per question is expected, not a fault; the count is
        # kept as a sanity check that every call did reach the same prompt.
        "rendered_prompt_ids": len(prompt_ids),
        "scorer_digest": scoring.SCORER_DIGEST,
        "dataset_digest": DATASET_DIGEST,
        # Flagged, not refused: a changed knob makes this a measurement of a
        # different system, not a different kind of measurement.
        "retrieval_config": config,
        "retrieval_digest": harness.digest(config) if config else None,
        "index_state": index,
        "index_digest": harness.digest(index) if index else None,
        "case_count": len(results),
        "k_observed": sorted(ks),
        "elapsed_s": round(time.time() - started, 1),
        "by_group": by_group,
        "cases": results,
    }


def answer_given_retrieval(run: dict):
    """Answer score over cases where retrieval found everything it should.

    Not stored: it is derived from the per-case metrics, and storing a derived
    figure invites it drifting out of step with the numbers it comes from.
    """
    pool = [r for r in run["cases"].values() if r["metrics"]["retrieval"] == 1.0]
    return harness.mean(r["metrics"]["answer"] for r in pool), len(pool)


def report(run, cases, reference, ref_why, previous, prev_why) -> int:
    sub = run.get("subset")
    basis = reference if reference else previous
    basis_name = "reference" if reference else "previous run"
    by_id = {c["id"]: c for c in cases}
    accepted, regression, outstanding, fixed = harness.sort_misses(run, by_id, basis)

    if sub:
        print(f"\nSUBSET RUN  group={sub['group']}  {sub['cases']} of {sub['of']} cases"
              f"  ({sub['of'] - sub['cases']} not run)")
        print("       not recorded and cannot be pinned as a reference")
    print(f"\n{harness.fmt_scores(run['scores'])}   ({run['case_count']} cases, {run['elapsed_s']}s)")
    given, n = answer_given_retrieval(run)
    print(f"answer|found={'—' if given is None else f'{given:.3f}'}"
          f"   (answer over the {n} cases where retrieval found everything)")
    print(f"run    {harness.run_id_of(run)}")
    print(f"prompt {run['prompt_id']}   ({run.get('rendered_prompt_ids', 0)} rendered variants, one per question)")
    print(f"scorer {run['scorer_digest']}   dataset {run['dataset_digest']}   k={run['k_observed']}")
    cfg, idx = run.get("retrieval_config"), run.get("index_state")
    print(f"retrieval {run.get('retrieval_digest') or 'not recorded'}"
          + (f"   {'  '.join(f'{k.split(chr(46))[-1]}={v}' for k, v in cfg.items())}" if cfg else ""))
    if idx:
        print(f"index {run.get('index_digest')}   {idx['chunks']} chunks from"
              f" {idx['documents']} documents, indexed {idx['indexed_at']}")

    print("\nby group          retrieval   answer")
    for g, v in run["by_group"].items():
        r = "—" if v["retrieval"] is None else f"{v['retrieval']:.3f}"
        a = "—" if v["answer"] is None else f"{v['answer']:.3f}"
        print(f"  {g:<16} {r:>9}   {a:>6}")
    print("  (unanswerable has no retrieval score: there is nothing to find)")

    if reference:
        harness.compare(run, reference, "vs REFERENCE (pinned)", sub, flags=FLAGS)
    else:
        print(f"\nvs REFERENCE (pinned): none - {ref_why}")
        print("  pin one with:  python evals/run_retrieval.py --set-baseline [RUN_ID]")

    if previous and not (reference and harness.run_id_of(previous) == harness.run_id_of(reference)):
        harness.compare(run, previous, "vs previous run", sub, flags=FLAGS)
    elif not previous:
        print(f"\nvs previous run: none - {prev_why}")
    else:
        print("\nvs previous run: same run as the reference")

    if fixed:
        print("\naccepted failures that now pass (consider removing the acceptance)")
        for cid, metric in fixed:
            print(f"  {cid}.{metric}")

    scope = f" within group {sub['group']}" if sub else ""
    print(f"\naccepted failures{scope}: {len(accepted)}")
    for cid, metric, value in accepted:
        print(f"  {cid}.{metric} = {value}")

    if basis:
        print(f"\nREGRESSIONS vs {basis_name} {harness.run_id_of(basis)}{scope}: {len(regression)}")
        if sub:
            print(f"  only {sub['cases']} of {sub['of']} cases ran - NOT an all-clear")
        for cid, metric, value, prev in sorted(regression, key=lambda r: r[2] - r[3]):
            print(f"  {cid}.{metric}  {prev} -> {value}")
    else:
        print("\nREGRESSIONS: n/a   (nothing to compare against)")

    label = "outstanding" if basis else "failing, not accepted"
    print(f"\n{label}{scope}: {len(outstanding)}")
    for cid, metric, value in sorted(outstanding, key=lambda u: u[2]):
        d = run["cases"][cid]["detail"]
        extra = ""
        if metric == "retrieval":
            missed = [e for e, ok in zip(d.get("expected", []), d.get("found", [])) if not ok]
            extra = f"  missed: {missed}"
        elif metric == "answer":
            extra = f"  refused={d.get('refused')}  answer={d.get('answer', '')[:90]!r}"
        print(f"  {cid}.{metric} = {value}{extra}")

    return 1 if regression else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    harness.add_common_args(ap)
    ap.add_argument("--group", help="direct, vocabulary, multi_doc, unanswerable")
    args = ap.parse_args()

    if args.history:
        return harness.show_history(STORE)
    if args.detail is not None:
        return harness.show_detail(STORE, args.detail or None)
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
