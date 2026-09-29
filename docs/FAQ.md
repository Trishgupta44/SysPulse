# SysPulse FAQ

## The basics

### What is SysPulse?
SysPulse watches the health of a Linux machine and tells you when something looks abnormal. A small C program reads the machine's own counters every 5 seconds and saves them. A Python program reads the system log for failed SSH logins and out-of-memory kills. Three detectors then look at the recent history and flag moments that do not look normal. A dashboard shows what is happening and what was flagged.

### What does it actually measure?
Sixteen readings per sample: how busy the CPU is, how long it waits on disk, memory and swap use, three load averages, running and total processes, disk read and write speed, network in and out, context switches per second, new processes per second, and how full the disk is. From the logs it counts failed SSH logins and OOM kills.

### Who is it for?
Anyone who wants to understand how monitoring and anomaly detection work end to end, and how to measure whether an alerting method is any good. It is also a portfolio project. For running a real fleet you would use an established monitoring stack. See the next question.

## Why it matters

### Why not just use Prometheus, Grafana or Datadog?
For production, you should. Those tools are mature and cover far more. SysPulse exists to show every layer working together, and to do something most tools skip: **measure its own alerting accuracy**. It injects known faults, then reports how many the detectors caught, how fast, and how many false alarms they raised. "We have alerts" is not the same as "our alerts work".

### Why is anomaly detection hard?
A fixed rule such as "alert above 90% CPU" misses a database that quietly doubles its memory use, and fires all day on a machine that always runs hot. A detector that learns what normal looks like for this machine can catch both. The trade-off is that it can also fire on harmless changes, and how often depends on the detector and the noise in the baseline.

### Why watch SSH failures and OOM kills as well as metrics?
Some problems are visible only in logs. Someone guessing passwords shows up as failed logins with almost no change in CPU. A process killed for using too much memory shows up as one log line. Combining both sources catches more.

## How it works

### What is the pipeline?
1. The **collector** (C) reads `/proc` every 5 seconds and writes one row to a SQLite database.
2. The **log parser** (Python, run by a systemd timer every minute) reads the system journal and records failed SSH logins and OOM kills.
3. **Features** are computed over rolling windows: the value now, the last minute, and the last five minutes.
4. Three **detectors** score every sample.
5. The **dashboard** and the **evaluator** read the same database.

### Why is the collector written in C?
It runs all the time on the machine it watches, so it should cost almost nothing. Measured on the installed service: about 0.07% of one CPU core, 3.4 MB of memory and a single thread. It also reads `/proc` directly, which makes it a good place to learn how Linux reports its own state.

### Why SQLite?
It is one file, needs no server, handles a reader (the dashboard) and a writer (the collector) at the same time in write-ahead-log mode, and is plenty for one row every 5 seconds.

### What do the three detectors do?
- **Rolling z-score.** Asks "how many standard deviations is this from the last five minutes?" for every reading, and reports the biggest. Simple, needs no training, and quick to react. Its weakness is that after a few minutes the fault becomes the new normal.
- **Isolation Forest.** A machine-learning model trained on a clean stretch of normal behaviour. It asks how easily a sample can be separated from the normal data. It only asks whether a point is outside the range it has seen, not how far outside.
- **Mahalanobis distance.** Measures how far a sample is from normal, taking into account that readings move together (busy CPU usually means higher load). It uses the size of the deviation, so bigger problems score higher.

### What is the "alarm threshold"?
Each detector produces a score. A sample is flagged when its score is above the threshold. For the trained detectors the threshold is set from a slice of normal data the model never saw, so it reflects how normal behaves on unseen data. You can move it in the sidebar: higher means fewer flags and more missed problems, lower means more flags and more false alarms.

### How long until it is useful?
The charts work immediately. The trained detectors need about 5 minutes of samples before they can score, and they work better with 15 minutes or more of ordinary behaviour. Until then the dashboard says it is still learning.

### What is fault injection, and why is it in a monitoring project?
To know whether a detector works you need to know when something really went wrong. `make inject` runs a scripted 28-minute scenario on the machine: 15 minutes idle, then a CPU burst, memory pressure, a burst of new processes, heavy disk I/O and a wave of fake failed SSH logins, each for 60 seconds with recovery time in between. Every phase is recorded with exact start and end times. That record is the **ground truth** the detectors are scored against.

### What do precision, recall and F1 mean here?
- **Precision:** of the samples a detector flagged, how many were during a real fault. Low precision means lots of false alarms.
- **Recall:** of the samples during real faults, how many were flagged. Low recall means missed problems.
- **F1:** one number that balances the two. It is high only when both are.

Two more numbers matter to an operator: **faults caught** (was each of the five noticed at all) and **latency** (seconds from the fault starting to the first flag). **False-alarm runs** counts stretches of flags that fall outside every fault window. Faults are counted as lasting 30 seconds after they stop, because the rolling windows keep reacting briefly.

## Reading the dashboard

### What does the headline mean?
The sentence at the top is generated from the data: how many samples in the chosen window were flagged by the selected detector, and when the latest one was. "No samples flagged" is a normal healthy answer.

### What do Live, Stale and Recorded mean?
Live means the newest sample is under 20 seconds old. Stale means it is older and the collector may have stopped. Recorded means the data ended hours ago, such as a saved run. Each has a different symbol and a word, so you never have to rely on colour.

### Why are there flagged dots after a fault ends?
The detectors compare against the last few minutes, and that window still contains the fault. Some readings, like load average, also fade slowly. So the recovery period looks unusual for a while. Most of the false alarms in the results come from here.

### What is the Results tab?
It scores all three detectors on the most recent fault-injection run and shows precision, recall, F1, faults caught, latency and false alarms, with the best value in each column in bold, plus a per-fault table and a timeline. It appears only when a run exists in the database.

### Why do Events and Results show a recorded run?
A fresh install has no SSH failures and no fault-injection run yet, which would leave those tabs blank. When the database being viewed has none, the tabs show the recorded reference run bundled with SysPulse (`data/live-run.db`) and say so in a note at the top. The reference file is only ever opened read-only. Once your database has its own events or a run from `make inject`, the tabs switch to it.

## The results

### Which detector is best?
None, and the honest answer depends on what you value. Across three live runs, the Mahalanobis model had the best F1 each time but its precision fell to about 0.5, so it flags a lot. The rolling z-score was the most consistent and the most conservative, and it missed a slow memory ramp once. The Isolation Forest went from nearly useless in the first run to competitive in the next two. See docs/RESULTS.md for the numbers and the caveats.

### Why did the Isolation Forest fail once and then work?
Its alarm threshold comes from a small set of calibration samples, so it depends on how noisy the quiet baseline happened to be. The exact cause is a hypothesis and has not been isolated. The lesson is that one run cannot rank detectors.

### Can I trust these numbers?
Treat them as a demonstration of the method, not a benchmark. There are three runs on one machine, faults come from load generators, and two of the runs were compromised (the laptop slept during one, and other work was running during another). The README says so next to the numbers. A clean run on an otherwise idle machine is still owed.

## Running it

### What do I need?
Linux with systemd, or WSL2 Ubuntu with systemd enabled. A C compiler and the SQLite development headers to build the collector, and Python 3.10 or newer. `make apt-deps` installs the system packages. No root is needed to run the collector, only to install it as a service.

### How do I run it?
```bash
make apt-deps
make test
make install-service
make dashboard        # then open http://localhost:8501
make inject           # optional, about 28 minutes, keep the machine idle
```
`make demo` needs no root and no `stress-ng`: it generates synthetic data and evaluates it.

### Does it work in Docker?
Yes: `make docker-up` starts the collector and the dashboard. Inside a container `/proc` describes the host (or the Docker Desktop VM), not the container, and the system journal is not available, so the log parser only runs in the systemd install.

### How much does it cost to run?
Measured on the installed service: about 0.07% of one CPU core and 3.4 MB of memory. The database grows about 8.5 MB per day at one sample every 5 seconds, roughly 3 GB a year. **There is no retention policy yet**, so on a long-lived machine you would want to prune old rows.

### Is it secure? Does data leave the machine?
Nothing is sent anywhere. The collector only reads `/proc` and the parser only reads the journal. The dashboard has **no authentication**, so do not expose its port to a network you do not trust. The systemd units run with a restricted set of permissions.

### What happens if the laptop sleeps?
The collector simply stops writing, which leaves a hole in the data. Rolling windows that span the hole would mix before and after, so the features are blanked for five minutes after any sampling gap and the detectors skip those rows.

## Limitations and next steps

### What are the known limits?
- One machine, one short scenario per run, synthetic faults.
- No retention policy, so the database grows without bound.
- The detectors are trained on the start of the data, not continuously retrained.
- Fault windows are scored with a fixed 30-second grace, which favours neither detector but is an assumption.
- The dashboard has no login.

### What would come next?
A clean, longer baseline run and more runs to give confidence intervals. Excluding a cool-down period after a fault from the baseline, to cut recovery false alarms. Combining the z-score and Mahalanobis scores, since they miss different faults. Retention and downsampling for old data. Testing against real incidents instead of load generators.

## Glossary
- **Anomaly:** a sample that does not look like the machine's normal behaviour.
- **Baseline:** the recent or clean history a detector compares against.
- **Ground truth:** the recorded start and end times of the faults that were really injected.
- **False alarm:** a flag raised when nothing was wrong.
- **`/proc`:** a virtual folder where Linux exposes live counters about CPU, memory, disks and more.
- **journald:** the systemd service that collects system logs.
- **WAL (write-ahead log):** the SQLite mode that lets one program read while another writes.
- **OOM kill:** the kernel ending a process because the machine ran out of memory.
