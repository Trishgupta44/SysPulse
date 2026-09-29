"""Generate a synthetic run, score it, and write the report. Needs no root and no stress-ng.

    python -m syspulse.demo --db demo.db --out results

The numbers describe synthetic data only. Use `make inject` on a real host for real results.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import db, evaluate, synth


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="demo.db")
    ap.add_argument("--out", default="results")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    for suffix in ("", "-wal", "-shm"):  # start from a clean file so reruns do not stack runs
        if os.path.exists(args.db + suffix):
            os.remove(args.db + suffix)
    conn = db.connect(args.db)
    try:
        synth.write(conn, *synth.generate(seed=args.seed))
        res = evaluate.run_evaluation(conn, persist=True)
    finally:
        conn.close()
    _, md = evaluate.write_report(res, args.out)
    print(md.read_text(encoding="utf-8"))
    print(f"demo database: {args.db}   (view it: SYSPULSE_DB={args.db} streamlit run dashboard/app.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
