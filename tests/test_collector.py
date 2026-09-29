"""Integration tests for the C collector. They run the real binary against fixture /proc files."""
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from syspulse import db

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build" / "syspulse-collector"
FIXTURES = ROOT / "tests" / "fixtures" / "proc"

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="collector reads Linux /proc")


@pytest.fixture(scope="module")
def collector():
    if shutil.which("make") is None:
        pytest.fail("make is required to build the collector")
    build = subprocess.run(["make", "build"], cwd=ROOT, capture_output=True, text=True)
    if build.returncode != 0:
        pytest.fail(f"collector failed to build (is libsqlite3-dev installed?):\n{build.stderr}")
    return str(BINARY)


def run_once(collector, dbfile, root=FIXTURES, interval="0.05"):
    return subprocess.run(
        [collector, "--db", str(dbfile), "--once", "--interval", interval, "--proc-root", str(root)],
        capture_output=True, text=True, timeout=30,
    )


def rows(dbfile):
    conn = sqlite3.connect(dbfile)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM samples ORDER BY ts")]
    finally:
        conn.close()


def test_once_writes_one_row_from_fixture_values(collector, tmp_path):
    dbfile = tmp_path / "m.db"
    before = time.time()
    res = run_once(collector, dbfile)
    assert res.returncode == 0, res.stderr
    (row,) = rows(dbfile)
    assert before - 1 <= row["ts"] <= time.time() + 1
    assert row["mem_pct"] == pytest.approx(25.0)      # 12 GB of 16 GB available
    assert row["swap_pct"] == pytest.approx(25.0)     # 3 GB of 4 GB swap free
    assert row["load1"] == pytest.approx(0.52)
    assert row["load5"] == pytest.approx(0.41)
    assert row["load15"] == pytest.approx(0.30)
    assert row["procs_running"] == 3 and row["procs_total"] == 412
    # Static fixtures mean no counter movement between the two reads.
    for rate in ("cpu_pct", "iowait_pct", "disk_read_kbs", "disk_write_kbs",
                 "net_rx_kbs", "net_tx_kbs", "ctx_switches_ps", "forks_ps"):
        assert row[rate] == 0.0, rate
    assert 0.0 < row["disk_used_pct"] <= 100.0


def test_schema_matches_python_definition(collector, tmp_path):
    dbfile = tmp_path / "m.db"
    assert run_once(collector, dbfile).returncode == 0
    conn = sqlite3.connect(dbfile)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(samples)")]
    assert cols == db.SAMPLE_COLUMNS
    conn.close()
    # Python side can open the collector's database and add its own tables.
    py = db.connect(str(dbfile))
    assert py.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1


def test_database_uses_wal_mode(collector, tmp_path):
    dbfile = tmp_path / "m.db"
    assert run_once(collector, dbfile).returncode == 0
    mode = sqlite3.connect(dbfile).execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_repeated_runs_append_rows(collector, tmp_path):
    dbfile = tmp_path / "m.db"
    for _ in range(3):
        assert run_once(collector, dbfile).returncode == 0
        time.sleep(0.01)
    assert len(rows(dbfile)) == 3


def test_missing_proc_root_fails_cleanly(collector, tmp_path):
    res = run_once(collector, tmp_path / "m.db", root=tmp_path / "nope")
    assert res.returncode == 1
    assert "cannot read" in res.stderr


def test_rejects_bad_interval(collector, tmp_path):
    res = run_once(collector, tmp_path / "m.db", interval="-3")
    assert res.returncode == 2
    assert "--interval" in res.stderr


def test_unwritable_database_path_fails_cleanly(collector, tmp_path):
    res = run_once(collector, tmp_path / "no" / "such" / "dir" / "m.db")
    assert res.returncode == 1
    assert "cannot open" in res.stderr


def test_sigterm_stops_cleanly_after_writing_rows(collector, tmp_path):
    dbfile = tmp_path / "m.db"
    proc = subprocess.Popen(
        [collector, "--db", str(dbfile), "--interval", "0.1", "--proc-root", str(FIXTURES)],
        stderr=subprocess.PIPE, text=True,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        if dbfile.exists() and len(rows(dbfile)) >= 3:
            break
        time.sleep(0.05)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=10) == 0
    assert "stopped by signal" in proc.stderr.read()
    assert len(rows(dbfile)) >= 3
    # A clean close checkpoints and removes the WAL so the file is self-contained.
    assert not (tmp_path / "m.db-wal").exists() or (tmp_path / "m.db-wal").stat().st_size == 0


def test_real_proc_produces_sane_values(collector, tmp_path):
    """Smoke test against the live /proc of the machine running the tests."""
    dbfile = tmp_path / "m.db"
    res = subprocess.run([collector, "--db", str(dbfile), "--once", "--interval", "1"],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    (row,) = rows(dbfile)
    assert 0 <= row["cpu_pct"] <= 100
    assert 0 < row["mem_pct"] <= 100
    assert row["procs_total"] > 0
    assert row["ctx_switches_ps"] > 0
