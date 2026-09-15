#!/usr/bin/env python3
"""One-off: split existing run records into history and detail.

    python evals/migrate_detail.py --dry-run
    python evals/migrate_detail.py

Rewrites `runs.jsonl` and `retrieval_runs.jsonl` in place, keeping every run
exactly as it was apart from the bulky half of each case, which is written to
the untracked detail directory beside it. Run ids, scores, digests and
per-case metrics are untouched, so pinned references and every comparison
keep working.

Detail for older runs is pruned afterwards by the same rule a new run uses -
the most recent few are kept, the rest are dropped. Nothing is lost that the
history does not already hold.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import harness  # noqa: E402

STORES = [
    harness.Store(HERE / "runs.jsonl", HERE / "reference.json"),
    harness.Store(HERE / "retrieval_runs.jsonl", HERE / "retrieval_reference.json"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for store in STORES:
        if not store.runs_path.exists():
            print(f"{store.runs_path.name}: not present, skipped")
            continue

        runs = store.load_runs()
        before = store.runs_path.stat().st_size
        histories, details = [], []
        for run in runs:
            history, detail = store.split_record(run)
            histories.append(history)
            if detail["cases"]:
                details.append(detail)

        after = sum(len(json.dumps(h)) + 1 for h in histories)
        print(f"\n{store.runs_path.name}: {len(runs)} runs")
        print(f"  tracked   {before/1024:7.1f}KB -> {after/1024:7.1f}KB"
              f"   ({100 * (1 - after / before):.0f}% smaller)")
        print(f"  detail    {len(details)} files, keeping the most recent"
              f" {harness.KEEP_DETAIL_FOR}")
        if args.dry_run:
            continue

        # Detail first: if anything fails, the original file is still intact.
        store.details_dir.mkdir(exist_ok=True)
        for detail in details:
            store.detail_path(detail["run_id"]).write_text(
                json.dumps(detail, indent=1) + "\n")
        store.runs_path.write_text(
            "".join(json.dumps(h) + "\n" for h in histories))
        dropped = store.prune_details()
        print(f"  pruned    {dropped} older detail files")

    if args.dry_run:
        print("\ndry run: nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
