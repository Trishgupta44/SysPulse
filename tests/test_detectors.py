import numpy as np
import pandas as pd
import pytest

from syspulse import db, detectors, features, synth


@pytest.fixture(scope="module")
def run():
    return synth.generate(seed=1)


@pytest.fixture(scope="module")
def feats(run):
    samples, events, _ = run
    return features.build(samples, events)


def baseline_mask(feats, truth):
    base = truth[truth.label == "baseline"].iloc[0]
    return (feats.index >= base.start_ts) & (feats.index < base.end_ts)


def test_synth_is_deterministic_and_covers_all_faults(run):
    a = synth.generate(seed=1)
    pd.testing.assert_frame_equal(run[0], a[0])
    labels = set(run[2].label)
    assert {"baseline", "cpu_burst", "memory_pressure", "fork_burst", "disk_io", "ssh_spam"} <= labels
    assert set(run[1].kind) == {"ssh_fail"}


def test_synth_columns_match_db_schema(run):
    assert list(run[0].columns) == db.SAMPLE_COLUMNS


def test_features_index_and_columns(feats, run):
    samples = run[0]
    assert len(feats) == len(samples)
    assert feats.index.is_monotonic_increasing
    for col in ("cpu_pct", "cpu_pct_mean60", "cpu_pct_std60", "cpu_pct_mean300", "cpu_pct_dev300",
                "ssh_fail_60s", "oom_kill_60s"):
        assert col in feats.columns


def test_features_do_not_leak_the_future(run):
    """Truncating the series must not change earlier feature rows."""
    samples, events, _ = run
    full = features.build(samples, events)
    cut = features.build(samples.iloc[:200], events[events.ts <= samples.ts.iloc[199]])
    pd.testing.assert_frame_equal(full.iloc[:200], cut, check_exact=False, rtol=1e-9)


def test_event_count_window():
    """Half-open window (t - 60, t]: events age out and are counted once they have happened."""
    events = pd.DataFrame({"ts": [101.0, 102.0, 109.0], "kind": ["ssh_fail"] * 3})
    counts = features.event_counts(np.array([100.0, 105.0, 110.0, 200.0]), events["ts"].to_numpy(), 60)
    assert list(counts) == [0, 2, 3, 0]


def test_event_features_flow_into_the_frame():
    samples = pd.DataFrame({c: [1.0] * 4 for c in db.SAMPLE_COLUMNS})
    samples["ts"] = [100.0, 105.0, 110.0, 115.0]
    events = pd.DataFrame({"ts": [101.0, 102.0, 109.0], "kind": ["ssh_fail"] * 3})
    assert list(features.build(samples, events)["ssh_fail_60s"]) == [0, 2, 3, 3]


@pytest.mark.parametrize("cls", [detectors.ZScoreDetector, detectors.IsolationForestDetector])
def test_detector_interface(cls, feats, run):
    det = cls().fit(feats[baseline_mask(feats, run[2])])
    out = det.score(feats)
    assert list(out.columns) == ["score", "is_anomaly"]
    assert out.index.equals(feats.index)
    assert out.is_anomaly.dtype == bool
    warmup = out.score.isna()
    assert not out.is_anomaly[warmup].any()


@pytest.mark.parametrize("cls, min_fraction", [
    (detectors.ZScoreDetector, 0.3),          # rolling baseline adapts to a sustained shift
    (detectors.IsolationForestDetector, 0.5),  # trained on clean data, so it keeps flagging
])
def test_detector_flags_cpu_burst_and_stays_quiet_on_baseline(cls, min_fraction, feats, run):
    truth = run[2]
    det = cls().fit(feats[baseline_mask(feats, truth)])
    out = det.score(feats)
    burst = truth[truth.label == "cpu_burst"].iloc[0]
    in_burst = (out.index >= burst.start_ts) & (out.index < burst.end_ts)
    base = baseline_mask(feats, truth) & out.score.notna().to_numpy()
    flagged = out.is_anomaly[in_burst]
    assert flagged.mean() >= min_fraction
    assert flagged.iloc[:3].any(), "burst must be caught within the first three samples"
    assert out.is_anomaly[base].mean() < 0.05


def test_isolation_forest_needs_training_data():
    with pytest.raises(ValueError, match="training"):
        detectors.IsolationForestDetector().fit(pd.DataFrame())


def test_zscore_constant_series_has_no_division_error():
    n = 100
    samples = pd.DataFrame({c: np.ones(n) for c in db.SAMPLE_COLUMNS})
    samples["ts"] = np.arange(n) * 5.0
    f = features.build(samples, None)
    out = detectors.ZScoreDetector().fit(f).score(f)
    assert np.isfinite(out.score.dropna()).all()
    assert not out.is_anomaly.any()


def test_forest_threshold_is_calibrated_on_rows_it_did_not_train_on(feats, run):
    det = detectors.IsolationForestDetector().fit(feats[baseline_mask(feats, run[2])])
    assert det.n_calibration >= 10
    assert det.n_train + det.n_calibration == feats[baseline_mask(feats, run[2])][det._cols].dropna().shape[0]
    # calibration rows come after training rows in time, so nothing in it was seen by the forest
    assert det.threshold > 0


def test_forest_rejects_tiny_training_set():
    import numpy as np
    n = 20
    samples = pd.DataFrame({c: np.random.default_rng(0).normal(size=n) for c in db.SAMPLE_COLUMNS})
    samples["ts"] = np.arange(n) * 5.0
    f = features.build(samples, None)
    with pytest.raises(ValueError, match="at least 30"):
        detectors.IsolationForestDetector().fit(f)


def test_mahalanobis_interface_and_burst(feats, run):
    truth = run[2]
    det = detectors.MahalanobisDetector().fit(feats[baseline_mask(feats, truth)])
    out = det.score(feats)
    assert list(out.columns) == ["score", "is_anomaly"]
    assert out.index.equals(feats.index)
    burst = truth[truth.label == "cpu_burst"].iloc[0]
    in_burst = (out.index >= burst.start_ts) & (out.index < burst.end_ts)
    assert out.is_anomaly[in_burst].mean() > 0.8      # magnitude-aware: a sustained burst stays flagged
    base = baseline_mask(feats, truth)
    assert out.is_anomaly[base].mean() < 0.05


def test_mahalanobis_score_grows_with_magnitude(feats, run):
    """The property the isolation forest lacks: a bigger deviation scores higher."""
    det = detectors.MahalanobisDetector().fit(feats[baseline_mask(feats, run[2])])
    row = feats.iloc[[100]].copy()
    small, big = row.copy(), row.copy()
    small["cpu_pct"] = row["cpu_pct"] + 20
    big["cpu_pct"] = row["cpu_pct"] + 90
    assert det.score(big)["score"].iloc[0] > det.score(small)["score"].iloc[0] > det.score(row)["score"].iloc[0]


def test_mahalanobis_needs_training_data_and_handles_constant_series():
    with pytest.raises(ValueError, match="training"):
        detectors.MahalanobisDetector().fit(pd.DataFrame())
    n = 120
    samples = pd.DataFrame({c: np.ones(n) for c in db.SAMPLE_COLUMNS})
    samples["ts"] = np.arange(n) * 5.0
    f = features.build(samples, None)
    out = detectors.MahalanobisDetector().fit(f).score(f)
    assert np.isfinite(out.score.dropna()).all()
    assert not out.is_anomaly.any()


def test_pipeline_fits_all_three_detectors(feats, run):
    from syspulse import pipeline
    mask = baseline_mask(feats, run[2])
    dets = pipeline.fit_detectors(feats, mask)
    assert set(dets) == {"zscore", "isolation_forest", "mahalanobis"}


def _steady(n=200, step=5.0):
    samples = pd.DataFrame({c: np.random.default_rng(1).normal(10, 1, n) for c in db.SAMPLE_COLUMNS})
    samples["ts"] = np.arange(n) * step
    return samples


def test_features_are_blanked_for_one_long_window_after_a_sampling_gap():
    samples = _steady()
    samples.loc[60:, "ts"] += 4000          # the machine slept for over an hour after row 59
    f = features.build(samples, None)
    resume = samples.ts.iloc[60]
    after = (f.index >= resume) & (f.index < resume + features.LONG_S)
    assert after.sum() > 10
    assert f[after].isna().all().all(), "rolling windows that span the gap are meaningless"
    assert f[f.index >= resume + features.LONG_S].notna().any(axis=None)


def test_rows_before_a_gap_are_unaffected():
    samples = _steady()
    clean = features.build(samples, None)
    slept = samples.copy()
    slept.loc[90:, "ts"] += 4000
    f = features.build(slept, None)
    pd.testing.assert_frame_equal(clean.iloc[:90], f.iloc[:90])


def test_normal_jitter_is_not_a_gap():
    samples = _steady()
    samples["ts"] += np.random.default_rng(2).uniform(-0.4, 0.4, len(samples))
    samples["ts"] = samples["ts"].cummax() + 1e-6 * np.arange(len(samples))
    f = features.build(samples, None)
    assert f.iloc[40:].notna().all().all()
