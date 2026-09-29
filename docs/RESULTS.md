# Results

Three live runs of the same scenario on one machine, scored on samples no detector was fitted on. The recorded databases are in `data/`, so every number here can be reproduced:

```bash
python -m syspulse.evaluate --db data/live-run.db     # run 1
python -m syspulse.evaluate --db data/live-run-2.db   # run 2
python -m syspulse.evaluate --db data/live-run-3.db   # run 3
```

## Method

- **Scenario.** 15 minutes idle, then CPU burst, memory pressure, fork burst, disk I/O and fake SSH login spam for 60 seconds each, with 90 seconds of recovery after each. Every phase is recorded with exact start and end times, and that record is the ground truth.
- **Training.** Detectors are fitted on the first 60% of the idle baseline. The rest of the baseline is held out, so false alarms are measured on unseen data. The trained detectors set their alarm threshold from the last quarter of their training rows, which the model never fitted on.
- **Scoring.** Every scored sample is one prediction. A sample counts as truly anomalous inside a fault window plus 30 seconds, because rolling features keep reacting briefly after a fault stops. All detectors are scored on the same rows.
- **Metrics.** Precision, recall and F1 per sample. Faults caught, and median seconds to the first flag. False-alarm runs are stretches of flags outside every fault window.

## The runs

| Run | Load tool | Condition |
|---|---|---|
| 1 | built-in generators | clean baseline, machine idle |
| 2 | `stress-ng` | the laptop slept twice during the baseline (8 and 66 minutes), leaving 33 usable training rows once gap rows were excluded |
| 3 | `stress-ng`, run through the installed systemd service | other work (a test suite and a Docker build) was running during the baseline |

Runs 2 and 3 are compromised and are reported anyway. The figures printed at the end of run 3 differ slightly from those below, because the script then evaluated before the last 30 seconds of grace had been sampled. It now waits, and the recorded database gives the numbers below. The run 3 database is the service's own database, so it also holds other host activity.

| Detector | Run | Precision | Recall | F1 | Faults caught | Median latency | False-alarm runs |
|---|---|---|---|---|---|---|---|
| Rolling z-score | 1 | 0.95 | 0.43 | 0.59 | 5 of 5 | 4 s | 1 |
| | 2 | 0.88 | 0.42 | 0.56 | 5 of 5 | 1 s | 4 |
| | 3 | 0.81 | 0.41 | 0.55 | 4 of 5 | 6 s | 7 |
| Isolation Forest | 1 | 1.00 | 0.02 | 0.04 | 1 of 5 | 14 s | 0 |
| | 2 | 0.63 | 0.73 | 0.68 | 5 of 5 | 1 s | 10 |
| | 3 | 0.61 | 0.58 | 0.59 | 5 of 5 | 6 s | 13 |
| Mahalanobis distance | 1 | 1.00 | 0.55 | 0.71 | 4 of 5 | 4 s | 0 |
| | 2 | 0.59 | 1.00 | 0.74 | 5 of 5 | 1 s | 6 |
| | 3 | 0.49 | 1.00 | 0.65 | 5 of 5 | 6 s | 15 |

Seconds from the start of each fault to the first flag, as z-score / forest / Mahalanobis:

| Fault | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| CPU burst | 2 / missed / 2 | 2 / 2 / 2 | 6 / 6 / 6 |
| Memory pressure | 4 / missed / 4 | 1 / 1 / 1 | missed / 21 / 6 |
| Fork burst | 5 / missed / 5 | 1 / 1 / 1 | 6 / 6 / 6 |
| Disk I/O | 2 / 14 / 7 | 0 / 0 / 0 | 5 / 5 / 5 |
| SSH spam | 6 / missed / missed | 5 / 51 / 5 | 5 / 5 / 5 |

## What the runs support

- **No detector dominates.** Mahalanobis had the best F1 every time, but the margin over the forest shrank to about 0.06 in run 3 and its precision fell from 1.00 to 0.49. From run 2 on it catches every fault and raises many false alarms.
- **The rolling z-score is the conservative one.** Most stable F1 (0.55 to 0.59) and better precision than the others after run 1. It adapts to a sustained shift, so in run 3 it missed the gradual memory ramp entirely.
- **The Isolation Forest's first result did not repeat.** Its alarm threshold comes from a handful of calibration rows, so how noisy the baseline happened to be decided whether it worked. Run 1's failure is best read as fragility, not as proof the method is unsuitable. That explanation is a hypothesis; it has not been isolated.
- **Most false alarms sit in recovery.** The trailing baseline still contains the fault, and load average, for example, stays high for minutes after a CPU burst.

## What is not proven

Two of the three runs are compromised, the faults are synthetic, and each run is one machine for under 30 minutes. A clean run on an idle machine with a longer baseline is still owed. The Mahalanobis detector was designed after seeing run 1, with its design fixed before its scores were looked at and evaluated once with no tuning, but that is why the later runs matter.

## Isolation Forest feature ablation (run 1)

Run 1's forest result raised the question of whether too many correlated features were to blame. `scripts/ablate_forest.py` fits the forest on different feature sets and alert quantiles. Fewer features did not help.

| Features | Columns | Quantile | Precision | Recall | F1 | Faults caught |
|---|---|---|---|---|---|---|
| all (default) | 68 | 0.995 | 1.00 | 0.02 | 0.04 | 1 of 5 |
| all (default) | 68 | 0.980 | 1.00 | 0.05 | 0.09 | 2 of 5 |
| value and deviation | 34 | 0.995 | 0.00 | 0.00 | 0.00 | 0 of 5 |
| deviation only | 17 | 0.995 | 0.00 | 0.00 | 0.00 | 0 of 5 |
| value only | 17 | 0.995 | 1.00 | 0.15 | 0.26 | 2 of 5 |
| value only | 17 | 0.980 | 1.00 | 0.16 | 0.27 | 3 of 5 |

The forest default was left unchanged rather than tuned to run 1.
