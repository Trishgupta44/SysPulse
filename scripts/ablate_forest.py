"""Isolation Forest feature-set ablation on a recorded run.

    python scripts/ablate_forest.py data/live-run.db

Fits the forest on the same clean baseline rows the evaluator uses, with different feature sets
and alert quantiles, and prints point-level precision, recall and F1 on the held-out rows.
Run after seeing the results of a live run: it explains the numbers, it is not a tuning loop.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from syspulse import db, detectors, evaluate, features, pipeline  # noqa: E402


def main(path: str) -> int:
    samples, events, truth_all = pipeline.load_frames(db.connect(path))
    truth = pipeline.latest_run(truth_all)
    if truth.empty:
        print("no ground truth in this database", file=sys.stderr)
        return 2
    feats = features.build(samples, events)
    mask = pipeline.train_mask(feats.index, truth)
    metrics = features.ALL_METRICS
    sets = {
        "full (default)": features.forest_columns(feats),
        "value + dev300": [c for m in metrics for c in (m, f"{m}_dev300")],
        "dev300 only": [f"{m}_dev300" for m in metrics],
        "value only": list(metrics),
    }
    lo, hi = truth.start_ts.min(), truth.end_ts.max() + evaluate.GRACE_S
    eval_rows = (feats.index >= lo) & (feats.index <= hi) & ~mask

    original = features.forest_columns
    rows = []
    for name, cols in sets.items():
        for q in (0.995, 0.98):
            features.forest_columns = lambda f, c=cols: c  # detector picks its columns through this hook
            try:
                det = detectors.IsolationForestDetector(quantile=q).fit(feats[mask])
            finally:
                features.forest_columns = original
            out = det.score(feats)[eval_rows]
            out = out[out["score"].notna()]
            y = evaluate.label_samples(out.index, truth)
            m = evaluate.prf(y, out["is_anomaly"])
            per_fault, false_runs = evaluate.fault_events(out["is_anomaly"], truth)
            rows.append({"features": name, "columns": len(cols), "quantile": q,
                         "precision": round(m["precision"], 2), "recall": round(m["recall"], 2),
                         "f1": round(m["f1"], 2),
                         "faults caught": f"{sum(r['detected'] for r in per_fault)}/{len(per_fault)}",
                         "false-alarm runs": false_runs})
    print(pd.DataFrame(rows).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else db.db_path()))
