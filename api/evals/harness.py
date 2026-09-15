"""Shared machinery for the evaluation suites.

Everything here is task-agnostic: recording a run, pinning a reference,
comparing two runs, and sorting imperfect results into buckets that mean
different things. What a suite supplies is its own cases, its own scoring, and
a function that turns one case into a set of named metrics.

The two suites differ in what they measure but not in how a measurement is
kept, so this exists to stop the second one becoming a parallel system with
its own slightly different answers to the same questions.

Backward compatibility matters here: runs recorded before this module existed
are still in runs.jsonl and are still comparable. They store a single `score`
and per-case `fields`; newer records store a `scores` dict and `metrics`. Both
are read.
"""

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path


# --- identity ---------------------------------------------------------------

def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def digest(obj) -> str:
    return sha256(canonical(obj).encode("utf-8")).hexdigest()[:12]


def run_id_of(run: dict) -> str:
    """Short stable handle, derived from the timestamp rather than stored, so
    runs written before this existed are addressable too."""
    return digest(run["at"])[:8]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- reading runs, old and new ---------------------------------------------

def scores_of(run: dict) -> dict:
    """Named headline scores. Older records carry a single unnamed `score`."""
    if "scores" in run:
        return run["scores"]
    return {"overall": run.get("score")}


def metrics_of(case_result: dict) -> dict:
    """Per-case metric values. Older records call them `fields`."""
    return case_result.get("metrics", case_result.get("fields", {}))


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


# --- persistence ------------------------------------------------------------

# Per-case keys that belong to the permanent history. Everything else on a
# case is detail: large, and only of interest while working on the run that
# produced it.
#
# The split is drawn here and not elsewhere because comparison needs exactly
# these. A baseline run is only ever read through `metrics_of()` and its set
# of case ids; the observed values and answer excerpts are read from the
# current run alone. So the history can be slimmed without any comparison
# losing information.
CASE_HISTORY_KEYS = ("group", "metrics", "fields", "score")

# How many runs keep their detail on disk. Older detail is pruned on the next
# run: it is not in the repository, so losing it costs nothing that the
# history does not already hold.
KEEP_DETAIL_FOR = 5


class Store:
    """Where one suite keeps its runs and its pinned reference.

    Two files, for two different lifetimes. `runs.jsonl` holds scores,
    digests and per-case metrics - small, permanent, in the repository.
    The detail directory beside it holds the bulky per-case record for the
    most recent runs only, and is not tracked.
    """

    def __init__(self, runs_path: Path, ref_path: Path):
        self.runs_path = runs_path
        self.ref_path = ref_path
        self.details_dir = runs_path.parent / f"{runs_path.stem}_detail"

    def load_runs(self) -> list:
        if not self.runs_path.exists():
            return []
        return [json.loads(l) for l in self.runs_path.read_text().splitlines() if l.strip()]

    @staticmethod
    def split_record(run: dict) -> tuple[dict, dict]:
        """(history, detail). The history keeps everything except the bulky
        half of each case; the detail keeps only that half."""
        slim_cases, detail_cases = {}, {}
        for cid, case in run.get("cases", {}).items():
            slim_cases[cid] = {k: v for k, v in case.items() if k in CASE_HISTORY_KEYS}
            rest = {k: v for k, v in case.items() if k not in CASE_HISTORY_KEYS}
            if rest:
                detail_cases[cid] = rest
        history = {**run, "cases": slim_cases}
        return history, {"run_id": run_id_of(run), "at": run["at"], "cases": detail_cases}

    def detail_path(self, run_id: str) -> Path:
        return self.details_dir / f"{run_id}.json"

    def read_detail(self, run_id: str) -> dict | None:
        path = self.detail_path(run_id)
        return json.loads(path.read_text()) if path.exists() else None

    def prune_details(self, keep: int = KEEP_DETAIL_FOR) -> int:
        """Drop detail for all but the most recent `keep` runs."""
        if not self.details_dir.exists():
            return 0
        recent = {run_id_of(r) for r in self.load_runs()[-keep:]}
        removed = 0
        for path in self.details_dir.glob("*.json"):
            if path.stem not in recent:
                path.unlink()
                removed += 1
        return removed

    def append(self, run: dict) -> None:
        history, detail = self.split_record(run)
        with self.runs_path.open("a") as fh:
            fh.write(json.dumps(history) + "\n")
        if detail["cases"]:
            self.details_dir.mkdir(exist_ok=True)
            self.detail_path(detail["run_id"]).write_text(
                json.dumps(detail, indent=1) + "\n")
        self.prune_details()

    def load_reference(self) -> dict | None:
        if not self.ref_path.exists():
            return None
        return json.loads(self.ref_path.read_text())

    def set_reference(self, run_id: str | None) -> int:
        runs = self.load_runs()
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
        self.ref_path.write_text(
            json.dumps(
                {
                    "run_id": run_id_of(target),
                    "at": target["at"],
                    "prompt_id": target.get("prompt_id"),
                    "scores": scores_of(target),
                    "scorer_digest": target["scorer_digest"],
                    "pinned_at": now(),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"reference pinned: {run_id_of(target)}  {target['at']}  "
              f"{fmt_scores(scores_of(target))}  {target.get('prompt_id')}")
        return 0

    def clear_reference(self) -> int:
        if self.ref_path.exists():
            self.ref_path.unlink()
            print("reference cleared; comparisons fall back to the previous run")
        else:
            print("no reference was pinned")
        return 0

    def find_previous(self, run: dict):
        prior = self.load_runs()
        if not prior:
            return None, "no earlier runs"
        same = [p for p in prior if p["scorer_digest"] == run["scorer_digest"]]
        if not same:
            return None, (
                f"scoring changed (digest {prior[-1]['scorer_digest']} -> "
                f"{run['scorer_digest']}); earlier scores are not comparable"
            )
        return same[-1], None

    def find_reference(self, run: dict):
        ref = self.load_reference()
        if ref is None:
            return None, "none pinned"
        match = [r for r in self.load_runs() if run_id_of(r) == ref["run_id"]]
        if not match:
            return None, f"pinned run {ref['run_id']} is no longer in {self.runs_path.name}"
        if match[0]["scorer_digest"] != run["scorer_digest"]:
            return None, (
                f"pinned run {ref['run_id']} was scored with {match[0]['scorer_digest']}, "
                f"this run with {run['scorer_digest']}; re-pin after a scoring change"
            )
        return match[0], None


# --- buckets ----------------------------------------------------------------

def sort_misses(run: dict, cases_by_id: dict, baseline):
    """Split imperfect metrics into buckets that mean different things.

    accepted     blessed in the dataset - never an alarm
    regression   passing in the baseline and failing now, or failing by more
    outstanding  failing, not accepted, no worse than the baseline
    fixed        an accepted failure that now passes; the acceptance is stale
    """
    accepted, regression, outstanding, fixed = [], [], [], []
    for cid, res in run["cases"].items():
        case = cases_by_id.get(cid, {})
        ok_list = case.get("accepted_failures", [])
        base = baseline["cases"].get(cid) if baseline else None
        for metric, value in metrics_of(res).items():
            if value is None or value == 1.0:
                continue
            entry = (cid, metric, value)
            prev = metrics_of(base).get(metric) if base else None
            if metric in ok_list:
                accepted.append(entry)
            elif prev is None:
                outstanding.append(entry)
            elif value < prev:
                regression.append((*entry, prev))
            elif prev < 1.0:
                outstanding.append(entry)
            else:
                regression.append((*entry, prev))
        for metric in ok_list:
            if metrics_of(res).get(metric) == 1.0:
                fixed.append((cid, metric))
    return accepted, regression, outstanding, fixed


# --- reporting --------------------------------------------------------------

def fmt_scores(scores: dict) -> str:
    return "  ".join(
        f"{k}={'—' if v is None else f'{v:.3f}'}" for k, v in scores.items()
    )


def compare(run: dict, other: dict, heading: str, subset=None, flags=()) -> None:
    """One comparison against another run, over the cases they share.

    `flags` names record fields whose difference should be called out rather
    than refused. A refusal is right when the numbers stop meaning the same
    thing - that is what `scorer_digest` does. A flag is right when the two
    runs are honest measurements of two different systems, which is the
    comparison you actually want: a changed prompt, or a changed retrieval
    configuration.
    """
    shared = sorted(set(run["cases"]) & set(other["cases"]))
    print(f"\n{heading}  {run_id_of(other)}  {other['at']}  ({len(shared)} shared cases)")
    if other.get("prompt_id") != run.get("prompt_id"):
        print(f"  ACROSS PROMPTS  {other.get('prompt_id')} -> {run.get('prompt_id')}")
    else:
        print(f"  same prompt ({run.get('prompt_id')}) - differences are model noise")
    for field, label in flags:
        mine, theirs = run.get(field), other.get(field)
        if mine != theirs:
            # A run recorded before the field existed reads as "not recorded",
            # never as "the same" - otherwise the gap looks covered.
            show = lambda v: "not recorded" if v is None else v
            print(f"  {label.upper()} CHANGED  {show(theirs)} -> {show(mine)}")

    if subset:
        # Printing the other run's FULL score beside the recomputed figures is
        # what stops "1.000 vs 0.911" reading as an improvement when it is
        # only a different slice of the same cases.
        print(f"  subset comparison: that run's full scores were"
              f" {fmt_scores(scores_of(other))}; the figures below are recomputed"
              f" over these {len(shared)} shared cases")
    elif len(shared) < other["case_count"] or len(shared) < run["case_count"]:
        print(f"  dataset changed: that run had {other['case_count']},"
              f" this run {run['case_count']}; compared on the overlap only")

    for name in scores_of(run):
        a = mean(metrics_of(run["cases"][c]).get(name) for c in shared) \
            if name in _metric_names(run) else _headline_over(run, shared, name)
        b = mean(metrics_of(other["cases"][c]).get(name) for c in shared) \
            if name in _metric_names(other) else _headline_over(other, shared, name)
        if a is None or b is None:
            print(f"  {name:<12} —")
            continue
        print(f"  {name:<12} {b:.3f} -> {a:.3f}   {a - b:+.3f}")

    moved = []
    for c in shared:
        for metric, value in metrics_of(run["cases"][c]).items():
            prev = metrics_of(other["cases"][c]).get(metric)
            if value is not None and prev is not None and abs(value - prev) > 1e-9:
                moved.append((c, metric, value - prev))
    for cid, metric, d in sorted(moved, key=lambda m: m[2]):
        print(f"    {'WORSE' if d < 0 else 'better'} {d:+.3f}  {cid}.{metric}")
    if not moved:
        print("    no case changed")


def _metric_names(run: dict) -> set:
    names = set()
    for res in run["cases"].values():
        names |= set(metrics_of(res))
    return names


def _headline_over(run: dict, shared: list, name: str):
    """Fallback for a headline that is not a per-case metric (the old single
    `overall`): average each case's own mean."""
    per_case = []
    for c in shared:
        vals = [v for v in metrics_of(run["cases"][c]).values() if v is not None]
        per_case.append(sum(vals) / len(vals) if vals else None)
    return mean(per_case)


def show_history(store: Store) -> int:
    runs = store.load_runs()
    if not runs:
        print("no runs yet")
        return 0
    ref = store.load_reference()
    ref_id = ref["run_id"] if ref else None
    for r in runs:
        rid = run_id_of(r)
        mark = "REF ->" if rid == ref_id else "      "
        print(f"{mark} {rid}  {r['at']}  {fmt_scores(scores_of(r))}  "
              f"{r['case_count']:>3} cases  scorer {r['scorer_digest']}  {r.get('prompt_id')}")
    if not ref_id:
        print("\nno reference pinned - comparisons fall back to the previous run")
    return 0


def show_detail(store: Store, run_id: str | None) -> int:
    """Print the per-case detail of a run, if it is still on disk.

    Detail is kept for the most recent runs only and is not in the repository,
    so an older run answers with what it has: the history knows the scores,
    nothing knows the excerpts any more.
    """
    runs = store.load_runs()
    if not runs:
        print("no runs yet")
        return 0
    if run_id:
        matches = [r for r in runs if run_id_of(r).startswith(run_id)]
        if not matches:
            print(f"no run matching {run_id!r} (see --history)")
            return 2
        target = matches[-1]
    else:
        target = runs[-1]

    rid = run_id_of(target)
    detail = store.read_detail(rid)
    print(f"run {rid}  {target['at']}  {fmt_scores(scores_of(target))}")
    if detail is None:
        kept = sorted(p.stem for p in store.details_dir.glob("*.json")) \
            if store.details_dir.exists() else []
        print(f"\n  no detail on disk for this run.")
        print(f"  detail is kept for the most recent {KEEP_DETAIL_FOR} runs"
              f" and is not tracked in git.")
        print(f"  still available: {', '.join(kept) or 'none'}")
        return 1

    for cid, case in detail["cases"].items():
        metrics = metrics_of(target["cases"].get(cid, {}))
        shown = "  ".join(f"{k}={v}" for k, v in metrics.items())
        print(f"\n  {cid}   {shown}")
        for key, value in case.items():
            if isinstance(value, dict):
                value = "  ".join(f"{k}={v!r}" for k, v in value.items())
            print(f"     {key}: {str(value)[:300]}")
    return 0


def add_common_args(ap) -> None:
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--history", action="store_true", help="list past runs and exit")
    ap.add_argument("--no-save", action="store_true", help="do not record this run")
    ap.add_argument("--set-baseline", nargs="?", const="", metavar="RUN_ID",
                    help="pin a run as the reference (default: the most recent) and exit")
    ap.add_argument("--clear-baseline", action="store_true",
                    help="unpin the reference and exit")
    ap.add_argument("--detail", nargs="?", const="", metavar="RUN_ID",
                    help="show a run's per-case detail (default: the most recent) and exit")
