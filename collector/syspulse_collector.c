/* SysPulse collector: samples /proc every N seconds and appends rows to SQLite.
 *
 *   syspulse-collector [--db PATH] [--interval SEC] [--once] [--proc-root DIR] [--verbose]
 *
 * --once   take a baseline snapshot, wait one interval, write one row, exit. Rates need two
 *          snapshots, so the row appears after `interval` seconds.
 * SIGTERM and SIGINT stop the loop cleanly so the database is closed and checkpointed.
 */
#include <errno.h>
#include <getopt.h>
#include <signal.h>
#include <sqlite3.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/statvfs.h>
#include <time.h>

#include "proc_parse.h"

#define DEFAULT_DB "/var/lib/syspulse/metrics.db"

/* Must stay identical to SAMPLES_DDL in syspulse/db.py; tests/test_collector.py checks the columns. */
static const char *DDL =
    "CREATE TABLE IF NOT EXISTS samples ("
    "ts REAL PRIMARY KEY,"
    "cpu_pct REAL, iowait_pct REAL, mem_pct REAL, swap_pct REAL,"
    "load1 REAL, load5 REAL, load15 REAL,"
    "procs_running REAL, procs_total REAL,"
    "disk_read_kbs REAL, disk_write_kbs REAL,"
    "net_rx_kbs REAL, net_tx_kbs REAL,"
    "ctx_switches_ps REAL, forks_ps REAL, disk_used_pct REAL)";

static const char *INSERT =
    "INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)";

static volatile sig_atomic_t g_stop = 0;

static void on_signal(int sig) {
    (void)sig;
    g_stop = 1;
}

static void usage(FILE *out) {
    fprintf(out,
            "usage: syspulse-collector [--db PATH] [--interval SEC] [--once] [--proc-root DIR] [--verbose]\n"
            "  --db PATH        SQLite file (default $SYSPULSE_DB or " DEFAULT_DB ")\n"
            "  --interval SEC   seconds between samples, fractions allowed (default 5)\n"
            "  --once           write a single row and exit\n"
            "  --proc-root DIR  read from DIR instead of /proc (used by tests)\n"
            "  --verbose        print each row to stdout\n");
}

static double disk_used_pct(void) {
    struct statvfs v;
    if (statvfs("/", &v) != 0 || v.f_blocks == 0) return 0.0;
    double used = (double)(v.f_blocks - v.f_bfree);
    double denom = used + (double)v.f_bavail; /* same definition as df(1) */
    return denom > 0 ? 100.0 * used / denom : 0.0;
}

static double now_realtime(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (double)ts.tv_sec + ts.tv_nsec / 1e9;
}

static struct timespec add_seconds(struct timespec t, double sec) {
    long long ns = (long long)(sec * 1e9);
    t.tv_sec += (time_t)(ns / 1000000000LL);
    t.tv_nsec += (long)(ns % 1000000000LL);
    if (t.tv_nsec >= 1000000000L) { t.tv_sec++; t.tv_nsec -= 1000000000L; }
    return t;
}

/* Sleep to an absolute deadline so per-tick work does not accumulate drift. Returns 1 if interrupted. */
static int sleep_until(const struct timespec *deadline) {
    int rc;
    do {
        rc = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, deadline, NULL);
    } while (rc == EINTR && !g_stop);
    return g_stop;
}

static int open_db(const char *path, sqlite3 **db, sqlite3_stmt **stmt) {
    if (sqlite3_open(path, db) != SQLITE_OK) {
        fprintf(stderr, "syspulse-collector: cannot open %s: %s\n", path, sqlite3_errmsg(*db));
        return -1;
    }
    sqlite3_busy_timeout(*db, 10000);
    sqlite3_exec(*db, "PRAGMA journal_mode=WAL", NULL, NULL, NULL);
    sqlite3_exec(*db, "PRAGMA synchronous=NORMAL", NULL, NULL, NULL);
    char *err = NULL;
    if (sqlite3_exec(*db, DDL, NULL, NULL, &err) != SQLITE_OK) {
        fprintf(stderr, "syspulse-collector: schema error: %s\n", err);
        sqlite3_free(err);
        return -1;
    }
    if (sqlite3_prepare_v2(*db, INSERT, -1, stmt, NULL) != SQLITE_OK) {
        fprintf(stderr, "syspulse-collector: prepare failed: %s\n", sqlite3_errmsg(*db));
        return -1;
    }
    return 0;
}

static int insert_row(sqlite3 *db, sqlite3_stmt *stmt, double ts, const sample_t *s, double disk_used) {
    const double v[] = {ts, s->cpu_pct, s->iowait_pct, s->mem_pct, s->swap_pct,
                        s->load1, s->load5, s->load15, s->procs_running, s->procs_total,
                        s->disk_read_kbs, s->disk_write_kbs, s->net_rx_kbs, s->net_tx_kbs,
                        s->ctx_switches_ps, s->forks_ps, disk_used};
    int rc = SQLITE_OK;
    for (int attempt = 0; attempt < 3; attempt++) { /* busy_timeout already waited; retry once more for luck */
        sqlite3_reset(stmt);
        for (int i = 0; i < 17; i++) sqlite3_bind_double(stmt, i + 1, v[i]);
        rc = sqlite3_step(stmt);
        if (rc == SQLITE_DONE) return 0;
        if (rc != SQLITE_BUSY && rc != SQLITE_LOCKED) break;
    }
    fprintf(stderr, "syspulse-collector: insert failed: %s\n", sqlite3_errmsg(db));
    return -1;
}

int main(int argc, char **argv) {
    const char *db_path = getenv("SYSPULSE_DB");
    if (!db_path || !*db_path) db_path = DEFAULT_DB;
    const char *proc_root = "/proc";
    double interval = 5.0;
    int once = 0, verbose = 0;

    static const struct option opts[] = {
        {"db", required_argument, 0, 'd'}, {"interval", required_argument, 0, 'i'},
        {"once", no_argument, 0, 'o'}, {"proc-root", required_argument, 0, 'p'},
        {"verbose", no_argument, 0, 'v'}, {"help", no_argument, 0, 'h'}, {0, 0, 0, 0}};
    int c;
    while ((c = getopt_long(argc, argv, "d:i:op:vh", opts, NULL)) != -1) {
        switch (c) {
        case 'd': db_path = optarg; break;
        case 'i': {
            char *end = NULL;
            interval = strtod(optarg, &end);
            if (end == optarg || *end || interval <= 0 || interval > 3600) {
                fprintf(stderr, "syspulse-collector: --interval must be between 0 and 3600 seconds\n");
                return 2;
            }
            break;
        }
        case 'o': once = 1; break;
        case 'p': proc_root = optarg; break;
        case 'v': verbose = 1; break;
        case 'h': usage(stdout); return 0;
        default: usage(stderr); return 2;
        }
    }

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_signal; /* no SA_RESTART: we want the sleep to return */
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);

    sqlite3 *db = NULL;
    sqlite3_stmt *stmt = NULL;
    if (open_db(db_path, &db, &stmt) != 0) { sqlite3_close(db); return 1; }

    snapshot_t prev, cur;
    if (snapshot_read(proc_root, &prev) != 0) {
        fprintf(stderr, "syspulse-collector: cannot read %s\n", proc_root);
        sqlite3_finalize(stmt);
        sqlite3_close(db);
        return 1;
    }
    fprintf(stderr, "syspulse-collector: writing to %s every %.3gs\n", db_path, interval);

    struct timespec deadline;
    clock_gettime(CLOCK_MONOTONIC, &deadline);
    int status = 0;
    while (!g_stop) {
        deadline = add_seconds(deadline, interval);
        if (sleep_until(&deadline)) break;
        if (snapshot_read(proc_root, &cur) != 0) {
            fprintf(stderr, "syspulse-collector: read failed, skipping tick\n");
            continue; /* keep `prev`, so the next rate spans the gap correctly */
        }
        sample_t s;
        compute_sample(&prev, &cur, &s);
        double ts = now_realtime(), used = disk_used_pct();
        if (insert_row(db, stmt, ts, &s, used) != 0) {
            if (once) status = 1;
        } else if (verbose) {
            printf("%.3f cpu=%.1f mem=%.1f load1=%.2f\n", ts, s.cpu_pct, s.mem_pct, s.load1);
            fflush(stdout);
        }
        prev = cur;
        if (once) break;
    }

    sqlite3_finalize(stmt);
    sqlite3_exec(db, "PRAGMA wal_checkpoint(TRUNCATE)", NULL, NULL, NULL);
    sqlite3_close(db);
    if (g_stop) fprintf(stderr, "syspulse-collector: stopped by signal\n");
    return status;
}
