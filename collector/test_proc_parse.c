/* Unit tests for proc_parse.c. Build and run with `make test-c`. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "proc_parse.h"

static int checks = 0, failures = 0;

#define CHECK(cond)                                                          \
    do {                                                                     \
        checks++;                                                            \
        if (!(cond)) {                                                       \
            failures++;                                                      \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);  \
        }                                                                    \
    } while (0)

#define NEAR(a, b) CHECK(fabs((a) - (b)) < 1e-6)

static const char *STAT =
    "cpu  4705 150 1120 16250856 1000 127 456 0 0 0\n"
    "cpu0 2352 75 560 8125428 500 63 228 0 0 0\n"
    "intr 1462898 0 9\n"
    "ctxt 1990473\n"
    "btime 1640000000\n"
    "processes 2443\n"
    "procs_running 2\n";

static void test_stat(void) {
    proc_stat_t s;
    CHECK(parse_proc_stat(STAT, &s) == 0);
    CHECK(s.user == 4705 && s.nice == 150 && s.system == 1120);
    CHECK(s.idle == 16250856ULL && s.iowait == 1000 && s.irq == 127 && s.softirq == 456);
    CHECK(s.ctxt == 1990473ULL && s.forks == 2443);
    CHECK(parse_proc_stat("garbage\n", &s) == -1);
    CHECK(parse_proc_stat("cpu  1 2 3 4\nctxt 5\nprocesses 6\n", &s) == 0); /* old 4-field kernels */
    CHECK(s.idle == 4 && s.iowait == 0);
    CHECK(parse_proc_stat("cpu  1 2 3 4\n", &s) == -1); /* missing ctxt/processes */
}

static void test_meminfo(void) {
    meminfo_t m;
    const char *ok = "MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\nSwapTotal: 200 kB\nSwapFree: 50 kB\n";
    CHECK(parse_meminfo(ok, &m) == 0);
    NEAR(m.mem_total_kb, 1000); NEAR(m.mem_avail_kb, 400);
    NEAR(m.swap_total_kb, 200); NEAR(m.swap_free_kb, 50);
    const char *old = "MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 20 kB\nCached: 30 kB\n";
    CHECK(parse_meminfo(old, &m) == 0);
    NEAR(m.mem_avail_kb, 150);
    NEAR(m.swap_total_kb, 0);
    CHECK(parse_meminfo("nothing here\n", &m) == -1);
}

static void test_loadavg(void) {
    loadavg_t l;
    CHECK(parse_loadavg("0.52 0.41 0.30 3/412 9999\n", &l) == 0);
    NEAR(l.l1, 0.52); NEAR(l.l5, 0.41); NEAR(l.l15, 0.30);
    CHECK(l.running == 3 && l.total == 412);
    CHECK(parse_loadavg("bad", &l) == -1);
}

static void test_whole_disk_filter(void) {
    const char *yes[] = {"sda", "sdb", "vda", "xvda", "hda", "nvme0n1", "nvme12n3", "mmcblk0"};
    const char *no[] = {"sda1", "vda2", "nvme0n1p1", "mmcblk0p1", "loop0", "ram0", "dm-0", "md0", "sr0", "sd", ""};
    for (size_t i = 0; i < sizeof yes / sizeof *yes; i++) CHECK(is_whole_disk(yes[i]));
    for (size_t i = 0; i < sizeof no / sizeof *no; i++) CHECK(!is_whole_disk(no[i]));
}

static void test_diskstats(void) {
    const char *text =
        "   7 0 loop0 100 0 800 10 0 0 0 0 0 10 10\n"
        "   8 0 sda 1000 50 20000 300 2000 60 40000 900 0 500 1200\n"
        "   8 1 sda1 900 40 18000 250 1900 55 38000 800 0 450 1050\n"
        " 259 0 nvme0n1 500 0 10000 100 700 0 14000 200 0 150 300\n"
        " 259 1 nvme0n1p1 400 0 8000 90 600 0 12000 180 0 140 270\n";
    diskio_t d;
    CHECK(parse_diskstats(text, &d) == 0);
    CHECK(d.sectors_read == 30000ULL);    /* sda + nvme0n1 only */
    CHECK(d.sectors_written == 54000ULL);
    CHECK(parse_diskstats("", &d) == -1);
}

static void test_net_dev(void) {
    const char *text =
        "Inter-|   Receive |  Transmit\n"
        " face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n"
        "    lo: 5000000 10000 0 0 0 0 0 0 5000000 10000 0 0 0 0 0 0\n"
        "  eth0: 1048576 2000 0 0 0 0 0 0 524288 1500 0 0 0 0 0 0\n"
        " wlan0: 100 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0\n";
    netio_t n;
    CHECK(parse_net_dev(text, &n) == 0);
    CHECK(n.rx_bytes == 1048676ULL && n.tx_bytes == 524488ULL); /* loopback excluded */
    CHECK(parse_net_dev("only one line", &n) == -1);
}

static snapshot_t snap(double mono) {
    snapshot_t s;
    memset(&s, 0, sizeof s);
    s.mono = mono;
    s.mem.mem_total_kb = 1000; s.mem.mem_avail_kb = 250;
    s.mem.swap_total_kb = 100; s.mem.swap_free_kb = 75;
    s.load.l1 = 1.5; s.load.running = 4; s.load.total = 300;
    return s;
}

static void test_compute_rates(void) {
    snapshot_t a = snap(100.0), b = snap(105.0);
    b.st.user = 300; b.st.system = 100; b.st.idle = 500; b.st.iowait = 100; /* total 1000: busy 400, wait 100 */
    b.st.ctxt = 5000; b.st.forks = 50;
    b.disk.sectors_read = 2000; b.disk.sectors_written = 4000;
    b.net.rx_bytes = 10240; b.net.tx_bytes = 5120;
    sample_t o;
    compute_sample(&a, &b, &o);
    NEAR(o.cpu_pct, 40.0);
    NEAR(o.iowait_pct, 10.0);
    NEAR(o.mem_pct, 75.0);
    NEAR(o.swap_pct, 25.0);
    NEAR(o.load1, 1.5); NEAR(o.procs_running, 4); NEAR(o.procs_total, 300);
    NEAR(o.disk_read_kbs, 2000 * 0.5 / 5);
    NEAR(o.disk_write_kbs, 4000 * 0.5 / 5);
    NEAR(o.net_rx_kbs, 10240 / 1024.0 / 5);
    NEAR(o.net_tx_kbs, 5120 / 1024.0 / 5);
    NEAR(o.ctx_switches_ps, 1000.0);
    NEAR(o.forks_ps, 10.0);
}

static void test_counter_reset_and_zero_interval(void) {
    snapshot_t a = snap(100.0), b = snap(105.0);
    a.st.ctxt = 9000; b.st.ctxt = 10;           /* counter went backwards (reboot or wrap) */
    a.disk.sectors_read = 500; b.disk.sectors_read = 0;
    sample_t o;
    compute_sample(&a, &b, &o);
    NEAR(o.ctx_switches_ps, 0.0);
    NEAR(o.disk_read_kbs, 0.0);
    CHECK(isfinite(o.cpu_pct));

    snapshot_t c = snap(100.0);
    b.st.ctxt = 20000; a.st.ctxt = 0;
    compute_sample(&c, &c, &o);                 /* dt == 0 */
    NEAR(o.ctx_switches_ps, 0.0);
    CHECK(isfinite(o.net_rx_kbs));
}

static void test_idle_machine_no_division_by_zero(void) {
    snapshot_t a = snap(1.0), b = snap(6.0);
    sample_t o;
    compute_sample(&a, &b, &o); /* identical counters: total delta is zero */
    NEAR(o.cpu_pct, 0.0);
    NEAR(o.iowait_pct, 0.0);
}

static void test_snapshot_from_fixture_dir(const char *root) {
    snapshot_t s;
    CHECK(snapshot_read(root, &s) == 0);
    CHECK(s.st.ctxt == 1990473ULL);
    NEAR(s.mem.mem_total_kb, 16000000);
    CHECK(s.load.total == 412);
    CHECK(s.disk.sectors_read == 30000ULL);
    CHECK(s.net.rx_bytes == 1048576ULL);
    CHECK(snapshot_read("/definitely/not/a/proc", &s) == -1);
}

int main(int argc, char **argv) {
    test_stat();
    test_meminfo();
    test_loadavg();
    test_whole_disk_filter();
    test_diskstats();
    test_net_dev();
    test_compute_rates();
    test_counter_reset_and_zero_interval();
    test_idle_machine_no_division_by_zero();
    if (argc > 1) test_snapshot_from_fixture_dir(argv[1]);
    if (failures) {
        fprintf(stderr, "%d of %d checks failed\n", failures, checks);
        return 1;
    }
    printf("proc_parse: %d checks passed\n", checks);
    return 0;
}
