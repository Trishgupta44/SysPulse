"""Rolling-window feature engineering over `samples` and `events`."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db

EVENT_KINDS = {"ssh_fail_60s": "ssh_fail", "oom_kill_60s": "oom_kill"}
EVENT_COLUMNS = list(EVENT_KINDS)
# Constant on most hosts; carries no anomaly signal.
EXCLUDED = {"disk_used_pct"}
BASE_METRICS = [c for c in db.METRIC_COLUMNS if c not in EXCLUDED]
ALL_METRICS = BASE_METRICS + EVENT_COLUMNS
SHORT_S, LONG_S = 60, 300


def _interval(ts: np.ndarray) -> float:
    if len(ts) < 2:
        return 5.0
    return float(np.median(np.diff(ts))) or 5.0


def event_counts(sample_ts: np.ndarray, event_ts: np.ndarray, window_s: float) -> np.ndarray:
    """Count events in the half-open window (t - window_s, t] for each sample time t."""
    ev = np.sort(np.asarray(event_ts, dtype=float))
    hi = np.searchsorted(ev, sample_ts, side="right")
    lo = np.searchsorted(ev, sample_ts - window_s, side="right")
    return (hi - lo).astype(float)


def build(samples: pd.DataFrame, events: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return a feature frame indexed by sample timestamp.

    Per metric m: the raw value, `m_mean60`/`m_std60` (trailing 60 s including now),
    `m_mean300`/`m_std300` (trailing 300 s excluding now, so a spike never dilutes its own
    baseline) and `m_dev300 = m - m_mean300`. Every row depends only on the past.
    """
    df = samples.sort_values("ts").reset_index(drop=True)
    ts = df["ts"].to_numpy(dtype=float)
    step = _interval(ts)
    w_short = max(2, int(round(SHORT_S / step)))
    w_long = max(4, int(round(LONG_S / step)))

    base = df[BASE_METRICS].astype(float).copy()
    have_events = events is not None and not events.empty
    for col, kind in EVENT_KINDS.items():
        sel = events.loc[events["kind"] == kind, "ts"].to_numpy(dtype=float) if have_events else np.array([])
        base[col] = event_counts(ts, sel, SHORT_S)

    out = {}
    for m in ALL_METRICS:
        s = base[m]
        out[m] = s
        out[f"{m}_mean60"] = s.rolling(w_short, min_periods=max(2, w_short // 2)).mean()
        out[f"{m}_std60"] = s.rolling(w_short, min_periods=max(2, w_short // 2)).std()
        hist = s.shift(1)
        out[f"{m}_mean300"] = hist.rolling(w_long, min_periods=w_long // 2).mean()
        out[f"{m}_std300"] = hist.rolling(w_long, min_periods=w_long // 2).std()
        out[f"{m}_dev300"] = s - out[f"{m}_mean300"]
    feats = pd.DataFrame(out)
    feats.index = pd.Index(ts, name="ts")
    return _blank_after_gaps(feats, ts, step)


GAP_FACTOR = 3  # a step longer than this many sampling intervals is a gap, not jitter


def _blank_after_gaps(feats: pd.DataFrame, ts: np.ndarray, step: float) -> pd.DataFrame:
    """Set every feature to NaN for one long window after a sampling gap.

    When the machine sleeps or a VM pauses, the collector writes nothing. Rolling windows that
    span the hole would mix pre-sleep and post-sleep behaviour, and the first rate after the gap
    averages over the whole pause. Those rows are unreliable, so detectors skip them.
    """
    if len(ts) < 2:
        return feats
    resumed = ts[1:][np.diff(ts) > GAP_FACTOR * step]
    if resumed.size == 0:
        return feats
    bad = np.zeros(len(ts), dtype=bool)
    for t in resumed:
        bad |= (ts >= t) & (ts < t + LONG_S)
    feats = feats.copy()
    feats.loc[bad, :] = np.nan
    return feats


def forest_columns(feats: pd.DataFrame) -> list[str]:
    """Columns fed to the Isolation Forest: value, short-window stats and long-window deviation."""
    suffixes = ("", "_mean60", "_std60", "_dev300")
    return [f"{m}{s}" for m in ALL_METRICS for s in suffixes if f"{m}{s}" in feats.columns]
