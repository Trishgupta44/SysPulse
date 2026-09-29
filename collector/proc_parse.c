#include "proc_parse.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef unsigned long long ull;

/* Pointer to the start of the line that begins with `key`, or NULL. */
static const char *find_line(const char *text, const char *key) {
    size_t n = strlen(key);
    const char *p = text;
    while (p && *p) {
        if (strncmp(p, key, n) == 0) return p;
        p = strchr(p, '\n');
        if (p) p++;
    }
    return NULL;
}

int parse_proc_stat(const char *text, proc_stat_t *o) {
    memset(o, 0, sizeof *o);
    const char *cpu = find_line(text, "cpu ");
    if (!cpu) return -1;
    ull v[8] = {0};
    int n = sscanf(cpu, "cpu %llu %llu %llu %llu %llu %llu %llu %llu",
                   &v[0], &v[1], &v[2], &v[3], &v[4], &v[5], &v[6], &v[7]);
    if (n < 4) return -1; /* old kernels omit iowait/irq/softirq/steal */
    o->user = v[0]; o->nice = v[1]; o->system = v[2]; o->idle = v[3];
    o->iowait = v[4]; o->irq = v[5]; o->softirq = v[6]; o->steal = v[7];
    const char *ctxt = find_line(text, "ctxt ");
    const char *procs = find_line(text, "processes ");
    ull c = 0, f = 0;
    if (!ctxt || sscanf(ctxt, "ctxt %llu", &c) != 1) return -1;
    if (!procs || sscanf(procs, "processes %llu", &f) != 1) return -1;
    o->ctxt = c;
    o->forks = f;
    return 0;
}

static int meminfo_value(const char *text, const char *key, double *dst) {
    const char *line = find_line(text, key);
    if (!line) return -1;
    double kb = 0;
    if (sscanf(line + strlen(key), " %lf", &kb) != 1) return -1;
    *dst = kb;
    return 0;
}

int parse_meminfo(const char *text, meminfo_t *o) {
    memset(o, 0, sizeof *o);
    if (meminfo_value(text, "MemTotal:", &o->mem_total_kb) != 0 || o->mem_total_kb <= 0) return -1;
    if (meminfo_value(text, "MemAvailable:", &o->mem_avail_kb) != 0) {
        /* Kernels before 3.14: approximate available memory. */
        double free_kb = 0, buffers = 0, cached = 0;
        if (meminfo_value(text, "MemFree:", &free_kb) != 0) return -1;
        meminfo_value(text, "Buffers:", &buffers);
        meminfo_value(text, "Cached:", &cached);
        o->mem_avail_kb = free_kb + buffers + cached;
    }
    meminfo_value(text, "SwapTotal:", &o->swap_total_kb); /* absent means no swap */
    meminfo_value(text, "SwapFree:", &o->swap_free_kb);
    return 0;
}

int parse_loadavg(const char *text, loadavg_t *o) {
    memset(o, 0, sizeof *o);
    int n = sscanf(text, "%lf %lf %lf %d/%d", &o->l1, &o->l5, &o->l15, &o->running, &o->total);
    return n == 5 ? 0 : -1;
}

static int all_digits(const char *s) {
    if (!*s) return 0;
    for (; *s; s++) if (!isdigit((unsigned char)*s)) return 0;
    return 1;
}

int is_whole_disk(const char *name) {
    static const char *const simple[] = {"sd", "vd", "xvd", "hd"};
    for (size_t i = 0; i < sizeof simple / sizeof *simple; i++) {
        size_t n = strlen(simple[i]);
        if (strncmp(name, simple[i], n) == 0) {
            const char *rest = name + n; /* whole disk = letters only, partitions end in digits */
            if (!*rest) return 0;
            for (; *rest; rest++) if (!islower((unsigned char)*rest)) return 0;
            return 1;
        }
    }
    if (strncmp(name, "nvme", 4) == 0) { /* nvme<ctrl>n<ns>, partitions add p<N> */
        const char *p = name + 4;
        if (!isdigit((unsigned char)*p)) return 0;
        while (isdigit((unsigned char)*p)) p++;
        if (*p != 'n') return 0;
        p++;
        return all_digits(p);
    }
    if (strncmp(name, "mmcblk", 6) == 0) return all_digits(name + 6);
    return 0; /* loop, ram, dm-*, md*, sr*: virtual or removable */
}

int parse_diskstats(const char *text, diskio_t *o) {
    memset(o, 0, sizeof *o);
    const char *p = text;
    int lines = 0;
    while (p && *p) {
        char name[64];
        ull rd = 0, wr = 0;
        if (sscanf(p, " %*u %*u %63s %*u %*u %llu %*u %*u %*u %llu", name, &rd, &wr) == 3) {
            lines++;
            if (is_whole_disk(name)) { o->sectors_read += rd; o->sectors_written += wr; }
        }
        p = strchr(p, '\n');
        if (p) p++;
    }
    return lines > 0 ? 0 : -1;
}

int parse_net_dev(const char *text, netio_t *o) {
    memset(o, 0, sizeof *o);
    const char *p = strchr(text, '\n'); /* skip the two header lines */
    if (!p) return -1;
    p = strchr(p + 1, '\n');
    if (!p) return -1;
    p++;
    int ifaces = 0;
    while (p && *p) {
        const char *colon = strchr(p, ':');
        const char *eol = strchr(p, '\n');
        if (!colon || (eol && colon > eol)) { p = eol ? eol + 1 : NULL; continue; }
        char name[64];
        size_t len = 0;
        const char *s = p;
        while (s < colon && isspace((unsigned char)*s)) s++;
        while (s < colon && len < sizeof name - 1) name[len++] = *s++;
        name[len] = '\0';
        ull rx = 0, tx = 0;
        if (sscanf(colon + 1, " %llu %*u %*u %*u %*u %*u %*u %*u %llu", &rx, &tx) == 2) {
            ifaces++;
            if (strcmp(name, "lo") != 0) { o->rx_bytes += rx; o->tx_bytes += tx; }
        }
        p = eol ? eol + 1 : NULL;
    }
    return ifaces > 0 ? 0 : -1;
}

/* Counter difference that treats a reset or wrap as "no change" instead of underflowing. */
static double delta(uint64_t now, uint64_t before) {
    return now >= before ? (double)(now - before) : 0.0;
}

void compute_sample(const snapshot_t *prev, const snapshot_t *cur, sample_t *o) {
    memset(o, 0, sizeof *o);
    o->load1 = cur->load.l1;
    o->load5 = cur->load.l5;
    o->load15 = cur->load.l15;
    o->procs_running = cur->load.running;
    o->procs_total = cur->load.total;
    if (cur->mem.mem_total_kb > 0) {
        o->mem_pct = 100.0 * (1.0 - cur->mem.mem_avail_kb / cur->mem.mem_total_kb);
        if (o->mem_pct < 0) o->mem_pct = 0;
    }
    if (cur->mem.swap_total_kb > 0)
        o->swap_pct = 100.0 * (1.0 - cur->mem.swap_free_kb / cur->mem.swap_total_kb);

    double dt = cur->mono - prev->mono;
    double total = delta(cur->st.user, prev->st.user) + delta(cur->st.nice, prev->st.nice) +
                   delta(cur->st.system, prev->st.system) + delta(cur->st.idle, prev->st.idle) +
                   delta(cur->st.iowait, prev->st.iowait) + delta(cur->st.irq, prev->st.irq) +
                   delta(cur->st.softirq, prev->st.softirq) + delta(cur->st.steal, prev->st.steal);
    if (total > 0) {
        double idle = delta(cur->st.idle, prev->st.idle) + delta(cur->st.iowait, prev->st.iowait);
        o->cpu_pct = 100.0 * (total - idle) / total;
        o->iowait_pct = 100.0 * delta(cur->st.iowait, prev->st.iowait) / total;
    }
    if (dt <= 0) return; /* rates below are undefined without elapsed time */
    o->disk_read_kbs = delta(cur->disk.sectors_read, prev->disk.sectors_read) * 0.5 / dt;
    o->disk_write_kbs = delta(cur->disk.sectors_written, prev->disk.sectors_written) * 0.5 / dt;
    o->net_rx_kbs = delta(cur->net.rx_bytes, prev->net.rx_bytes) / 1024.0 / dt;
    o->net_tx_kbs = delta(cur->net.tx_bytes, prev->net.tx_bytes) / 1024.0 / dt;
    o->ctx_switches_ps = delta(cur->st.ctxt, prev->st.ctxt) / dt;
    o->forks_ps = delta(cur->st.forks, prev->st.forks) / dt;
}

/* /proc/stat carries one "intr" line that can run to tens of kilobytes on big machines. */
static char g_buf[1 << 20];

static int read_file(const char *root, const char *rel, const char **text) {
    char path[1024];
    if (snprintf(path, sizeof path, "%s/%s", root, rel) >= (int)sizeof path) return -1;
    FILE *fp = fopen(path, "r");
    if (!fp) return -1;
    size_t n = fread(g_buf, 1, sizeof g_buf - 1, fp);
    int failed = ferror(fp);
    fclose(fp);
    if (failed || n == 0) return -1;
    g_buf[n] = '\0';
    *text = g_buf;
    return 0;
}

int snapshot_read(const char *root, snapshot_t *s) {
    const char *t;
    memset(s, 0, sizeof *s);
    if (read_file(root, "stat", &t) || parse_proc_stat(t, &s->st)) return -1;
    if (read_file(root, "meminfo", &t) || parse_meminfo(t, &s->mem)) return -1;
    if (read_file(root, "loadavg", &t) || parse_loadavg(t, &s->load)) return -1;
    if (read_file(root, "diskstats", &t) || parse_diskstats(t, &s->disk)) return -1;
    if (read_file(root, "net/dev", &t) || parse_net_dev(t, &s->net)) return -1;
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    s->mono = (double)ts.tv_sec + ts.tv_nsec / 1e9;
    return 0;
}
