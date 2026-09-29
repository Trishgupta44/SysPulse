"""Smoke tests: the Streamlit app renders every state without raising."""
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from syspulse import db, synth  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "dashboard" / "app.py")


def run_app(monkeypatch, path):
    monkeypatch.setenv("SYSPULSE_DB", str(path))
    at = AppTest.from_file(APP, default_timeout=90)
    at.run()
    return at


def test_missing_database_shows_guidance(monkeypatch, tmp_path):
    at = run_app(monkeypatch, tmp_path / "absent.db")
    assert not at.exception
    assert any("No database" in i.value for i in at.info)


def test_empty_database_shows_waiting_message(monkeypatch, tmp_path):
    path = tmp_path / "empty.db"
    db.connect(str(path)).close()
    at = run_app(monkeypatch, path)
    assert not at.exception
    assert any("no samples" in i.value for i in at.info)


@pytest.fixture()
def demo_db(tmp_path):
    path = tmp_path / "demo.db"
    conn = db.connect(str(path))
    synth.write(conn, *synth.generate(seed=2))
    conn.close()
    return path


def html_of(at):
    return " ".join(e.proto.body for e in at.get("html"))


def test_populated_database_renders_all_tabs(monkeypatch, demo_db):
    at = run_app(monkeypatch, demo_db)
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Live", "Events", "Results", "About"]
    page = html_of(at)
    assert "<h1>SysPulse</h1>" in page                   # header
    assert "sp-headline" in page                         # the page leads with a sentence
    assert "sp-now" in page and "CPU busy" in page       # current readings
    assert "sp-status--recorded" in page                 # demo data is old, so it must say Recorded
    assert "sp-table" in page and "Mahalanobis distance" in page   # detector comparison
    assert "scored highest" in page
    assert "What is SysPulse?" in [e.label for e in at.expander]   # the FAQ is inside the app
    assert "Glossary" in page


def test_switching_detector_and_disabling_refresh(monkeypatch, demo_db):
    at = run_app(monkeypatch, demo_db)
    for key in ("zscore", "mahalanobis", "isolation_forest"):
        at.sidebar.selectbox[0].select(key).run()
        assert not at.exception
    at.sidebar.toggle[1].set_value(False).run()
    assert not at.exception


@pytest.fixture()
def samples_only_db(tmp_path):
    """A live database that has samples but no ground truth and no events, like a fresh install."""
    path = tmp_path / "fresh.db"
    conn = db.connect(str(path))
    samples, _, _ = synth.generate(seed=4)
    synth.write(conn, samples, samples.iloc[0:0].assign(kind="x", message="x")[["ts", "kind", "message"]],
                pd.DataFrame(columns=["run_id", "label", "start_ts", "end_ts", "is_fault"]))
    conn.close()
    return path


def test_empty_events_and_results_tabs_show_the_reference_run(monkeypatch, samples_only_db):
    at = run_app(monkeypatch, samples_only_db)
    assert not at.exception
    page = html_of(at)
    assert "scored highest" in page                       # results filled from the reference run
    assert "failed SSH logins" in page                    # events filled from the reference run
    notes = [i.value for i in at.info]
    assert sum("recorded reference run" in n for n in notes) == 2


def test_reference_run_is_opened_read_only(monkeypatch, samples_only_db):
    data_dir = Path(APP).parent.parent / "data"
    before = sorted(p.name for p in data_dir.iterdir())
    run_app(monkeypatch, samples_only_db)
    assert sorted(p.name for p in data_dir.iterdir()) == before   # no -wal or -shm files appeared


def test_viewing_the_reference_database_itself_does_not_show_the_fallback_note(monkeypatch):
    at = run_app(monkeypatch, Path(APP).parent.parent / "data" / "live-run.db")
    assert not at.exception
    assert not any("recorded reference run" in i.value for i in at.info)


def test_dark_theme_is_the_configured_default():
    config = (Path(APP).parent.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert 'base = "dark"' in config
