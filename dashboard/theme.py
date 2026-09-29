"""SysPulse dashboard theme.

A quiet ops tool, not a poster: warm graphite, one accent, rules instead of boxes, a small type
scale and tabular numerals. Dark only, and set in .streamlit/config.toml so it does not depend on
the system setting.

Colours for data come from the validated dataviz palette (dark steps): blue is slot 1, orange
slot 2, aqua slot 3, and red is reserved for anomalies.
"""

PALETTE = dict(
    bg="#171716", surface="#201f1e", rule="#31302d", ink="#ecebe6", muted="#a09d94",
    accent="#3987e5", line="#3987e5", second="#d95926", third="#199e70",
    alarm="#e66767", alarm_text="#f08a8a", good="#3fb950", warn="#d29922",
    band="rgba(236,235,230,.08)", grid="rgba(236,235,230,.10)",
)

FONT_UI = '"Public Sans", "Segoe UI", system-ui, sans-serif'
FONT_SERIF = '"Source Serif 4", "Iowan Old Style", Georgia, serif'


def css() -> str:
    p = PALETTE
    return f"""
@import url('https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,600&display=swap');

:root {{
  --bg: {p['bg']}; --surface: {p['surface']}; --rule: {p['rule']}; --ink: {p['ink']}; --muted: {p['muted']};
  --accent: {p['accent']}; --alarm: {p['alarm_text']}; --good: {p['good']}; --warn: {p['warn']};
  --f-ui: {FONT_UI}; --f-serif: {FONT_SERIF};
}}
html {{ font-size: clamp(13px, 0.55vw + 9px, 16px); }}
.stApp, .stApp p, .stApp label, .stApp button, .stApp input, .stApp li {{ font-family: var(--f-ui); }}
.stApp {{ background: var(--bg); color: var(--ink); font-variant-numeric: tabular-nums; }}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stAppDeployButton"], [data-testid="stMainMenu"], [data-testid="stDecoration"], #MainMenu, footer {{ display: none !important; }}
[data-testid="stExpandSidebarButton"] button, [data-testid="stSidebarCollapseButton"] button {{ color: var(--muted); }}
.block-container {{ max-width: none; padding: 1rem 2rem 3rem; }}

[data-testid="stSidebar"] {{ background: var(--surface); border-right: 1px solid var(--rule); min-width: 15rem; max-width: 15rem; }}
[data-testid="stSidebar"] h2 {{ font: 600 .95rem/1.2 var(--f-ui); color: var(--ink); }}

.stTabs [data-baseweb="tab-list"] {{ gap: 1.6rem; border-bottom: 1px solid var(--rule); }}
.stTabs [data-baseweb="tab"] {{ height: 2.4rem; padding: 0; background: transparent; font: 500 .95rem var(--f-ui); color: var(--muted); }}
.stTabs [aria-selected="true"] {{ color: var(--ink); }}
.stTabs [data-baseweb="tab-highlight"] {{ background: var(--accent); height: 2px; }}
.stTabs [data-baseweb="tab-border"] {{ display: none; }}
[data-testid="stCaptionContainer"] {{ color: var(--muted); }}
[data-testid="stMultiSelect"] [data-tag] {{ background: var(--bg) !important; color: var(--ink) !important;
  border: 1px solid var(--rule); border-radius: 4px; }}
[data-testid="stMultiSelect"] [data-tag] span, [data-testid="stMultiSelect"] [data-tag] svg {{ color: var(--ink) !important; fill: var(--muted); }}

.sp-head {{ display: flex; align-items: baseline; gap: 1.1rem; flex-wrap: wrap; padding-bottom: .5rem; }}
.sp-head h1 {{ margin: 0; padding: 0; font: 600 1.35rem/1.1 var(--f-serif); color: var(--ink); }}
.sp-head p {{ margin: 0; color: var(--muted); font-size: .85rem; }}

.sp-headline {{ margin: .4rem 0 .15rem; font: 600 1.6rem/1.2 var(--f-serif); color: var(--ink); letter-spacing: -.005em; }}
.sp-sub {{ margin: 0 0 .3rem; color: var(--muted); font-size: .9rem; }}
.sp-status {{ margin: 0 0 .3rem; font-size: .85rem; color: var(--muted); }}
.sp-status b {{ font-weight: 600; }}
.sp-status--live b {{ color: var(--good); }}
.sp-status--stale b {{ color: var(--warn); }}

.sp-now {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr)); gap: .9rem 1.6rem;
  margin: .6rem 0 .5rem; padding: .6rem 0; list-style: none; border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); }}
.sp-now li {{ padding: 0; }}
.sp-now span {{ display: block; color: var(--muted); font-size: .8rem; }}
.sp-now strong {{ display: block; font-size: 1.3rem; font-weight: 600; line-height: 1.25; }}
.sp-now strong small {{ font-size: .85rem; font-weight: 400; color: var(--muted); margin-left: .2rem; }}
.sp-now em {{ display: block; font-style: normal; color: var(--muted); font-size: .78rem; }}
.sp-now .flag strong {{ color: var(--alarm); }}

.sp-label {{ margin: 1rem 0 .2rem; font: 600 1rem/1.3 var(--f-ui); color: var(--ink); }}
.sp-label span {{ margin-left: .6rem; font-weight: 400; font-size: .85rem; color: var(--muted); }}

.sp-table {{ width: 100%; border-collapse: collapse; font-size: .92rem; margin: .2rem 0 .6rem; }}
.sp-table th {{ padding: .5rem .8rem; text-align: right; font-weight: 500; font-size: .8rem; color: var(--muted);
  border-bottom: 1px solid var(--ink); }}
.sp-table td {{ padding: .5rem .8rem; text-align: right; border-bottom: 1px solid var(--rule); }}
.sp-table th:first-child, .sp-table td:first-child {{ text-align: left; padding-left: 0; }}
.sp-table td.best {{ font-weight: 600; }}
.sp-table td.miss {{ color: var(--alarm); }}
.sp-table .key {{ margin-right: .5rem; }}
.sp-table .na {{ color: var(--muted); }}

@media (max-width: 640px) {{
  .sp-headline {{ font-size: 1.35rem; }}
}}
"""


def plot_font() -> dict:
    return dict(family=FONT_UI, size=12, color=PALETTE["muted"])
