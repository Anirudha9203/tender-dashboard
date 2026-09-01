"""
app.py — DEX IT Global tender repository and performance dashboard.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import base64
import html
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import store
from exporter import build_workbook
from etl import (BLANK, CATEGORIES, FY_TARGET, NO_BID_REASONS, OUTCOMES, OWNERS,
                 clean_workbook, parse_volume)
from charts import gauge_figure, icon, ring_svg, sparkline_svg
from metrics import WON_OUTCOMES, compute, cr, fmt_date, inr, inr_short

st.set_page_config(page_title="Tender Repository — DEX IT Global",
                   page_icon="◆", layout="wide", initial_sidebar_state="collapsed")

INK, NAVY, VIOLET, TEAL = "#101828", "#1B3A6B", "#6D5BD0", "#17868F"
AMBER, GREEN, RED, SLATE = "#C97A18", "#0E9F6E", "#D03A3A", "#667085"
GOLD = "#B8860B"          # dark goldenrod, reserved for the Contract value card

CAT_COLOR = {"Submitted": VIOLET, "Not Qualified": AMBER, "Under Process": TEAL,
             "Cancelled": SLATE, "Empanelment": GREEN, "Unassigned": RED}
# Softer reds: the donut sits next to a lot of colour and a hard red reads as an
# alarm rather than a category.
OUT_COLOR = {"Won": GREEN, "Won - PO Awaited": "#7FD1B0", "Lost": "#F0938C",
             "Disqualified": "#F7C6BE", "Under Evaluation": "#9B8BE0",
             "Cancelled Post-Bid": "#B6BECC", "N/A": "#D0D5DD"}

APP_DIR = Path(__file__).resolve().parent
ASSETS = APP_DIR / "assets"

# Shared with Plotly, which cannot read the CSS variable.
PLOT_FONT = 'Calibri, Carlito, "Segoe UI", system-ui, sans-serif'

# Checked in order; the first that exists wins. Drop a file at any of these paths
# and the header picks it up — no code change needed.
LOGO_CANDIDATES = [
    ASSETS / "logo.png",
    ASSETS / "DEX_Logo.png",
    APP_DIR / "DEX_Logo.png",
    APP_DIR / "logo.png",
    Path(r"C:\Users\07826\Desktop\Python Projects\Tender Dashboard\DEX_Logo.png"),
]


@st.cache_data(show_spinner=False)
def logo_uri() -> str:
    """Inline the logo so the header renders as a single HTML block."""
    for path in LOGO_CANDIDATES:
        try:
            if path.is_file():
                mime = "image/svg+xml" if path.suffix.lower() == ".svg" else "image/png"
                return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
        except OSError:
            continue          # unreadable or a Windows path on a non-Windows box
    return ""


def logo_search_paths() -> str:
    return "\n".join(str(p) for p in LOGO_CANDIDATES)



CORAL = "#7FA6D9"          # panel accent, matched to the bar ramp below


def bar_shade(t: float) -> str:
    """Pale → mid pastel blue, weighted by count, so rank reads from colour too."""
    lo, hi = (223, 234, 247), (110, 155, 210)      # #DFEAF7 → #6E9BD2
    return "#%02X%02X%02X" % tuple(round(a + (b - a) * t) for a, b in zip(lo, hi))


# Pastel tints for the repository table. Deliberately no red — a lost bid is a
# fact to read, not an alarm to react to.
STAGE_TINT = {
    "Submitted": "#EEEBFA", "Not Qualified": "#FDF1DF", "Under Process": "#E6F4F5",
    "Cancelled": "#F1F3F6", "Empanelment": "#E7F5EE", "Unassigned": "#FBECEA",
}
OUTCOME_TINT = {
    "Won": "#E1F3EA", "Won - PO Awaited": "#E9F6F0", "Lost": "#FBE9E6",
    "Disqualified": "#FCF0EE", "Under Evaluation": "#EEEBFA",
    "Cancelled Post-Bid": "#F1F3F6",
}


def tint_stage(value):
    return f"background-color:{STAGE_TINT.get(value, '')}; font-weight:600"


def tint_outcome(value):
    return f"background-color:{OUTCOME_TINT.get(value, '')}"


def pastel_cmap(hex_colour: str):
    """A white→pastel colormap for table shading, so cells stay readable."""
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list("p", ["#FFFFFF", hex_colour])


def highlight_due(row):
    """Amber for deadlines inside a fortnight, mint beyond. No red — a near
    deadline is a prompt, not a failure."""
    days = row.get("Days", 99)
    tone = "#FDF3E2" if days <= 14 else "#F1F8F5"
    edge = "#C97A18" if days <= 14 else "#0E9F6E"
    return [f"background-color:{tone}; color:#101828" if c != "Days"
            else f"background-color:{tone}; color:{edge}; font-weight:700"
            for c in row.index]


# ================================================================ LOGIN GATE
# Static credentials, as requested — two plain variables, checked directly.
# Anyone with the link previously had full read/write access to the
# repository with no gate at all; this puts one password in front of it.
# Note for later: these two lines are the entire security model. Fine for an
# internal link shared with a small team, but anyone who can open this file
# can read the password in plain text. If this ever needs to be shared more
# widely, move these two values into `.streamlit/secrets.toml` (st.secrets)
# instead of leaving them in source — same check, credentials just live
# outside the code.
LOGIN_USERNAME = "DEXIT"
LOGIN_PASSWORD = "Tender@2026"


def _auth_css() -> None:
    """Shared styling for the landing and login screens."""
    st.markdown("""
    <style>
    html, body, [class*="css"] {
        font-family: Calibri, Carlito, "Segoe UI", system-ui, sans-serif !important; }
    #MainMenu, footer, header { visibility: hidden; }
    .block-container { padding-top: 8vh !important; max-width: 460px; }

    .login-brand { text-align: center; margin-bottom: 26px; }
    .login-brand img { height: 46px; width: auto; }

    .login-card { padding: 6px 4px 2px; }
    .login-title { font-family: Calibri, Carlito, sans-serif; font-weight: 700;
                    font-size: 22px; color: #101828; text-align: center;
                    letter-spacing: -.01em; margin-bottom: 4px; }
    .login-sub { text-align: center; font-size: 13.5px; color: #667085;
                 margin-bottom: 26px; }

    /* The bordered card itself: a real Streamlit container (key="logincard"),
       not a hand-written <div> — st.markdown calls each render as an isolated
       fragment, so an opening tag in one call and a closing tag in a later
       call never actually nest the content in between; it just leaves a
       stray empty box. A keyed container's border is real and does wrap. */
    .st-key-logincard { border: 1px solid #E4E7EC !important; border-radius: 14px !important;
                         padding: 34px 38px 30px !important;
                         box-shadow: 0 2px 10px rgba(16,24,40,.05); }

    div[data-testid="stForm"] { border: none; padding: 0; }
    div[data-testid="stTextInput"] label {
        font-family: Calibri, Carlito, sans-serif; font-weight: 600; font-size: 12.5px;
        text-transform: uppercase; letter-spacing: .06em; color: #475467; }
    div[data-testid="stTextInput"] input {
        font-family: Calibri, Carlito, sans-serif; font-size: 14.5px;
        border-radius: 8px; border: 1px solid #D0D5DD; padding: 10px 12px; }
    div[data-testid="stTextInput"] input:focus {
        border-color: #1B3A6B; box-shadow: 0 0 0 1px #1B3A6B; }

    div[data-testid="stFormSubmitButton"] { width: 100%; }
    div[data-testid="stFormSubmitButton"] button {
        width: 100%; font-family: Calibri, Carlito, sans-serif; font-weight: 700;
        font-size: 15px; background: #101828; color: #fff; border: none;
        border-radius: 8px; padding: 11px 0; margin-top: 10px; }
    div[data-testid="stFormSubmitButton"] button:hover { background: #1B3A6B; }
    div[data-testid="stFormSubmitButton"] button p { color: #fff; }

    /* Landing page's two choice buttons — plain st.button, not a form submit,
       so they need their own selector rather than reusing the one above. */
    .st-key-landing_wrap div[data-testid="stButton"] button {
        width: 100%; font-family: Calibri, Carlito, sans-serif; font-size: 14.5px;
        border-radius: 8px; padding: 13px 14px; margin-top: 10px; text-align: left; }
    .st-key-landing_wrap div[data-testid="stButton"] button p { text-align: left; }
    .st-key-btn_login button {
        background: #101828 !important; color: #fff !important; border: none !important; }
    .st-key-btn_login button p { color: #fff !important; }
    .st-key-btn_login button:hover { background: #1B3A6B !important; }
    .st-key-btn_view button {
        background: #fff !important; color: #101828 !important;
        border: 1px solid #D0D5DD !important; }
    .st-key-btn_view button:hover { border-color: #1B3A6B !important; color: #1B3A6B !important; }
    .choice-note { font-size: 11.5px; color: #98A2B3; margin: 3px 0 0 2px; }

    .login-foot { text-align: center; font-size: 11.5px; color: #98A2B3;
                  margin-top: 22px; }
    .back-link button {
        background: none !important; border: none !important; color: #667085 !important;
        font-family: Calibri, Carlito, sans-serif; font-size: 13px !important;
        padding: 0 !important; box-shadow: none !important; }
    .back-link button:hover { color: #101828 !important; text-decoration: underline; }
    </style>
    """, unsafe_allow_html=True)


def render_landing() -> None:
    """Entry screen: a choice between the full staff portal and a read-only
    dashboard view that needs no credential at all. Two separate audiences —
    the regional team who edit tenders, and leadership who only ever look."""
    _auth_css()
    logo = logo_uri()
    st.markdown(
        f'<div class="login-brand">{f"<img src=\'{logo}\'/>" if logo else "<b>DEX IT GLOBAL</b>"}</div>',
        unsafe_allow_html=True)

    with st.container(border=True, key="logincard"):
        st.markdown('<div class="login-title">Tender Repository</div>'
                    '<div class="login-sub">Choose how you\'d like to continue</div>',
                    unsafe_allow_html=True)

        with st.container(key="landing_wrap"):
            with st.container(key="btn_login"):
                if st.button("Staff login  →", width='stretch'):
                    st.session_state.show_login_form = True
                    st.rerun()
            st.markdown('<div class="choice-note">Regional team — add, edit and manage tenders</div>',
                       unsafe_allow_html=True)

            with st.container(key="btn_view"):
                if st.button("View dashboard  →", width='stretch'):
                    st.session_state.view_only = True
                    st.rerun()
            st.markdown('<div class="choice-note">Leadership — read-only, no sign-in needed</div>',
                       unsafe_allow_html=True)

    st.markdown('<div class="login-foot">DEX IT Global · Tender Repository</div>',
               unsafe_allow_html=True)


def render_login() -> None:
    """A static, centred sign-in screen. Blocks everything below it via
    st.stop() until the credentials match — nothing from the dashboard,
    including its own CSS, loads before this passes."""
    _auth_css()

    logo = logo_uri()
    st.markdown(
        f'<div class="login-brand">{f"<img src=\'{logo}\'/>" if logo else "<b>DEX IT GLOBAL</b>"}</div>',
        unsafe_allow_html=True)

    with st.container(border=True, key="logincard"):
        st.markdown('<div class="login-title">Tender Repository</div>'
                    '<div class="login-sub">Sign in to continue</div>', unsafe_allow_html=True)

        with st.form("login_form", clear_on_submit=False):
            user = st.text_input("Username", placeholder="Enter username")
            pw = st.text_input("Password", type="password", placeholder="Enter password")
            submitted = st.form_submit_button("Sign in", width='stretch')

        if submitted:
            if user == LOGIN_USERNAME and pw == LOGIN_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect username or password.")

        st.markdown('<div class="back-link">', unsafe_allow_html=True)
        if st.button("←  Back", key="back_to_landing"):
            st.session_state.show_login_form = False
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="login-foot">DEX IT Global · Tender Repository</div>',
               unsafe_allow_html=True)


# Three states: (1) neither flag set → landing choice; (2) mid-login →
# credential form; (3) either flag set → past the gate, into the app below.
if not st.session_state.get("authenticated") and not st.session_state.get("view_only"):
    if st.session_state.get("show_login_form"):
        render_login()
    else:
        render_landing()
    st.stop()


st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Carlito:ital,wght@0,400;0,700;1,400&display=swap');
:root{
  /* Calibri where it exists; Carlito is metric-compatible everywhere else. */
  --ui: Calibri, Carlito, "Segoe UI", system-ui, sans-serif;
  --num: Calibri, Carlito, "Segoe UI", system-ui, sans-serif;
}
html, body, [class*="css"] { font-family: var(--ui) !important; font-size:15px; }
/* Calibri numerals are proportional by default; tabular keeps columns aligned. */
.stDataFrame, .stMarkdown { font-family: var(--ui); }
/* Clears Streamlit's floating toolbar so the header is never clipped. */
.block-container { padding-top: 3.4rem !important; padding-bottom: 3rem; max-width: 1500px; }
#MainMenu, footer { visibility: hidden; }

/* ---------- header: no fill, just a rule and the mark ---------- */
.hdrlogo { margin-bottom:10px; }
.hdrlogo img { height:52px; width:auto; display:block; }
.hdr { display:flex; align-items:flex-end; gap:20px; flex-wrap:wrap;
       border-bottom:2px solid #101828; padding-bottom:12px; margin-bottom:6px; }
.hdr .t { font-family:var(--ui); font-weight:700; font-size:28px; letter-spacing:-.015em;
          color:#101828; line-height:1.15; }
.hdr .t span { display:block; font-size:12.5px; font-weight:700; letter-spacing:.15em;
               color:#667085; text-transform:uppercase; margin-top:3px; }
.hdr .sp { margin-left:auto; display:flex; align-items:center; gap:22px; }
.hdr .prog u { display:block; text-decoration:none; font-family:var(--ui); font-size:11px;
               letter-spacing:.12em; color:#667085; font-weight:700; }
.hdr .prog b { font-family:var(--num); font-variant-numeric:tabular-nums; font-size:21px; font-weight:700; color:#101828; }
.hdr .prog b i { color:#98A2B3; font-style:normal; font-size:15px; }
.meter { width:180px; height:6px; background:#EEF0F4; border-radius:99px; overflow:hidden; margin-top:5px; }
.meter i { display:block; height:100%; background:linear-gradient(90deg,#6D5BD0,#17868F); }

.subhdr { font-size:12.5px; color:#98A2B3; margin-bottom:14px; }
.gsplit { border-top:1px solid #EEF0F4; margin:10px 0 8px; }

/* ---------- header band ---------- */
.band { display:flex; align-items:center; gap:16px; flex-wrap:wrap; padding:14px 18px;
        border:1px solid #E4E7EC; border-radius:12px; margin-bottom:14px;
        background:linear-gradient(102deg,#F7F8FC 0%,#FFFFFF 46%,#F4FAF9 100%); }
.band-t { font-family:var(--ui); font-weight:700; font-size:19px; letter-spacing:.01em;
          color:#101828; text-transform:uppercase; }
.band-s { font-size:13px; color:#667085; margin-top:2px; }
.band-chip { margin-left:auto; display:flex; align-items:center; gap:8px; background:#fff;
             border:1px solid #E4E7EC; border-radius:9px; padding:8px 13px; font-size:13.5px;
             color:#344054; font-weight:500; }

/* ---------- KPI card v2 ---------- */
.kpi2 { background:#fff; border:1px solid #E4E7EC; border-radius:12px; padding:13px 14px 11px;
        height:154px; display:flex; flex-direction:column; position:relative; overflow:hidden; }
.kpi2:before { content:""; position:absolute; inset:0 auto 0 0; width:3px; background:var(--k); }
.kpi2-top { display:flex; align-items:center; gap:10px; }
.kpi2-ic { width:34px; height:34px; border-radius:9px; display:flex; align-items:center;
           justify-content:center; background:color-mix(in srgb, var(--k) 11%, white); flex:none; }
.kpi2-v { font-family:var(--num); font-variant-numeric:tabular-nums; font-weight:700; font-size:25px; color:var(--k);
          line-height:1; white-space:nowrap; }
.kpi2-l { font-family:var(--ui); font-size:11.5px; font-weight:700; text-transform:uppercase;
          letter-spacing:.085em; color:#344054; margin-top:9px; }
.kpi2-n { font-size:11.5px; color:#98A2B3; line-height:1.35; margin-top:3px; }
.kpi2-s { margin-top:auto; margin-left:-4px; line-height:0; }
.kpi2-s svg { display:block; }

/* ---------- panels ---------- */
.panel-h { display:flex; align-items:center; gap:9px; font-family:var(--ui); font-weight:700;
           font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:#101828; }
.panel-s { font-size:12.5px; color:#98A2B3; margin:3px 0 10px; }

/* ---------- pipeline nodes ---------- */
.pnode { text-align:center; padding:4px 2px 2px; }
.pnode-v { font-family:var(--num); font-variant-numeric:tabular-nums; font-weight:700; font-size:26px; line-height:1.1; }
.pnode-p { font-family:var(--num); font-variant-numeric:tabular-nums; font-size:12px; color:#98A2B3; margin-bottom:7px; }
.pnode svg { display:block; margin:0 auto; }
.pnode-l { font-family:var(--ui); font-size:12px; font-weight:700; text-transform:uppercase;
           letter-spacing:.06em; margin-top:9px; }
.pnode-s { font-size:11.5px; color:#98A2B3; margin-top:2px; }
.pnode-gap { height:41px; }
.rowgap { height:16px; }
/* ---------- form section header ---------- */
.fsec { display:flex; align-items:center; gap:11px; padding:4px 0 10px; margin-top:6px; }
.fsec-ic { width:36px; height:36px; border-radius:10px; display:flex; align-items:center;
           justify-content:center; flex:none; }
.fsec b { display:block; font-family:var(--ui); font-size:13px; font-weight:700;
          text-transform:uppercase; letter-spacing:.08em; color:#6D5BD0; }
.fsec s { text-decoration:none; font-size:12px; color:#98A2B3; }

/* Keeps the two chart panels the same height whatever their content. */
.st-key-chartrow div[data-testid="stVerticalBlockBorderWrapper"] { min-height:455px; }
/* Bottom-align the right card (Target completion + Win rate) with the left
   column's two stacked cards (Pipeline progression, Opportunity and pace).
   Streamlit already stretches both stColumns to equal height by default flex
   behaviour — confirmed at 623px/623px in testing — so the only real gap is
   that the bordered box *inside* the right column (a plain stVerticalBlock;
   this Streamlit build has no separate border-wrapper element) does not itself
   fill that column. `key="gaugecard"` on that container gives it the stable
   `.st-key-gaugecard` class, which is the only hook used here — no reliance on
   emotion-cache hashes or a border-wrapper testid that doesn't exist in this
   build. The card becomes a flex column at 100% height; a spacer at the very
   end of its content (after the Win rate insight card) absorbs the leftover
   space, so the heading, both gauges and both insight cards keep the exact
   size they already had. */
.st-key-gaugecard { height:100%; display:flex; flex-direction:column; }
.gauge-fill { flex:1 1 auto; min-height:0; }


/* ---------- donut legend ---------- */
.lgd-wrap { padding-top:6px; }
.lgd { display:flex; align-items:center; gap:9px; padding:5px 2px;
       border-bottom:1px solid #F2F4F7; font-size:12.5px; }
.lgd:last-child { border-bottom:0; }
.lgd i { font-style:normal; width:24px; height:20px; border-radius:5px; color:#fff;
         font-weight:700; font-size:11.5px; display:flex; align-items:center;
         justify-content:center; flex:none; font-variant-numeric:tabular-nums; }
.lgd span { flex:1; color:#344054; }
.lgd b { font-family:var(--num); font-variant-numeric:tabular-nums; font-size:12.5px;
         color:#101828; }

.pnote { font-size:12px; color:#98A2B3; margin-top:14px; text-align:center; }
.st-key-pipeline div[data-testid="stButton"] > button {
    background:#fff; border:1px solid #E4E7EC; border-radius:8px; padding:5px 8px;
    font-size:12px; font-family:var(--ui); font-weight:600; color:#475467; min-height:0;
    margin-top:8px; }
.st-key-pipeline div[data-testid="stButton"] > button:hover {
    background:#101828; color:#fff; border-color:#101828; }
.st-key-pipeline div[data-testid="stButton"] > button p { font-size:12px; margin:0; }

.drops { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:14px; }
.drop { background:#F8F9FB; border:1px solid #EEF0F4; border-radius:10px; padding:10px 13px; }
.drop u { display:block; text-decoration:none; font-family:var(--ui); font-size:10.5px;
          font-weight:700; text-transform:uppercase; letter-spacing:.07em; color:#667085; }
.drop b { font-family:var(--num); font-variant-numeric:tabular-nums; font-weight:700; font-size:19px; display:block;
          margin:2px 0 1px; }
.drop s { text-decoration:none; font-size:10.5px; color:#98A2B3; }

/* ---------- gauge verdict box ---------- */
.verdict { background:#F8F9FB; border:1px solid #EEF0F4; border-left:3px solid #0E9F6E;
           border-radius:9px; padding:11px 13px; height:100%; }
.verdict[data-ok="0"] { border-left-color:#C97A18; }
.verdict b { font-family:var(--ui); font-size:14px; color:#101828; display:block; }
.verdict p { font-size:11.5px; color:#667085; line-height:1.45; margin:4px 0 0; }
.vsplit { display:flex; gap:14px; margin-top:9px; padding-top:9px; border-top:1px solid #E4E7EC; }
.vsplit u { display:block; text-decoration:none; font-size:10.5px; text-transform:uppercase;
            letter-spacing:.07em; color:#98A2B3; font-family:var(--ui); font-weight:700; }
.vsplit s { text-decoration:none; font-family:var(--num); font-variant-numeric:tabular-nums; font-weight:700; font-size:16px;
            color:#101828; }
.vfoot { font-size:11px !important; color:#98A2B3 !important; margin-top:8px !important; }

/* ---------- summary strip ---------- */
.strip { display:flex; gap:12px; align-items:center; background:#F8F9FB; border:1px solid #EEF0F4;
         border-radius:11px; padding:13px 15px; height:84px; margin-bottom:11px; }
.strip > div { min-width:0; flex:1; }
.strip-ic { width:34px; height:34px; border-radius:9px; display:flex; align-items:center;
            justify-content:center; flex:none; }
.strip u { display:block; text-decoration:none; font-family:var(--ui); font-size:11px;
           font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:#667085; }
.strip b { font-family:var(--num); font-variant-numeric:tabular-nums; font-weight:700; font-size:21px; color:#101828;
           display:block; margin:2px 0 2px; line-height:1.15;
           overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.strip s { text-decoration:none; font-size:11.5px; color:#98A2B3; line-height:1.3; display:block; }

/* ---------- KPI cards: fixed height, full coloured border ---------- */
.kpi { background:#fff; border:1.5px solid var(--k); border-radius:11px; padding:14px 12px;
       text-align:center; height:126px; display:flex; flex-direction:column;
       align-items:center; justify-content:center; gap:2px; }
.kpi b { font-family:var(--num); font-variant-numeric:tabular-nums; font-weight:700; font-size:26px; line-height:1.1;
         color:var(--k); display:block; }
.kpi u { text-decoration:none; font-family:var(--ui); font-size:10px; font-weight:700;
         text-transform:uppercase; letter-spacing:.09em; color:#475467; }
.kpi p { font-size:10.5px; color:#98A2B3; line-height:1.35; margin:2px 0 0; }

/* ---------- funnel ---------- */
.node { border-radius:10px; padding:13px 15px; text-align:center; border:1.5px solid;
        height:112px; display:flex; flex-direction:column; justify-content:center; }
.node u { display:block; text-decoration:none; font-family:var(--ui); font-size:12px;
          font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:#475467; }
.node b { font-family:var(--num); font-variant-numeric:tabular-nums; font-weight:700; font-size:30px; display:block; }
.node s { text-decoration:none; font-size:11.5px; color:#98A2B3; display:block; }
.stem { text-align:center; color:#D0D5DD; font-size:15px; line-height:1; margin:5px 0; }

/* Leaf rows are real buttons so they can be clicked; styled to look like rows. */
.st-key-funnel div[data-testid="stButton"] > button {
    width:100%; text-align:left; justify-content:flex-start; background:#F8F9FB;
    border:1px solid #EEF0F4; border-radius:7px; padding:7px 12px; font-size:12.5px;
    font-family:var(--ui); font-weight:500; color:#101828; min-height:0; line-height:1.4; }
.st-key-funnel div[data-testid="stButton"] > button:hover {
    background:#101828; color:#fff; border-color:#101828; }
.st-key-funnel div[data-testid="stButton"] > button p {
    font-size:13.5px; margin:0; width:100%; text-align:left; }
/* The active selection stays dark so it is obvious which figure is open. */
.st-key-funnel div[data-testid="stButton"] > button:focus:not(:active) {
    background:#101828; color:#fff; border-color:#101828; }

.ins { padding:14px 17px; border-left:3px solid var(--c,#6D5BD0); background:#FAFAFE;
       border-radius:0 9px 9px 0; font-size:14.5px; line-height:1.6; margin-bottom:11px; }
.ins b { font-family:var(--ui); display:block; margin-bottom:2px; font-size:14px; color:#101828; }
.flag { padding:11px 14px; border-radius:9px; background:#FFF8EC; border:1px solid #F3DFBC;
        font-size:14px; line-height:1.55; margin-bottom:9px; }
.flag[data-t="hi"] { background:#FEF1F1; border-color:#F3CFCF; }
.flag[data-t="ok"] { background:#EDF9F4; border-color:#C3E8D8; }
.flag b { font-family:var(--ui); }
.sec { font-family:var(--ui); font-size:12.5px; font-weight:700; text-transform:uppercase;
       letter-spacing:.09em; color:#6D5BD0; margin:16px 0 4px; }
.stTabs [data-baseweb="tab"] { font-family:var(--ui); font-weight:700; font-size:15.5px; }
/* The 'Press Enter to submit form' hint invites accidental half-filled saves. */
div[data-testid="InputInstructions"] { display:none !important; }

/* Selectbox dropdowns: BaseWeb computes the popover's max-height dynamically
   from whatever room happens to be free at the click position, and then
   auto-scrolls the list to keep the *currently selected* option in view. On a
   short window this combination can leave an option — commonly "Won", since
   it sits first while a later value like "N/A" is what's selected — scrolled
   out of the visible box entirely. A viewport-relative cap (not a fixed
   pixel value, so it can never itself force the box past the opposite screen
   edge) keeps enough of the list visible that nothing needs scrolling on any
   normal window, and a visibly-styled scrollbar covers the rare case where it
   still does. */
div[data-testid="stSelectboxVirtualDropdown"] { max-height: min(60vh, 360px) !important; }
div[data-testid="stSelectboxVirtualDropdown"] [role="listbox"] {
    scrollbar-width: thin; scrollbar-color: #C7CCD6 transparent; }
div[data-testid="stSelectboxVirtualDropdown"] [role="listbox"]::-webkit-scrollbar { width: 7px; }
div[data-testid="stSelectboxVirtualDropdown"] [role="listbox"]::-webkit-scrollbar-thumb {
    background: #C7CCD6; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- state
def load_rows() -> list[dict]:
    if "rows" not in st.session_state:
        st.session_state.rows = store.load_tenders()
    return st.session_state.rows


def commit(rows: list[dict], action: str, message: str) -> None:
    store.save_tenders(rows, action)
    st.session_state.rows = rows
    st.session_state.flash = message


rows = load_rows()

if flash := st.session_state.pop("flash", None):
    st.success(flash)


def owner_options(all_rows: list[dict]) -> list[str]:
    """Existing owners plus anyone already typed in — never a closed list."""
    seen = {r.get("owner") for r in all_rows if r.get("owner")}
    named = (seen | set(OWNERS)) - {"Unassigned", ""}
    return sorted(named) + ["Unassigned"]        # 'Unassigned' always sits last, once


def reason_options(all_rows: list[dict]) -> list[str]:
    """The fixed reason taxonomy plus any custom reason already typed in by
    someone else — same open-list pattern as owner_options, so a reason typed
    once becomes a normal pickable option for every tender after it."""
    fixed = [r for r in NO_BID_REASONS if r]                     # drop the blank sentinel
    seen = {r.get("noBidReason") for r in all_rows if r.get("noBidReason")}
    custom = sorted(seen - set(fixed) - {"Not categorised"})
    return fixed[:-1] + custom + [fixed[-1]]      # 'Not categorised' stays last


# ---------------------------------------------------------------- import gate
def import_panel(first_run: bool) -> None:
    if first_run:
        st.markdown('<div class="hdr"><div class="t">Tender Repository'
                    '<span>DEX IT Global</span></div></div>', unsafe_allow_html=True)
        st.subheader("Upload your tender workbook to get started")
        st.caption("This happens once. The cleaned records are written to a `data/` folder "
                   "beside the app, and every later visit reads from there — no re-upload.")

    up = st.file_uploader("Tender workbook (.xlsx) — must contain a sheet named 'Tenders'",
                          type=["xlsx", "xlsm"], key="uploader")
    if up is None:
        return

    try:
        records, notes = clean_workbook(up)
    except Exception as exc:                              # noqa: BLE001
        st.error(f"Could not read that workbook. {exc}")
        return

    st.success(f"Read {len(records)} tenders from **{up.name}**.")
    for n in notes:
        st.markdown(f"- {n}")

    if first_run:
        if st.button("Save to repository", type="primary"):
            commit(records, f"import {up.name}", f"Repository created with {len(records)} tenders.")
            st.rerun()
        return

    st.warning("A repository already exists. Choose how to bring this file in.")
    c1, c2 = st.columns(2)
    if c1.button("Merge — update matches, add new", type="primary", width='stretch'):
        existing = {(r["organization"].lower(), r.get("submissionDate", "")): i
                    for i, r in enumerate(rows)}
        merged, added, updated = list(rows), 0, 0
        for rec in records:
            key = (rec["organization"].lower(), rec.get("submissionDate", ""))
            if key in existing:
                keep_id = merged[existing[key]]["id"]
                merged[existing[key]] = {**rec, "id": keep_id}
                updated += 1
            else:
                merged.append({**rec, "id": store.next_id(merged)})
                added += 1
        commit(merged, f"merge {up.name}", f"Merged: {updated} updated, {added} added.")
        st.rerun()
    if c2.button("Replace everything", width='stretch'):
        commit(records, f"replace {up.name}", f"Repository replaced with {len(records)} tenders.")
        st.rerun()


if not rows:
    import_panel(first_run=True)
    st.stop()

m = compute(rows)
meta = store.read_meta()

# ---------------------------------------------------------------- header
logo = logo_uri()
_, lcol = st.columns([8, 1])
with lcol:
    if st.session_state.get("view_only"):
        if st.button("Exit view", key="exit_view_btn", width='stretch'):
            st.session_state.view_only = False
            st.rerun()
    else:
        if st.button("Log out", key="logout_btn", width='stretch'):
            st.session_state.authenticated = False
            st.rerun()
st.markdown(f"""
{f'<div class="hdrlogo"><img src="{logo}" alt="DEX IT Global"/></div>' if logo else ''}
<div class="hdr">
  <div class="t">Tender Repository<span>Financial Year 2026&ndash;27</span></div>
  <div class="sp">
    <div class="prog"><u>PROCESSED / TARGET</u>
      <b>{m['processed']} <i>/ {FY_TARGET}</i></b>
      <div class="meter"><i style="width:{min(100, m['pct_of_target']):.1f}%"></i></div>
    </div>
  </div>
</div>
{f'<div class="subhdr">Last saved {meta.get("lastSaved", "—").replace("T", " ")} &nbsp;·&nbsp; {len(rows)} tenders in the repository</div>' if not st.session_state.get("view_only") else ''}
""", unsafe_allow_html=True)

if not logo:
    with st.expander("Logo not found — click to see where to put it"):
        st.caption("Save the logo as any one of these and refresh:")
        st.code(logo_search_paths(), language=None)

# Data health is hidden for now. To bring it back, restore the entry below and
# un-comment the `with tab_health:` block further down.
#
# In view-only mode there's exactly one thing to show, so there's no tab bar
# at all — tab_dash becomes a plain container standing in for the real tab,
# and Repository / Add-edit / Import-backup are skipped entirely below via the
# same view_only check, rather than existing but disabled.
if st.session_state.get("view_only"):
    tab_dash = st.container()
else:
    tab_dash, tab_repo, tab_edit, tab_data = st.tabs(
        ["Dashboard", f"Repository ({len(rows)})", "Add / edit tender",
         "Import & backups"])
# tab_dash, tab_repo, tab_edit, tab_health, tab_data = st.tabs(
#     ["Dashboard", f"Repository ({len(rows)})", "Add / edit tender",
#      f"Data health ({len(m['issues'])})", "Import & backups"])


# ================================================================ DASHBOARD
def kpi(col, label, value, note, color):
    col.markdown(f'<div class="kpi" style="--k:{color}"><b>{value}</b><u>{label}</u>'
                 f'<p>{note}</p></div>', unsafe_allow_html=True)


def drill_rows(kind: str, value: str) -> list[dict]:
    """Records behind a funnel figure."""
    if kind == "won":
        return [r for r in m["submitted"] if r["outcome"] in WON_OUTCOMES]
    if kind == "outcome":
        return [r for r in m["submitted"] if r["outcome"] == value]
    if kind == "reason":
        return [r for r in m["not_qualified"] if (r.get("noBidReason") or "Not categorised") == value]
    if kind == "live":
        # Everything counted in the funnel, i.e. every tender with a real status.
        return m["live"]
    return [r for r in rows if r.get("category") == value]


with tab_dash:
    # ---------- header band ----------
    st.markdown(f"""
    <div class="band">
      <div>
        <div class="band-t">FY27 Performance Overview</div>
        <div class="band-s">Tracking {m['processed']} processed tenders against a {FY_TARGET} annual target</div>
      </div>
      <div class="band-chip">{icon('calendar', '#475467', 15)}
        <span>FY27 &nbsp;Apr 26 – Mar 27</span></div>
    </div>""", unsafe_allow_html=True)

    # ---------- KPI cards with icon + sparkline ----------
    CARDS = [
        ("documents", "Processed FY27", str(m["processed"]),
         f"{m['pending']} to go · {m['pct_of_target']:.0f}% of target", NAVY, "processed"),
        ("target", "Win rate", f"{m['win_rate']:.0f}%",
         f"{len(m['won'])} won of {m['decided']} decided", GREEN, "win_rate"),
        ("send", "Bid Submission", f"{m['bid_rate']:.0f}%",
         f"{len(m['submitted'])} of {m['processed']} submitted", VIOLET, "bid_rate"),
        ("speed", "Required run rate", f"{m['required_run_rate']:.1f}/mo",
         f"To reach {FY_TARGET} by 31 Mar 27", TEAL, "required"),
        ("rupee", "Contract value", cr(m["won_value"]) if m["won_value"] else "—",
         f"{len(m['won_valued'])} of {len(m['won'])} wins valued · Won + PO Awaited only",
         GOLD, "contract_value"),
    ]
    cols = st.columns(len(CARDS))
    for col, (ic, label, value, note, colour, series) in zip(cols, CARDS):
        col.markdown(
            f'<div class="kpi2" style="--k:{colour}">'
            f'  <div class="kpi2-top">'
            f'    <span class="kpi2-ic">{icon(ic, colour, 19)}</span>'
            f'    <span class="kpi2-v">{value}</span>'
            f'  </div>'
            f'  <div class="kpi2-l">{label}</div>'
            f'  <div class="kpi2-n">{note}</div>'
            f'  <div class="kpi2-s">{sparkline_svg(m["spark"][series], colour)}</div>'
            f'</div>', unsafe_allow_html=True)

    st.markdown('<div class="rowgap"></div>', unsafe_allow_html=True)

    mainrow = st.container(key="mainrow")
    left, right = mainrow.columns([1.25, 1], gap="medium")

    # ---------- pipeline progression ----------
    with left.container(border=True):
        st.markdown(f'<div class="panel-h">{icon("layers", VIOLET, 17)}'
                    f'<span>Pipeline progression</span></div>'
                    f'<div class="panel-s">Every stage is clickable — select one to list those tenders</div>',
                    unsafe_allow_html=True)

        STAGES = [
            ("Target FY27", FY_TARGET, "flag", "#8FA0BF", "Annual target", None, None),
            ("Processed", m["processed"], "gear", NAVY, "Tenders processed", "live", "all"),
            ("Bid submitted", len(m["submitted"]), "send", VIOLET, "Bids submitted", "category", "Submitted"),
            ("Won", len(m["won"]), "trophy", GREEN, "Projects won", "won", "Won"),
        ]

        with st.container(key="pipeline"):
            node_cols = st.columns(len(STAGES))
            for col, (name, count, ic, colour, sub, kind, val) in zip(node_cols, STAGES):
                pct = count / FY_TARGET * 100 if FY_TARGET else 0
                col.markdown(
                    f'<div class="pnode">'
                    f'  <div class="pnode-v" style="color:{colour}">{count}</div>'
                    f'  <div class="pnode-p">{pct:.0f}%</div>'
                    f'  {ring_svg(pct, colour, 84, 7, icon(ic, colour, 26, 1.6))}'
                    f'  <div class="pnode-l" style="color:{colour}">{name}</div>'
                    f'  <div class="pnode-s">{sub}</div>'
                    f'</div>', unsafe_allow_html=True)
                if not kind:
                    col.markdown('<div class="pnode-gap"></div>', unsafe_allow_html=True)
                else:
                    if col.button("View tenders", key=f"pn_{name}", width='stretch'):
                        cur = st.session_state.get("drill")
                        new_sel = (kind, val, f"{name} ({count})")
                        st.session_state.drill = None if cur == new_sel else new_sel
                        st.rerun()

        st.markdown('<div class="pnote">Percentages are each stage as a share of the '
                    f'{FY_TARGET}-tender annual target.</div>', unsafe_allow_html=True)

    # ---------- opportunity & pace, filling the space beside the gauges ----------
    with left.container(border=True):
        st.markdown(f'<div class="panel-h">{icon("chart", NAVY, 17)}'
                    f'<span>Opportunity and pace</span></div>'
                    f'<div class="panel-s">Sized on contract value where it has been entered — '
                    f'{len(m["all_valued"])} of {m["processed"]} tenders so far</div>',
                    unsafe_allow_html=True)

        # The header KPI row already states the won value on its own (added
        # separately) — repeating it here would be a pure duplicate, so this
        # card converts it to a rate instead: what share of everything we've
        # tracked in money terms actually converted into a win. That's the
        # same "upstream loss" story as the block-rate insight, told in value
        # rather than count, and it is new information rather than a repeat.
        value_conv = (m["won_value"] / m["total_tracked_value"] * 100
                     if m["total_tracked_value"] else 0)
        STRIP = [
            ("rupee", "Contract value tracked", cr(m["total_tracked_value"]),
             f"Sum of the {len(m['all_valued'])} tenders with a value entered", VIOLET),
            ("trophy", "Value conversion", f"{value_conv:.0f}%",
             (f"{cr(m['won_value'])} won of {cr(m['total_tracked_value'])} tracked"
              if m["total_tracked_value"] else "No values recorded yet"), TEAL),
            ("speed", "Current run rate", f"{m['per_month']:.1f}/mo",
             f"Projects to {m['projected']} against a target of {FY_TARGET}", NAVY),
            ("target", "Target attainment", f"{m['attainment']:.0f}%",
             f"{m['months_left']:.1f} months of FY27 left", GREEN),
        ]

        # Two per row, matching the requested 2x2 arrangement.
        for chunk in (STRIP[:2], STRIP[2:4]):
            row = st.columns(2)
            for col, (ic, label, value, note, colour) in zip(row, chunk):
                col.markdown(
                    f'<div class="strip">'
                    f'  <span class="strip-ic" style="background:{colour}14">{icon(ic, colour, 18)}</span>'
                    f'  <div><u>{label}</u><b>{value}</b><s>{note}</s></div>'
                    f'</div>', unsafe_allow_html=True)

    # ---------- gauges ----------
    with right.container(border=True, key="gaugecard"):
        st.markdown(f'<div class="panel-h">{icon("target", TEAL, 17)}'
                    f'<span>Participatory Target completion</span></div>', unsafe_allow_html=True)
        gcol, ncol = st.columns([1.5, 1])
        gcol.plotly_chart(
            gauge_figure(m["pct_of_target"], marker=m["year_elapsed_pct"],
                         center_label=f"{m['pct_of_target']:.1f}%",
                         sub_label=f"{m['processed']} of {FY_TARGET}", height=250),
            width='stretch', config={"displayModeBar": False})
        on_pace = m["per_month"] >= m["required_run_rate"]
        ncol.markdown(
            f'<div class="verdict" data-ok="{1 if on_pace else 0}">'
            f'  <b>{"On track" if on_pace else "Behind pace"}</b>'
            f'  <p>{"Current run rate covers the remaining target." if on_pace else "Run rate is short of what the remaining target needs."}</p>'
            f'  <div class="vsplit">'
            f'    <div><u>Required</u><s>{m["required_run_rate"]:.1f}/mo</s></div>'
            f'    <div><u>Current</u><s>{m["per_month"]:.1f}/mo</s></div>'
            f'  </div>'
            f'  <p class="vfoot">Dotted marker = {m["year_elapsed_pct"]:.0f}% of FY27 elapsed.</p>'
            f'</div>', unsafe_allow_html=True)

        st.markdown('<div class="gsplit"></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="panel-h">{icon("trophy", GREEN, 17)}'
                    f'<span>Win rate</span></div>', unsafe_allow_html=True)
        g2, n2 = st.columns([1.5, 1])
        g2.plotly_chart(
            gauge_figure(m["win_rate"], center_label=f"{m['win_rate']:.1f}%",
                         sub_label=f"{len(m['won'])} of {m['decided']} decided", height=250),
            width='stretch', config={"displayModeBar": False})
        n2.markdown(
            f'<div class="verdict" data-ok="1">'
            f'  <b>Strong on entered bids</b>'
            f'  <p>The loss is upstream: {m["block_rate"]:.0f}% never reached a bid.</p>'
            f'  <div class="vsplit">'
            f'    <div><u>Won</u><s style="color:{GREEN}">{len(m["won"])}</s></div>'
            f'    <div><u>Lost</u><s style="color:#E0776D">{len(m["lost"])}</s></div>'
            f'    <div><u>Disqualified</u><s style="color:{AMBER}">{len(m["disq"])}</s></div>'
            f'  </div>'
            f'  <p class="vfoot">Won + Lost + Disqualified = {m["decided"]} decided bids. '
            f'{len(m["under_eval"])} more are still under evaluation.</p>'
            f'</div>', unsafe_allow_html=True)

        # Absorbs the leftover height so the panel's bottom edge lands exactly on
        # the left column's, without resizing anything above it.
        st.markdown('<div class="gauge-fill"></div>', unsafe_allow_html=True)
    # ---------- clickable funnel tree ----------
    st.markdown('<div class="sec">FY27 tender funnel — select any figure to list those tenders</div>',
                unsafe_allow_html=True)

    with st.container(key="funnel"):
        mid = st.columns([1, 2, 1])[1]
        mid.markdown(f"""<div class="node" style="background:#EEF3FB;border-color:#CBDBF0">
          <u>Tenders processed</u><b style="color:{NAVY}">{m['processed']}</b>
          <s>of {FY_TARGET} targeted · {m['pending']} pending</s></div>
          <div class="stem">│</div>""", unsafe_allow_html=True)

        b1, b2, b3 = st.columns(3)

        def branch(col, color, bg, label, n, sub, leafs, kind):
            col.markdown(f'<div class="node" style="background:{bg};border-color:{color}55">'
                         f'<u>{label}</u><b style="color:{color}">{n}</b><s>{sub}</s></div>'
                         '<div style="height:9px"></div>', unsafe_allow_html=True)
            for name, val, key_kind, key_val in leafs:
                if not val:
                    continue
                if col.button(f"{name}  ·  {val}", key=f"lf_{kind}_{name}", width='stretch'):
                    cur = st.session_state.get("drill")
                    new = (key_kind, key_val, f"{name} ({val})")
                    st.session_state.drill = None if cur == new else new
                    st.rerun()

        branch(b1, VIOLET, "#F3F0FC", "Qualified &amp; submitted", len(m["submitted"]),
               "A bid was actually entered",
               [("Won", len(m["won"]), "won", "Won"),
                ("Lost", len(m["lost"]), "outcome", "Lost"),
                ("Under evaluation", len(m["under_eval"]), "outcome", "Under Evaluation"),
                ("Disqualified", len(m["disq"]), "outcome", "Disqualified"),
                ("Cancelled after bid", len(m["post_cancel"]), "outcome", "Cancelled Post-Bid")],
               "sub")
        branch(b2, AMBER, "#FFF8EC", "Did not qualify", len(m["not_qualified"]),
               "Blocked before submission",
               [(r["reason"], r["count"], "reason", r["reason"]) for r in m["reasons"][:6]],
               "nq")
        branch(b3, TEAL, "#EAF6F7", "Pipeline &amp; other",
               len(m["under_process"]) + len(m["cancelled"]) + len(m["empanelment"]),
               "No bid outcome yet",
               [("Under process", len(m["under_process"]), "category", "Under Process"),
                ("Tender cancelled", len(m["cancelled"]), "category", "Cancelled"),
                ("Empanelment", len(m["empanelment"]), "category", "Empanelment")],
               "pl")

    if drill := st.session_state.get("drill"):
        kind, value, label = drill
        picked = drill_rows(kind, value)
        head, close = st.columns([5, 1])
        head.markdown(f'<div class="sec" style="margin-top:8px">{label} — {len(picked)} tenders</div>',
                      unsafe_allow_html=True)
        if close.button("Close", key="drill_close"):
            st.session_state.drill = None
            st.rerun()
        if picked:
            st.dataframe(pd.DataFrame([{
                "ID": r["id"], "Organisation": r["organization"], "City": r.get("city", ""),
                "Owner": r.get("owner", ""), "Bid due": fmt_date(r.get("submissionDate", "")),
                "Value (₹ Cr)": cr(r["contractValue"]) if r.get("contractValue") else "—",
                "EMD": inr(r["emd"]) if r.get("emd") else "—",
                "Outcome": r.get("outcome") if r.get("outcome") != "N/A" else (r.get("noBidReason") or "—"),
                "Notes": (r.get("concerns") or r.get("remarks") or "")[:120],
            } for r in picked]), hide_index=True, width='stretch',
                height=min(420, 44 + 35 * len(picked)))

    st.markdown('<div class="rowgap"></div>', unsafe_allow_html=True)
    chartrow = st.container(key="chartrow")
    left, right = chartrow.columns([1.05, 1], gap="medium")

    # ---------- why we could not bid ----------
    with left.container(border=True):
        st.markdown(f'<div class="panel-h">{icon("flag", CORAL, 17)}'
                    f'<span>Why we could not bid</span></div>'
                    f'<div class="panel-s">{len(m["not_qualified"])} tenders blocked before a bid '
                    f'could be entered</div>', unsafe_allow_html=True)
        if m["reasons"]:
            d = list(reversed(m["reasons"]))
            # Softer coral→peach ramp: the longest bar is the darkest, so rank reads
            # from colour as well as length.
            hi = max(r["count"] for r in d) or 1
            shades = [bar_shade(r["count"] / hi) for r in d]
            fig = go.Figure(go.Bar(
                x=[r["count"] for r in d], y=[r["reason"] for r in d],
                orientation="h", marker=dict(color=shades,
                                             line=dict(color="white", width=1)),
                text=[r["count"] for r in d], textposition="outside",
                textfont=dict(size=12, color="#475467"),
                hovertemplate="%{y}: %{x} tenders<extra></extra>"))
            fig.update_layout(height=330, margin=dict(l=0, r=26, t=6, b=6),
                              plot_bgcolor="white", showlegend=False, bargap=.34,
                              xaxis=dict(showgrid=True, gridcolor="#F2F4F7", dtick=1,
                                         zeroline=False, title=None),
                              yaxis=dict(tickfont=dict(size=11.5)),
                              font=dict(family=PLOT_FONT, size=12, color="#344054"))
            st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})
        else:
            st.info("No blocked tenders recorded.")

    # ---------- outcome of submitted bids ----------
    with right.container(border=True):
        st.markdown(f'<div class="panel-h">{icon("trophy", GREEN, 17)}'
                    f'<span>Outcome of submitted bids</span></div>'
                    f'<div class="panel-s">Where the {len(m["submitted"])} entered bids ended up</div>',
                    unsafe_allow_html=True)
        counts = [(o, len([r for r in m["submitted"] if r["outcome"] == o]))
                  for o in OUTCOMES if o != "N/A"]
        counts = [c2 for c2 in counts if c2[1]]
        if counts:
            total = sum(c2[1] for c2 in counts)
            dcol, lcol = st.columns([1.15, 1])
            fig = go.Figure(go.Pie(
                labels=[c2[0] for c2 in counts], values=[c2[1] for c2 in counts], hole=.60,
                marker=dict(colors=[OUT_COLOR[c2[0]] for c2 in counts],
                            line=dict(color="white", width=2.5)),
                textinfo="value", textfont=dict(size=13, color="white"), sort=False,
                hovertemplate="%{label}: %{value} bids<extra></extra>"))
            fig.update_layout(height=330, margin=dict(l=0, r=0, t=6, b=6), showlegend=False,
                              font=dict(family=PLOT_FONT, size=12),
                              annotations=[dict(text=f"<b>{total}</b><br>bids", showarrow=False,
                                                font=dict(size=17, color=INK))])
            dcol.plotly_chart(fig, width='stretch', config={"displayModeBar": False})

            rows_html = "".join(
                f'<div class="lgd"><i style="background:{OUT_COLOR[o]}">{n}</i>'
                f'<span>{o}</span><b>{n / total * 100:.1f}%</b></div>'
                for o, n in counts)
            lcol.markdown(f'<div class="lgd-wrap">{rows_html}</div>', unsafe_allow_html=True)

        else:
            st.info("No submitted bids recorded.")

    # ---------- monthly activity ----------
    st.markdown('<div class="rowgap"></div>', unsafe_allow_html=True)
    if m["trend"]:
        with st.container(border=True):
            st.markdown(f'<div class="panel-h">{icon("chart", VIOLET, 17)}'
                        f'<span>Monthly activity by bid deadline</span></div>'
                        f'<div class="panel-s">Stacked, so each column is total workload that month '
                        f'and the split shows how it landed. Grouped by bid due date, so open '
                        f'tenders with a future deadline show up ahead of today.</div>',
                        unsafe_allow_html=True)
            labels = [t["label"] for t in m["trend"]]
            totals = [t["Submitted"] + t["Not qualified"] + t["Other"] for t in m["trend"]]
            today_label = date.today().strftime("%b %y")
            fig = go.Figure()
            # "Pipeline" — clearer than "Other" for what this bucket actually is:
            # tenders still under process, cancelled, or empanelled, i.e. no
            # outcome yet. Matches the three states asked for: submitted, not
            # qualified, pipeline.
            for key, colr in [("Submitted", "#1B3A6B"), ("Not qualified", "#5CC2A0"),
                              ("Other", "#8FBFE0")]:
                label = "Pipeline" if key == "Other" else key
                fig.add_trace(go.Bar(
                    x=labels, y=[t[key] for t in m["trend"]], name=label,
                    marker=dict(color=colr, line=dict(color="white", width=1)),
                    hovertemplate="%{x} · %{y} " + label.lower() + "<extra></extra>"))
            # Total sits above each column so the peak months read at a glance.
            fig.add_trace(go.Scatter(
                x=labels, y=totals, mode="text", text=[str(t) for t in totals],
                textposition="top center", textfont=dict(size=12, color="#475467"),
                showlegend=False, hoverinfo="skip"))
            # A dotted line at today separates months already lived through
            # (fixed outcomes) from months still ahead (scheduled deadlines that
            # haven't happened yet) — without this, a lone bar past the current
            # month reads as an error rather than a known upcoming due date.
            if today_label in labels:
                fig.add_shape(type="line", x0=today_label, x1=today_label,
                              y0=0, y1=1, xref="x", yref="paper",
                              line=dict(color="#98A2B3", width=1.4, dash="dot"))
                fig.add_annotation(x=today_label, y=1, xref="x", yref="paper",
                                   yanchor="bottom", showarrow=False, text="Today",
                                   font=dict(size=10.5, color="#98A2B3"))
            fig.update_layout(
                barmode="stack", height=290, margin=dict(l=0, r=0, t=26, b=4),
                plot_bgcolor="white", bargap=.42,
                yaxis=dict(showgrid=True, gridcolor="#F2F4F7", dtick=2, zeroline=False,
                           title=dict(text="Tenders", font=dict(size=11, color="#98A2B3"))),
                xaxis=dict(showgrid=False),
                # Legend reads bottom-up to match the stacking order.
                legend=dict(orientation="h", y=1.16, x=0, font=dict(size=12),
                            traceorder="normal"),
                font=dict(family=PLOT_FONT, size=12, color="#344054"))
            st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})

            peak = max(m["trend"], key=lambda t: t["Submitted"] + t["Not qualified"] + t["Other"])
            future = [t for t in m["trend"] if t["month"] > date.today().strftime("%Y-%m")]
            future_n = sum(t["Submitted"] + t["Not qualified"] + t["Other"] for t in future)
            note = (f" · {future_n} tender(s) due after today are already scheduled and show up "
                   f"ahead of the line." if future_n else "")
            st.caption(f"Busiest month by deadline: **{peak['label']}** with "
                       f"{peak['Submitted'] + peak['Not qualified'] + peak['Other']} tenders due."
                       f"{note}")

    # ---------- owner load & deadlines ----------
    st.markdown('<div class="rowgap"></div>', unsafe_allow_html=True)
    l2, r2 = st.columns(2, gap="medium")

    with l2.container(border=True):
        st.markdown(f'<div class="panel-h">{icon("gear", NAVY, 17)}'
                    f'<span>Account owner load</span></div>'
                    f'<div class="panel-s">Shaded by volume — deeper means more of that column</div>',
                    unsafe_allow_html=True)
        odf = pd.DataFrame(m["owner_rows"]).rename(columns={
            "owner": "Owner", "total": "Total", "submitted": "Bid",
            "won": "Won", "lost": "Lost", "no_bid": "No bid"})
        st.dataframe(
            odf.style
               .background_gradient(cmap=pastel_cmap("#DDE4F2"), subset=["Total"])
               .background_gradient(cmap=pastel_cmap("#E2DFF6"), subset=["Bid"])
               .background_gradient(cmap=pastel_cmap("#D6EFE4"), subset=["Won"])
               .background_gradient(cmap=pastel_cmap("#FBE3DE"), subset=["Lost"])
               .background_gradient(cmap=pastel_cmap("#FDEBD3"), subset=["No bid"])
               .set_properties(**{"color": "#101828"}),
            hide_index=True, width='stretch', height=300)

    with r2.container(border=True):
        st.markdown(f'<div class="panel-h">{icon("calendar", AMBER, 17)}'
                    f'<span>Next bid deadlines</span></div>'
                    f'<div class="panel-s">Nearest first — amber marks the next two weeks</div>',
                    unsafe_allow_html=True)
        if m["upcoming"]:
            today = date.today()
            ddf = pd.DataFrame([{
                "Due": fmt_date(r["submissionDate"]),
                "Days": (datetime.strptime(r["submissionDate"], "%Y-%m-%d").date() - today).days,
                "Organisation": r["organization"][:34],
                "Owner": r["owner"], "Stage": r["category"]} for r in m["upcoming"][:14]])
            st.dataframe(
                ddf.style
                   .apply(highlight_due, axis=1)
                   .format({"Days": "{:d}"}),
                hide_index=True, width='stretch', height=300)
        else:
            st.info("No future deadlines recorded. Add a submission date to any open "
                    "tender and it appears here.")

    st.markdown('<div class="rowgap"></div>', unsafe_allow_html=True)
    st.markdown('<div class="sec">What the numbers say</div>', unsafe_allow_html=True)
    psu = next((r["count"] for r in m["reasons"] if r["reason"] == "PSU-only restriction"), 0)
    msme = next((r["count"] for r in m["reasons"] if r["reason"] == "MSME preference"), 0)
    recoverable = len(m["not_qualified"]) // 3

    st.markdown(f"""<div class="ins" style="--c:{VIOLET}">
      <b>Eligibility is the bottleneck, not competitiveness.</b>
      {len(m['not_qualified'])} of {m['processed']} tenders ({m['block_rate']:.0f}%) were lost before a bid
      was entered, while the win rate on bids actually entered is {m['win_rate']:.0f}%. Converting even a
      third of the blocked ones would add about {recoverable} bids and roughly
      {round(recoverable * m['win_rate'] / 100)} more wins — more than any pricing change would deliver.
    </div>
    <div class="ins" style="--c:{AMBER};background:#FFFDF8">
      <b>Two structural clauses cause most exclusions.</b>
      PSU-only restrictions and MSME preference block {psu + msme} tenders between them. Neither is
      fixable by bidding harder; both need a channel answer — a PSU or MSME entity to bid through —
      which is the same shape of fix as the MKCL tie-up already used for the OSM/LMS gap.
    </div>
    <div class="ins" style="--c:{GREEN if m['on_pace'] else AMBER};background:#FAFDFB">
      <b>The target is {'on pace' if m['on_pace'] else 'behind pace'} — the pending figure misleads.</b>
      "{m['pending']} still to process" sounds alarming, but at the current {m['per_month']:.1f} per month
      the year lands near {m['projected']} against a target of {FY_TARGET}. Report pace, not the remainder.
    </div>
    <div class="ins" style="--c:{TEAL};background:#F8FDFD">
      <b>{inr(m['emd_blocked'])} of working capital sits in unresolved bids.</b>
      EMD is refundable but idle. {len(m['under_eval'])} bids are still in evaluation; every week they
      stay unresolved is a week that money is unavailable. Chase the oldest first — Data health lists them.
    </div>""", unsafe_allow_html=True)

# View-only visitors never reach the code below this line at all — not hidden
# behind a disabled tab, genuinely never executed. Repository, Add/edit tender
# and Import & backups (and everything they can do — editing, deleting,
# resetting the whole dataset) simply don't run for this session.
if st.session_state.get("view_only"):
    st.stop()


# ================================================================ REPOSITORY
with tab_repo:
    st.markdown(f"""
    <div class="band">
      <div>
        <div class="band-t">Tender Repository</div>
        <div class="band-s">Search, filter and open any tender for editing</div>
      </div>
      <div class="band-chip">{icon('layers', VIOLET, 15)}
        <span>{len(rows)} tenders</span></div>
    </div>""", unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown(f'<div class="panel-h">{icon("target", NAVY, 16)}'
                    f'<span>Filters</span></div>', unsafe_allow_html=True)
        f1, f2, f3, f4 = st.columns([3, 1.4, 1.4, 1.2])
        q = f1.text_input("Search", placeholder="🔍  Organisation, city, scope, remarks…",
                          label_visibility="collapsed")
        f_cat = f2.selectbox("Stage", ["All stages"] + CATEGORIES, label_visibility="collapsed")
        f_out = f3.selectbox("Outcome", ["All outcomes"] + OUTCOMES, label_visibility="collapsed")
        f_own = f4.selectbox("Owner", ["All owners"] + owner_options(rows),
                             label_visibility="collapsed")

    view = rows
    if q:
        ql = q.lower()
        view = [r for r in view if ql in " ".join(str(r.get(k, "")) for k in
                ("organization", "city", "details", "remarks", "concerns", "owner")).lower()]
    if f_cat != "All stages":
        view = [r for r in view if r.get("category") == f_cat]
    if f_out != "All outcomes":
        view = [r for r in view if r.get("outcome") == f_out]
    if f_own != "All owners":
        view = [r for r in view if r.get("owner") == f_own]

    if view:
        # Same columns as before — only the presentation changes.
        table = pd.DataFrame([{
            "ID": r["id"], "Organisation": r["organization"], "City": r.get("city", ""),
            "Owner": r.get("owner", ""), "Bid due": fmt_date(r.get("submissionDate", "")),
            "Stage": r.get("category", ""),
            "Outcome": r.get("outcome") if r.get("outcome") != "N/A" else (r.get("noBidReason") or "—"),
            "Value (₹ Cr)": cr(r["contractValue"]) if r.get("contractValue") else "—",
            "EMD": inr(r["emd"]) if r.get("emd") else "—",
            "Notes": (r.get("concerns") or r.get("remarks") or "")[:90],
        } for r in view])

        with st.container(border=True):
            head, dl = st.columns([3, 1])
            head.markdown(
                f'<div class="panel-h">{icon("documents", VIOLET, 16)}'
                f'<span>Results</span></div>'
                f'<div class="panel-s">Showing <b>{len(view)}</b> of {len(rows)} tenders · '
                f'stage and outcome are tinted by status</div>', unsafe_allow_html=True)
            dl.download_button("⬇  Export CSV",
                               table.to_csv(index=False).encode("utf-8"),
                               f"tenders-{date.today().isoformat()}.csv", "text/csv",
                               width='stretch')

            st.dataframe(
                table.style
                     .map(tint_stage, subset=["Stage"])
                     .map(tint_outcome, subset=["Outcome"])
                     .set_properties(subset=["ID"], **{"color": "#667085"})
                     .set_properties(subset=["Organisation"],
                                     **{"font-weight": "700", "color": "#101828"}),
                hide_index=True, width='stretch', height=520,
                column_config={
                    "ID": st.column_config.TextColumn(width="small"),
                    "Organisation": st.column_config.TextColumn(width="medium"),
                    "Notes": st.column_config.TextColumn(width="medium"),
                })

        with st.container(border=True):
            st.markdown(f'<div class="panel-h">{icon("gear", TEAL, 16)}'
                        f'<span>Open a tender</span></div>'
                        f'<div class="panel-s">Pick one, then switch to the '
                        f'<b>Add / edit tender</b> tab</div>', unsafe_allow_html=True)
            pick = st.selectbox("Open a tender to edit", ["—"] +
                                [f"{r['id']} · {r['organization']}" for r in view],
                                label_visibility="collapsed")
            if pick != "—":
                st.session_state.edit_id = pick.split(" · ")[0]
                st.success(f"**{pick}** is loaded — open the **Add / edit tender** tab to change it.")
    else:
        st.info("Nothing matches those filters. Clear them to see the full repository.")


# ================================================================ ADD / EDIT
with tab_edit:
    st.markdown(f"""
    <div class="band">
      <div>
        <div class="band-t">Add / edit tender</div>
        <div class="band-s">Fill in the details to add or update a tender in the repository</div>
      </div>
      <div class="band-chip">{icon('documents', VIOLET, 15)}
        <span>All workbook fields</span></div>
    </div>""", unsafe_allow_html=True)

    ids = ["+ New tender"] + [f"{r['id']} · {r['organization']}" for r in rows]
    preset = 0
    if eid := st.session_state.get("edit_id"):
        preset = next((i for i, s in enumerate(ids) if s.startswith(f"{eid} ·")), 0)

    with st.container(border=True):
        st.markdown(f'<div class="panel-h">{icon("layers", NAVY, 16)}'
                    f'<span>Which tender</span></div>'
                    f'<div class="panel-s">Choose an existing tender to edit, or start a new one</div>',
                    unsafe_allow_html=True)
        chosen = st.selectbox("Tender", ids, index=preset, label_visibility="collapsed")
    is_new = chosen == "+ New tender"
    rec = dict(BLANK) if is_new else next(
        (dict(BLANK) | r for r in rows if r["id"] == chosen.split(" · ")[0]), dict(BLANK))

    def as_date(v):
        try:
            return datetime.strptime(v, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    NEW_OWNER = "➕ Add a new name…"
    owners_now = owner_options(rows)

    with st.form("tender_form", clear_on_submit=False):
        st.markdown(f'<div class="fsec"><span class="fsec-ic" style="background:#6D5BD014">{icon("documents", "#6D5BD0", 17)}</span><div><b>Who and what</b><s>Organisation, owner and scope</s></div></div>', unsafe_allow_html=True)
        a, b, c3 = st.columns([2, 1, 1])
        organization = a.text_input("Organisation *", rec["organization"],
                                    placeholder="e.g. Bihar Technical Service Commission")
        city = b.text_input("City", rec.get("city", ""))
        owner_choice = c3.selectbox(
            "Account owner", owners_now + [NEW_OWNER],
            index=owners_now.index(rec["owner"]) if rec.get("owner") in owners_now
            else owners_now.index("Unassigned"))
        new_owner = c3.text_input("New owner name", "",
                                  placeholder="Only used with ‘Add a new name’")
        details = st.text_area("Tender scope", rec.get("details", ""), height=68)
        d1, d2 = st.columns(2)
        volume_raw = d1.text_input("Candidate volume", rec.get("volumeRaw", ""),
                                   placeholder="10000-20000",
                                   help="As written on the tender — a range, an exact "
                                        "count, or a text description all work.")
        contract = d2.text_input("Contract period", rec.get("contractPeriod", ""), placeholder="2+1")

        st.markdown(f'<div class="fsec"><span class="fsec-ic" style="background:#17868F14">{icon("calendar", "#17868F", 17)}</span><div><b>Dates</b><s>Key milestones for this tender</s></div></div>', unsafe_allow_html=True)
        e1, e2, e3, e4 = st.columns(4)
        pre_bid = e1.date_input("Pre-bid meeting", as_date(rec.get("preBidDate")), format="DD/MM/YYYY")
        query_d = e2.date_input("Pre-bid query cut-off", as_date(rec.get("queryDate")), format="DD/MM/YYYY")
        submit_d = e3.date_input("Bid submission due", as_date(rec.get("submissionDate")), format="DD/MM/YYYY")
        tech_open = e4.date_input("Technical opening", as_date(rec.get("techOpening")), format="DD/MM/YYYY")
        f1b, f2b, f3b = st.columns(3)
        tech_pres = f1b.date_input("Technical presentation", as_date(rec.get("techPresentation")), format="DD/MM/YYYY")
        comm_open = f2b.date_input("Commercial opening", as_date(rec.get("commercialOpening")), format="DD/MM/YYYY")
        venue = f3b.text_input("Venue visit", rec.get("venueVisit", ""))

        st.markdown(f'<div class="fsec"><span class="fsec-ic" style="background:#C97A1814">{icon("rupee", "#C97A18", 17)}</span><div><b>Commercials</b><s>Fees, EMD and contract value</s></div></div>', unsafe_allow_html=True)
        g1, g2, g3, g4 = st.columns(4)
        evaluation = g1.text_input("Evaluation method", rec.get("evaluation", ""), placeholder="QCBS 70:30 / L1")
        msme = g2.text_input("MSME preference", rec.get("msmePref", ""))
        fee = g3.number_input("Tender fee (₹)", value=float(rec["tenderFees"] or 0), min_value=0.0, step=500.0)
        emd = g4.number_input("EMD (₹)", value=float(rec["emd"] or 0), min_value=0.0, step=10000.0)
        v1, v2 = st.columns([1, 3])
        contract_value = v1.number_input("Contract value (₹ Cr)",
                                         value=float(rec["contractValue"] or 0),
                                         min_value=0.0, step=0.05, format="%.2f",
                                         help="Order value if won, or the estimate while bidding, "
                                              "in ₹ crore (e.g. 90 lakh = 0.90). This drives the "
                                              "Contract value figure — only Won and Won - PO "
                                              "Awaited tenders are summed there.")
        v2.caption(f"Entered in ₹ crore. {cr(contract_value) if contract_value else '—'}")

        st.markdown(f'<div class="fsec"><span class="fsec-ic" style="background:#0E9F6E14">{icon("flag", "#0E9F6E", 17)}</span><div><b>Status</b><s>Stage, outcome and sign-off</s></div></div>', unsafe_allow_html=True)
        h1, h2, h3 = st.columns(3)
        category = h1.selectbox("Stage", CATEGORIES,
                                index=CATEGORIES.index(rec["category"]) if rec.get("category") in CATEGORIES else 2)
        outcome = h2.selectbox("Outcome (used when stage is Submitted)", OUTCOMES,
                               index=OUTCOMES.index(rec["outcome"]) if rec.get("outcome") in OUTCOMES else len(OUTCOMES) - 1)
        NEW_REASON = "➕ Add a reason…"
        reasons_now = reason_options(rows)
        no_bid_choice = h3.selectbox(
            "Reason we could not bid (used when stage is Not Qualified)",
            reasons_now + [NEW_REASON],
            index=reasons_now.index(rec["noBidReason"]) if rec.get("noBidReason") in reasons_now
            else reasons_now.index("Not categorised"))
        new_reason = h3.text_input("New reason", "",
                                   placeholder="Only used with ‘Add a reason’")
        i1, i2, i3 = st.columns(3)
        po = i1.text_input("PO received", rec.get("poReceived", ""))
        agreement = i2.selectbox("Agreement signed", ["", "Y", "N"],
                                 index=["", "Y", "N"].index(rec["agreementSigned"]) if rec.get("agreementSigned") in ("", "Y", "N") else 0)
        pbg = i3.number_input("PBG (fraction — 0.05 is 5%)", value=float(rec["pbg"] or 0),
                              min_value=0.0, max_value=1.0, step=0.01, format="%.2f")
        remarks = st.text_area("Remarks", rec.get("remarks", ""), height=68)
        concerns = st.text_area("Tender concerns", rec.get("concerns", ""), height=68,
                                placeholder="Who else bid, evaluation notes, eligibility blockers")

        st.markdown(f'<div class="fsec"><span class="fsec-ic" style="background:#1B3A6B14">{icon("gear", "#1B3A6B", 17)}</span><div><b>Reviewer remarks</b><s>Internal comments from each reviewer</s></div></div>', unsafe_allow_html=True)
        j1, j2, j3 = st.columns(3)
        rajan = j1.text_area("Rajan remarks", rec.get("rajanRemark", ""), height=68)
        chintan = j2.text_area("Chintan remark", rec.get("chintanRemark", ""), height=68)
        paresh = j3.text_area("Paresh Remark", rec.get("pareshRemark", ""), height=68)

        saved = st.form_submit_button("Save changes" if not is_new else "Add tender",
                                      type="primary")

    if saved:
        owner = new_owner.strip() if owner_choice == NEW_OWNER else owner_choice
        no_bid = new_reason.strip() if no_bid_choice == NEW_REASON else no_bid_choice
        if not organization.strip():
            st.error("Add an organisation name before saving.")
        elif owner_choice == NEW_OWNER and not owner:
            st.error("Type the new owner's name, or pick an existing one from the list.")
        elif no_bid_choice == NEW_REASON and not no_bid:
            st.error("Type the new reason, or pick an existing one from the list.")
        else:
            iso = lambda d: d.strftime("%Y-%m-%d") if d else ""   # noqa: E731
            payload = {
                **rec,
                "organization": organization.strip(), "city": city.strip(), "owner": owner,
                "details": details.strip(), "volumeRaw": volume_raw.strip(),
                # Same parser the importer uses on the source workbook, so a
                # manually-added tender ends up with a consistent 'volume' to
                # whatever imported ones have, without asking for it twice.
                "volume": parse_volume(volume_raw.strip()), "contractPeriod": contract.strip(),
                "preBidDate": iso(pre_bid), "queryDate": iso(query_d),
                "submissionDate": iso(submit_d), "techOpening": iso(tech_open),
                "techPresentation": iso(tech_pres), "commercialOpening": iso(comm_open),
                "venueVisit": venue.strip(), "evaluation": evaluation.strip(),
                "msmePref": msme.strip(), "tenderFees": fee or None, "emd": emd or None,
                "category": category,
                # Outcome and reason only apply to their own stage — keep the data coherent.
                "outcome": outcome if category == "Submitted" else "N/A",
                "noBidReason": no_bid if category == "Not Qualified" else "",
                "poReceived": po.strip(), "agreementSigned": agreement,
                "pbg": pbg or None, "contractValue": contract_value or None,
                "remarks": remarks.strip(), "concerns": concerns.strip(),
                "rajanRemark": rajan.strip(), "chintanRemark": chintan.strip(),
                "pareshRemark": paresh.strip(),
                "updatedAt": datetime.now().isoformat(timespec="seconds"),
            }
            if is_new:
                payload["id"] = store.next_id(rows)
                payload["srNo"] = len(rows) + 1
                payload["createdAt"] = payload["updatedAt"]
                commit(rows + [payload], "add", f"Added {payload['organization']}.")
            else:
                commit([payload if r["id"] == payload["id"] else r for r in rows],
                       "edit", f"Updated {payload['organization']}.")
            st.session_state.pop("edit_id", None)
            st.rerun()

    if not is_new:
        with st.expander("Delete this tender"):
            st.caption("Removes it from the repository. The previous version stays in "
                       "data/backups/ and can be restored from the Import & backups tab.")
            if st.button("Delete permanently", type="secondary"):
                commit([r for r in rows if r["id"] != rec["id"]], "delete",
                       f"Deleted {rec['organization']}.")
                st.session_state.pop("edit_id", None)
                st.rerun()


# --- DATA HEALTH TAB (hidden) ------------------------------------------
# Re-enable by restoring tab_health above and removing the '# ' prefixes.
# # ================================================================ DATA HEALTH
# with tab_health:
#     st.markdown('<div class="sec">Reconciliation against the FY27 review slide</div>',
#                 unsafe_allow_html=True)
#     st.markdown(f"""
#     <div class="flag" data-t="ok"><b>The slide's three buckets tie exactly.</b>
#       {len(m['submitted'])} qualified &amp; submitted, {len(m['not_qualified'])} not qualified,
#       {len(m['under_process']) + len(m['cancelled']) + len(m['empanelment'])} pipeline —
#       {m['processed']} processed. Every figure here is recomputed from the records, so it stays
#       tied as you edit.</div>
#     <div class="flag" data-t="hi"><b>The slide contradicts itself on the headline.</b>
#       It reports "48 tenders processed since 1st April 26" in one box and 58 in another. The
#       workbook supports 58. Correct the 48 before this circulates again.</div>
#     <div class="flag"><b>The win count depends on a definition.</b>
#       {len(m['won']) - 1} tenders are marked "Won the bid" and TDB is "Awaiting PO", which the slide
#       counts as a sixth win. It is held here as <i>Won - PO Awaited</i> so both readings stay
#       visible instead of being quietly merged.</div>
#     <div class="flag"><b>EDCIL's empanelment counts as pipeline, not a win</b> — matching the slide,
#       though its remark says "Got the Tender". Decide which it is; it moves the win rate.</div>
#     <div class="flag" data-t="hi"><b>Won/Lost was never a field.</b>
#       It existed only inside free-text remarks, so no formula could ever count it. It is a real
#       field now — keep it filled and the win rate maintains itself.</div>
#     """, unsafe_allow_html=True)
#
#     st.markdown(f'<div class="sec">Open data issues — {len(m["issues"])} across '
#                 f'{len({i["id"] for i in m["issues"]})} tenders</div>', unsafe_allow_html=True)
#     if m["issues"]:
#         idf = pd.DataFrame(m["issues"])
#         for issue, grp in sorted(idf.groupby("issue"), key=lambda kv: -len(kv[1])):
#             sev = grp["severity"].iloc[0]
#             with st.expander(f"{issue}  ·  {len(grp)}", expanded=(sev == "high")):
#                 st.write(", ".join(grp["org"].tolist()))
#     else:
#         st.success("No outstanding data issues.")
#
#     if m["repeats"]:
#         st.markdown('<div class="sec">Repeat organisations</div>', unsafe_allow_html=True)
#         st.caption("Repeats are normal — the same body floats several tenders a year. Listed so "
#                    "you can confirm none is an accidental duplicate.")
#         st.write(" · ".join(f"**{o}** ×{n}" for o, n in m["repeats"]))
#
#     st.markdown('<div class="sec">What to fix in the source workbook</div>', unsafe_allow_html=True)
#     st.markdown("""
#     <div class="flag"><b>Dates were text, not dates.</b> "16th Feb 2026" cannot be sorted, filtered
#       or aged. They were converted on import; use the date pickers from now on and every deadline
#       view stays live.</div>
#     <div class="flag"><b>Status had eight spellings for six states</b> ("Submitted"/"submitted",
#       "not submitted"/"did not submit"). It is a fixed dropdown here, so counts can no longer drift.</div>
#     <div class="flag"><b>Account owner had thirteen spellings for eight people</b> — "Prashant",
#       "prashant", "Prashant Sharma", "Prashanr". Merged on import; new names can still be added
#       from the form.</div>
#     <div class="flag" data-t="hi"><b>There is no contract value field anywhere in the workbook.</b>
#       This is the most important gap. Without it you can report counts but never pipeline value,
#       average deal size, or a value-weighted win rate — a ₹5 crore loss and a ₹5 lakh loss look
#       identical today. Add estimated contract value as a required field.</div>
#     <div class="flag"><b>One tender fee was recorded as ₹80,00,000</b>, which is not a fee. It was
#       left blank rather than allowed to distort totals — re-enter it from the tender document.</div>
#     <div class="flag"><b>PO received and Agreement signed are empty on every row</b>, including the
#       wins. The funnel therefore stops at "won" and cannot show conversion into revenue.</div>
#     """, unsafe_allow_html=True)

# ================================================================ IMPORT & BACKUPS
with tab_data:
    st.markdown(f"""
    <div class="band">
      <div>
        <div class="band-t">Backups &amp; data</div>
        <div class="band-s">Manage, back up and restore your tender repository</div>
      </div>
      <div class="band-chip">{icon('layers', TEAL, 15)}
        <span>Stored locally</span></div>
    </div>""", unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown(f'<div class="panel-h">{icon("documents", VIOLET, 16)}'
                    f'<span>Where your data lives</span></div>', unsafe_allow_html=True)
        st.code(f"{store.DATA_DIR}", language=None)
        cc = st.columns(3)
        for col, (ic, label, value, colour) in zip(cc, [
                ("layers", "Tenders stored", f"{len(rows)} records", VIOLET),
                ("calendar", "Last saved", meta.get("lastSaved", "—").replace("T", " "), TEAL),
                ("gear", "Last action", meta.get("lastAction", "—"), AMBER)]):
            safe_value = html.escape(str(value))
            col.markdown(
                f'<div class="strip">'
                f'  <span class="strip-ic" style="background:{colour}14">{icon(ic, colour, 18)}</span>'
                f'  <div><u>{label}</u><b style="font-size:16px" title="{safe_value}">{safe_value}</b></div>'
                f'</div>', unsafe_allow_html=True)
        st.caption("Records are written to data/tenders.json on every change, with the previous "
                   "version copied into data/backups/. Nothing is uploaded anywhere.")

    with st.container(border=True):
        st.markdown(f'<div class="panel-h">{icon("send", NAVY, 16)}'
                    f'<span>Import another workbook</span></div>'
                    f'<div class="panel-s">Must contain a sheet named ‘Tenders’</div>',
                    unsafe_allow_html=True)
        import_panel(first_run=False)

    export_box = st.container(border=True)
    export_box.markdown(f'<div class="panel-h">{icon("chart", GREEN, 16)}'
                        f'<span>Export</span></div>'
                        f'<div class="panel-s">Excel keeps the source workbook\'s sheet name and '
                        f'column order</div>', unsafe_allow_html=True)
    with export_box:
        full = pd.DataFrame(rows)
        x1, x2 = st.columns(2)
        x1.download_button(f"⬇  Download CSV — all {len(rows)} tenders",
                           full.to_csv(index=False).encode("utf-8"),
                           f"tender-repository-{date.today().isoformat()}.csv", "text/csv",
                           width='stretch')
        x2.download_button("⬇  Download Excel — source layout", build_workbook(rows),
                           f"tender-repository-{date.today().isoformat()}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           width='stretch')
        st.caption("Two columns are appended — Outcome and Reason not qualified — because neither "
                   "exists in the original layout and neither can be recovered from it.")

    restore_box = st.container(border=True)
    restore_box.markdown(f'<div class="panel-h">{icon("calendar", AMBER, 16)}'
                         f'<span>Restore a previous version</span></div>'
                         f'<div class="panel-s">Current data will be replaced</div>',
                         unsafe_allow_html=True)
    with restore_box:
        backups = store.list_backups()
        if backups:
            b1, b2 = st.columns([3, 1])
            label = b1.selectbox("Saved versions",
                                 [f"{when}  ·  {count} tenders  ·  {name}"
                                  for name, when, count in backups],
                                 label_visibility="collapsed")
            if b2.button("↻  Restore", width='stretch'):
                fname = label.split("  ·  ")[-1]
                st.session_state.rows = store.restore_backup(fname)
                st.session_state.flash = f"Restored the version from {label.split('  ·  ')[0]}."
                st.rerun()
        else:
            st.caption("No previous versions yet. One is kept automatically each time you save.")

    reset_box = st.container(border=True)
    reset_box.markdown(f'<div class="panel-h">{icon("hourglass", "#D03A3A", 16)}'
                       f'<span>Reset the repository</span></div>'
                       f'<div class="panel-s">Clears every tender and returns the app to the '
                       f'upload screen</div>', unsafe_allow_html=True)
    with reset_box:

        if not st.session_state.get("confirm_reset"):
            if st.button("Reset repository", type="secondary"):
                st.session_state.confirm_reset = True
                st.rerun()
        else:
            st.error(f"This deletes all {len(rows)} tenders from data/tenders.json. "
                     f"You will need to upload a workbook again before the dashboard returns.")
            st.caption("A timestamped copy is still written to data/backups/ first, so this is "
                       "recoverable from disk if it was a mistake.")
            typed = st.text_input("Type RESET to confirm", key="reset_confirm_text",
                                  placeholder="RESET")
            r1, r2 = st.columns([1, 4])
            if r1.button("Confirm reset", type="primary", disabled=typed.strip().upper() != "RESET"):
                store.save_tenders([], "reset")          # keeps a backup of what was there
                st.session_state.rows = []
                st.session_state.confirm_reset = False
                st.session_state.pop("drill", None)
                st.session_state.pop("edit_id", None)
                st.session_state.flash = "Repository cleared. Upload a workbook to start again."
                st.rerun()
            if r2.button("Cancel"):
                st.session_state.confirm_reset = False
                st.rerun()