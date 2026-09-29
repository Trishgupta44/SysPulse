"""Score detectors against fault-injection ground truth.

    python -m syspulse.evaluate --db metrics.db --out results/

Point-level metrics treat every scored sample as one prediction. A sample is a true positive
target when it lies inside a fault window extended by `grace_s`, because rolling features keep
reacting for a short while after a fault stops. Fault-level metrics ask the operator's question:
was each injected fault noticed, and how quickly?
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import db, features, pipeline

GRACE_S = 30.0
DISPLAY = {"zscore": "Rolling z-score", "isolation_forest": "Isolation Forest",
           "mahalanobis": "Mahalanobis distance"}


def prf(y_true: pd.Series, y_pred: pd.Series) -> dict:
    yt, yp = np.asarray(y_true, dtype=bool), np.asarray(y_pred, dtype=bool)
    tp = int((yt & yp).sum())
    fp = int((~yt & yp).sum())
    fn = int((yt & ~yp).sum())
    tn = int((~yt & ~yp).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _fault_windows(truth: pd.DataFrame) -> pd.DataFrame:
    return truth[truth["is_fault"] == 1]


def label_samples(ts: np.ndarray, truth: pd.DataFrame, grace_s: float = GRACE_S) -> np.ndarray:
    """True for timestamps inside any fault window, extended by `grace_s` at the end."""
    ts = np.asarray(ts, dtype=float)
    y = np.zeros(len(ts), dtype=bool)
    for w in _fault_windows(truth).itertuples():
        y |= (ts >= w.start_ts) & (ts < w.end_ts + grace_s)
    return y


def fault_events(pred: pd.Series, truth: pd.DataFrame, grace_s: float = GRACE_S):
    """Per-fault detection and latency, plus the number of false-alarm runs.

    A false-alarm run is a contiguous stretch of flagged samples outside every fault window.
    """
    ts = np.asarray(pred.index, dtype=float)
    flagged = np.asarray(pred, dtype=bool)
    rows = []
    for w in _fault_windows(truth).itertuples():
        hit = flagged & (ts >= w.start_ts) & (ts < w.end_ts + grace_s)
        if hit.any():
            rows.append({"label": w.label, "detected": True, "latency_s": float(ts[hit][0] - w.start_ts)})
        else:
            rows.append({"label": w.label, "detected": False, "latency_s": None})
    outside = flagged & ~label_samples(ts, truth, grace_s)
    runs = int(np.sum(outside & ~np.concatenate(([False], outside[:-1])))) if len(outside) else 0
    return rows, runs


def run_evaluation(conn, run_id: str | None = None, grace_s: float = GRACE_S,
                   persist: bool = False) -> dict:
    samples, events, truth_all = pipeline.load_frames(conn)
    if truth_all.empty:
        raise ValueError("no ground truth in the database: run scripts/inject_faults.sh first")
    if run_id is not None:
        truth = truth_all[truth_all["run_id"] == run_id].sort_values("start_ts").reset_index(drop=True)
        if truth.empty:
            raise ValueError(f"no ground truth rows for run {run_id!r}")
    else:
        truth = pipeline.latest_run(truth_all)
    if samples.empty:
        raise ValueError("no samples in the database: is the collector running?")

    feats = features.build(samples, events)
    mask = pipeline.train_mask(feats.index, truth)
    dets = pipeline.fit_detectors(feats, mask)
    scored = pipeline.score_all(feats, dets)

    lo, hi = truth["start_ts"].min(), truth["end_ts"].max() + grace_s
    in_run = (feats.index >= lo) & (feats.index <= hi)
    # Score every detector on the same rows: those where all of them have a score.
    have_score = np.all([s["score"].notna().to_numpy() for s in scored.values()], axis=0)
    eval_rows = in_run & ~mask & have_score
    result = {
        "run_id": str(truth["run_id"].iloc[0]),
        "grace_s": grace_s,
        "n_eval_rows": int(eval_rows.sum()),
        "n_train_rows": int(mask.sum()),
        "faults": [w.label for w in _fault_windows(truth).itertuples()],
        "detectors": {},
    }
    for name, out in scored.items():
        out = out[eval_rows]
        y_true = label_samples(out.index, truth, grace_s)
        y_pred = out["is_anomaly"]
        per_fault, false_alarm_runs = fault_events(y_pred, truth, grace_s)
        latencies = [r["latency_s"] for r in per_fault if r["detected"]]
        result["detectors"][name] = {
            "threshold": float(dets[name].threshold),
            "point": prf(y_true, y_pred),
            "fault_recall": float(np.mean([r["detected"] for r in per_fault])) if per_fault else 0.0,
            "median_latency_s": float(np.median(latencies)) if latencies else None,
            "false_alarm_runs": false_alarm_runs,
            "per_fault": per_fault,
        }
    if persist:
        rows = []
        for name, out in scored.items():
            o = out[out["score"].notna()]
            rows += [(float(t), name, float(s), int(a)) for t, s, a in zip(o.index, o["score"], o["is_anomaly"], strict=True)]
        conn.executemany("INSERT OR REPLACE INTO anomalies (ts, detector, score, is_anomaly) VALUES (?,?,?,?)", rows)
        conn.commit()
    return result


def to_markdown(res: dict) -> str:
    lines = [
        f"### Results for run `{res['run_id']}`",
        "",
        f"Scored {res['n_eval_rows']} held-out samples (trained on {res['n_train_rows']}). "
        f"Fault windows extended by {res['grace_s']:.0f} s.",
        "",
        "| Detector | Precision | Recall | F1 | Faults caught | Median latency | False-alarm runs |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, d in res["detectors"].items():
        p = d["point"]
        lat = "n/a" if d["median_latency_s"] is None else f"{d['median_latency_s']:.0f} s"
        lines.append(
            f"| {DISPLAY.get(name, name)} | {p['precision']:.2f} | {p['recall']:.2f} | {p['f1']:.2f} "
            f"| {d['fault_recall'] * 100:.0f}% | {lat} | {d['false_alarm_runs']} |"
        )
    names = list(res["detectors"])
    lines += ["", "| Fault | " + " | ".join(f"{DISPLAY.get(n, n)} latency" for n in names) + " |",
              "|---|" + "---|" * len(names)]
    for i, fault in enumerate(res["faults"]):
        cells = []
        for n in names:
            r = res["detectors"][n]["per_fault"][i]
            cells.append(f"{r['latency_s']:.0f} s" if r["detected"] else "missed")
        lines.append(f"| {fault} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_report(res: dict, out_dir) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out / "results.json", out / "results.md"
    json_path.write_text(json.dumps(res, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(res), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None)
    ap.add_argument("--run-id", default=None, help="default: most recent run")
    ap.add_argument("--out", default="results")
    ap.add_argument("--grace", type=float, default=GRACE_S)
    ap.add_argument("--persist", action="store_true", help="also store scores in the anomalies table")
    args = ap.parse_args(argv)
    conn = db.connect(args.db)
    try:
        res = run_evaluation(conn, args.run_id, args.grace, args.persist)
    except ValueError as exc:
        print(f"evaluate: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    _, md_path = write_report(res, args.out)
    print(md_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
