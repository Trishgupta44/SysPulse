"""Shared SQLite schema and connection helpers.

The C collector creates the `samples` table with identical DDL; a test asserts the two agree.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path

DEFAULT_DB = "/var/lib/syspulse/metrics.db"

SAMPLE_COLUMNS = [
    "ts", "cpu_pct", "iowait_pct", "mem_pct", "swap_pct",
    "load1", "load5", "load15", "procs_running", "procs_total",
    "disk_read_kbs", "disk_write_kbs", "net_rx_kbs", "net_tx_kbs",
    "ctx_switches_ps", "forks_ps", "disk_used_pct",
]
METRIC_COLUMNS = SAMPLE_COLUMNS[1:]

SAMPLES_DDL = """CREATE TABLE IF NOT EXISTS samples (
  ts REAL PRIMARY KEY,
  cpu_pct REAL, iowait_pct REAL, mem_pct REAL, swap_pct REAL,
  load1 REAL, load5 REAL, load15 REAL,
  procs_running REAL, procs_total REAL,
  disk_read_kbs REAL, disk_write_kbs REAL,
  net_rx_kbs REAL, net_tx_kbs REAL,
  ctx_switches_ps REAL, forks_ps REAL, disk_used_pct REAL
)"""

SCHEMA = [
    SAMPLES_DDL,
    """CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  UNIQUE (ts, kind, message)
)""",
    """CREATE TABLE IF NOT EXISTS ground_truth (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  label TEXT NOT NULL,
  start_ts REAL NOT NULL,
  end_ts REAL NOT NULL,
  is_fault INTEGER NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS anomalies (
  ts REAL NOT NULL,
  detector TEXT NOT NULL,
  score REAL NOT NULL,
  is_anomaly INTEGER NOT NULL,
  PRIMARY KEY (ts, detector)
)""",
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts)",
]


def db_path(explicit: str | None = None) -> str:
    return explicit or os.environ.get("SYSPULSE_DB", DEFAULT_DB)


def connect(path: str | None = None, readonly: bool = False) -> sqlite3.Connection:
    """Open the database, creating the file, parent directory and schema if needed.

    With readonly=True the file must already exist and nothing is created or changed, so it is
    safe on bundled reference data and on read-only filesystems.
    """
    p = db_path(path)
    if readonly:
        conn = sqlite3.connect(Path(p).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    if p != ":memory:":
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10)
    conn.row_factory = sqlite3.Row
    with contextlib.suppress(sqlite3.DatabaseError):  # e.g. a filesystem that cannot do WAL
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    for stmt in SCHEMA:
        conn.execute(stmt)
    conn.commit()
    return conn
