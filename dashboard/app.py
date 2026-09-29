"""SysPulse dashboard: what the host is doing, what the detectors flagged, and how well they did.

    SYSPULSE_DB=/var/lib/syspulse/metrics.db streamlit run dashboard/app.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

from dashboard import components as ui  # noqa: E402
from dashboard import theme  # noqa: E402
from syspulse import db, evaluate, features, pipeline  # noqa: E402

LOCAL_TZ = datetime.now().astimezone().tzinfo
P = theme.PALETTE
REFERENCE_DB = Path(__file__).resolve().parent.parent / "data" / "live-run.db"

# name, key glyph, palette colour, plotly marker
DETECTORS = {
    "zscore": ("Rolling z-score", "circle", "line", "circle"),
    "isolation_forest": ("Isolation Forest", "square", "second", "square"),
    "mahalanobis": ("Mahalanobis distance", "triangle", "third", "triangle-up"),
}
METRICS = {
    "cpu_pct": "CPU busy (%)",
    "mem_pct": "Memory used (%)",
    "load1": "Load average, 1 min",
    "iowait_pct": "I/O wait (%)",
    "swap_pct": "Swap used (%)",
    "ctx_switches_ps": "Context switches (/s)",
    "forks_ps": "Forks (/s)",
    "procs_total": "Processes",
    "disk_read_kbs": "Disk read (KB/s)",
    "disk_write_kbs": "Disk write (KB/s)",
    "net_rx_kbs": "Network in (KB/s)",
    "net_tx_kbs": "Network out (KB/s)",
}
DEFAULT_METRICS = ["cpu_pct", "mem_pct", "load1", "ctx_switches_ps"]
WINDOWS = {"Last 15 minutes": 15 * 60, "Last hour": 3600, "Last 6 hours": 6 * 3600, "All data": None}
SHORT_FAULT = {"cpu_burst": "CPU", "memory_pressure": "Memory", "fork_burst": "Forks",
               "disk_io": "Disk", "ssh_spam": "SSH"}
LIVE_S, RECORDED_S = 20, 6 * 3600  # newest-sample age limits for the status line

st.set_page_config(page_title="SysPulse", layout="wide")


def to_local(ts):
    """Unix seconds (Series, Index or array) to naive local datetimes for plotting."""
    conv = pd.to_datetime(ts, unit="s", utc=True)
    if isinstance(conv, pd.Series):
        return conv.dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    return conv.tz_convert(LOCAL_TZ).tz_localize(None)


def clock(ts: float, seconds: bool = True) -> str:
    return to_local(pd.Series([ts])).iloc[0].strftime("%H:%M:%S" if seconds else "%Y-%m-%d %H:%M")


def signature(path: str, readonly: bool = False) -> tuple:
    """Cheap fingerprint of the database so cached scoring refreshes only when data changes."""
    conn = db.connect(path, readonly=readonly)
    try:
        s = conn.execute("SELECT COUNT(*), COALESCE(MAX(ts), 0) FROM samples").fetchone()
        e = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        g = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM ground_truth").fetchone()
        return (s[0], s[1], e, g[0], g[1])
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def compute(path: str, sig: tuple, readonly: bool = False) -> dict:
    """Load data, fit every detector and score every sample. Cached per database state."""
    conn = db.connect(path, readonly=readonly)
    try:
        samples, events, truth_all = pipeline.load_frames(conn)
    finally:
        conn.close()
    truth = pipeline.latest_run(truth_all)
    feats = features.build(samples, events)
    mask = pipeline.train_mask(feats.index, truth)
    out = {"samples": samples, "events": events, "truth": truth, "n_train": int(mask.sum()),
           "have_truth": not truth.empty, "scores": {}, "thresholds": {}, "error": None}
    try:
        dets = pipeline.fit_detectors(feats, mask)
        out["scores"] = pipeline.score_all(feats, dets)
        out["thresholds"] = {k: float(d.threshold) for k, d in dets.items()}
    except ValueError:  # too little clean data for the trained detectors yet
        out["error"] = ("The trained detectors need about 5 minutes of samples before they can score. "
                        "The traces below already update.")
    return out


@st.cache_data(show_spinner=False)
def compute_results(path: str, sig: tuple, readonly: bool = False) -> dict | None:
    conn = db.connect(path, readonly=readonly)
    try:
        if conn.execute("SELECT COUNT(*) FROM ground_truth").fetchone()[0] == 0:
            return None
        return evaluate.run_evaluation(conn)
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def style(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=4, r=8, t=24, b=4), hovermode="x unified", font=theme.plot_font(),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(bgcolor=P["surface"], bordercolor=P["rule"], font=dict(color=P["ink"], size=12)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(showgrid=False, showline=True, linecolor=P["rule"], tickcolor=P["rule"])
    fig.update_yaxes(gridcolor=P["grid"], zeroline=False)
    return fig


def add_fault_bands(fig, truth: pd.DataFrame, lo, hi, rows=None):
    for w in truth[truth["is_fault"] == 1].itertuples():
        x0, x1 = to_local(pd.Series([w.start_ts, w.end_ts]))
        if x1 < lo or x0 > hi:
            continue
        kw = dict(x0=x0, x1=x1, fillcolor=P["band"], line_width=0, layer="below")
        if rows:
            for r in rows:
                fig.add_vrect(row=r, col=1, **kw)
        else:
            fig.add_vrect(**kw)
        fig.add_annotation(x=x0, y=1.0, yref="paper", text=SHORT_FAULT.get(w.label, w.label), showarrow=False,
                           xanchor="left", yanchor="bottom", font=dict(size=11, color=P["muted"]))


def score_figure(win_ts: pd.Series, score: pd.Series, flags: np.ndarray, threshold: float, truth, show_truth):
    # Scores span orders of magnitude (a z-score can reach the thousands), so plot each one as a
    # multiple of its own alarm threshold on a log axis: 1 is the alarm for every detector.
    x = to_local(win_ts)
    ratio = (score / threshold).to_numpy()
    shown = np.clip(ratio, 0.1, 150)  # the axis stops at 150x; hover still gives the true value
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=shown, customdata=ratio, mode="lines", line=dict(color=P["line"], width=1.6),
                             name="Score", showlegend=False, hovertemplate="%{customdata:,.1f}x the alarm"))
    fig.add_trace(go.Scatter(x=x[flags], y=shown[flags], mode="markers", name="Flagged",
                             marker=dict(color=P["alarm"], size=7), hovertemplate="flagged"))
    fig.add_hline(y=1, line=dict(color=P["alarm"], width=1, dash="dash"))
    fig.update_yaxes(type="log", range=[-1.05, 2.25], tickvals=[0.1, 1, 10, 100], ticktext=["0.1x", "1x", "10x", "100x"])
    if show_truth and not truth.empty:
        add_fault_bands(fig, truth, x.min(), x.max())
    return style(fig, 190)


def trace_figure(win: pd.DataFrame, metrics: list[str], flags: np.ndarray, truth, show_truth: bool):
    fig = make_subplots(rows=len(metrics), cols=1, shared_xaxes=True, vertical_spacing=0.08,
                        subplot_titles=[METRICS[m] for m in metrics])
    for title in fig.layout.annotations:
        title.update(x=0, xanchor="left", font=dict(size=12, color=P["ink"]))
    x = to_local(win["ts"])
    for i, m in enumerate(metrics, start=1):
        fig.add_trace(go.Scatter(x=x, y=win[m], mode="lines", line=dict(color=P["line"], width=1.6),
                                 name=METRICS[m], showlegend=False, hovertemplate="%{y:.2f}"), row=i, col=1)
        fig.add_trace(go.Scatter(x=x[flags], y=win[m].to_numpy()[flags], mode="markers", name="Flagged",
                                 marker=dict(color=P["alarm"], size=7), showlegend=(i == 1),
                                 hovertemplate="flagged"), row=i, col=1)
    if show_truth and not truth.empty:
        add_fault_bands(fig, truth, x.min(), x.max(), rows=list(range(1, len(metrics) + 1)))
    return style(fig, 140 * len(metrics) + 40)


def baseline_of(win: pd.DataFrame, col: str) -> float | None:
    last = win["ts"].iloc[-1]
    prior = win[(win["ts"] >= last - 300) & (win["ts"] < last)]
    return float(prior[col].mean()) if len(prior) >= 3 else None


def live_tab(data: dict, detector: str, threshold: float, window_s, metrics: list[str], show_truth: bool):
    samples = data["samples"]
    last_ts = float(samples["ts"].max())
    win = samples if window_s is None else samples[samples["ts"] >= last_ts - window_s]
    name = DETECTORS[detector][0]
    ready = detector in data["scores"]

    age = int(datetime.now().timestamp() - last_ts)
    if age <= LIVE_S:
        st.html(ui.status_line("live", f"latest sample {age} s ago"))
    elif age <= RECORDED_S:
        st.html(ui.status_line("stale", f"latest sample {age} s ago, is the collector running?"))
    else:
        st.html(ui.status_line("recorded", f"data ends {clock(last_ts, seconds=False)}"))

    if ready:
        sc = data["scores"][detector]["score"].reindex(win["ts"]).reset_index(drop=True)
        flags = (sc > threshold).fillna(False).to_numpy()
        n_scored, n_flagged = int(sc.notna().sum()), int(flags.sum())
        if n_flagged == 0:
            st.html(ui.headline("No samples flagged in this window.",
                                f"{name}, alarm above {threshold:.2f}, {n_scored:,} samples scored."))
        else:
            latest = float(win["ts"].to_numpy()[np.flatnonzero(flags)[-1]])
            st.html(ui.headline(f"{n_flagged} of {n_scored:,} samples flagged.",
                                f"Most recent at {clock(latest)}. {name}, alarm above {threshold:.2f}."))
    else:
        flags = np.zeros(len(win), dtype=bool)
        st.html(ui.headline("Still learning what normal looks like.", data["error"] or ""))

    latest = win.iloc[-1]
    latest_flagged = bool(flags[-1]) if len(flags) else False
    items = [("Latest sample", "Flagged" if latest_flagged else "Normal", "", clock(last_ts), latest_flagged)]
    for col, label, unit, dec in (("cpu_pct", "CPU busy", "%", 1), ("mem_pct", "Memory used", "%", 1),
                                  ("load1", "Load, 1 min", "", 2), ("procs_total", "Processes", "", 0)):
        items.append((label, f"{latest[col]:,.{dec}f}", unit, ui.delta_note(latest[col], baseline_of(win, col), dec), False))
    st.html(ui.now_row(items))

    if ready:
        st.html(ui.section_label("Anomaly score", "as a multiple of the alarm threshold, log scale; the dashed line is the alarm"))
        st.plotly_chart(score_figure(win["ts"], sc, flags, threshold, data["truth"], show_truth), width="stretch")
    if not metrics:
        st.info("Pick at least one trace in the sidebar.")
        return
    st.html(ui.section_label("Traces", "red dots are flagged samples, grey bands are injected faults"))
    st.plotly_chart(trace_figure(win, metrics, flags, data["truth"], show_truth), width="stretch")
    with st.expander("Table view of the plotted window"):
        table = win[["ts"] + metrics].copy()
        table.insert(0, "time", to_local(table.pop("ts")))
        table["flagged"] = flags
        st.dataframe(table.iloc[::-1].head(300), hide_index=True, width="stretch")


def is_reference(path: str) -> bool:
    """True for the bundled reference database, which is only ever opened read-only."""
    return Path(path).exists() and Path(path).resolve() == REFERENCE_DB.resolve()


def recorded_run(path: str) -> tuple[str, tuple] | None:
    """The bundled reference run, used when the database being viewed has no run of its own."""
    if not REFERENCE_DB.exists() or is_reference(path):
        return None
    ref = str(REFERENCE_DB)
    return ref, signature(ref, readonly=True)


def reference_note(what: str):
    st.info(f"This database has no {what} yet, so this is the recorded reference run bundled with "
            f"SysPulse (`data/live-run.db`). Your own results appear here after `make inject`.")


def events_tab(path: str, data: dict):
    ev = data["events"]
    if ev.empty:
        ref = recorded_run(path)
        if ref is None:
            st.info("No SSH failures or OOM kills recorded yet. The log parser checks the journal every minute.")
            return
        ev = compute(ref[0], ref[1], readonly=True)["events"]
        reference_note("SSH failures or OOM kills")
    ev = ev.copy()
    ev["time"] = to_local(ev["ts"])
    n_ssh, n_oom = int((ev["kind"] == "ssh_fail").sum()), int((ev["kind"] == "oom_kill").sum())
    st.html(ui.headline(f"{n_ssh:,} failed SSH logins, {n_oom:,} OOM kills.",
                        f"Between {clock(ev['ts'].min())} and {clock(ev['ts'].max())}."))
    per_min = ev.assign(minute=ev["time"].dt.floor("min")).groupby(["minute", "kind"]).size().unstack(fill_value=0)
    fig = go.Figure()
    for kind, label, colour in (("ssh_fail", "Failed SSH logins", P["line"]), ("oom_kill", "OOM kills", P["second"])):
        if kind in per_min:
            fig.add_trace(go.Bar(x=per_min.index, y=per_min[kind], name=label, marker_color=colour))
    fig.update_layout(barmode="stack", bargap=0.35)
    st.html(ui.section_label("Events per minute"))
    st.plotly_chart(style(fig, 240), width="stretch")
    st.html(ui.section_label("Most recent", "latest 200"))
    st.dataframe(ev.sort_values("ts", ascending=False)[["time", "kind", "message"]].head(200),
                 hide_index=True, width="stretch")


def results_tab(path: str, sig: tuple, data: dict):
    res = compute_results(path, sig, is_reference(path))
    if res is None:
        ref = recorded_run(path)
        if ref is None:
            st.info("No ground truth yet. Run `make inject` (about 28 minutes) or `make demo` for synthetic data.")
            return
        res = compute_results(ref[0], ref[1], readonly=True)
        data = compute(ref[0], ref[1], readonly=True)
        reference_note("fault-injection run")
    if "error" in res:
        st.error(res["error"])
        return

    rows, lat_cols = [], []
    for key, d in res["detectors"].items():
        label, glyph, colour_key, _ = DETECTORS.get(key, (key, "circle", "line", "circle"))
        p = d["point"]
        colour = P[colour_key]
        rows.append(dict(name=label, glyph=glyph, color=colour, precision=p["precision"], recall=p["recall"],
                         f1=p["f1"], caught=sum(1 for r in d["per_fault"] if r["detected"]),
                         total=len(d["per_fault"]), latency=d["median_latency_s"], false_runs=d["false_alarm_runs"]))
        lat_cols.append(dict(name=label, glyph=glyph, color=colour,
                             latencies=[r["latency_s"] if r["detected"] else None for r in d["per_fault"]]))
    best = max(rows, key=lambda r: r["f1"])
    caught = ", ".join(f"{r['name']} {r['caught']} of {r['total']}" for r in rows)
    st.html(ui.headline(f"{best['name']} scored highest, F1 {best['f1']:.2f}.", f"Faults caught: {caught}."))
    st.html(ui.comparison_table(rows))
    st.caption(f"Run {res['run_id']}. Trained on {res['n_train_rows']} baseline samples, scored "
               f"{res['n_eval_rows']} held-out samples, faults extended by {res['grace_s']:.0f} s.")

    st.html(ui.section_label("Time to first detection", "per injected fault"))
    st.html(ui.latency_table(res["faults"], lat_cols))

    truth = data["truth"]
    if not truth.empty and data["scores"]:
        fig = go.Figure()
        ticks = []
        for i, key in enumerate(k for k in DETECTORS if k in data["scores"]):
            label, _, colour_key, marker = DETECTORS[key]
            sc = data["scores"][key]
            hit = sc[sc["is_anomaly"]]
            fig.add_trace(go.Scatter(x=to_local(hit.index), y=[i] * len(hit), mode="markers", name=label,
                                     marker=dict(color=P[colour_key], symbol=marker, size=8),
                                     hovertemplate="flagged"))
            ticks.append((i, label))
        lo, hi = to_local(pd.Series([truth["start_ts"].min(), truth["end_ts"].max()]))
        add_fault_bands(fig, truth, lo, hi)
        fig.update_xaxes(range=[lo, hi])
        fig.update_yaxes(tickvals=[t for t, _ in ticks], ticktext=[n for _, n in ticks],
                         range=[-0.6, len(ticks) - 0.4])
        st.html(ui.section_label("Detections against injected faults", "grey bands are the fault windows"))
        fig.update_layout(showlegend=False)  # the row labels already name each detector
        st.plotly_chart(style(fig, 80 + 55 * len(ticks)), width="stretch")


def about_tab():
    faq = Path(__file__).resolve().parent.parent / "docs" / "FAQ.md"
    if not faq.exists():
        st.info("The FAQ file is missing from this install.")
        return
    sections = ui.parse_faq(faq.read_text(encoding="utf-8"))
    columns = st.columns(2, gap="large")
    for column, chunk in zip(columns, (sections[:3], sections[3:]), strict=True):
        with column:
            for section in chunk:
                st.html(ui.section_label(section["title"]))
                if section["intro"]:
                    st.markdown(section["intro"])
                for i, (question, answer) in enumerate(section["questions"]):
                    with st.expander(question, expanded=(section is sections[0] and i == 0)):
                        st.markdown(answer)


def main():
    st.html(f"<style>{theme.css()}</style>")
    default_db = db.db_path()
    with st.sidebar:
        st.header("Settings")
        path = st.text_input("Database", default_db)
        detector = st.selectbox("Detector", list(DETECTORS), format_func=lambda k: DETECTORS[k][0])

    exists = Path(path).exists()
    ro = is_reference(path)
    sig = signature(path, ro) if exists else (0,)
    data = compute(path, sig, ro) if exists and sig[0] > 0 else None

    thr = 0.0
    with st.sidebar:
        if data and detector in data["scores"]:
            thr_default = data["thresholds"][detector]
            lo, hi = (1.0, 12.0) if detector == "zscore" else (round(thr_default * 0.6, 2), round(thr_default * 1.6, 2))
            step = 0.05 if detector == "zscore" else max(round((hi - lo) / 100, 3), 0.001)
            thr = st.slider("Alarm threshold", lo, hi, float(min(max(thr_default, lo), hi)), step,
                            key=f"thr-{detector}", help="Samples scoring above this are flagged.")
            if detector != "zscore":
                note = "the start of the baseline" if data["have_truth"] else "the first hour of history"
                st.caption(f"Trained on {data['n_train']} samples from {note}.")
        window = st.selectbox("Time window", list(WINDOWS), index=1)
        metrics = st.multiselect("Traces", list(METRICS), default=DEFAULT_METRICS, format_func=METRICS.get)
        show_truth = st.toggle("Show fault windows", value=True)
        auto = st.toggle("Auto-refresh", value=True)
        every = st.select_slider("Refresh every (s)", [5, 10, 30, 60], value=10, disabled=not auto)

    st.html(ui.header(path, sig[0]))
    if not exists:
        st.info(f"No database at `{path}` yet. Start the collector (`make install-service` or "
                f"`make run-collector`), or generate demo data with `make demo`.")
        return
    if data is None:
        st.info("The database exists but holds no samples yet. Wait a few seconds for the first row.")
        return

    tabs = st.tabs(["Live", "Events", "Results", "About"])

    def render_live():
        fresh = compute(path, signature(path, ro), ro)
        live_tab(fresh, detector, thr, WINDOWS[window], metrics, show_truth)

    with tabs[0]:
        if auto:
            st.fragment(render_live, run_every=every)()  # reruns alone, so other tabs do not flicker
        else:
            render_live()
    with tabs[1]:
        events_tab(path, data)
    with tabs[2]:
        results_tab(path, sig, data)
    with tabs[3]:
        about_tab()


main()
