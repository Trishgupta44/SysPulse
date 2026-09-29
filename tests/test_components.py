import pytest

pytest.importorskip("streamlit")
from dashboard import components as ui  # noqa: E402


def row(name, f1, precision=0.9, recall=0.5, caught=4, latency=4.0, false_runs=0, glyph="circle"):
    return dict(name=name, glyph=glyph, color="#2a78d6", precision=precision, recall=recall, f1=f1,
                caught=caught, total=5, latency=latency, false_runs=false_runs)


def test_data_derived_text_is_escaped():
    assert "<script>" not in ui.header("<script>alert(1)</script>", 3)
    assert "<b>x</b>" not in ui.status_line("stale", "<b>x</b>")
    assert "<i>" not in ui.headline("<i>hi</i>", "<i>sub</i>")
    assert "<img" not in ui.comparison_table([row("<img src=x>", 0.5)])
    assert "<img" not in ui.latency_table(["<img>"], [dict(name="d", glyph="circle", color="#000", latencies=[1.0])])


def test_no_svg_or_images_are_emitted():
    page = ui.header("d", 1) + ui.status_line("live", "x") + ui.now_row([("a", "1", "%", "n", True)])
    page += ui.comparison_table([row("A", 0.5)])
    assert "<svg" not in page and "<img" not in page


@pytest.mark.parametrize("state, glyph, word", [("live", "●", "Live"), ("stale", "▲", "Stale"), ("recorded", "■", "Recorded")])
def test_status_uses_a_distinct_glyph_and_a_word(state, glyph, word):
    html = ui.status_line(state, "detail")
    assert glyph in html and word in html and f"sp-status--{state}" in html


def test_status_glyphs_are_all_different():
    assert len({g for g, _ in ui.STATUS.values()}) == 3


def test_now_row_flags_and_notes():
    html = ui.now_row([("Latest sample", "Flagged", "", "17:05:42", True), ("CPU busy", "12.3", "%", "up 2.3 vs 5 min avg", False)])
    assert 'class="flag"' in html and "Flagged" in html
    assert "12.3<small>%</small>" in html and "up 2.3 vs 5 min avg" in html


def test_delta_note_wording():
    assert ui.delta_note(12.0, 10.0, 1) == "up 2.0 vs 5 min avg"
    assert ui.delta_note(5.0, 6.0, 1) == "down 1.0 vs 5 min avg"
    assert ui.delta_note(5.0, 5.0, 1) == "same as 5 min avg"
    assert ui.delta_note(5.0, None, 1) is None


def test_comparison_table_bolds_the_best_value_in_each_column():
    rows = [row("A", 0.59, precision=0.95, recall=0.43, caught=5, latency=4, false_runs=1),
            row("B", 0.04, precision=1.00, recall=0.02, caught=1, latency=14, false_runs=0)]
    html = ui.comparison_table(rows)
    # A wins F1, recall, caught, latency; B wins precision and false alarms
    a_row, b_row = html.split("<tr>")[2:4]
    assert a_row.count('class="best"') == 4
    assert b_row.count('class="best"') == 2


def test_comparison_table_ties_are_both_bold_and_missing_latency_is_not_a_win():
    rows = [row("A", 0.5, latency=None), row("B", 0.5, latency=None)]
    html = ui.comparison_table(rows)
    assert html.count("n/a") == 2
    assert html.split("<tr>")[2].count('class="best"') == 6 - 1   # all but latency


def test_comparison_table_is_empty_for_no_rows():
    assert ui.comparison_table([]) == ""


def test_latency_table_marks_misses():
    html = ui.latency_table(["cpu_burst", "ssh_spam"], [
        dict(name="A", glyph="circle", color="#111", latencies=[2.0, None]),
        dict(name="B", glyph="square", color="#222", latencies=[None, 6.0]),
    ])
    assert html.count("missed") == 2
    assert "cpu burst" in html and "2 s" in html and "6 s" in html


def test_headline_without_sub():
    assert "sp-sub" not in ui.headline("Hello")
    assert "sp-sub" in ui.headline("Hello", "world")


FAQ = """# Title

## First section

Intro text.

### Question one?
Answer one.

### Question two?
Answer two, line one.
Line two.

## Glossary
- **A:** thing
"""


def test_parse_faq_splits_sections_and_questions():
    sections = ui.parse_faq(FAQ)
    assert [s["title"] for s in sections] == ["First section", "Glossary"]
    first = sections[0]
    assert first["intro"] == "Intro text."
    assert [q for q, _ in first["questions"]] == ["Question one?", "Question two?"]
    assert first["questions"][1][1] == "Answer two, line one.\nLine two."
    assert sections[1]["questions"] == [] and "**A:** thing" in sections[1]["intro"]


def test_parse_faq_on_the_real_file_finds_every_section():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "docs" / "FAQ.md").read_text(encoding="utf-8")
    sections = ui.parse_faq(text)
    assert len(sections) >= 6
    assert all(s["title"] for s in sections)
    assert sum(len(s["questions"]) for s in sections) >= 20
