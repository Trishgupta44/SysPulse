"""Record fault-injection windows. Called by scripts/inject_faults.sh.

    python -m syspulse.groundtruth add --run-id R --label cpu_burst --start T0 --end T1 --fault 1
"""
from __future__ import annotations

import argparse
import sys

from . import db


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="insert one ground-truth window")
    add.add_argument("--run-id", required=True)
    add.add_argument("--label", required=True)
    add.add_argument("--start", type=float, required=True, help="unix seconds")
    add.add_argument("--end", type=float, required=True, help="unix seconds")
    add.add_argument("--fault", type=int, choices=(0, 1), required=True)
    args = ap.parse_args(argv)

    if args.end <= args.start:
        ap.error("--end must be after --start")
    conn = db.connect(args.db)
    conn.execute(
        "INSERT INTO ground_truth (run_id, label, start_ts, end_ts, is_fault) VALUES (?,?,?,?,?)",
        (args.run_id, args.label, args.start, args.end, args.fault),
    )
    conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
