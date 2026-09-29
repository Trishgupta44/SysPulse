import json

from syspulse import db, logparser


def entry(msg, ident="sshd", ts_us=1_700_000_000_000_000, cursor="c1", transport="syslog"):
    return json.dumps({
        "__REALTIME_TIMESTAMP": str(ts_us),
        "__CURSOR": cursor,
        "SYSLOG_IDENTIFIER": ident,
        "_TRANSPORT": transport,
        "MESSAGE": msg,
    })


def test_classify_failed_password():
    m = "Failed password for invalid user admin from 10.0.0.5 port 4242 ssh2"
    assert logparser.classify(m, "sshd") == "ssh_fail"


def test_classify_invalid_user_and_pam():
    assert logparser.classify("Invalid user bob from 1.2.3.4 port 22", "sshd") == "ssh_fail"
    assert logparser.classify("pam_unix(sshd:auth): authentication failure; user=root", "sshd") == "ssh_fail"


def test_successful_login_is_ignored():
    assert logparser.classify("Accepted publickey for trish from 1.2.3.4 port 22", "sshd") is None


def test_ssh_message_from_other_program_is_ignored():
    assert logparser.classify("Failed password for root from 1.2.3.4", "cron") is None


def test_classify_oom_kill():
    m = "Out of memory: Killed process 4321 (stress-ng) total-vm:1234kB"
    assert logparser.classify(m, "kernel") == "oom_kill"
    m2 = "Memory cgroup out of memory: Killed process 77 (python) total-vm:1kB"
    assert logparser.classify(m2, "kernel") == "oom_kill"


def test_oom_summary_line_not_double_counted():
    assert logparser.classify("oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null)", "kernel") is None


def test_parse_binary_message_list():
    raw = json.dumps({
        "__REALTIME_TIMESTAMP": "1700000000000000",
        "SYSLOG_IDENTIFIER": "sshd",
        "MESSAGE": [int(b) for b in b"Failed password for x from 1.1.1.1"],
    })
    ev = logparser.parse_entry(raw)
    assert ev is not None and ev["kind"] == "ssh_fail"


def test_ingest_inserts_and_dedupes():
    conn = db.connect(":memory:")
    lines = [
        entry("Failed password for root from 1.1.1.1 port 1 ssh2", cursor="a", ts_us=1_700_000_000_000_001),
        entry("Accepted password for root from 1.1.1.1", cursor="b", ts_us=1_700_000_001_000_000),
        entry("Out of memory: Killed process 9 (x)", ident="kernel", cursor="c", ts_us=1_700_000_002_000_000),
    ]
    added, last = logparser.ingest(conn, lines)
    assert added == 2 and last == "c"
    added_again, _ = logparser.ingest(conn, lines)
    assert added_again == 0
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM events ORDER BY ts")]
    assert kinds == ["ssh_fail", "oom_kill"]


def test_ingest_skips_malformed_lines():
    conn = db.connect(":memory:")
    added, last = logparser.ingest(conn, ["not json", "", entry("Failed password for r from 1.1.1.1")])
    assert added == 1


def test_timestamp_converted_to_seconds():
    ev = logparser.parse_entry(entry("Failed password for r from 1.1.1.1", ts_us=1_700_000_000_500_000))
    assert abs(ev["ts"] - 1_700_000_000.5) < 1e-6


def test_cursor_not_advanced_when_write_fails(tmp_path):
    cursor_file = tmp_path / "cursor"
    cursor_file.write_text("old")
    lines = [entry("Failed password for r from 1.1.1.1", cursor="new")]

    class Boom:
        total_changes = 0

        def executemany(self, *a, **k):
            raise RuntimeError("disk full")

        def commit(self):
            pass

    import pytest

    with pytest.raises(RuntimeError, match="disk full"):
        logparser.process(Boom(), lines, str(cursor_file))
    assert cursor_file.read_text() == "old"


def test_process_advances_cursor_after_success(tmp_path):
    conn = db.connect(":memory:")
    cursor_file = tmp_path / "cursor"
    added = logparser.process(conn, [entry("Failed password for r from 1.1.1.1", cursor="zz")], str(cursor_file))
    assert added == 1 and cursor_file.read_text() == "zz"
