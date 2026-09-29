/* Pure parsing and rate math for /proc files. No SQLite dependency, so it is unit-testable alone. */
#ifndef SYSPULSE_PROC_PARSE_H
#define SYSPULSE_PROC_PARSE_H

#include <stdint.h>

typedef struct {
    uint64_t user, nice, system, idle, iowait, irq, softirq, steal;
    uint64_t ctxt;   /* context switches since boot */
    uint64_t forks;  /* "processes" line: forks since boot */
} proc_stat_t;

typedef struct {
    double mem_total_kb, mem_avail_kb, swap_total_kb, swap_free_kb;
} meminfo_t;

typedef struct {
    double l1, l5, l15;
    int running, total;
} loadavg_t;

typedef struct { uint64_t sectors_read, sectors_written; } diskio_t;
typedef struct { uint64_t rx_bytes, tx_bytes; } netio_t;

typedef struct {
    proc_stat_t st;
    meminfo_t mem;
    loadavg_t load;
    diskio_t disk;
    netio_t net;
    double mono; /* CLOCK_MONOTONIC seconds when the snapshot was taken */
} snapshot_t;

/* One output row, minus ts and disk_used_pct which the caller supplies. */
typedef struct {
    double cpu_pct, iowait_pct, mem_pct, swap_pct;
    double load1, load5, load15, procs_running, procs_total;
    double disk_read_kbs, disk_write_kbs, net_rx_kbs, net_tx_kbs;
    double ctx_switches_ps, forks_ps;
} sample_t;

/* Each parser returns 0 on success, -1 if the text is not in the expected format. */
int parse_proc_stat(const char *text, proc_stat_t *out);
int parse_meminfo(const char *text, meminfo_t *out);
int parse_loadavg(const char *text, loadavg_t *out);
int parse_diskstats(const char *text, diskio_t *out);
int parse_net_dev(const char *text, netio_t *out);

/* True for whole physical disks (sda, vda, nvme0n1, mmcblk0), false for partitions and virtual devices. */
int is_whole_disk(const char *name);

/* Rates between two snapshots. Counter resets and a non-positive interval yield zero rates. */
void compute_sample(const snapshot_t *prev, const snapshot_t *cur, sample_t *out);

/* Read every /proc file under `root` (normally "/proc") into `out`. Returns 0 on success. */
int snapshot_read(const char *root, snapshot_t *out);

#endif
