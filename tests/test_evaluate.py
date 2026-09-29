import json

import numpy as np
import pandas as pd
import pytest

from syspulse import db, evaluate, groundtruth, pipeline, synth


def test_prf_arithmetic():
    y_true = pd.Series([1, 1, 0, 0], dtype=bool)
    y_pred = pd.Series([1, 0, 1, 0], dtype=bool)
    m = evaluate.prf(y_true, y_pred)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 1)
    assert m["precision"] == m["recall"] == m["f1"] == 0.5


def test_prf_no_predictions_is_zero_not_error():
    m = evaluate.prf(pd.Series([1, 0], dtype=bool), pd.Series([0, 0], dtype=bool))
    assert m["precision"] == 0.0 and m["recall"] == 0.0 and m["f1"] == 0.0


def truth_frame():
    return pd.DataFrame({
        "label": ["baseline", "cpu_burst", "recovery"],
        "start_ts": [0.0, 100.0, 160.0],
        "end_ts": [100.0, 160.0, 300.0],
        "is_fault": [0, 1, 0],
    })


def test_label_samples_extends_fault_by_grace():
    ts = np.array([50.0, 100.0, 159.0, 170.0, 200.0])
    y = evaluate.label_samples(ts, truth_frame(), grace_s=30)
    assert list(y) == [False, True, True, True, False]


def test_fault_events_detected_and_latency():
    ts = np.arange(0, 300, 5.0)
    pred = pd.Series(False, index=ts)
    pred[110.0] = True          # 10 s after the fault starts
    pred[250.0] = True          # false alarm far from any fault
    rows, false_alarms = evaluate.fault_events(pred, truth_frame(), grace_s=30)
    assert rows == [{"label": "cpu_burst", "detected": True, "latency_s": 10.0}]
    assert false_alarms == 1


def test_fault_event_missed():
    ts = np.arange(0, 300, 5.0)
    pred = pd.Series(False, index=ts)
    rows, false_alarms = evaluate.fault_events(pred, truth_frame(), grace_s=30)
    assert rows[0]["detected"] is False and rows[0]["latency_s"] is None
    assert false_alarms == 0


def test_train_mask_uses_first_part_of_baseline_only():
    idx = pd.Index(np.arange(0, 300, 5.0), name="ts")
    mask = pipeline.train_mask(idx, truth_frame(), train_frac=0.6)
    assert idx[mask].min() == 0.0 and idx[mask].max() < 60.0
    assert mask.sum() == 12


def test_train_mask_without_ground_truth_falls_back_to_leading_rows():
    idx = pd.Index(np.arange(0, 1000, 5.0), name="ts")
    mask = pipeline.train_mask(idx, pd.DataFrame(), train_frac=0.6)
    assert 0 < mask.sum() < len(idx)
    assert mask[: mask.sum()].all()


@pytest.fixture()
def populated():
    conn = db.connect(":memory:")
    synth.write(conn, *synth.generate(seed=3))
    return conn


def test_end_to_end_on_synthetic_run(populated, tmp_path):
    res = evaluate.run_evaluation(populated)
    assert set(res["detectors"]) == {"zscore", "isolation_forest", "mahalanobis"}
    for det in res["detectors"].values():
        assert 0.0 <= det["point"]["f1"] <= 1.0
        assert {"precision", "recall", "f1"} <= set(det["point"])
        assert det["fault_recall"] >= 0.6
    assert res["detectors"]["isolation_forest"]["point"]["f1"] > 0.5
    # every injected fault type shows up in the per-fault table
    labels = {r["label"] for r in res["detectors"]["zscore"]["per_fault"]}
    assert labels == {"cpu_burst", "memory_pressure", "fork_burst", "disk_io", "ssh_spam"}

    json_path, md_path = evaluate.write_report(res, tmp_path)
    assert json.loads(json_path.read_text())["run_id"] == res["run_id"]
    assert "Precision" in md_path.read_text()


def test_scored_anomalies_can_be_persisted(populated):
    res = evaluate.run_evaluation(populated, persist=True)
    n = populated.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]
    assert n > 0 and res["n_eval_rows"] > 0


def test_evaluate_without_ground_truth_raises_clear_error():
    conn = db.connect(":memory:")
    s, e, _ = synth.generate(seed=1)
    synth.write(conn, s, e, pd.DataFrame(columns=["run_id", "label", "start_ts", "end_ts", "is_fault"]))
    with pytest.raises(ValueError, match="ground truth"):
        evaluate.run_evaluation(conn)


def test_groundtruth_cli_adds_rows(tmp_path):
    path = str(tmp_path / "gt.db")
    rc = groundtruth.main(["--db", path, "add", "--run-id", "r1", "--label", "cpu_burst",
                           "--start", "10", "--end", "70", "--fault", "1"])
    assert rc == 0
    conn = db.connect(path)
    row = conn.execute("SELECT run_id, label, start_ts, end_ts, is_fault FROM ground_truth").fetchone()
    assert tuple(row) == ("r1", "cpu_burst", 10.0, 70.0, 1)
