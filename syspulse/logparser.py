"""Parse journald entries for failed SSH logins and OOM kills into the `events` table.

Usage:
  python -m syspulse.logparser                      # read new journal entries since the cursor
  python -m syspulse.logparser --from-file dump.json  # ingest a `journalctl -o json` export
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable

from . import db

SSH_IDENTS = {"sshd", "sshd-session"}
SSH_FAIL = re.compile(
    r"(Failed (password|publickey|keyboard-interactive/pam)\b"
    r"|Invalid user\b"
    r"|authentication failure\b"
    r"|Connection closed by (invalid|authenticating) user\b)",
    re.IGNORECASE,
)
OOM_KILL = re.compile(r"(Memory cgroup out of memory|Out of memory): Killed process \d+", re.IGNORECASE)
OOMD_KILL = re.compile(r"Killed .* due to memory pressure", re.IGNORECASE)

# One journalctl query: '+' is a logical OR between match groups.
JOURNAL_MATCHES = [
    "SYSLOG_IDENTIFIER=sshd", "+", "SYSLOG_IDENTIFIER=sshd-session", "+",
    "_TRANSPORT=kernel", "+", "SYSLOG_IDENTIFIER=kernel", "+",
    "SYSLOG_IDENTIFIER=systemd-oomd",
]


def classify(message: str, ident: str) -> str | None:
    """Return the event kind for a journal message, or None if it is not interesting."""
    if ident in SSH_IDENTS and SSH_FAIL.search(message):
        return "ssh_fail"
    if ident in ("kernel", "systemd-oomd") and (OOM_KILL.search(message) or OOMD_KILL.search(message)):
        return "oom_kill"
    return None


def _message_text(raw) -> str:
    if isinstance(raw, list):  # journald encodes non-UTF8 messages as byte arrays
        return bytes(raw).decode("utf-8", "replace")
    return raw if isinstance(raw, str) else ""


def parse_entry(line: str) -> dict | None:
    """Parse one JSON journal line into an event dict, or None."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    message = _message_text(obj.get("MESSAGE"))
    ident = obj.get("SYSLOG_IDENTIFIER") or obj.get("_COMM") or ""
    if obj.get("_TRANSPORT") == "kernel" and not ident:
        ident = "kernel"
    kind = classify(message, ident)
    if kind is None:
        return None
    try:
        ts = int(obj["__REALTIME_TIMESTAMP"]) / 1_000_000
    except (KeyError, ValueError, TypeError):
        return None
    return {"ts": ts, "kind": kind, "message": message[:500], "cursor": obj.get("__CURSOR")}


def ingest(conn, lines: Iterable[str]) -> tuple[int, str | None]:
    """Insert events for matching lines. Returns (rows_added, last_cursor_seen).

    The last cursor covers every line seen, not only matches, so re-runs skip old input.
    """
    rows, last_cursor = [], None
    for line in lines:
        try:
            cur = json.loads(line).get("__CURSOR") if line.strip() else None
        except (json.JSONDecodeError, AttributeError):
            cur = None
        if cur:
            last_cursor = cur
        ev = parse_entry(line)
        if ev:
            rows.append((ev["ts"], ev["kind"], ev["message"]))
    before = conn.total_changes
    conn.executemany("INSERT OR IGNORE INTO events (ts, kind, message) VALUES (?, ?, ?)", rows)
    conn.commit()
    return conn.total_changes - before, last_cursor


def _read_cursor(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read().strip()
    except FileNotFoundError:
        return None
    return text or None


def _write_cursor(path: str, cursor: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(cursor)
    os.replace(tmp, path)


def process(conn, lines: Iterable[str], cursor_file: str | None) -> int:
    """Ingest lines, then advance the cursor. The cursor moves only after a successful write."""
    added, last = ingest(conn, lines)
    if cursor_file and last:
        _write_cursor(cursor_file, last)
    return added


def journal_lines(cursor: str | None, since: str) -> list[str]:
    cmd = ["journalctl", "-o", "json", "--no-pager", "-q"]
    cmd += [f"--after-cursor={cursor}"] if cursor else [f"--since={since}"]
    cmd += JOURNAL_MATCHES
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(f"journalctl failed: {out.stderr.strip()}")
    return out.stdout.splitlines()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None)
    ap.add_argument("--from-file", help="ingest a journalctl -o json export instead of reading journald")
    ap.add_argument("--cursor-file", default=os.environ.get("SYSPULSE_CURSOR", "/var/lib/syspulse/journal.cursor"))
    ap.add_argument("--since", default="1 hour ago", help="first-run lookback when no cursor exists")
    args = ap.parse_args(argv)

    conn = db.connect(args.db)
    if args.from_file:
        with open(args.from_file, encoding="utf-8") as fh:
            added = process(conn, fh, None)
    else:
        os.makedirs(os.path.dirname(args.cursor_file) or ".", exist_ok=True)
        lines = journal_lines(_read_cursor(args.cursor_file), args.since)
        added = process(conn, lines, args.cursor_file)
    print(f"logparser: {added} new event(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
