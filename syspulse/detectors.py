"""Anomaly detectors sharing one interface.

    det = Detector().fit(clean_feature_frame)
    out = det.score(feature_frame)      # DataFrame[score, is_anomaly], same index

`is_anomaly` is `score > det.threshold`. Rows still in the rolling-window warm-up get a NaN
score and are never flagged.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from . import features

ABS_FLOOR_DEFAULT = 1.0
ABS_FLOOR = {"load1": 0.25, "load5": 0.25, "load15": 0.25}
REL_FLOOR = 0.05


MIN_TRAIN_ROWS = 30
CAL_FRAC = 0.25


def calibration_split(x: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split clean rows in time order: the first 75% fit the model, the last 25% calibrate the alarm.

    Scores on rows the model never saw show what normal looks like for unseen data. Using the
    training rows themselves would set the threshold too low and over-flag.
    """
    n_cal = max(10, int(len(x) * CAL_FRAC))
    return x.iloc[:-n_cal], x.iloc[-n_cal:]


class ZScoreDetector:
    """Baseline: max |z| over all metrics against a trailing 300 s window.

    The standard deviation is floored (5% of the mean, or an absolute minimum) so quiet metrics
    such as event counts do not divide by zero or turn tiny wiggles into huge scores.
    """

    name = "zscore"

    def __init__(self, threshold: float = 4.0):
        self.threshold = threshold

    def fit(self, feats: pd.DataFrame | None = None) -> ZScoreDetector:
        return self  # stateless: the baseline is the rolling window itself

    def score(self, feats: pd.DataFrame) -> pd.DataFrame:
        zs = {}
        for m in features.ALL_METRICS:
            mean, std = feats[f"{m}_mean300"], feats[f"{m}_std300"]
            floor = np.maximum(REL_FLOOR * mean.abs(), ABS_FLOOR.get(m, ABS_FLOOR_DEFAULT))
            zs[m] = ((feats[m] - mean) / np.maximum(std, floor)).abs()
        score = pd.DataFrame(zs).max(axis=1, skipna=False)
        out = pd.DataFrame({"score": score})
        out["is_anomaly"] = (out["score"] > self.threshold).fillna(False).astype(bool)
        return out


class IsolationForestDetector:
    """Isolation Forest trained on a clean window.

    Clean rows are split in time order by `calibration_split`; the alert threshold is a high
    quantile of scores on rows the forest never saw.
    """

    name = "isolation_forest"
    MIN_ROWS = MIN_TRAIN_ROWS

    def __init__(self, quantile: float = 0.995, n_estimators: int = 200, seed: int = 0):
        self.quantile = quantile
        self.n_estimators = n_estimators
        self.seed = seed
        self.threshold = float("inf")
        self.n_train = 0
        self.n_calibration = 0
        self._cols: list[str] = []

    def fit(self, feats: pd.DataFrame) -> IsolationForestDetector:
        if feats is None or feats.empty:
            raise ValueError("Isolation Forest needs training data: the clean window is empty")
        self._cols = features.forest_columns(feats)
        x = feats[self._cols].dropna()
        if len(x) < self.MIN_ROWS:
            raise ValueError(
                f"Isolation Forest needs at least {self.MIN_ROWS} complete training rows, got {len(x)}")
        train, cal = calibration_split(x)
        self.n_train, self.n_calibration = len(train), len(cal)
        self._scaler = StandardScaler().fit(train)
        self._model = IsolationForest(n_estimators=self.n_estimators, random_state=self.seed)
        self._model.fit(self._scaler.transform(train))
        cal_scores = -self._model.score_samples(self._scaler.transform(cal))
        train_scores = -self._model.score_samples(self._scaler.transform(train))
        self.threshold = float(max(np.quantile(cal_scores, self.quantile),
                                   np.quantile(train_scores, self.quantile)))
        return self

    def score(self, feats: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame({"score": np.nan}, index=feats.index)
        x = feats[self._cols]
        ok = x.notna().all(axis=1)
        if ok.any():
            out.loc[ok, "score"] = -self._model.score_samples(self._scaler.transform(x[ok]))
        out["is_anomaly"] = (out["score"] > self.threshold).fillna(False).astype(bool)
        return out


class MahalanobisDetector:
    """Multivariate distance from the clean baseline, using magnitude.

    Raw metric values are log-compressed (counters such as context switches span orders of
    magnitude), standardised, and scored by Mahalanobis distance under a Ledoit-Wolf shrunk
    covariance, which stays well behaved with only a few dozen training rows. Unlike the
    Isolation Forest, the score keeps growing as a value moves further from normal.
    """

    name = "mahalanobis"

    def __init__(self, quantile: float = 0.995):
        self.quantile = quantile
        self.threshold = float("inf")
        self.n_train = 0
        self.n_calibration = 0
        self._cols = list(features.ALL_METRICS)

    @staticmethod
    def _prepare(x: pd.DataFrame) -> np.ndarray:
        return np.log1p(x.clip(lower=0).to_numpy(dtype=float))

    def fit(self, feats: pd.DataFrame) -> MahalanobisDetector:
        if feats is None or feats.empty:
            raise ValueError("Mahalanobis needs training data: the clean window is empty")
        x = feats[self._cols].dropna()
        if len(x) < MIN_TRAIN_ROWS:
            raise ValueError(f"Mahalanobis needs at least {MIN_TRAIN_ROWS} training rows, got {len(x)}")
        train, cal = calibration_split(x)
        self.n_train, self.n_calibration = len(train), len(cal)
        self._scaler = StandardScaler().fit(self._prepare(train))
        self._cov = LedoitWolf().fit(self._scaler.transform(self._prepare(train)))
        cal_scores = self._distance(cal)
        train_scores = self._distance(train)
        self.threshold = float(max(np.quantile(cal_scores, self.quantile),
                                   np.quantile(train_scores, self.quantile)))
        return self

    def _distance(self, x: pd.DataFrame) -> np.ndarray:
        z = self._scaler.transform(self._prepare(x))
        return np.sqrt(self._cov.mahalanobis(z))

    def score(self, feats: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame({"score": np.nan}, index=feats.index)
        x = feats[self._cols]
        ok = x.notna().all(axis=1)
        if ok.any():
            out.loc[ok, "score"] = self._distance(x[ok])
        out["is_anomaly"] = (out["score"] > self.threshold).fillna(False).astype(bool)
        return out
