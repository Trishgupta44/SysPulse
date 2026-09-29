"""Glue shared by the evaluator and the dashboard: load data, pick training rows, score."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import detectors

TRAIN_FRAC = 0.6
LIVE_TRAIN_CAP = 720  # rows (one hour at 5 s): keeps live-mode training bounded and stable


def load_frames(conn) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (samples, events, ground_truth) as DataFrames."""
    samples = pd.read_sql_query("SELECT * FROM samples ORDER BY ts", conn)
    events = pd.read_sql_query("SELECT ts, kind, message FROM events ORDER BY ts", conn)
    truth = pd.read_sql_query("SELECT * FROM ground_truth ORDER BY start_ts", conn)
    return samples, events, truth


def latest_run(truth: pd.DataFrame) -> pd.DataFrame:
    """Rows of the most recent ground-truth run (highest row id)."""
    if truth.empty:
        return truth
    run_id = truth.loc[truth["id"].idxmax(), "run_id"] if "id" in truth else truth["run_id"].iloc[-1]
    return truth[truth["run_id"] == run_id].sort_values("start_ts").reset_index(drop=True)


def train_mask(index: pd.Index, truth: pd.DataFrame, train_frac: float = TRAIN_FRAC) -> np.ndarray:
    """Rows used to fit detectors: the first `train_frac` of the clean baseline window.

    The rest of the baseline is held out so false-positive rate is measured out of sample.
    Without ground truth, the leading `train_frac` of all rows, capped at one hour, is used
    (the dashboard's live mode, where the first stretch of history is assumed to be normal).
    """
    ts = np.asarray(index, dtype=float)
    base = truth[(truth["label"] == "baseline") & (truth["is_fault"] == 0)] if not truth.empty else truth
    if base is not None and not base.empty:
        b = base.iloc[0]
        cut = b["start_ts"] + train_frac * (b["end_ts"] - b["start_ts"])
        return (ts >= b["start_ts"]) & (ts < cut)
    mask = np.zeros(len(ts), dtype=bool)
    mask[: min(int(len(ts) * train_frac), LIVE_TRAIN_CAP)] = True
    return mask


def fit_detectors(feats: pd.DataFrame, mask: np.ndarray, z_threshold: float = 4.0,
                  quantile: float = 0.995) -> dict:
    """Fit every detector on the same clean training rows."""
    train = feats[mask]
    return {
        "zscore": detectors.ZScoreDetector(threshold=z_threshold).fit(train),
        "isolation_forest": detectors.IsolationForestDetector(quantile=quantile).fit(train),
        "mahalanobis": detectors.MahalanobisDetector(quantile=quantile).fit(train),
    }


def score_all(feats: pd.DataFrame, dets: dict) -> dict[str, pd.DataFrame]:
    return {name: det.score(feats) for name, det in dets.items()}
