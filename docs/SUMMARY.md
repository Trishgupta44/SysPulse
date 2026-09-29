# SysPulse: summary

## The problem

Alerts on a Linux host usually fire on a fixed threshold, and nobody measures whether they work. A rule such as "CPU above 90%" misses a slow memory leak and fires all day on a machine that runs hot. Learned detectors promise better, but a detector with no ground truth is just an opinion.

## What I built

A pipeline that watches a host, flags abnormal moments with three different detectors, and scores each detector against faults I inject on purpose.

- **Collector.** A C11 program samples `/proc` every 5 seconds into SQLite. Installed as a systemd service it costs about 0.07% of one CPU core and 3.4 MB of memory.
- **Log parser.** A Python program, run by a systemd timer, reads the journal for failed SSH logins and OOM kills.
- **Detectors.** A rolling z-score, an Isolation Forest, and a Mahalanobis distance model, all behind one interface.
- **Fault injection.** A 28-minute Bash scenario (CPU burst, memory pressure, fork burst, disk I/O, fake SSH login spam) that records the exact start and end of every fault.
- **Evaluation.** Precision, recall and F1 on held-out samples, plus how many faults were caught, how fast, and how many false-alarm stretches.
- **Dashboard.** A Streamlit app with live traces, an events view, the evaluation results and an in-app FAQ.

## What it found

F1 across three live runs of the same scenario:

| Detector | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Rolling z-score | 0.59 | 0.56 | 0.55 |
| Isolation Forest | 0.04 | 0.68 | 0.59 |
| Mahalanobis distance | 0.71 | 0.74 | 0.65 |

- No detector dominates. Mahalanobis had the best F1 every time, but its precision fell from 1.00 to about 0.5, so it trades false alarms for coverage.
- The z-score is the conservative one. It adapts to a sustained shift, so it caught the start of every fault but missed a slow memory ramp once.
- The Isolation Forest found 1 of 5 faults in run 1 and 5 of 5 in the next two. Its threshold comes from a few calibration rows, so how noisy the baseline happened to be decided the outcome. I treat that as fragility, not as proof the method is wrong. The cause is a hypothesis I have not isolated.
- Most false alarms fall in the recovery period, because the trailing baseline still contains the fault.

## How much to trust this

Treat it as a demonstration of a sound method, not a benchmark.

- Two of the three runs are compromised: my laptop slept during the baseline of one, and other work ran during the baseline of another. Both are reported anyway, with the caveat next to the numbers.
- Faults come from load generators on one machine, and each run is under 30 minutes.
- I designed the Mahalanobis detector after seeing run 1. Its design was fixed before I looked at its scores and it was scored once with no tuning, but that is why the later runs matter.
- A clean run on an idle machine with a longer baseline is still owed.

## What was verified, and what was not

| Verified on real Linux | Not yet done |
|---|---|
| 76 C checks and 85 Python tests pass, 96% coverage, lint clean | The GitHub Actions workflow has never run |
| Collector runs as an enabled systemd service and survives a WSL restart | A clean, idle, longer benchmark run |
| Log-parser timer imports real journal events every minute | A retention policy for the database |
| Docker image builds and the compose stack runs healthy | Authentication on the dashboard |
| Every number in the results is reproducible from the databases in `data/` | |

## Decisions worth a second look

- **A calibration split.** Scoring the training rows themselves sets the alarm threshold too low. The trained detectors set it from the last quarter of their clean rows, which the model never fitted on.
- **No lookahead, by test.** Every feature row depends only on the past, and a test truncates the series and checks earlier rows do not change.
- **Sampling gaps.** When a machine sleeps the collector writes nothing, and rolling windows across the hole mix before and after. Features are blanked for five minutes after any gap. I found this the hard way when a laptop sleep wrecked a run.
- **Score every detector on the same rows.** Otherwise a detector with less warm-up is scored on an easier set.
- **Report the failures.** The forest's first result and the compromised runs are in the README, not hidden.

## In numbers

| | |
|---|---|
| Source | 589 lines of C, about 1,360 lines of Python, 150 lines of Bash |
| Tests | 76 C checks, 85 pytest tests, 96% coverage |
| Collector overhead | about 0.07% of one core, 3.4 MB resident |
| Storage | about 8.5 MB a day at one sample every 5 seconds |
| Live runs | 3, each 28 minutes, recorded in `data/` |

## Next

1. A clean third benchmark: idle machine, machine kept awake, longer baseline.
2. Exclude a cool-down period after a fault from the baseline, to cut recovery false alarms.
3. Combine the z-score and Mahalanobis scores, since they miss different faults.
4. Retention and downsampling for old data.

Read [DESIGN.md](DESIGN.md) for how it works and [RESULTS.md](RESULTS.md) for the runs in full.
