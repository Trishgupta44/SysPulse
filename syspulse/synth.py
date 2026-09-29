"""Deterministic synthetic telemetry with known fault windows.

Used by unit tests, CI (no stress-ng needed) and the demo. Shapes mimic what the real
fault injector produces on a quiet host, but this is not a substitute for the live run.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db

T0 = 1_700_000_000.0
DEFAULT_FAULTS = [
    ("cpu_burst", 60), ("memory_pressure", 75), ("fork_burst", 45),
    ("disk_io", 60), ("ssh_spam", 45),
]


def _ar1(rng, n, phi, sigma):
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal(0, sigma)
    return x


def _ramp(n, rise=4):
    return np.minimum(np.arange(1, n + 1) / rise, 1.0)


def generate(seed: int = 0, baseline_s: int = 900, gap_s: int = 120, faults=None,
             interval: int = 5, t0: float = T0):
    """Return (samples, events, ground_truth) DataFrames.

    Timeline: baseline, then for each fault a fault window followed by a recovery gap.
    """
    faults = DEFAULT_FAULTS if faults is None else faults
    rng = np.random.default_rng(seed)
    plan = [("baseline", baseline_s, False)]
    for name, dur in faults:
        plan += [(name, dur, True), ("recovery", gap_s, False)]
    total = sum(d for _, d, _ in plan)
    n = total // interval
    ts = t0 + np.arange(n) * interval

    d = {
        "cpu_pct": 6 + _ar1(rng, n, 0.6, 1.2),
        "iowait_pct": np.abs(0.5 + _ar1(rng, n, 0.4, 0.2)),
        "mem_pct": 40 + np.cumsum(rng.normal(0, 0.02, n)),
        "swap_pct": np.abs(1 + _ar1(rng, n, 0.5, 0.05)),
        "load1": np.abs(0.3 + _ar1(rng, n, 0.8, 0.05)),
        "procs_running": 1.0 + rng.poisson(0.6, n),
        "procs_total": 220.0 + rng.integers(-3, 4, n),
        "disk_read_kbs": np.abs(rng.normal(20, 10, n)),
        "disk_write_kbs": np.abs(rng.normal(60, 25, n)),
        "net_rx_kbs": np.abs(rng.normal(40, 15, n)),
        "net_tx_kbs": np.abs(rng.normal(25, 10, n)),
        "ctx_switches_ps": 3000 + rng.normal(0, 250, n),
        "forks_ps": np.abs(rng.normal(2, 0.8, n)),
        "disk_used_pct": np.full(n, 55.0),
    }
    d["load5"] = pd.Series(d["load1"]).rolling(12, min_periods=1).mean().to_numpy()
    d["load15"] = pd.Series(d["load1"]).rolling(36, min_periods=1).mean().to_numpy()
    # Benign one-sample blips so the baseline is not trivially flat.
    for i in rng.choice(np.arange(20, baseline_s // interval), size=4, replace=False):
        d["cpu_pct"][i] += rng.uniform(8, 14)

    truth, event_ts = [], []
    cursor = 0
    for name, dur, is_fault in plan:
        k = dur // interval
        sl = slice(cursor, cursor + k)
        r = _ramp(k)
        if name == "cpu_burst":
            d["cpu_pct"][sl] += 70 * r
            d["load1"][sl] += 4 * r
            d["ctx_switches_ps"][sl] += 8000 * r
        elif name == "memory_pressure":
            d["mem_pct"][sl] += 35 * r
            d["swap_pct"][sl] += 10 * r
            d["iowait_pct"][sl] += 3 * r
        elif name == "fork_burst":
            d["forks_ps"][sl] += 300 * r
            d["procs_total"][sl] += np.floor(250 * r)
            d["ctx_switches_ps"][sl] += 15000 * r
            d["cpu_pct"][sl] += 15 * r
        elif name == "disk_io":
            d["disk_write_kbs"][sl] += 80000 * r
            d["disk_read_kbs"][sl] += 20000 * r
            d["iowait_pct"][sl] += 25 * r
            d["cpu_pct"][sl] += 10 * r
        elif name == "ssh_spam":
            d["cpu_pct"][sl] += 2 * r
            start = t0 + cursor * interval
            event_ts += list(start + np.sort(rng.uniform(0, dur, dur * 2)))
        truth.append((name, t0 + cursor * interval, t0 + (cursor + k) * interval, int(is_fault)))
        cursor += k

    samples = pd.DataFrame({"ts": ts, **d})[db.SAMPLE_COLUMNS].astype(float)
    events = pd.DataFrame({"ts": event_ts, "kind": "ssh_fail", "message": "synthetic failed password"})
    ground_truth = pd.DataFrame(truth, columns=["label", "start_ts", "end_ts", "is_fault"])
    ground_truth.insert(0, "run_id", f"synth-{seed}")
    return samples, events, ground_truth


def write(conn, samples, events, truth) -> None:
    """Persist generated frames into an open SysPulse database."""
    conn.executemany(
        f"INSERT OR REPLACE INTO samples ({','.join(db.SAMPLE_COLUMNS)}) "
        f"VALUES ({','.join('?' * len(db.SAMPLE_COLUMNS))})",
        samples.itertuples(index=False, name=None),
    )
    conn.executemany("INSERT OR IGNORE INTO events (ts, kind, message) VALUES (?,?,?)",
                     events[["ts", "kind", "message"]].itertuples(index=False, name=None))
    conn.executemany(
        "INSERT INTO ground_truth (run_id, label, start_ts, end_ts, is_fault) VALUES (?,?,?,?,?)",
        truth[["run_id", "label", "start_ts", "end_ts", "is_fault"]].itertuples(index=False, name=None),
    )
    conn.commit()
