#!/usr/bin/env bash
# Timed fault-injection scenario that produces ground truth for SysPulse.
#
# Phases: baseline (idle), then cpu_burst, memory_pressure, fork_burst, disk_io, ssh_spam,
# each followed by an idle recovery. Every phase is recorded in the ground_truth table.
# The collector must already be running (make install-service, or `make run-collector`).
#
# Environment:
#   SYSPULSE_DB     database path        (default /var/lib/syspulse/metrics.db)
#   SYSPULSE_PY     python interpreter   (default: .venv/bin/python if present, else python3)
#   BASELINE_S      idle baseline length (default 900)
#   FAULT_S         length of each fault (default 60)
#   GAP_S           recovery after each  (default 90)
#   MEM_PCT         memory to pin        (default 50, percent of total RAM)
#   LOAD_TOOL       auto (default), stress-ng, or builtin. auto uses stress-ng when installed and
#                   otherwise the builtin bash/python load generators, with a warning.
#   SKIP_EVAL=1     do not run the evaluator at the end
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export SYSPULSE_DB="${SYSPULSE_DB:-/var/lib/syspulse/metrics.db}"
export SYSPULSE_CURSOR="${SYSPULSE_CURSOR:-$(dirname "$SYSPULSE_DB")/journal.cursor}"
if [[ -z "${SYSPULSE_PY:-}" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then SYSPULSE_PY="$ROOT/.venv/bin/python"; else SYSPULSE_PY="python3"; fi
fi
BASELINE_S="${BASELINE_S:-900}"
FAULT_S="${FAULT_S:-60}"
GAP_S="${GAP_S:-90}"
MEM_PCT="${MEM_PCT:-50}"
RUN_ID="run-$(date +%Y%m%d%H%M%S)"
TMP_DIR="$(mktemp -d /tmp/syspulse-inject.XXXXXX)"

die() { echo "inject_faults: $*" >&2; exit 1; }
now() { date +%s.%N; }
py() { (cd "$ROOT" && "$SYSPULSE_PY" -m "$@"); }

cleanup() {
  jobs -p | xargs -r kill 2>/dev/null || true
  pkill -f "stress-ng.*syspulse-inject" 2>/dev/null || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

# --- preflight -----------------------------------------------------------------------------
LOAD_TOOL="${LOAD_TOOL:-auto}"
if [[ "$LOAD_TOOL" == auto ]]; then
  if command -v stress-ng >/dev/null; then LOAD_TOOL=stress-ng; else
    LOAD_TOOL=builtin
    echo "inject_faults: stress-ng not installed, using the builtin load generators (sudo apt install stress-ng to change)" >&2
  fi
fi
if [[ "$LOAD_TOOL" == stress-ng ]]; then
  command -v stress-ng >/dev/null || die "stress-ng not found (sudo apt install stress-ng)"
elif [[ "$LOAD_TOOL" != builtin ]]; then
  die "LOAD_TOOL must be auto, stress-ng or builtin"
fi
command -v python3 >/dev/null || die "python3 not found"
command -v logger >/dev/null || die "logger not found (part of util-linux)"
fresh="$(cd "$ROOT" && "$SYSPULSE_PY" - <<'PY'
import os, sqlite3, time
p = os.environ["SYSPULSE_DB"]
try:
    row = sqlite3.connect(p).execute("SELECT MAX(ts) FROM samples").fetchone()
    print(int(time.time() - row[0]) if row and row[0] else "none")
except Exception:
    print("none")
PY
)"
[[ "$fresh" != "none" && "$fresh" -le 20 ]] || die "collector is not writing to $SYSPULSE_DB (newest sample age: $fresh)"

# --- helpers -------------------------------------------------------------------------------
record() { # label is_fault start end
  py syspulse.groundtruth --db "$SYSPULSE_DB" add --run-id "$RUN_ID" --label "$1" --fault "$2" --start "$3" --end "$4"
  printf '  recorded %-16s fault=%s  %.0fs\n' "$1" "$2" "$(awk -v a="$3" -v b="$4" 'BEGIN{print b-a}')"
}

phase() { # label is_fault seconds command...
  local label="$1" fault="$2" secs="$3"; shift 3
  echo "[$(date +%T)] $label (${secs}s)"
  local start end
  start="$(now)"
  "$@"
  end="$(now)"
  record "$label" "$fault" "$start" "$end"
}

idle() { sleep "$1"; }
# --- load generators: stress-ng, or the builtin fallback -----------------------------------
# SECONDS is a bash builtin, so the busy loops below do not fork just to read the clock.
builtin_cpu() {
  local pids=() i
  for ((i = 0; i < $(nproc); i++)); do
    ( t0=$SECONDS; while (( SECONDS - t0 < FAULT_S )); do :; done ) & pids+=($!)
  done
  wait "${pids[@]}"
}
builtin_mem() { # pins MEM_PCT percent of currently available memory, touching every page
  python3 - "$MEM_PCT" "$FAULT_S" <<'PY'
import sys, time
pct, secs = float(sys.argv[1]), float(sys.argv[2])
avail = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemAvailable")) * 1024
n = int(avail * pct / 100)
buf = bytearray(n)
for i in range(0, n, 4096):
    buf[i] = 1
time.sleep(secs)
PY
}
builtin_fork() { # eight loops that fork and exec /bin/true as fast as they can
  local pids=() i
  for i in 1 2 3 4 5 6 7 8; do
    ( t0=$SECONDS; while (( SECONDS - t0 < FAULT_S )); do /bin/true; done ) & pids+=($!)
  done
  wait "${pids[@]}"
}
builtin_disk() { # sequential write with fsync then read, repeated; falls back if O_DIRECT is refused
  local t0=$SECONDS f="$TMP_DIR/io.bin"
  while (( SECONDS - t0 < FAULT_S )); do
    dd if=/dev/zero of="$f" bs=1M count=256 oflag=direct conv=fsync status=none 2>/dev/null       || dd if=/dev/zero of="$f" bs=1M count=256 conv=fsync status=none
    dd if="$f" of=/dev/null bs=1M iflag=direct status=none 2>/dev/null || dd if="$f" of=/dev/null bs=1M status=none
  done
  rm -f "$f"
}

cpu_burst() {
  if [[ "$LOAD_TOOL" == stress-ng ]]; then stress-ng --cpu "$(nproc)" --timeout "${FAULT_S}s" --temp-path "$TMP_DIR" -q; else builtin_cpu; fi
}
memory_pressure() {
  if [[ "$LOAD_TOOL" == stress-ng ]]; then stress-ng --vm 1 --vm-bytes "${MEM_PCT}%" --vm-keep --timeout "${FAULT_S}s" --temp-path "$TMP_DIR" -q; else builtin_mem; fi
}
fork_burst() {
  if [[ "$LOAD_TOOL" == stress-ng ]]; then stress-ng --fork 8 --timeout "${FAULT_S}s" --temp-path "$TMP_DIR" -q; else builtin_fork; fi
}
disk_io() {
  if [[ "$LOAD_TOOL" == stress-ng ]]; then stress-ng --hdd 2 --hdd-bytes 256M --timeout "${FAULT_S}s" --temp-path "$TMP_DIR" -q; else builtin_disk; fi
}
ssh_spam() {
  local end=$(( $(date +%s) + FAULT_S )) i=0
  while (( $(date +%s) < end )); do
    i=$((i + 1))
    logger -t sshd -p authpriv.info \
      "Failed password for invalid user admin$((i % 7)) from 203.0.113.$((i % 250 + 1)) port $((40000 + i)) ssh2"
    sleep 0.25
  done
}

# --- scenario ------------------------------------------------------------------------------
echo "run id: $RUN_ID   db: $SYSPULSE_DB   load tool: $LOAD_TOOL"
echo "Keep the host otherwise idle. Total time about $(( BASELINE_S + 5 * (FAULT_S + GAP_S) )) s."
phase baseline 0 "$BASELINE_S" idle "$BASELINE_S"
for fault in cpu_burst memory_pressure fork_burst disk_io ssh_spam; do
  phase "$fault" 1 "$FAULT_S" "$fault"
  phase recovery 0 "$GAP_S" idle "$GAP_S"
done

echo "[$(date +%T)] waiting for the collector to record the 30 s scoring grace period"
sleep 40
echo "[$(date +%T)] importing journal events"
py syspulse.logparser --db "$SYSPULSE_DB" --since "$(( (BASELINE_S + 5 * (FAULT_S + GAP_S)) / 60 + 5 )) minutes ago"

if [[ "${SKIP_EVAL:-0}" != "1" ]]; then
  py syspulse.evaluate --db "$SYSPULSE_DB" --run-id "$RUN_ID" --out "$ROOT/results" --persist
fi
