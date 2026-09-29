"""Command-line entry points: demo, evaluate, and the log parser's file and journal modes."""
import json
import subprocess

import pytest

from syspulse import db, demo, evaluate, logparser

SSH_LINE = json.dumps({
    "__REALTIME_TIMESTAMP": "1700000000000000", "__CURSOR": "c1", "SYSLOG_IDENTIFIER": "sshd",
    "MESSAGE": "Failed password for invalid user admin from 203.0.113.9 port 4444 ssh2",
})


def test_demo_writes_database_and_report(tmp_path, capsys):
    rc = demo.main(["--db", str(tmp_path / "d.db"), "--out", str(tmp_path / "out")])
    assert rc == 0
    assert (tmp_path / "out" / "results.md").exists() and (tmp_path / "out" / "results.json").exists()
    out = capsys.readouterr().out
    assert "Mahalanobis distance" in out and "Rolling z-score" in out
    conn = db.connect(str(tmp_path / "d.db"))
    assert conn.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0] > 0
    conn.close()


def test_demo_rerun_starts_clean_instead_of_stacking_runs(tmp_path):
    for _ in range(2):
        assert demo.main(["--db", str(tmp_path / "d.db"), "--out", str(tmp_path / "out")]) == 0
    conn = db.connect(str(tmp_path / "d.db"))
    assert conn.execute("SELECT COUNT(DISTINCT run_id) FROM ground_truth").fetchone()[0] == 1
    conn.close()


def test_evaluate_cli_reports_missing_ground_truth(tmp_path, capsys):
    path = str(tmp_path / "e.db")
    db.connect(path).close()
    assert evaluate.main(["--db", path, "--out", str(tmp_path / "o")]) == 2
    assert "no ground truth" in capsys.readouterr().err


def test_logparser_from_file(tmp_path, capsys):
    export = tmp_path / "journal.json"
    export.write_text(SSH_LINE + "\n" + "not json\n", encoding="utf-8")
    path = str(tmp_path / "l.db")
    assert logparser.main(["--db", path, "--from-file", str(export)]) == 0
    assert "1 new event" in capsys.readouterr().out
    conn = db.connect(path)
    assert conn.execute("SELECT kind FROM events").fetchone()[0] == "ssh_fail"
    conn.close()


def fake_journalctl(stdout="", returncode=0, stderr=""):
    def run(cmd, **kwargs):
        run.cmd = cmd
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)
    return run


def test_journal_mode_uses_since_on_first_run_and_cursor_afterwards(tmp_path, monkeypatch, capsys):
    cursor = tmp_path / "cur"
    runner = fake_journalctl(stdout=SSH_LINE + "\n")
    monkeypatch.setattr(logparser.subprocess, "run", runner)
    args = ["--db", str(tmp_path / "j.db"), "--cursor-file", str(cursor)]
    assert logparser.main(args) == 0
    assert any(a.startswith("--since=") for a in runner.cmd)
    assert cursor.read_text() == "c1"
    assert logparser.main(args) == 0
    assert "--after-cursor=c1" in runner.cmd
    assert "0 new event" in capsys.readouterr().out


def test_journalctl_failure_is_an_error_not_silence(monkeypatch):
    monkeypatch.setattr(logparser.subprocess, "run", fake_journalctl(returncode=1, stderr="no access"))
    with pytest.raises(RuntimeError, match="no access"):
        logparser.journal_lines(None, "1 hour ago")


def test_groundtruth_rejects_backwards_window(tmp_path):
    from syspulse import groundtruth
    with pytest.raises(SystemExit):
        groundtruth.main(["--db", str(tmp_path / "g.db"), "add", "--run-id", "r", "--label", "x",
                          "--start", "10", "--end", "5", "--fault", "1"])


def test_readonly_connection_reads_but_cannot_write(tmp_path):
    path = tmp_path / "ro.db"
    conn = db.connect(str(path))
    conn.execute("INSERT INTO events (ts, kind, message) VALUES (1, 'ssh_fail', 'x')")
    conn.commit()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    ro = db.connect(str(path), readonly=True)
    assert ro.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO events (ts, kind, message) VALUES (2, 'ssh_fail', 'y')")
    ro.close()
    assert not (tmp_path / "ro.db-wal").exists() and not (tmp_path / "ro.db-shm").exists()


def test_readonly_connection_to_a_missing_file_fails_instead_of_creating_it(tmp_path):
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        db.connect(str(tmp_path / "nope.db"), readonly=True)
    assert not (tmp_path / "nope.db").exists()
