"""HTML builders for the SysPulse dashboard. Pure functions that return strings, so they are unit-testable.

No inline SVG (Streamlit's sanitizer strips it) and no decoration: state uses a text glyph plus a
word, comparisons are real tables, and everything that can come from data is escaped.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from html import escape

# Text glyphs double as the legend for chart markers: circle, square, triangle.
KEY_GLYPH = {"circle": "●", "square": "■", "triangle": "▲"}
STATUS = {
    "live": ("●", "Live"),
    "stale": ("▲", "Stale"),
    "recorded": ("■", "Recorded"),
}


def header(db_path: str, n_samples: int) -> str:
    return (f'<header class="sp-head"><h1>SysPulse</h1>'
            f'<p>{escape(db_path)} · {n_samples:,} samples</p></header>')


def status_line(state: str, detail: str) -> str:
    glyph, word = STATUS[state]
    return f'<p class="sp-status sp-status--{state}"><b>{glyph} {word}</b> · {escape(detail)}</p>'


def headline(text: str, sub: str = "") -> str:
    sub_html = f'<p class="sp-sub">{escape(sub)}</p>' if sub else ""
    return f'<h2 class="sp-headline">{escape(text)}</h2>{sub_html}'


def now_row(items: Sequence[tuple[str, str, str, str | None, bool]]) -> str:
    """Current readings as one ruled strip. Items are (label, value, unit, note, flagged)."""
    cells = []
    for label, value, unit, note, flagged in items:
        unit_html = f"<small>{escape(unit)}</small>" if unit else ""
        note_html = f"<em>{escape(note)}</em>" if note else ""
        cells.append(f'<li class="{"flag" if flagged else ""}"><span>{escape(label)}</span>'
                     f'<strong>{escape(value)}{unit_html}</strong>{note_html}</li>')
    return f'<ul class="sp-now">{"".join(cells)}</ul>'


def section_label(text: str, hint: str = "") -> str:
    hint_html = f"<span>{escape(hint)}</span>" if hint else ""
    return f'<h3 class="sp-label">{escape(text)}{hint_html}</h3>'


def delta_note(value: float, baseline: float | None, decimals: int) -> str | None:
    if baseline is None:
        return None
    d = value - baseline
    if abs(d) < 10 ** -decimals:
        return "same as 5 min avg"
    return f"{'up' if d > 0 else 'down'} {abs(d):,.{decimals}f} vs 5 min avg"


def _key(glyph: str, color: str) -> str:
    return f'<span class="key" style="color:{escape(color)}">{KEY_GLYPH.get(glyph, "●")}</span>'


def comparison_table(rows: Sequence[dict]) -> str:
    """One row per detector. The best value in each numeric column is bold.

    Each row is {name, glyph, color, precision, recall, f1, caught, total, latency, false_runs}.
    """
    if not rows:
        return ""
    best = {
        "precision": max(r["precision"] for r in rows),
        "recall": max(r["recall"] for r in rows),
        "f1": max(r["f1"] for r in rows),
        "caught": max(r["caught"] for r in rows),
        "false_runs": min(r["false_runs"] for r in rows),
    }
    latencies = [r["latency"] for r in rows if r["latency"] is not None]
    best_latency = min(latencies) if latencies else None

    def cell(text: str, is_best: bool) -> str:
        return f'<td class="{"best" if is_best else ""}">{text}</td>'

    body = []
    for r in rows:
        lat = '<span class="na">n/a</span>' if r["latency"] is None else f'{r["latency"]:.0f} s'
        body.append(
            "<tr>"
            f'<td>{_key(r["glyph"], r["color"])}{escape(r["name"])}</td>'
            + cell(f'{r["precision"]:.2f}', r["precision"] == best["precision"])
            + cell(f'{r["recall"]:.2f}', r["recall"] == best["recall"])
            + cell(f'{r["f1"]:.2f}', r["f1"] == best["f1"])
            + cell(f'{r["caught"]} of {r["total"]}', r["caught"] == best["caught"])
            + cell(lat, r["latency"] is not None and r["latency"] == best_latency)
            + cell(f'{r["false_runs"]}', r["false_runs"] == best["false_runs"])
            + "</tr>"
        )
    head = ("<tr><th>Detector</th><th>Precision</th><th>Recall</th><th>F1</th>"
            "<th>Faults caught</th><th>Median latency</th><th>False alarms</th></tr>")
    return f'<table class="sp-table"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table>'


def latency_table(faults: Sequence[str], detectors: Sequence[dict]) -> str:
    """Time to first detection per fault. detectors: [{name, glyph, color, latencies: [float|None]}]."""
    head = "<tr><th>Fault</th>" + "".join(
        f'<th>{_key(d["glyph"], d["color"])}{escape(d["name"])}</th>' for d in detectors) + "</tr>"
    rows = []
    for i, fault in enumerate(faults):
        cells = []
        for d in detectors:
            lat = d["latencies"][i]
            cells.append('<td class="miss">missed</td>' if lat is None else f"<td>{lat:.0f} s</td>")
        rows.append(f'<tr><td>{escape(fault.replace("_", " "))}</td>{"".join(cells)}</tr>')
    return f'<table class="sp-table"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'


def parse_faq(text: str) -> list[dict]:
    """Split the FAQ markdown into sections (## heading) holding questions (### heading)."""
    sections = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        intro, *questions = re.split(r"^### ", body, flags=re.M)
        sections.append({
            "title": title.strip(),
            "intro": intro.strip(),
            "questions": [(q.partition("\n")[0].strip(), q.partition("\n")[2].strip()) for q in questions],
        })
    return sections
