"""Online DARE-Arbo Assessor and Appraiser.

Run with:
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import base64
from datetime import date, datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any

import altair as alt
import pandas as pd
from PIL import Image
import streamlit as st

EXPECTED_CORE_API_VERSION = "2026.09.11.1"
try:
    from dare_arbo import CORE_API_VERSION
except ImportError:
    st.error(
        "DARE-Arbo deployment files are out of sync. The deployed streamlit_app.py "
        "requires the matching updated dare_arbo.py. Deploy both files from the same "
        "revision, then reboot the Streamlit app."
    )
    st.stop()

if CORE_API_VERSION != EXPECTED_CORE_API_VERSION:
    st.error(
        "DARE-Arbo deployment version mismatch: "
        f"interface expects core {EXPECTED_CORE_API_VERSION}, but loaded {CORE_API_VERSION}. "
        "Deploy streamlit_app.py and dare_arbo.py together, then reboot the app."
    )
    st.stop()

from dare_arbo import (
    ASSAY_OPTIONS_BY_SYNTHESIS_PATH,
    CRITERIA,
    DOMAINS,
    ENDPOINTS,
    ENDPOINTS_BY_KEY,
    GUIDE_VERSION,
    NUMERATOR_PROVENANCE,
    OVERVIEW_COLOR_PRESETS,
    POPULATION_TARGET_CATEGORIES,
    Q4_USER_METHOD_SCORES,
    SAMPLING_FRAME_CATEGORIES,
    SYNTHESIS_PATHS_BY_ENDPOINT,
    SYNTHESIS_PATHS_BY_KEY,
    VERIFICATION_DESIGNS,
    applicable_maximum,
    audit_endpoint_counts,
    audit_synthesis_path_counts,
    assess_target_frame_alignment,
    assay_path_profile,
    build_designer_report_model,
    build_export_record,
    classify_synthesis,
    criterion_is_applicable,
    extract_citation_or_doi,
    extract_pdf_text,
    evaluate_study_design_plan,
    record_to_json,
    render_assessment_png,
    render_study_design_png,
    render_surveillance_design_report_pdf,
    normalize_target_population_category,
    normalize_sampling_frame_category,
    score_assessment,
    suggest_scores,
)


APP_DIR = Path(__file__).resolve().parent
LOGO_PATH = APP_DIR / "Logo.jpeg"
SYNERGY_LOGO_PATH = APP_DIR / "Synergy_NGS2025.png"
DARE_BANNER_PATH = APP_DIR / "DARE_Arbo_brand_banner.png"
PATHWAY_LABELS = {
    "serologic": "Serologic measurement pathway (Q10 applicability is endpoint-specific)",
    "direct_detection": "Direct/reference-standard measurement pathway (Q10 = N/A)",
}
PATHWAYS_BY_LABEL = {label: key for key, label in PATHWAY_LABELS.items()}
ENDPOINT_LABELS = {endpoint.label: endpoint.key for endpoint in ENDPOINTS}
SUMMARY_ESTIMATORS = (
    "Apparent prevalence (observed positives / tested)",
    "Sensitivity/specificity-adjusted prevalence",
    "Tier B algorithm-confirmed prevalence",
    "Tier C screen-conditioned estimate",
    "Tier C representative-subset weighted estimate",
    "Two-phase adjusted estimate",
    "Assay performance only (not prevalence)",
    "Other / unresolved",
)
SYNTHESIS_ENDPOINT_ORDER = (
    "prior_exposure",
    "neutralizing_antibody",
    "presumptive_igm",
    "presumptive_ns1",
    "mixed_serology",
    "confirmed_active",
    "assay_performance",
)
TARGET_POPULATION_CATEGORY_OPTIONS = [label for label, _ in POPULATION_TARGET_CATEGORIES]
SAMPLING_FRAME_CATEGORY_OPTIONS = [label for label, _ in SAMPLING_FRAME_CATEGORIES]
ACHIEVED_REPRESENTATION_OPTIONS = (
    "Not reported / unclear",
    "Adequate within the defined target population",
    "Partial or poor representation within the defined target population",
)
ENDPOINT_CONSISTENCY_OPTIONS = (
    "Not reported / unclear",
    "Same primary endpoint-defining procedure for all eligible samples",
    "Predefined staged algorithm completed consistently for all eligible at each step",
    "Materially inconsistent or selective endpoint-defining testing",
)
DESIGNER_ARBOVIRUS_OPTIONS = (
    "Dengue virus (DENV)",
    "Chikungunya virus (CHIKV)",
    "Zika virus (ZIKV)",
    "Yellow fever virus (YFV)",
    "West Nile virus (WNV)",
    "Japanese encephalitis virus (JEV)",
    "Rift Valley fever virus (RVFV)",
    "O'nyong-nyong virus (ONNV)",
    "Mayaro virus (MAYV)",
    "Crimean-Congo haemorrhagic fever virus (CCHFV)",
)
PATH_COUNT_FIELDS = {
    "total_recruited_n": (
        "Total recruited",
        "All eligible participants/specimens recruited or enrolled for this study-virus-estimand before endpoint testing. Q5 first calculates Total tested / Total recruited.",
    ),
    "total_tested_n": (
        "Total tested",
        "All recruited participants/specimens that completed the primary or universal endpoint-defining assay. Q5 compares this with Total recruited.",
    ),
    "molecular_positive_n": (
        "Molecular positive",
        "Observed PCR/NAAT, virus-isolation, or sequencing-supported positives. In Tier B/C, count positives among those retested.",
    ),
    "igm_positive_n": ("IgM positive", "Observed IgM-positive results among Total tested."),
    "ns1_positive_n": ("NS1 positive", "Observed NS1-antigen-positive results among Total tested."),
    "mixed_serology_positive_n": (
        "Combined serologic positive",
        "Observed inseparable combined serologic positives among Total tested. Do not relabel this count as IgM- or IgG-specific.",
    ),
    "primary_positive_n": (
        "Primary positive",
        "Observed positives on the primary screening assay. For Tier B, all of these should be retested.",
    ),
    "neutralization_positive_n": (
        "Neutralization positive",
        "Observed PRNT/cVNT/pVNT/MN or other neutralization-positive results among Total tested.",
    ),
    "number_retested_n": (
        "Number retested",
        "Actual number receiving verification. It is the observed verification-subset denominator. Tier B population prevalence instead uses Total tested; Tier C also requires Total tested and Screen-positive N for the weighted/screen-conditioned estimator.",
    ),
    "confirmed_positive_n": (
        "Confirmed positive",
        "Observed confirmatory-positive results among Number retested.",
    ),
    "retested_positive_stratum_n": (
        "Retested primary/screening-positive N",
        "Randomly selected primary/screening-positive samples included in the validation dataset.",
    ),
    "retested_negative_stratum_n": (
        "Retested primary/screening-negative N",
        "Randomly selected primary/screening-negative samples included in the validation dataset.",
    ),
    "confirmed_negative_n": (
        "Confirmed negative",
        "Confirmatory-negative results within the retested primary/screening-negative stratum.",
    ),
    "planned_retest_n": (
        "Planned number to retest (optional Q5 field)",
        "Protocol-planned verification/subset size. Q5 compares the actual Number retested with this planned number. It is not the estimator denominator.",
    ),
}
SYNTHESIS_PATH_LABELS = {
    f"{ENDPOINTS_BY_KEY[path.endpoint_key].label} — {path.testing_strategy}": path.key
    for path in SYNTHESIS_PATHS_BY_KEY.values()
}
SYNTHESIS_PATH_LABELS_BY_KEY = {key: label for label, key in SYNTHESIS_PATH_LABELS.items()}


@st.cache_data(show_spinner=False)
def prepared_square_logo(logo_bytes: bytes) -> bytes:
    """Trim transparent padding and return a centered square PNG."""
    image = Image.open(BytesIO(logo_bytes)).convert("RGBA")
    alpha_bounds = image.getchannel("A").getbbox()
    if alpha_bounds:
        image = image.crop(alpha_bounds)
    side = max(image.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(image, ((side - image.width) // 2, (side - image.height) // 2), image)
    output = BytesIO()
    square.save(output, format="PNG", optimize=True)
    return output.getvalue()


@st.cache_data(show_spinner=False)
def png_data_uri(image_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")


def configure_page() -> None:
    st.set_page_config(
        page_title="DARE-Arbo | Arboviral Risk-of-Bias Tools",
        page_icon="🦟",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        :root {
          color-scheme: light;
          --dare-navy-950: #071C35;
          --dare-navy-900: #0B2947;
          --dare-navy-800: #123B5D;
          --dare-teal-700: #0F766E;
          --dare-teal-800: #115E59;
          --dare-blue-600: #2563EB;
          --dare-orange-700: #C2410C;
          --dare-purple-700: #6D3A91;
          --dare-ink: #102A43;
          --dare-muted: #52606D;
          --dare-border: #CBD5E1;
          --dare-border-strong: #94A3B8;
          --dare-surface: #FFFFFF;
          --dare-surface-alt: #F6F9FB;
          --dare-teal-pale: #ECFDF5;
          --dare-orange-pale: #FFF7ED;
          --dare-focus: rgba(37, 99, 235, .24);
          --dare-shadow: 0 10px 30px rgba(7, 28, 53, .09);
          --dare-radius: 14px;
          --dare-page-bg: #F4F7F5;
        }
        html, body, .stApp {
          font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }
        [data-testid="stIconMaterial"] {
          font-family: "Material Symbols Rounded", "Material Icons" !important;
          font-feature-settings: "liga" !important;
          -webkit-font-feature-settings: "liga" !important;
        }
        html { font-size: 16px; }
        .stApp {
          background: var(--dare-page-bg);
          color: var(--dare-ink);
        }
        [data-testid="stAppViewContainer"] > .main {
          background: transparent;
        }
        [data-testid="stMainBlockContainer"] {
          position: relative;
          width: min(100%, 1480px);
          min-height: 100vh;
          margin-inline: auto;
          padding: 1.35rem clamp(1rem, 2.5vw, 2.5rem) 3rem;
          background: rgba(255, 255, 255, .96);
          border-inline: 1px solid rgba(203, 213, 225, .72);
          box-shadow: var(--dare-shadow);
        }
        [data-testid="stMainBlockContainer"] p,
        [data-testid="stMainBlockContainer"] li,
        [data-testid="stMainBlockContainer"] label {
          color: var(--dare-ink);
          line-height: 1.55;
        }
        [data-testid="stMainBlockContainer"] h1 {
          color: var(--dare-navy-950);
          font-size: clamp(1.85rem, 2.6vw, 2.65rem);
          line-height: 1.14;
          letter-spacing: -.025em;
        }
        [data-testid="stMainBlockContainer"] h2 {
          color: var(--dare-navy-900);
          font-size: clamp(1.45rem, 2vw, 2rem);
          line-height: 1.2;
        }
        [data-testid="stMainBlockContainer"] h3 {
          color: var(--dare-navy-800);
          font-size: clamp(1.18rem, 1.45vw, 1.42rem);
          line-height: 1.3;
          margin-top: 1.6rem;
          padding-bottom: .42rem;
          border-bottom: 1px solid #E2E8F0;
        }
        [data-testid="stSidebar"] {
          background: linear-gradient(180deg, var(--dare-navy-950), #091F36 70%, #06182C);
          border-right: 1px solid rgba(255, 255, 255, .1);
        }
        [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
          padding-top: 1rem;
        }
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] small { color: #CBD5E1; }
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] strong { color: #FFFFFF; }
        [data-testid="stSidebar"] .stRadio label {
          min-height: 44px;
          padding: .58rem .72rem;
          border-radius: 10px;
          transition: background-color .16s ease, box-shadow .16s ease;
        }
        [data-testid="stSidebar"] .stRadio label:hover {
          background: rgba(15, 118, 110, .28);
        }
        [data-testid="stSidebar"] input[type="radio"] { accent-color: #2DD4BF; }
        [data-testid="stSidebar"] label:has(input[type="radio"]:checked) {
          background: rgba(15, 118, 110, .38);
          box-shadow: inset 4px 0 0 #5EEAD4;
        }
        [data-testid="stSidebar"] label:has(input[type="radio"]:checked) p {
          color: #FFFFFF;
          font-weight: 750;
        }
        [data-testid="stSidebar"] [data-testid="stExpander"] {
          background: rgba(255,255,255,.07);
          border-color: rgba(255,255,255,.16);
        }
        .dare-hero {
          position: relative;
          overflow: hidden;
          background: var(--dare-surface);
          border: 1px solid var(--dare-border);
          border-radius: 16px;
          padding: clamp(.65rem, 1.2vw, 1rem);
          margin: 0 0 1.5rem;
          box-shadow: 0 8px 24px rgba(7, 28, 53, .1);
        }
        .dare-hero::after {
          content: "";
          position: absolute;
          inset: auto 0 0;
          height: 5px;
          background: linear-gradient(90deg, #E45B16 0 24%, #18864B 24% 51%, #6A3A91 51% 74%, #123B5D 74% 100%);
        }
        .dare-hero img {
          display: block;
          width: 100%;
          max-width: 1098px;
          height: auto;
          margin: 0 auto;
          object-fit: contain;
        }
        .dare-mobile-brand { display: none; }
        .dare-card {
          border: 1px solid var(--dare-border);
          border-radius: var(--dare-radius);
          background: var(--dare-surface);
          padding: clamp(1rem, 1.7vw, 1.4rem);
          margin: .65rem 0 1.15rem;
          box-shadow: 0 6px 18px rgba(7, 28, 53, .07);
        }
        .dare-chip {
          display: inline-flex;
          align-items: center;
          min-height: 30px;
          border-radius: 999px;
          padding: .3rem .65rem;
          margin: .12rem .35rem .2rem 0;
          background: var(--dare-teal-pale);
          color: var(--dare-teal-800);
          border: 1px solid #A7D8CB;
          font-size: .8rem;
          font-weight: 750;
        }
        .evidence {
          border-left: 4px solid var(--dare-orange-700);
          padding: .7rem .9rem;
          margin: .65rem 0;
          background: var(--dare-orange-pale);
          border-radius: 0 10px 10px 0;
          color: var(--dare-ink);
        }
        .muted { color: var(--dare-muted); }
        div[data-testid="stMetric"] {
          position: relative;
          min-height: 104px;
          overflow: hidden;
          background: var(--dare-surface);
          border: 1px solid var(--dare-border);
          padding: .85rem 1rem;
          border-radius: var(--dare-radius);
          box-shadow: 0 5px 16px rgba(7, 28, 53, .06);
        }
        div[data-testid="stMetric"]::before {
          content: "";
          position: absolute;
          inset: 0 auto 0 0;
          width: 4px;
          background: var(--dare-teal-700);
        }
        [data-testid="stMetricLabel"] p {
          color: var(--dare-muted);
          font-weight: 650;
        }
        [data-testid="stMetricValue"] {
          color: var(--dare-navy-950);
          font-size: clamp(1.45rem, 2vw, 2rem);
          font-weight: 800;
        }
        .stButton > button,
        .stDownloadButton > button,
        [data-testid="stFormSubmitButton"] > button {
          min-height: 42px;
          border: 1px solid var(--dare-navy-800);
          border-radius: 10px;
          background: #FFFFFF;
          color: var(--dare-navy-900);
          font-weight: 700;
          line-height: 1.2;
          transition: background-color .15s ease, color .15s ease, box-shadow .15s ease, transform .15s ease;
        }
        .stButton > button:hover,
        .stDownloadButton > button:hover,
        [data-testid="stFormSubmitButton"] > button:hover {
          transform: translateY(-1px);
          background: #F0FDFA;
          border-color: var(--dare-teal-700);
          color: var(--dare-teal-800);
          box-shadow: 0 6px 16px rgba(15, 118, 110, .16);
        }
        .stButton > button:focus-visible,
        .stDownloadButton > button:focus-visible,
        [data-testid="stFormSubmitButton"] > button:focus-visible {
          outline: 3px solid var(--dare-focus);
          outline-offset: 2px;
        }
        .stButton > button[kind="primary"],
        [data-testid="stFormSubmitButton"] > button[kind="primary"] {
          background: var(--dare-teal-700);
          border-color: var(--dare-teal-700);
          color: #FFFFFF;
          box-shadow: 0 7px 18px rgba(15, 118, 110, .2);
        }
        .stButton > button[kind="primary"]:hover,
        [data-testid="stFormSubmitButton"] > button[kind="primary"]:hover {
          background: var(--dare-teal-800);
          border-color: var(--dare-teal-800);
          color: #FFFFFF;
        }
        button:disabled,
        .stDownloadButton > button:disabled {
          opacity: .62;
          cursor: not-allowed;
          transform: none;
          box-shadow: none;
        }
        [data-testid="stFileUploaderDropzone"] {
          min-height: 132px;
          background: var(--dare-surface-alt);
          border: 1.5px dashed var(--dare-border-strong);
          border-radius: var(--dare-radius);
        }
        [data-testid="stFileUploaderDropzone"]:hover {
          background: #F0FDFA;
          border-color: var(--dare-teal-700);
        }
        [data-baseweb="tab-list"] {
          gap: .3rem;
          overflow-x: auto;
          scrollbar-width: thin;
          background: #EEF2F6;
          padding: .32rem;
          border-radius: 11px;
        }
        [data-baseweb="tab"] {
          flex: 0 0 auto;
          min-height: 42px;
          border-radius: 8px;
          padding: .5rem .8rem;
          color: var(--dare-muted);
        }
        [data-baseweb="tab"][aria-selected="true"] {
          background: #FFFFFF;
          color: var(--dare-teal-800);
          box-shadow: inset 0 -3px 0 var(--dare-teal-700), 0 2px 8px rgba(7, 28, 53, .08);
        }
        [data-testid="stExpander"] {
          overflow: hidden;
          border: 1px solid var(--dare-border);
          border-radius: 11px;
          background: var(--dare-surface);
        }
        [data-testid="stExpander"] summary:hover {
          background: var(--dare-surface-alt);
        }
        [data-baseweb="input"] > div,
        [data-baseweb="textarea"] > div,
        [data-baseweb="select"] > div,
        [data-baseweb="base-input"] {
          min-height: 42px;
          background: #FFFFFF !important;
          border-color: var(--dare-border-strong) !important;
          border-width: 1px !important;
          border-radius: 9px !important;
          color: var(--dare-ink) !important;
        }
        [data-baseweb="input"] input,
        [data-baseweb="textarea"] textarea,
        [data-baseweb="select"] input {
          color: var(--dare-ink) !important;
          caret-color: var(--dare-teal-700);
        }
        [data-baseweb="input"] > div:focus-within,
        [data-baseweb="textarea"] > div:focus-within,
        [data-baseweb="select"] > div:focus-within {
          border-color: var(--dare-blue-600) !important;
          border-width: 2px !important;
          box-shadow: 0 0 0 3px var(--dare-focus) !important;
        }
        [role="listbox"], [data-baseweb="popover"] {
          color: var(--dare-ink);
        }
        [role="option"] { min-height: 40px; }
        [data-testid="stAlert"] {
          border: 1px solid var(--dare-border);
          border-left-width: 5px;
          border-radius: 10px;
          background: #F8FAFC;
          color: var(--dare-ink);
        }
        [data-testid="stDataFrame"],
        [data-testid="stTable"] {
          overflow: hidden;
          border: 1px solid var(--dare-border);
          border-radius: 10px;
          background: #FFFFFF;
        }
        [data-testid="stImage"] img {
          max-width: 100%;
          height: auto;
          border-radius: 10px;
        }
        [data-testid="stProgress"] > div > div > div {
          background-color: var(--dare-teal-700);
        }
        hr { border-color: var(--dare-border); }
        a { color: #1D4ED8; text-underline-offset: 2px; }
        a:hover { color: #1E40AF; }
        @media (max-width: 980px) {
          [data-testid="stSidebar"] {
            min-width: 250px !important;
            max-width: 250px !important;
          }
          [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
            width: 250px !important;
          }
          [data-testid="stMainBlockContainer"] {
            padding: 1rem 1rem 2.5rem;
            border-inline: 0;
          }
          div[data-testid="stMetric"] { min-height: 94px; }
        }
        @media (max-width: 700px) {
          html { font-size: 15px; }
          [data-testid="stMainBlockContainer"] { padding: .8rem .75rem 2rem; }
          .dare-hero { padding: 0; border-radius: 12px; }
          .dare-hero > img { display: none; }
          .dare-mobile-brand {
            display: block;
            padding: 1.05rem 1rem 1.15rem;
            background: linear-gradient(135deg, var(--dare-navy-950), var(--dare-navy-800));
          }
          .dare-mobile-brand strong {
            display: block;
            color: #FFFFFF;
            font-size: clamp(1.65rem, 8vw, 2.15rem);
            line-height: 1.05;
            letter-spacing: -.025em;
          }
          .dare-mobile-brand span {
            display: block;
            margin-top: .45rem;
            color: #E2E8F0;
            font-size: .88rem;
            line-height: 1.4;
          }
          [data-baseweb="tab-list"] { border-radius: 9px; }
          .stButton > button, .stDownloadButton > button { width: 100%; }
          [data-testid="stMetricValue"] { font-size: 1.45rem; }
        }
        @media (max-width: 420px) {
          html { font-size: 14.5px; }
          [data-testid="stMainBlockContainer"] { padding-inline: .55rem; }
          .dare-card { padding: .85rem; }
          div[data-testid="stMetric"] { padding: .75rem .8rem; }
        }
        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after {
            scroll-behavior: auto !important;
            transition-duration: .01ms !important;
            animation-duration: .01ms !important;
            animation-iteration-count: 1 !important;
          }
        }
        @media print {
          [data-testid="stSidebar"], [data-testid="stHeader"] { display: none !important; }
          .stApp, [data-testid="stMainBlockContainer"] {
            background: #FFFFFF !important;
            box-shadow: none !important;
            border: 0 !important;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    page_background = st.session_state.get("page_background_color", "#F4F7F5")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(page_background)):
        page_background = "#F4F7F5"
    st.markdown(
        f"<style>:root {{ --dare-page-bg: {page_background}; }}</style>",
        unsafe_allow_html=True,
    )


def initialize_state() -> None:
    defaults: dict[str, Any] = {
        "pdf_hash": None,
        "pdf_result": None,
        "suggestions": {},
        "pending_citation_autofill": None,
        "citation_autofilled_value": "",
        "citation_autofill_notice": "",
        "q5_count_signature": "",
        "q5_autofilled_score": None,
        "q5_autofilled_comment": "",
        "q5_count_notice": "",
        "registry": [],
        "batch_grid": pd.DataFrame([empty_batch_row()]),
        "batch_editor_version": 0,
        "batch_row_edit_index": None,
        "batch_row_edit_version": 0,
        "batch_rows_pending_delete": [],
        "batch_row_notice": "",
        "registry_batch_loaded_count": 0,
        "batch_pdf_hashes": [],
        "batch_pdf_evidence": [],
        "single_endpoint": ENDPOINTS[0].key,
        "single_pathway": ENDPOINTS[0].default_pathway,
        "verification_design": "full_population",
        "synthesis_path_key": "serology_apparent",
        "endpoint_selector": ENDPOINTS[0].key,
        "pathway_selector": ENDPOINTS[0].default_pathway,
        "verification_selector": "full_population",
        "synthesis_path_selector": "serology_apparent",
        "sampling_frame_category": "",
        "page_background_color": "#F4F7F5",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    for criterion in CRITERIA:
        st.session_state.setdefault(f"score_{criterion.code}", None)
        st.session_state.setdefault(f"comment_{criterion.code}", "")


def empty_batch_row() -> dict[str, Any]:
    row: dict[str, Any] = {
        "Study ID": "",
        "Citation or DOI": "",
        "Virus": "",
        "Target population": "",
        "Target-population category": "",
        "Achieved sample representation": ACHIEVED_REPRESENTATION_OPTIONS[0],
        "Sampling frame / source population": "",
        "Sampling-frame category": "",
        "Sampling/recruitment method": "",
        "Primary assay": "",
        "Confirmatory assay": "",
        "Assay role": "",
        "Endpoint structure": "",
        "Confirmation role": "",
        "Confirmation coverage": "",
        "Assay validation / QC": "Not reported / unclear",
        "Endpoint procedure consistency": ENDPOINT_CONSISTENCY_OPTIONS[0],
        "Assay-performance correction": "Not reported / no explicit correction",
        "Synthesis path": SYNTHESIS_PATH_LABELS_BY_KEY["serology_apparent"],
        "Endpoint": ENDPOINTS[0].label,
        "Endpoint-defining assay": "",
        "Testing/verification population": "",
        "Summary estimator": SUMMARY_ESTIMATORS[0],
        "Numerator provenance": NUMERATOR_PROVENANCE["observed"],
        "Measurement pathway": PATHWAY_LABELS[ENDPOINTS[0].default_pathway],
        "Verification design": VERIFICATION_DESIGNS["full_population"],
        "Source population N": None,
        "Planned verification N": None,
        "Endpoint tested N": None,
        "Endpoint positive n": None,
        "Screen-positive n": None,
        "Screen-negative n": None,
        "Verified screen-negative N": None,
        "Positive among verified screen-negative n": None,
        "Reference-positive N": None,
        "Reference-positive detected n": None,
        "Reference-negative N": None,
        "Reference-negative correctly negative n": None,
    }
    for field_name, (label, _) in PATH_COUNT_FIELDS.items():
        row[label] = None
    for criterion in CRITERIA:
        row[criterion.code] = None
    return row


def hero() -> None:
    if DARE_BANNER_PATH.exists():
        banner_uri = png_data_uri(DARE_BANNER_PATH.read_bytes())
        st.markdown(
            f"""
            <div class="dare-hero">
              <img src="{banner_uri}" alt="DARE-Arbo framework: purpose, web-based assessment features, reproducibility, transparency, and shareable reports">
              <div class="dare-mobile-brand" aria-label="DARE-Arbo Design, Assay and Reporting Evaluation framework">
                <strong>DARE-Arbo</strong>
                <span>Design, Assay and Reporting Evaluation for reproducible arbovirus evidence</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown("# DARE-Arbo")


def reset_page_background() -> None:
    st.session_state["page_background_color"] = "#F4F7F5"


def sidebar() -> str:
    if LOGO_PATH.exists() and SYNERGY_LOGO_PATH.exists():
        logo_columns = st.sidebar.columns([1, 1], gap="small")
        logo_columns[0].image(str(LOGO_PATH), width=84)
        logo_columns[1].image(prepared_square_logo(SYNERGY_LOGO_PATH.read_bytes()), width=84)
    elif LOGO_PATH.exists():
        st.sidebar.image(str(LOGO_PATH), width=88)
    elif SYNERGY_LOGO_PATH.exists():
        st.sidebar.image(prepared_square_logo(SYNERGY_LOGO_PATH.read_bytes()), width=84)
    st.sidebar.markdown("### DARE-Arbo")
    page = st.sidebar.radio(
        "Workspace",
        ("Assessor", "Appraiser", "DARE-Arbo Designer", "Framework", "About & deployment"),
        label_visibility="collapsed",
    )
    with st.sidebar.expander("Appearance", expanded=False):
        st.color_picker(
            "Workspace page color",
            key="page_background_color",
            help="Changes the app workspace only. It does not change the downloadable PNG.",
        )
        st.button(
            "Reset page color",
            on_click=reset_page_background,
            use_container_width=True,
            key="reset_page_background",
        )
        st.caption("The sidebar and presentation-export colors remain independent.")
    st.sidebar.markdown("---")
    st.sidebar.caption("The total is a continuous methodological-quality score. DARE-Arbo does not impose low/moderate/high bands.")
    st.sidebar.caption(GUIDE_VERSION)
    return page


def reset_single_assessment() -> None:
    for criterion in CRITERIA:
        st.session_state[f"score_{criterion.code}"] = None
        st.session_state[f"comment_{criterion.code}"] = ""
    st.session_state["pdf_hash"] = None
    st.session_state["pdf_result"] = None
    st.session_state["suggestions"] = {}
    st.session_state["pending_citation_autofill"] = None
    st.session_state["citation_autofilled_value"] = ""
    st.session_state["citation_autofill_notice"] = ""
    st.session_state["q5_count_signature"] = ""
    st.session_state["q5_autofilled_score"] = None
    st.session_state["q5_autofilled_comment"] = ""
    st.session_state["q5_count_notice"] = ""


def apply_count_based_q5(count_audit: dict[str, Any]) -> None:
    """Autofill Q5 before its widget exists, while preserving reviewer overrides."""
    signature_payload = {
        key: count_audit.get(key)
        for key in (
            "synthesis_path_key", "total_recruited_n", "total_tested_n",
            "primary_positive_n", "number_retested_n", "planned_retest_n",
        )
    }
    signature = json.dumps(signature_payload, sort_keys=True, default=str)
    if signature == st.session_state.get("q5_count_signature"):
        return
    st.session_state["q5_count_signature"] = signature
    suggested = count_audit.get("q5_suggested_score")
    if suggested is None:
        current_score = st.session_state.get("score_Q5")
        previous_auto = st.session_state.get("q5_autofilled_score")
        current_comment = str(st.session_state.get("comment_Q5") or "").strip()
        previous_comment = str(st.session_state.get("q5_autofilled_comment") or "").strip()
        if previous_auto is not None and current_score == previous_auto:
            st.session_state["score_Q5"] = None
            if previous_comment and current_comment == previous_comment:
                st.session_state["comment_Q5"] = ""
            st.session_state["q5_autofilled_score"] = None
            st.session_state["q5_autofilled_comment"] = ""
        st.session_state["q5_count_notice"] = (
            "Q5 is not auto-scored yet. Enter Total recruited and Total tested and, for a staged pathway, the actual and planned verification counts."
        )
        return

    current_score = st.session_state.get("score_Q5")
    previous_auto = st.session_state.get("q5_autofilled_score")
    if current_score not in (None, previous_auto):
        st.session_state["q5_count_notice"] = (
            f"Counts imply Q5 = {suggested}/2, but the reviewer-entered Q5 score was preserved. "
            f"Basis: {count_audit.get('q5_basis', '')}."
        )
        return

    rationale = "Count-based Q5: " + str(count_audit.get("q5_basis") or "completion calculated from entered counts") + "."
    current_comment = str(st.session_state.get("comment_Q5") or "").strip()
    previous_comment = str(st.session_state.get("q5_autofilled_comment") or "").strip()
    st.session_state["score_Q5"] = suggested
    st.session_state["q5_autofilled_score"] = suggested
    if not current_comment or current_comment == previous_comment:
        st.session_state["comment_Q5"] = rationale
        st.session_state["q5_autofilled_comment"] = rationale
    st.session_state["q5_count_notice"] = f"Q5 auto-scored {suggested}/2 from counts. {count_audit.get('q5_basis', '')}."


def refresh_draft_suggestions(
    pathway: str,
    endpoint_key: str,
    verification_design: str | None,
    synthesis_path_key: str | None = None,
) -> None:
    """Rebuild PDF suggestions with the current target and count context."""
    if not st.session_state.get("pdf_result"):
        return
    selected_path = synthesis_path_key or st.session_state.get("synthesis_path_key")
    counts = {
        field_name: st.session_state.get(f"path_count_{field_name}")
        for field_name in PATH_COUNT_FIELDS
    }
    st.session_state["suggestions"] = suggest_scores(
        st.session_state["pdf_result"]["pages"],
        pathway,
        endpoint_key,
        verification_design,
        synthesis_path_key=selected_path,
        counts=counts,
        target_population=str(st.session_state.get("target_population") or ""),
        target_population_category=str(
            st.session_state.get("target_population_category") or ""
        ),
        achieved_sample_representation=str(
            st.session_state.get("achieved_sample_representation") or ""
        ),
        sampling_frame_description=str(
            st.session_state.get("sampling_frame_description") or ""
        ),
        sampling_frame_category=str(
            st.session_state.get("sampling_frame_category") or ""
        ),
        sampling_recruitment_method=str(
            st.session_state.get("sampling_recruitment_method") or ""
        ),
        endpoint_procedure_consistency=str(
            st.session_state.get("endpoint_procedure_consistency") or ""
        ),
        primary_assay=str(st.session_state.get("primary_assay_choice") or ""),
        confirmatory_assay="; ".join(
            st.session_state.get("confirmatory_assay_choices") or []
        ),
    )


def sync_target_population_suggestion() -> None:
    """Refresh target-dependent Q1/Q3 drafts after reviewer target entry changes."""
    path_key = str(st.session_state.get("synthesis_path_key") or "")
    path = SYNTHESIS_PATHS_BY_KEY.get(path_key)
    endpoint_key = path.endpoint_key if path else st.session_state.get("single_endpoint", ENDPOINTS[0].key)
    pathway = ENDPOINTS_BY_KEY[endpoint_key].default_pathway
    verification_design = path.verification_design if path else st.session_state.get("verification_design")
    refresh_draft_suggestions(
        pathway,
        endpoint_key,
        verification_design,
        path_key or None,
    )


def sync_sampling_method_suggestion() -> None:
    """Refresh Q4 after the reviewer changes its dictionary fallback entry."""
    sync_target_population_suggestion()


def sync_sampling_frame_suggestion() -> None:
    """Infer a controlled frame from reviewer text, then refresh Q3."""
    current = str(st.session_state.get("sampling_frame_category") or "")
    if not current:
        inferred = normalize_sampling_frame_category(
            "", str(st.session_state.get("sampling_frame_description") or "")
        )
        if inferred:
            st.session_state["sampling_frame_category"] = inferred
    sync_target_population_suggestion()


def sync_endpoint_defaults() -> None:
    endpoint_key = st.session_state["endpoint_selector"]
    synthesis_path = SYNTHESIS_PATHS_BY_ENDPOINT[endpoint_key][0]
    pathway = ENDPOINTS_BY_KEY[endpoint_key].default_pathway
    st.session_state["single_endpoint"] = endpoint_key
    st.session_state["single_pathway"] = pathway
    st.session_state["pathway_selector"] = pathway
    st.session_state["synthesis_path_key"] = synthesis_path.key
    st.session_state["synthesis_path_selector"] = synthesis_path.key
    st.session_state["verification_design"] = synthesis_path.verification_design
    refresh_draft_suggestions(
        pathway, endpoint_key, synthesis_path.verification_design, synthesis_path.key
    )


def sync_pathway() -> None:
    pathway = st.session_state["pathway_selector"]
    st.session_state["single_pathway"] = pathway
    refresh_draft_suggestions(
        pathway,
        st.session_state.get("single_endpoint", ENDPOINTS[0].key),
        st.session_state.get("verification_design"),
    )


def sync_verification_design() -> None:
    design = st.session_state["verification_selector"]
    st.session_state["verification_design"] = design
    refresh_draft_suggestions(
        st.session_state.get("single_pathway", ENDPOINTS[0].default_pathway),
        st.session_state.get("single_endpoint", ENDPOINTS[0].key),
        design,
    )


def sync_synthesis_path() -> None:
    path = SYNTHESIS_PATHS_BY_KEY[st.session_state["synthesis_path_selector"]]
    pathway = ENDPOINTS_BY_KEY[path.endpoint_key].default_pathway
    st.session_state["synthesis_path_key"] = path.key
    st.session_state["verification_design"] = path.verification_design
    st.session_state["single_pathway"] = pathway
    st.session_state["pathway_selector"] = pathway
    refresh_draft_suggestions(
        pathway, path.endpoint_key, path.verification_design, path.key
    )


def endpoint_controls() -> tuple[str, str, str, str]:
    st.subheader("1. Define the extracted endpoint")
    left, right = st.columns([1, 1.35])
    endpoint_keys = list(SYNTHESIS_ENDPOINT_ORDER)
    with left:
        endpoint_key = st.selectbox(
            "Synthesis endpoint",
            endpoint_keys,
            format_func=lambda key: ENDPOINTS_BY_KEY[key].label,
            key="endpoint_selector",
            on_change=sync_endpoint_defaults,
        )
    endpoint = ENDPOINTS_BY_KEY[endpoint_key]
    st.session_state["single_endpoint"] = endpoint_key
    path_options = [path.key for path in SYNTHESIS_PATHS_BY_ENDPOINT[endpoint_key]]
    if st.session_state.get("synthesis_path_selector") not in path_options:
        st.session_state["synthesis_path_selector"] = path_options[0]
    with right:
        synthesis_path_key = st.selectbox(
            "Testing strategy",
            path_options,
            format_func=lambda key: SYNTHESIS_PATHS_BY_KEY[key].testing_strategy,
            key="synthesis_path_selector",
            on_change=sync_synthesis_path,
            help="Testing strategies are filtered by the selected biological synthesis endpoint.",
        )
    synthesis_path = SYNTHESIS_PATHS_BY_KEY[synthesis_path_key]
    pathway = endpoint.default_pathway
    verification_design = synthesis_path.verification_design
    st.session_state["synthesis_path_key"] = synthesis_path_key
    st.session_state["single_pathway"] = pathway
    st.session_state["verification_design"] = verification_design
    classification = classify_synthesis(endpoint_key, verification_design)
    st.markdown(
        f"""
        <div class="dare-card">
          <span class="dare-chip">{classification['endpoint']}</span>
          <span class="dare-chip">{synthesis_path.summary_estimator}</span>
          <p><strong>Assay role:</strong> {classification['assay_role']}</p>
          <p><strong>Measurement pathway:</strong> {classification['measurement_pathway']}</p>
          <p><strong>Assay type:</strong> {synthesis_path.assay_type}</p>
          <p><strong>Numerator:</strong> {synthesis_path.numerator} &nbsp; <strong>Denominator:</strong> {synthesis_path.denominator}</p>
          <p><strong>False positives:</strong> {synthesis_path.false_positives}</p>
          <p class="muted">{classification['caution']}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return endpoint_key, pathway, verification_design, synthesis_path_key


def apply_all_draft_suggestions(pathway: str, endpoint_key: str) -> None:
    """Apply all applicable draft scores during Streamlit's callback phase."""
    suggestions = st.session_state.get("suggestions", {})
    for criterion in CRITERIA:
        suggestion = suggestions.get(criterion.code)
        if suggestion and criterion_is_applicable(criterion.code, pathway, endpoint_key):
            st.session_state[f"score_{criterion.code}"] = suggestion.score


def estimator_count_guide(verification_design: str | None) -> tuple[str, str]:
    """Return a concise required-count guide for the selected testing design."""
    guides = {
        "full_population": (
            "Full-population endpoint",
            "Enter Source population N, Endpoint tested N, and Endpoint positive n. If everyone was planned for testing, Planned verification N should equal Source population N.",
        ),
        "representative_subsample": (
            "Representative population subsample",
            "Enter the planned subsample size, the number actually endpoint-tested, and observed endpoint positives. Retain Source population N when weights or population coverage are relevant; do not call planned subsampling missingness.",
        ),
        "all_screen_positive": (
            "Tier B complete screen-positive verification",
            "Enter Source population N (everyone entering screening), Screen-positive n, Planned verification N, Endpoint tested N, and confirmed Endpoint positive n. Tier B is retained only when planned verification, screen-positive, and actually tested counts are equal.",
        ),
        "representative_positive_subset": (
            "Tier C representative screen-positive subset",
            "Enter Source population N, Screen-positive n, Planned verification N, Endpoint tested N, and Endpoint positive n. For neutralization, these support the weighted estimate (screen-positive/source) × (neutralization-positive/neutralization-tested).",
        ),
        "subset_positive": (
            "Selected or unspecified screen-positive subset",
            "Enter Screen-positive n, Endpoint tested N, and observed Endpoint positive n; add Source population N for context. The raw verified-subset proportion is conditional and must not be presented as unrestricted population prevalence.",
        ),
        "selected_subsample": (
            "Selected endpoint-testing subset",
            "Enter Endpoint tested N and observed Endpoint positive n, plus Source population N when reported. Interpret the result only for the selected testing population.",
        ),
        "two_phase_validation": (
            "Two-phase positive/negative molecular validation",
            "Enter total Source population N and both screening-stratum sizes. Use Endpoint tested/positive for the verified screen-positive stratum, and the two 'verified screen-negative' fields for the negative stratum. The combined raw molecular subset proportion is not population prevalence.",
        ),
        "ancillary_validation": (
            "Ancillary reference-sample validation",
            "Use the four reference-positive/reference-negative fields to reconstruct sensitivity and specificity. These samples support assay appraisal or adjustment parameters; they are not a prevalence endpoint.",
        ),
        "mixed_two_phase": (
            "Mixed or unresolved verification",
            "Record every available screening and verification-stratum count. Leave the population prevalence unresolved until the actual selection fractions and estimator can be reconstructed.",
        ),
        "incomplete": (
            "Incomplete linkage or denominator",
            "Enter only counts explicitly reported and document what is missing. Do not invent a source, tested, or prevalence denominator.",
        ),
    }
    return guides.get(
        verification_design,
        ("Design not selected", "Select an endpoint testing design to see which counts the estimator requires."),
    )


def document_intake(
    pathway: str,
    endpoint_key: str,
    verification_design: str | None,
    synthesis_path_key: str,
) -> tuple[Any, dict[str, Any]]:
    pending_identifier = st.session_state.pop("pending_citation_autofill", None)
    if pending_identifier and pending_identifier.get("value"):
        current_citation = str(st.session_state.get("citation") or "").strip()
        previous_autofill = str(st.session_state.get("citation_autofilled_value") or "").strip()
        if not current_citation or current_citation == previous_autofill:
            st.session_state["citation"] = pending_identifier["value"]
            st.session_state["citation_autofilled_value"] = pending_identifier["value"]
            st.session_state["citation_autofill_notice"] = (
                f"Autofilled {pending_identifier['kind']} from {pending_identifier['source']} "
                f"({pending_identifier['confidence']} confidence). Please verify it against the article."
            )
        else:
            st.session_state["citation_autofill_notice"] = (
                f"Found {pending_identifier['kind']} '{pending_identifier['value']}', but preserved the citation/DOI already entered by the reviewer."
            )
    st.subheader("2. Add the study and source document")
    synthesis_path = SYNTHESIS_PATHS_BY_KEY[synthesis_path_key]
    metadata_col, upload_col = st.columns([1, 1.05])
    with metadata_col:
        study_id = st.text_input("Study ID / Author-year", placeholder="e.g., Author et al., 2024", key="study_id")
        citation = st.text_input(
            "Citation or DOI",
            key="citation",
            help="After PDF extraction, the Assessor uses a DOI when available. If no DOI is found, it constructs a citation from PDF metadata or first-page text.",
        )
        if st.session_state.get("citation_autofill_notice"):
            st.caption(st.session_state["citation_autofill_notice"])
        virus = st.text_input("Arbovirus", placeholder="e.g., DENV", key="virus")
        target_population = st.text_area(
            "Target population",
            placeholder="Describe the community, region, febrile-patient group, antenatal population, etc.",
            height=86,
            key="target_population",
            on_change=sync_target_population_suggestion,
        )
        current_target_category = str(
            st.session_state.get("target_population_category") or ""
        )
        normalized_target_category = normalize_target_population_category(
            current_target_category, target_population
        )
        if current_target_category not in ([""] + TARGET_POPULATION_CATEGORY_OPTIONS):
            st.session_state["target_population_category"] = normalized_target_category or ""
        target_population_category = st.selectbox(
            "Target-population category",
            options=[""] + TARGET_POPULATION_CATEGORY_OPTIONS,
            format_func=lambda value: "Select the estimand-specific target category" if not value else value,
            key="target_population_category",
            on_change=sync_target_population_suggestion,
            help="Q1 and Q3 are judged against this declared target, which may legitimately be local, clinical, occupational, antenatal, pediatric, or otherwise restricted.",
        )
        achieved_sample_representation = st.selectbox(
            "Q1 fallback: achieved age/sex representation",
            options=ACHIEVED_REPRESENTATION_OPTIONS,
            key="achieved_sample_representation",
            on_change=sync_target_population_suggestion,
            help="Used as a low-confidence fallback only when the PDF does not establish whether the achieved sample represents relevant age and sex groups within the declared target.",
        )
        sampling_frame_description = st.text_area(
            "Q3 fallback: sampling frame / source population",
            placeholder="Describe the source list, communities, facilities, surveillance system, registry, biobank, or other frame from which eligible members could be identified.",
            height=82,
            key="sampling_frame_description",
            on_change=sync_sampling_frame_suggestion,
            help="The Target population entry alone cannot earn Q3. This field supplies the missing frame evidence when the PDF does not.",
        )
        current_frame_category = str(
            st.session_state.get("sampling_frame_category") or ""
        )
        normalized_frame_category = normalize_sampling_frame_category(
            current_frame_category, sampling_frame_description
        )
        if current_frame_category not in ([""] + SAMPLING_FRAME_CATEGORY_OPTIONS):
            st.session_state["sampling_frame_category"] = normalized_frame_category or ""
        sampling_frame_category = st.selectbox(
            "Sampling-frame category",
            options=[""] + SAMPLING_FRAME_CATEGORY_OPTIONS,
            format_func=lambda value: "Classify the operational source frame" if not value else value,
            key="sampling_frame_category",
            on_change=sync_target_population_suggestion,
            help="Classify the list, registry, service, surveillance system, cohort, enumeration system, or operational specimen source from which participants could enter. The laboratory is a frame only when it supplied the specimens, not merely because testing occurred there.",
        )
        frame_alignment = assess_target_frame_alignment(
            target_population_category, sampling_frame_category
        )
        st.caption(
            f"Target–frame matrix: {frame_alignment['status'].replace('-', ' ')}. "
            f"{frame_alignment['rationale']}"
        )
        sampling_recruitment_method = st.selectbox(
            "Q4 fallback: sampling and recruitment method",
            options=[""] + list(Q4_USER_METHOD_SCORES),
            format_func=lambda value: "Select a method when PDF evidence is unavailable" if not value else value,
            key="sampling_recruitment_method",
            on_change=sync_sampling_method_suggestion,
            help=(
                "Uses the Sampling/Recruitment Method Dictionary to draft Q4 only when the uploaded PDF has no classifiable recruitment evidence. PDF evidence takes precedence."
            ),
        )
        assay_options = ASSAY_OPTIONS_BY_SYNTHESIS_PATH[synthesis_path_key]
        primary_options = list(assay_options["primary"])
        current_primary = str(st.session_state.get("primary_assay_choice") or "")
        if current_primary not in primary_options:
            st.session_state["primary_assay_choice"] = ""
        primary_assay_choice = st.selectbox(
            "Primary / screening assay",
            options=[""] + primary_options,
            format_func=lambda value: "Select one primary/screening assay" if not value else value,
            key="primary_assay_choice",
            on_change=sync_target_population_suggestion,
            help=(
                "Select exactly one assay that generated the primary, screening, or universal endpoint result. Options are filtered by the Synthesis endpoint and Testing strategy."
            ),
        )
        primary_other = ""
        if primary_assay_choice == "Other primary assay (describe)":
            primary_other = st.text_input(
                "Describe the other primary assay",
                key="primary_assay_other",
                placeholder="Enter the assay name and platform",
            )
        primary_assay = primary_other.strip() or primary_assay_choice

        confirmatory_options = list(assay_options["confirmatory"])
        current_confirmatory = st.session_state.get("confirmatory_assay_choices", [])
        if not isinstance(current_confirmatory, list):
            current_confirmatory = []
        valid_confirmatory = [
            value for value in current_confirmatory if value in confirmatory_options
        ]
        if current_confirmatory != valid_confirmatory:
            st.session_state["confirmatory_assay_choices"] = valid_confirmatory
        confirmatory_assay_choices = st.multiselect(
            "Confirmatory assay(s)",
            options=confirmatory_options,
            key="confirmatory_assay_choices",
            on_change=sync_target_population_suggestion,
            disabled=not confirmatory_options,
            placeholder=(
                "Select one or more confirmatory assays"
                if confirmatory_options
                else "Not part of the selected testing strategy"
            ),
            help=(
                "Select every confirmatory method actually applied. Multiple selections are allowed. If no options appear, confirmation is not part of the selected testing strategy; choose a staged/validation strategy if confirmation was performed."
            ),
        )
        confirmatory_other = ""
        if "Other confirmatory assay (describe)" in confirmatory_assay_choices:
            confirmatory_other = st.text_input(
                "Describe the other confirmatory assay",
                key="confirmatory_assay_other",
                placeholder="Enter the assay name and platform",
            )
        resolved_confirmatory_assays = [
            confirmatory_other.strip() or choice
            if choice == "Other confirmatory assay (describe)"
            else choice
            for choice in confirmatory_assay_choices
        ]
        confirmatory_assay = "; ".join(resolved_confirmatory_assays)
        testing_verification_population = st.text_area(
            "Testing / verification population",
            placeholder="Who received the primary test, and who was eligible for and actually received each verification step?",
            height=76,
            key="testing_verification_population",
            help="Keep this distinct from the target population. It defines the measurement pathway and is required to interpret staged or subset estimates.",
        )
        endpoint_procedure_consistency = st.selectbox(
            "Q7 fallback: endpoint procedure consistency",
            options=ENDPOINT_CONSISTENCY_OPTIONS,
            key="endpoint_procedure_consistency",
            on_change=sync_target_population_suggestion,
            help="A supporting or ancillary validation subset does not lower Q7 when the primary endpoint procedure was otherwise consistent. A staged endpoint requires consistent completion of every required step.",
        )
        endpoint_classification = classify_synthesis(
            synthesis_path.endpoint_key, synthesis_path.verification_design
        )
        path_profile = assay_path_profile(synthesis_path_key)
        endpoint_defining_assay = (
            f"{primary_assay} → {confirmatory_assay}"
            if "staged" in endpoint_classification["assay_role"].lower()
            and primary_assay and confirmatory_assay
            else primary_assay
        )
        supporting_assay = (
            confirmatory_assay
            if confirmatory_assay and confirmatory_assay not in endpoint_defining_assay
            else ""
        )
        st.caption(
            f"Endpoint structure: {path_profile['endpoint_structure']}. "
            f"Primary role: {path_profile['primary_assay_role']}. "
            f"Confirmation role: {path_profile['confirmation_role']}. "
            f"Coverage: {path_profile['confirmation_coverage']}. "
            + (
                f"Endpoint-defining assay/algorithm: {endpoint_defining_assay}."
                if endpoint_defining_assay else
                "Select the assay(s) to complete the endpoint definition."
            )
            + (f" Supporting confirmation: {supporting_assay}." if supporting_assay else "")
        )
        st.markdown("**Counts required by the actual estimator**")
        st.caption("Enter observed whole-number counts. Q5 uses Total tested / Total recruited and, for staged testing, actual / planned verification; the lower applicable percentage determines the score. Leave a field blank when it does not apply; enter 0 only when zero was genuinely observed.")
        with st.expander("How will these counts be synthesized?", expanded=False):
            st.markdown(f"**Summary estimator:** {synthesis_path.summary_estimator}")
            st.markdown(f"**Assay type:** {synthesis_path.assay_type}")
            st.markdown(f"**Numerator:** {synthesis_path.numerator}")
            st.markdown(f"**Denominator:** {synthesis_path.denominator}")
            st.markdown(f"**False positives:** {synthesis_path.false_positives}")
            st.markdown("**Tier B:** prevalence uses Confirmed positive / Total tested and requires complete verification of all planned screening positives. **Tier C:** uses Total tested, Screen-positive N, Number retested, and Confirmed positive in an explicit weighted/screen-conditioned estimator. **Tier D:** the raw Confirmed positive / Number retested proportion is conditional on the selected subset and is not population prevalence.")
        count_field_names = list(dict.fromkeys(
            ("total_recruited_n", "total_tested_n", *synthesis_path.required_counts)
        ))
        if synthesis_path_key in {
            "serology_tier_c", "serology_tier_d", "confirmed_active_tier_c",
            "confirmed_active_tier_d", "serology_validation", "molecular_validation"
        }:
            count_field_names.append("planned_retest_n")
        count_cols = st.columns(2)
        path_count_values: dict[str, Any] = {}
        for index, field_name in enumerate(count_field_names):
            label, help_text = PATH_COUNT_FIELDS[field_name]
            path_count_values[field_name] = count_cols[index % 2].text_input(
                label,
                key=f"path_count_{field_name}",
                help=help_text,
                on_change=sync_target_population_suggestion,
            )

        with st.expander("Audit details", expanded=False):
            numerator_provenance = st.selectbox(
                "Numerator provenance",
                list(NUMERATOR_PROVENANCE),
                format_func=lambda key: NUMERATOR_PROVENANCE[key],
                key="numerator_provenance",
                help="Observed counts are required for Q8. Posterior/model-derived expected positives remain separate.",
            )
            reviewer = st.text_input("Reviewer/appraiser", key="reviewer")

    with upload_col:
        uploaded_pdf = st.file_uploader(
            "Upload article PDF",
            type=["pdf"],
            accept_multiple_files=False,
            help="The PDF is processed in memory. Text-based PDFs work best; scanned images require OCR before upload.",
        )
        if uploaded_pdf is not None:
            pdf_bytes = uploaded_pdf.getvalue()
            file_hash = hashlib.sha256(pdf_bytes).hexdigest()
            st.caption(f"{uploaded_pdf.name} · {len(pdf_bytes) / 1024:.1f} KB")
            if st.button("Extract evidence and draft suggestions", type="primary", use_container_width=True):
                rerun_for_identifier = False
                try:
                    with st.spinner("Extracting page-aware evidence…"):
                        extracted = extract_pdf_text(pdf_bytes)
                        bibliographic_identifier = extract_citation_or_doi(
                            extracted["pages"], extracted.get("metadata")
                        )
                        extracted["bibliographic_identifier"] = bibliographic_identifier
                        suggestions = suggest_scores(
                            extracted["pages"], pathway, endpoint_key, verification_design,
                            synthesis_path_key=synthesis_path_key,
                            counts=path_count_values,
                            target_population=target_population,
                            target_population_category=target_population_category,
                            achieved_sample_representation=achieved_sample_representation,
                            sampling_frame_description=sampling_frame_description,
                            sampling_frame_category=sampling_frame_category,
                            sampling_recruitment_method=sampling_recruitment_method,
                            endpoint_procedure_consistency=endpoint_procedure_consistency,
                            primary_assay=primary_assay,
                            confirmatory_assay=confirmatory_assay,
                        )
                    st.session_state["pdf_hash"] = file_hash
                    st.session_state["pdf_result"] = extracted
                    st.session_state["suggestions"] = suggestions
                    if bibliographic_identifier["value"]:
                        st.session_state["pending_citation_autofill"] = bibliographic_identifier
                        rerun_for_identifier = True
                    if extracted["scanned_or_empty"]:
                        st.warning("No extractable text was found. Run OCR on this scanned PDF, or complete the assessment manually.")
                    else:
                        st.success(f"Extracted {extracted['page_count']} pages and {extracted['character_count']:,} characters.")
                except Exception as exc:
                    st.error(f"PDF extraction failed: {exc}")
                if rerun_for_identifier:
                    st.rerun()
            if st.session_state.get("pdf_hash") == file_hash and st.session_state.get("pdf_result"):
                extracted = st.session_state["pdf_result"]
                st.info(f"Evidence ready: {extracted['page_count']} pages · {extracted['character_count']:,} extracted characters")
                identifier = extracted.get("bibliographic_identifier") or {}
                if identifier.get("value"):
                    st.caption(
                        f"Detected {identifier['kind']}: {identifier['value']} "
                        f"({identifier['confidence']} confidence; {identifier['source']})."
                    )
        else:
            st.info("PDF upload is optional. Every criterion can be completed manually.")

        suggestions = st.session_state.get("suggestions", {})
        if suggestions:
            st.warning("Suggestions are draft evidence aids, not final judgments. Verify each score against the article and the stated target population.")
            st.button(
                "Apply all draft suggestions",
                use_container_width=True,
                on_click=apply_all_draft_suggestions,
                args=(pathway, endpoint_key),
            )

    path_count_values["numerator_provenance"] = numerator_provenance
    path_count_audit = audit_synthesis_path_counts(synthesis_path_key, path_count_values)
    apply_count_based_q5(path_count_audit)
    if st.session_state.get("q5_count_notice"):
        st.info(st.session_state["q5_count_notice"])
    metadata = {
        "study_id": study_id,
        "citation": citation,
        "virus": virus,
        "target_population": target_population,
        "target_population_category": target_population_category,
        "achieved_sample_representation": achieved_sample_representation,
        "sampling_frame_description": sampling_frame_description,
        "sampling_frame_category": sampling_frame_category,
        "sampling_recruitment_method": sampling_recruitment_method,
        "primary_assay": primary_assay,
        "confirmatory_assay": confirmatory_assay,
        "primary_assay_choice": primary_assay_choice,
        "confirmatory_assay_choices": resolved_confirmatory_assays,
        "endpoint_structure": path_profile["endpoint_structure"],
        "assay_role": path_profile["primary_assay_role"],
        "confirmation_role": path_profile["confirmation_role"],
        "confirmation_coverage": path_profile["confirmation_coverage"],
        "endpoint_testing_population_class": path_profile["measurement_pathway"],
        "endpoint_defining_assay": endpoint_defining_assay,
        "supporting_confirmation_assay": supporting_assay,
        "testing_population": testing_verification_population or synthesis_path.testing_strategy,
        "endpoint_procedure_consistency": endpoint_procedure_consistency,
        "summary_estimand": synthesis_path.summary_estimator,
        "synthesis_path_key": synthesis_path_key,
        "testing_strategy": synthesis_path.testing_strategy,
        "assay_type": synthesis_path.assay_type,
        "numerator_label": synthesis_path.numerator,
        "denominator_label": synthesis_path.denominator,
        "false_positive_rule": synthesis_path.false_positives,
        "numerator_provenance": numerator_provenance,
        "reviewer": reviewer,
        "assessment_date": date.today().isoformat(),
        "source_pdf": uploaded_pdf.name if uploaded_pdf else "",
        "source_pdf_sha256": hashlib.sha256(uploaded_pdf.getvalue()).hexdigest() if uploaded_pdf else "",
        **path_count_values,
        "source_population_n": path_count_audit["source_population_n"],
        "planned_verification_n": path_count_audit["planned_verification_n"],
        "endpoint_tested_n": path_count_audit["endpoint_tested_n"],
        "endpoint_positive_n": path_count_audit["endpoint_positive_n"],
        "screen_positive_n": path_count_audit["screen_positive_n"],
        "screen_negative_n": path_count_audit.get("retested_negative_stratum_n"),
        "verification_negative_tested_n": path_count_audit.get("retested_negative_stratum_n"),
        "verification_negative_positive_n": path_count_audit.get("false_negatives_n"),
        "confirmed_negative_n": path_count_audit.get("confirmed_negative_n"),
        "reference_positive_n": (
            (path_count_audit.get("confirmed_positive_n") or 0) + (path_count_audit.get("false_negatives_n") or 0)
            if synthesis_path_key in {"serology_validation", "molecular_validation"} else None
        ),
        "reference_positive_detected_n": path_count_audit.get("confirmed_positive_n"),
        "reference_negative_n": (
            (path_count_audit.get("confirmed_negative_n") or 0) + (path_count_audit.get("false_positives_n") or 0)
            if synthesis_path_key in {"serology_validation", "molecular_validation"} else None
        ),
        "reference_negative_correct_n": path_count_audit.get("confirmed_negative_n"),
    }
    return uploaded_pdf, metadata


def apply_suggested_score(criterion_code: str, suggested_score: int) -> None:
    """Apply a widget value before Streamlit instantiates it on the next run."""
    st.session_state[f"score_{criterion_code}"] = suggested_score


def criterion_panel(criterion: Any, pathway: str, endpoint_key: str) -> None:
    applicable = criterion_is_applicable(criterion.code, pathway, endpoint_key)
    suggestion = st.session_state.get("suggestions", {}).get(criterion.code)
    st.markdown(f"#### {criterion.code}. {criterion.label} · 0-{criterion.maximum}")
    st.caption(criterion.question)
    if not applicable:
        st.session_state[f"score_{criterion.code}"] = None
        st.info("Not applicable for this synthesis endpoint. Do not score this as zero.")
        return

    score_options = [None] + list(range(criterion.maximum + 1))
    score_col, comment_col = st.columns([0.72, 1.55])
    with score_col:
        st.radio(
            "Score",
            options=score_options,
            key=f"score_{criterion.code}",
            format_func=lambda value: "Not scored" if value is None else f"{value} / {criterion.maximum}",
            horizontal=criterion.maximum <= 2,
        )
        if suggestion:
            suggested = "N/A" if suggestion.score is None else f"{suggestion.score}/{criterion.maximum}"
            st.caption(f"Draft suggestion: {suggested} · {suggestion.confidence} confidence")
            if suggestion.score is not None:
                st.button(
                    "Use suggestion",
                    key=f"use_{criterion.code}",
                    on_click=apply_suggested_score,
                    args=(criterion.code, suggestion.score),
                )
    with comment_col:
        st.text_area(
            "Audit comment / rationale",
            key=f"comment_{criterion.code}",
            placeholder="Record the exact reported feature that supports this score.",
            height=105,
        )
    with st.expander("Operational scoring rules and extracted evidence"):
        for score, rule in criterion.rules.items():
            st.markdown(f"**{score}:** {rule}")
        if suggestion:
            st.markdown(f"**Why the draft suggested this:** {suggestion.rationale}")
            if suggestion.evidence:
                for snippet in suggestion.evidence:
                    st.markdown(
                        f"<div class='evidence'><strong>Page {snippet.page}</strong><br>{snippet.text}</div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No directly matched passage was found in extracted text.")
        else:
            st.caption("Upload and extract a text-based PDF to show page-aware evidence suggestions here.")
    st.markdown("---")


def assessment_form(pathway: str, endpoint_key: str) -> tuple[dict[str, Any], dict[str, str]]:
    st.subheader("3. Score each DARE-Arbo criterion")
    with st.expander("Q1-Q5 decision order and dictionary mapping", expanded=False):
        st.markdown(
            """
            **Q1 — achieved representation:** define the endpoint-specific target first, then assess whether the achieved sample includes the age and sex groups expected for that target without a major imbalance. Only *adequate within the defined target* scores 1; underrepresentation, overrepresentation, inappropriate generalization, or insufficient age/sex information scores 0. Geographic, facility, and source-frame coverage are judged under Q3.

            **Q2 — participant source:** score 1 for directly collected participant data or participant-derived specimens. Stored specimens qualify only when their participant/source population is clear; proxy, aggregate, or unclear secondary sources score 0.

            **Q3 — sampling frame:** judge whether the actual source list, facilities, communities, surveillance platform, registry, biobank, or recruitment setting could identify eligible members of that same target. A target-population label alone cannot earn Q3. When PDF evidence is absent, use the explicit Sampling frame / source population fallback and verify it before finalizing. Named communities, multisite recruitment, or archived samples do not automatically establish adequate frame coverage.

            **Q4 — actual endpoint pathway:** classify the method that assembled the participants/specimens contributing the numerator and denominator: insufficient = 0; non-probability/selected = 1; simple/systematic random or materially broadened multisite capture = 2; multistage/stratified/cluster probability, census, universal, or verified complete capture = 3. For mixed/hybrid recruitment, use the weakest consequential selection stage. Random retesting or validation does not upgrade the source recruitment method.

            **Q5 — completeness:** calculate Total tested / Total recruited and, when staged testing applies, actual completed / planned. The lower applicable percentage scores 0 if <70%, 1 if 70% to <80%, and 2 if ≥80%.
            """
        )
    tabs = st.tabs(DOMAINS)
    for tab, domain in zip(tabs, DOMAINS):
        with tab:
            for criterion in CRITERIA:
                if criterion.domain == domain:
                    criterion_panel(criterion, pathway, endpoint_key)
    scores = {criterion.code: st.session_state.get(f"score_{criterion.code}") for criterion in CRITERIA}
    comments = {criterion.code: st.session_state.get(f"comment_{criterion.code}", "") for criterion in CRITERIA}
    return scores, comments


def overview_color_controls() -> dict[str, str]:
    """Select presentation colors without changing the assessor UI theme."""
    with st.expander("Presentation colors", expanded=False):
        st.caption(
            "The DARE-Arbo classic palette is the default. Choose a preset or customize the high-resolution PNG for a slide deck, poster, or report."
        )
        preset_name = st.selectbox(
            "Palette preset",
            options=list(OVERVIEW_COLOR_PRESETS),
            key="overview_palette_preset",
        )
        selected = dict(OVERVIEW_COLOR_PRESETS[preset_name])
        customize = st.checkbox(
            "Customize this palette",
            key="overview_palette_customize",
        )
        if customize:
            labels = {
                "header": "Header",
                "primary": "Primary accent",
                "secondary": "Secondary accent",
                "background": "PNG page color",
                "text": "Text",
                "success": "Maximum points",
                "warning": "Partial points",
                "danger": "Zero points",
            }
            color_columns = st.columns(4)
            for index, (field, label) in enumerate(labels.items()):
                selected[field] = color_columns[index % 4].color_picker(
                    label,
                    value=selected[field],
                    key=f"overview_color_{preset_name}_{field}",
                )
            st.caption("Changes update the preview and downloaded PNG immediately; scores and classifications are unaffected.")
        else:
            st.caption("Enable customization to edit individual presentation colors.")
    return selected


def single_results(
    metadata: dict[str, Any],
    endpoint_key: str,
    pathway: str,
    verification_design: str | None,
    scores: dict[str, Any],
    comments: dict[str, str],
) -> None:
    st.subheader("4. Review and export")
    result = score_assessment(scores, pathway, endpoint_key)
    selected_classification = classify_synthesis(endpoint_key, verification_design)
    synthesis_path_key = str(metadata.get("synthesis_path_key") or "")
    path_definition = SYNTHESIS_PATHS_BY_KEY.get(synthesis_path_key)
    if path_definition:
        path_count_audit = audit_synthesis_path_counts(synthesis_path_key, metadata)
        effective_path = SYNTHESIS_PATHS_BY_KEY[path_count_audit["effective_synthesis_path_key"]]
        classification = classify_synthesis(endpoint_key, effective_path.verification_design)
        classification = {
            **classification,
            "tier": effective_path.summary_estimator,
        }
        count_audit = {
            **path_count_audit,
            "complete_counts": path_count_audit["estimand_ready"],
            "effective_verification_design": effective_path.verification_design,
            "effective_tier": effective_path.summary_estimator,
            "endpoint_test_positivity": (
                path_count_audit.get("verification_subset_positivity")
                if path_count_audit.get("verification_subset_positivity") is not None
                else path_count_audit["estimate"]
            ),
            "synthesis_denominator_n": path_count_audit["denominator_n"],
            "prevalence": path_count_audit["estimate"],
        }
    else:
        count_audit = audit_endpoint_counts(
            endpoint_key,
            verification_design,
            metadata.get("source_population_n"),
            metadata.get("endpoint_tested_n"),
            metadata.get("endpoint_positive_n"),
            metadata.get("screen_positive_n"),
            metadata.get("planned_verification_n"),
            metadata.get("screen_negative_n"),
            metadata.get("verification_negative_tested_n"),
            metadata.get("verification_negative_positive_n"),
            metadata.get("numerator_provenance", "unclear"),
            metadata.get("reference_positive_n"),
            metadata.get("reference_positive_detected_n"),
            metadata.get("reference_negative_n"),
            metadata.get("reference_negative_correct_n"),
        )
        classification = classify_synthesis(
            endpoint_key, count_audit["effective_verification_design"]
        )
    cols = st.columns(5)
    cols[0].metric("DARE-Arbo score", f"{result['total']} / {result['maximum']}")
    cols[1].metric("Items completed", f"{result['scored_count']} / {result['applicable_count']}")
    cols[2].metric("Study design", f"{sum(result['domains'][d]['score'] for d in DOMAINS[:2])} / {sum(result['domains'][d]['maximum'] for d in DOMAINS[:2])}")
    cols[3].metric("Assay", f"{result['domains']['Laboratory assay']['score']} / {result['domains']['Laboratory assay']['maximum']}")
    cols[4].metric("Reporting", f"{result['domains']['Reporting outcome']['score']} / {result['domains']['Reporting outcome']['maximum']}")

    if result["complete"]:
        st.success("All applicable criteria are scored. The total remains continuous and should be interpreted with the domain scores and audit comments.")
    else:
        st.warning("This assessment is incomplete. Unscored applicable items currently contribute zero to the displayed draft total.")

    if path_definition:
        st.markdown(
            f"**Testing strategy:** {path_definition.testing_strategy}  \n"
            f"**Summary estimator:** {count_audit['summary_estimator']}  \n"
            f"**Numerator:** {path_definition.numerator}  \n"
            f"**Denominator:** {path_definition.denominator}  \n"
            f"**False positives:** {path_definition.false_positives}"
        )
    else:
        st.markdown(
            f"**Endpoint count rule:** {classification['numerator_rule']}  \n"
            f"**Denominator rule:** {classification['denominator_rule']}  \n"
            f"**Synthesis eligibility:** {classification['eligibility']}"
        )
    if count_audit["issues"]:
        for issue in count_audit["issues"]:
            st.error(f"Count audit: {issue}")
        if count_audit["effective_verification_design"] != verification_design:
            st.warning(
                f"Count-based reclassification: {count_audit['effective_tier']}. "
                "Tier B was not retained because Number retested differs from Primary positive."
            )
    elif count_audit["complete_counts"]:
        if count_audit["endpoint_test_positivity"] is not None:
            st.success(
                f"Observed counts are internally consistent. {count_audit.get('numerator_label', 'Endpoint positive')}: "
                f"{count_audit['endpoint_positive_n']}/{count_audit.get('verification_tested_n') or count_audit['endpoint_tested_n']} "
                f"({100 * count_audit['endpoint_test_positivity']:.2f}%)."
            )
        else:
            st.success("The validation counts are internally consistent and reconstructable.")
        if count_audit["prevalence"] is not None:
            if synthesis_path_key in {"serology_tier_c", "confirmed_active_tier_c"}:
                st.info(
                    "Tier C weighted/screen-conditioned estimate: "
                    f"(Primary positive / Total tested) × (Confirmed positive / Number retested) = "
                    f"{100 * count_audit['prevalence']:.2f}%. The raw verification-subset proportion remains separate."
                )
            else:
                st.info(
                    f"Synthesis estimate under the selected pathway: "
                    f"{count_audit['endpoint_positive_n']}/{count_audit['synthesis_denominator_n']} "
                    f"({100 * count_audit['prevalence']:.2f}%)."
                )
        if count_audit.get("false_positives_n") is not None:
            st.info(f"False positives: {count_audit['false_positives_n']}.")
        if count_audit.get("false_negatives_n") is not None:
            st.info(f"False negatives: {count_audit['false_negatives_n']}.")
    else:
        st.info("Enter observed endpoint-positive and endpoint-tested counts to audit Q8 and synthesis denominators.")

    q5_rates: list[str] = []
    if count_audit.get("recruitment_testing_rate") is not None:
        q5_rates.append(
            f"tested/recruited {100 * count_audit['recruitment_testing_rate']:.1f}%"
        )
    if count_audit.get("planned_pathway_completion_rate") is not None:
        q5_rates.append(
            f"planned-pathway completion {100 * count_audit['planned_pathway_completion_rate']:.1f}%"
        )
    if count_audit["completion_rate"] is not None:
        st.info(
            "Q5 count audit: " + "; ".join(q5_rates) + ". "
            f"Limiting percentage {100 * count_audit['completion_rate']:.1f}% "
            f"(count-based score {count_audit['q5_suggested_score']}/2)."
        )
    elif q5_rates:
        st.warning(
            "Q5 is not fully calculable because one required completion component is missing. Available: "
            + "; ".join(q5_rates) + "."
        )
    for warning in count_audit.get("warnings", []):
        st.warning(f"Count note: {warning}")
    if count_audit["missing_required_fields"]:
        st.warning(
            "Estimator is not reconstructable for Q8; missing: "
            + ", ".join(count_audit["missing_required_fields"])
        )
    if count_audit["sensitivity"] is not None or count_audit["specificity"] is not None:
        validation_metrics = []
        if count_audit["sensitivity"] is not None:
            validation_metrics.append(f"sensitivity {100 * count_audit['sensitivity']:.1f}%")
        if count_audit["specificity"] is not None:
            validation_metrics.append(f"specificity {100 * count_audit['specificity']:.1f}%")
        st.info("Ancillary validation: " + "; ".join(validation_metrics) + ". These samples are not a prevalence endpoint and do not automatically earn Q6c.")

    domain_df = pd.DataFrame(
        [
            {
                "Domain": domain,
                "Score": values["score"],
                "Maximum": values["maximum"],
                "Attainment (%)": round(100 * values["score"] / values["maximum"], 1),
            }
            for domain, values in result["domains"].items()
        ]
    )
    chart = (
        alt.Chart(domain_df)
        .mark_bar(cornerRadiusEnd=6, color="#18864b")
        .encode(
            x=alt.X("Attainment (%):Q", scale=alt.Scale(domain=[0, 100]), title="Descriptive domain attainment (%)"),
            y=alt.Y("Domain:N", sort=list(DOMAINS), title=None),
            tooltip=["Domain", "Score", "Maximum", "Attainment (%)"],
        )
        .properties(height=190)
    )
    st.altair_chart(chart, use_container_width=True)
    st.caption("Domain attainment is a descriptive display of earned points, not a DARE risk-of-bias category.")

    metadata = {
        **metadata,
        "endpoint_key": endpoint_key,
        "endpoint_label": ENDPOINTS_BY_KEY[endpoint_key].label,
        "verification_design": verification_design or "",
    }
    record = build_export_record(metadata, scores, comments, pathway, selected_classification)
    csv_bytes = pd.DataFrame([record]).to_csv(index=False).encode("utf-8-sig")
    json_bytes = record_to_json(record).encode("utf-8")
    raw_name = "_".join(filter(None, [metadata.get("study_id", "study"), metadata.get("virus", "")])).strip()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("_") or "dare_arbo_assessment"

    st.markdown("#### Visual study-quality overview")
    presentation_colors = overview_color_controls()
    png_bytes = render_assessment_png(
        metadata,
        scores,
        pathway,
        classification,
        presentation_colors=presentation_colors,
        branding_logo=SYNERGY_LOGO_PATH.read_bytes() if SYNERGY_LOGO_PATH.exists() else None,
    )
    st.image(
        png_bytes,
        caption="DARE-Arbo endpoint pathway, domain scores, item attainment, and synthesis classification.",
        width="stretch",
    )
    st.caption("The maximum/partial/zero item colors describe points earned within each criterion; they are not official low/unclear/high risk-of-bias categories. PNG size: 1800 × 1950 px.")

    download_cols = st.columns([1, 1, 1, 1, 1])
    download_cols[0].download_button("Download PNG overview", png_bytes, f"{safe_name}_DARE-Arbo_overview.png", "image/png", use_container_width=True)
    download_cols[1].download_button("Download CSV", csv_bytes, f"{safe_name}_DARE-Arbo.csv", "text/csv", use_container_width=True)
    download_cols[2].download_button("Download JSON", json_bytes, f"{safe_name}_DARE-Arbo.json", "application/json", use_container_width=True)
    if download_cols[3].button("Save to Appraiser", use_container_width=True):
        saved = {**record, "saved_at": datetime.now(timezone.utc).isoformat()}
        st.session_state["registry"].append(saved)
        st.success("Assessment added to the in-session Appraiser registry.")
    download_cols[4].button(
        "Reset assessment",
        use_container_width=True,
        on_click=reset_single_assessment,
    )


def assessor_page() -> None:
    hero()
    st.markdown("Use one record per **study-virus-estimand**. Define the target population, biological endpoint, endpoint-defining assay, testing/verification population, observed numerator/denominator, and summary estimator before scoring.")
    endpoint_key, pathway, verification_design, synthesis_path_key = endpoint_controls()
    _, metadata = document_intake(
        pathway, endpoint_key, verification_design, synthesis_path_key
    )
    scores, comments = assessment_form(pathway, endpoint_key)
    single_results(metadata, endpoint_key, pathway, verification_design, scores, comments)


def import_records(uploaded_files: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for uploaded in uploaded_files:
        try:
            raw = uploaded.getvalue()
            if uploaded.name.lower().endswith(".json"):
                parsed = json.loads(raw.decode("utf-8-sig"))
                if isinstance(parsed, list):
                    records.extend(item for item in parsed if isinstance(item, dict))
                elif isinstance(parsed, dict):
                    records.append(parsed)
                else:
                    raise ValueError("JSON must contain an object or list of objects")
            elif uploaded.name.lower().endswith(".csv"):
                frame = pd.read_csv(uploaded)
                records.extend(frame.where(pd.notna(frame), None).to_dict(orient="records"))
            elif uploaded.name.lower().endswith((".xlsx", ".xls")):
                frame = pd.read_excel(uploaded, sheet_name=0)
                clean_frame = frame.where(pd.notna(frame), None)
                if any(str(column).strip() in {"Author/year", "Author, year"} for column in clean_frame.columns):
                    records.extend(workbook_rows_to_records(clean_frame))
                else:
                    records.extend(clean_frame.to_dict(orient="records"))
            else:
                raise ValueError("unsupported file type")
        except Exception as exc:
            errors.append(f"{uploaded.name}: {exc}")
    return records, errors


def workbook_rows_to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Migrate the estimand-aligned DARE workbook into Assessor registry records."""
    columns = list(frame.columns)

    def normalized_column(label: str) -> str | None:
        target = re.sub(r"[^a-z0-9]+", "", label.lower())
        return next(
            (column for column in columns if re.sub(r"[^a-z0-9]+", "", str(column).lower()) == target),
            None,
        )

    author_column = normalized_column("Author, year") or normalized_column("Author/year")
    score_columns = {
        criterion.code: next(
            (column for column in columns if str(column).startswith(f"{criterion.code} ")),
            None,
        )
        for criterion in CRITERIA
    }
    comment_columns = {
        criterion.code: (
            columns[columns.index(score_columns[criterion.code]) + 1]
            if score_columns[criterion.code] in columns and columns.index(score_columns[criterion.code]) + 1 < len(columns)
            else None
        )
        for criterion in CRITERIA
    }

    def endpoint_from_text(value: Any) -> str:
        text = str(value or "").lower()
        if "igm" in text:
            return "presumptive_igm"
        if "ns1" in text:
            return "presumptive_ns1"
        if any(term in text for term in ("neutral", "prnt", "vnt")):
            return "neutralizing_antibody"
        if any(term in text for term in ("pcr", "naat", "direct detection", "confirmed active", "isolation")):
            return "confirmed_active"
        return "prior_exposure"

    def design_from_text(value: Any) -> str:
        text = str(value or "").lower()
        if "reference-positive" in text or "reference positive" in text:
            return "ancillary_validation"
        if ("positive" in text and "negative" in text) and any(term in text for term in ("random", "two-phase", "two phase")):
            return "two_phase_validation"
        if "representative" in text and "positive" in text:
            return "representative_positive_subset"
        if "all" in text and "positive" in text:
            return "all_screen_positive"
        if "subset" in text and "positive" in text:
            return "subset_positive"
        if any(term in text for term in ("universal", "full population", "all participants", "all specimens")):
            return "full_population"
        if "representative" in text or "random sample" in text:
            return "representative_subsample"
        if any(term in text for term in ("selected", "convenience", "clinical subset")):
            return "selected_subsample"
        return "incomplete"

    migrated: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        study_id = str(row.get(author_column) or "").strip()
        if not study_id:
            continue
        endpoint_key = endpoint_from_text(row.get("Outcome classification"))
        design_key = design_from_text(row.get("Testing pathway"))
        pathway = ENDPOINTS_BY_KEY[endpoint_key].default_pathway
        record: dict[str, Any] = {
            "study_id": study_id,
            "target_population": row.get("Target population"),
            "target_population_category": row.get("Target-population category"),
            "sampling_frame_category": row.get("Sampling-frame category"),
            "endpoint_key": endpoint_key,
            "endpoint_label": ENDPOINTS_BY_KEY[endpoint_key].label,
            "testing_population": row.get("Testing pathway"),
            "verification_design": design_key,
            "endpoint_positive_n": row.get(normalized_column("Endpoint-positive count")),
            "endpoint_tested_n": row.get("Total evaluated"),
            "summary_estimand": row.get("Summary estimand") or SUMMARY_ESTIMATORS[-1],
            "measurement_pathway": pathway,
            "numerator_provenance": "observed",
            "guide_version": row.get(normalized_column("DARE guide version")) or GUIDE_VERSION,
            "workbook_audit_status": row.get(normalized_column("DARE audit status")),
            "migration_status": "Complete" if all(
                row.get(field) not in (None, "")
                for field in ("Target population", "Outcome classification", "Testing pathway", "Endpoint-positive count", "Total evaluated", "Summary estimand")
            ) else "Needs estimand-field completion",
        }
        for criterion in CRITERIA:
            score_column = score_columns[criterion.code]
            comment_column = comment_columns[criterion.code]
            record[f"{criterion.code}_score"] = row.get(score_column) if score_column else None
            record[f"{criterion.code}_comment"] = row.get(comment_column) if comment_column else ""
        migrated.append(record)
    return migrated


def registry_records_to_batch_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert Assessor export records into the structured Appraiser grid schema."""
    rows: list[dict[str, Any]] = []
    endpoint_keys = set(ENDPOINTS_BY_KEY)
    verification_labels = set(VERIFICATION_DESIGNS.values())

    def clean(value: Any) -> Any:
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        return value

    for record in records:
        row = empty_batch_row()
        row["Study ID"] = str(clean(record.get("study_id")) or "").strip()
        row["Citation or DOI"] = str(clean(record.get("citation")) or "").strip()
        row["Virus"] = str(clean(record.get("virus")) or "").strip()
        row["Target population"] = str(clean(record.get("target_population")) or "").strip()
        row["Target-population category"] = normalize_target_population_category(
            str(clean(record.get("target_population_category")) or ""),
            row["Target population"],
        ) or ""
        row["Achieved sample representation"] = str(
            clean(record.get("achieved_sample_representation")) or ACHIEVED_REPRESENTATION_OPTIONS[0]
        ).strip()
        row["Sampling frame / source population"] = str(
            clean(record.get("sampling_frame_description")) or ""
        ).strip()
        row["Sampling-frame category"] = normalize_sampling_frame_category(
            str(clean(record.get("sampling_frame_category")) or ""),
            row["Sampling frame / source population"],
        ) or ""
        row["Sampling/recruitment method"] = str(
            clean(record.get("sampling_recruitment_method")) or ""
        ).strip()
        row["Primary assay"] = str(clean(record.get("primary_assay")) or "").strip()
        row["Confirmatory assay"] = str(clean(record.get("confirmatory_assay")) or "").strip()
        row["Assay role"] = str(clean(record.get("assay_role")) or "").strip()
        row["Endpoint structure"] = str(clean(record.get("endpoint_structure")) or "").strip()
        row["Confirmation role"] = str(clean(record.get("confirmation_role")) or "").strip()
        row["Confirmation coverage"] = str(clean(record.get("confirmation_coverage")) or "").strip()
        row["Endpoint procedure consistency"] = str(
            clean(record.get("endpoint_procedure_consistency")) or ENDPOINT_CONSISTENCY_OPTIONS[0]
        ).strip()
        row["Endpoint-defining assay"] = str(clean(record.get("endpoint_defining_assay")) or "").strip()
        row["Testing/verification population"] = str(clean(record.get("testing_population")) or "").strip()
        summary_estimand = str(clean(record.get("summary_estimand")) or "").strip()
        if summary_estimand in SUMMARY_ESTIMATORS:
            row["Summary estimator"] = summary_estimand
        provenance = str(clean(record.get("numerator_provenance")) or "observed").strip()
        row["Numerator provenance"] = NUMERATOR_PROVENANCE.get(provenance, provenance if provenance in NUMERATOR_PROVENANCE.values() else NUMERATOR_PROVENANCE["unclear"])

        endpoint_key = str(clean(record.get("endpoint_key")) or "").strip()
        endpoint_label = str(
            clean(record.get("endpoint_label"))
            or clean(record.get("endpoint_classification"))
            or ""
        ).strip()
        if endpoint_label not in ENDPOINT_LABELS:
            endpoint_label = (
                ENDPOINTS_BY_KEY[endpoint_key].label
                if endpoint_key in endpoint_keys
                else ENDPOINTS_BY_KEY["other"].label
            )
        endpoint_key = ENDPOINT_LABELS.get(endpoint_label, "other")
        row["Endpoint"] = endpoint_label

        pathway_value = str(clean(record.get("measurement_pathway")) or "").strip()
        if pathway_value in PATHWAY_LABELS:
            row["Measurement pathway"] = PATHWAY_LABELS[pathway_value]
            pathway_key = pathway_value
        elif pathway_value in PATHWAYS_BY_LABEL:
            row["Measurement pathway"] = pathway_value
            pathway_key = PATHWAYS_BY_LABEL[pathway_value]
        else:
            pathway_key = ENDPOINTS_BY_KEY[endpoint_key].default_pathway
            row["Measurement pathway"] = PATHWAY_LABELS[pathway_key]

        verification_value = str(clean(record.get("verification_design")) or "").strip()
        if verification_value in VERIFICATION_DESIGNS:
            row["Verification design"] = VERIFICATION_DESIGNS[verification_value]
            verification_key = verification_value
        elif verification_value in verification_labels:
            row["Verification design"] = verification_value
            verification_key = next(key for key, label in VERIFICATION_DESIGNS.items() if label == verification_value)
        else:
            verification_key = "full_population"

        synthesis_path_key = str(clean(record.get("synthesis_path_key")) or "").strip()
        if synthesis_path_key not in SYNTHESIS_PATHS_BY_KEY:
            legacy_candidates = [
                path for path in SYNTHESIS_PATHS_BY_ENDPOINT.get(endpoint_key, ())
                if path.verification_design == verification_key
            ]
            synthesis_path_key = legacy_candidates[0].key if legacy_candidates else "serology_apparent"
        row["Synthesis path"] = SYNTHESIS_PATH_LABELS_BY_KEY[synthesis_path_key]
        for field_name, (label, _) in PATH_COUNT_FIELDS.items():
            row[label] = clean(record.get(field_name))

        for export_name, batch_name in (
            ("source_population_n", "Source population N"),
            ("planned_verification_n", "Planned verification N"),
            ("endpoint_tested_n", "Endpoint tested N"),
            ("endpoint_positive_n", "Endpoint positive n"),
            ("screen_positive_n", "Screen-positive n"),
            ("screen_negative_n", "Screen-negative n"),
            ("verification_negative_tested_n", "Verified screen-negative N"),
            ("verification_negative_positive_n", "Positive among verified screen-negative n"),
            ("reference_positive_n", "Reference-positive N"),
            ("reference_positive_detected_n", "Reference-positive detected n"),
            ("reference_negative_n", "Reference-negative N"),
            ("reference_negative_correct_n", "Reference-negative correctly negative n"),
        ):
            row[batch_name] = clean(record.get(export_name))

        for criterion in CRITERIA:
            value = clean(record.get(f"{criterion.code}_score", record.get(criterion.code)))
            if not criterion_is_applicable(criterion.code, pathway_key, endpoint_key):
                row[criterion.code] = None
            elif value in (None, ""):
                row[criterion.code] = None
            else:
                try:
                    row[criterion.code] = int(float(value))
                except (TypeError, ValueError):
                    row[criterion.code] = None
        rows.append(row)

    return pd.DataFrame(rows, columns=list(empty_batch_row()))


def generate_batch_from_registry() -> None:
    """Populate a fresh batch grid from the in-session Assessor registry."""
    records = st.session_state.get("registry", [])
    frame = registry_records_to_batch_frame(records)
    st.session_state["batch_grid"] = frame if not frame.empty else pd.DataFrame([empty_batch_row()])
    st.session_state["batch_editor_version"] += 1
    st.session_state["registry_batch_loaded_count"] = len(frame)


def batch_editor() -> pd.DataFrame:
    st.subheader("Rapid structured batch entry")
    row_notice = st.session_state.pop("batch_row_notice", "")
    if row_notice:
        st.success(row_notice)
    st.caption(
        "Use one row per study-virus-estimand. Edit cells directly in the grid, or select "
        "one or more rows below the grid to open a focused editor or delete them. Complete "
        "only the count columns required by the selected synthesis path."
    )
    column_config: dict[str, Any] = {
        "Study ID": st.column_config.TextColumn(required=True),
        "Citation or DOI": st.column_config.TextColumn(),
        "Virus": st.column_config.TextColumn(),
        "Target population": st.column_config.TextColumn(),
        "Target-population category": st.column_config.SelectboxColumn(
            options=[""] + TARGET_POPULATION_CATEGORY_OPTIONS,
            help="Controlled target for endpoint-specific Q1 and Q3 judgment.",
        ),
        "Achieved sample representation": st.column_config.SelectboxColumn(
            options=list(ACHIEVED_REPRESENTATION_OPTIONS),
            help="Structured Q1 fallback judged within the declared target.",
        ),
        "Sampling frame / source population": st.column_config.TextColumn(
            help="Actual source frame for Q3; the target label alone is insufficient."
        ),
        "Sampling-frame category": st.column_config.SelectboxColumn(
            options=[""] + SAMPLING_FRAME_CATEGORY_OPTIONS,
            help="Controlled operational source-frame class from the 2026-09-10 Design Library.",
        ),
        "Sampling/recruitment method": st.column_config.SelectboxColumn(
            options=[""] + list(Q4_USER_METHOD_SCORES),
            help="Dictionary fallback for Q4 when document evidence is unavailable.",
        ),
        "Primary assay": st.column_config.TextColumn(),
        "Confirmatory assay": st.column_config.TextColumn(),
        "Assay role": st.column_config.TextColumn(disabled=True),
        "Endpoint structure": st.column_config.TextColumn(disabled=True),
        "Confirmation role": st.column_config.TextColumn(disabled=True),
        "Confirmation coverage": st.column_config.TextColumn(disabled=True),
        "Assay validation / QC": st.column_config.SelectboxColumn(
            options=["Not reported / unclear", "Validated, standardized, or appropriate controls reported"],
            help="Structured Q6b input.",
        ),
        "Endpoint procedure consistency": st.column_config.SelectboxColumn(
            options=list(ENDPOINT_CONSISTENCY_OPTIONS),
            help="Structured Q7 input.",
        ),
        "Assay-performance correction": st.column_config.SelectboxColumn(
            options=["Not reported / no explicit correction", "Explicit sensitivity/specificity correction"],
            help="Q10 applies to prior-exposure primary-antibody, IgM, and NS1 endpoints; it is N/A for the other implemented endpoint categories.",
        ),
        "Synthesis path": st.column_config.SelectboxColumn(options=list(SYNTHESIS_PATH_LABELS), required=True),
        "Endpoint": st.column_config.SelectboxColumn(options=list(ENDPOINT_LABELS), required=True),
        "Endpoint-defining assay": st.column_config.TextColumn(),
        "Testing/verification population": st.column_config.TextColumn(),
        "Summary estimator": st.column_config.SelectboxColumn(options=list(SUMMARY_ESTIMATORS)),
        "Numerator provenance": st.column_config.SelectboxColumn(options=list(NUMERATOR_PROVENANCE.values())),
        "Measurement pathway": st.column_config.SelectboxColumn(options=list(PATHWAYS_BY_LABEL), required=True),
        "Verification design": st.column_config.SelectboxColumn(options=list(VERIFICATION_DESIGNS.values())),
    }
    for _, (count_label, help_text) in PATH_COUNT_FIELDS.items():
        column_config[count_label] = st.column_config.NumberColumn(
            help=help_text, min_value=0, step=1, format="%d"
        )
    for count_column in (
        "Source population N",
        "Planned verification N",
        "Endpoint tested N",
        "Endpoint positive n",
        "Screen-positive n",
        "Screen-negative n",
        "Verified screen-negative N",
        "Positive among verified screen-negative n",
        "Reference-positive N",
        "Reference-positive detected n",
        "Reference-negative N",
        "Reference-negative correctly negative n",
    ):
        column_config[count_column] = st.column_config.NumberColumn(
            min_value=0, step=1, format="%d"
        )
    for criterion in CRITERIA:
        column_config[criterion.code] = st.column_config.NumberColumn(
            label=f"{criterion.code} (0-{criterion.maximum})",
            min_value=0,
            max_value=criterion.maximum,
            step=1,
        )
    editor_column_order = [
        "Study ID", "Citation or DOI", "Virus", "Target population", "Target-population category",
        "Achieved sample representation", "Sampling frame / source population", "Sampling-frame category", "Sampling/recruitment method",
        "Primary assay", "Confirmatory assay", "Assay validation / QC",
        "Endpoint procedure consistency", "Assay-performance correction", "Synthesis path",
        *[label for label, _ in PATH_COUNT_FIELDS.values()],
        "Numerator provenance", *[criterion.code for criterion in CRITERIA],
    ]
    row_edit_index = st.session_state.get("batch_row_edit_index")
    pending_delete = list(st.session_state.get("batch_rows_pending_delete", []))
    row_action_active = row_edit_index is not None or bool(pending_delete)
    edited = st.data_editor(
        st.session_state["batch_grid"],
        column_config=column_config,
        column_order=editor_column_order,
        num_rows="dynamic",
        disabled=row_action_active,
        use_container_width=True,
        hide_index=True,
        key=f"batch_editor_widget_{st.session_state['batch_editor_version']}",
    )
    st.session_state["batch_grid"] = edited.reset_index(drop=True)

    batch_snapshot = st.session_state["batch_grid"]

    def describe_row(position: int) -> str:
        row = batch_snapshot.iloc[position]
        study_value = row.get("Study ID")
        virus_value = row.get("Virus")
        study_id = (
            "" if study_value is None or pd.isna(study_value) else str(study_value).strip()
        ) or "Untitled study"
        virus = "" if virus_value is None or pd.isna(virus_value) else str(virus_value).strip()
        suffix = f" · {virus}" if virus else ""
        return f"Row {position + 1} — {study_id}{suffix}"

    row_positions = list(range(len(batch_snapshot)))
    row_labels = {position: describe_row(position) for position in row_positions}
    selected_rows = st.multiselect(
        "Select row(s) to manage",
        row_positions,
        format_func=lambda position: row_labels.get(position, f"Row {position + 1}"),
        disabled=row_action_active,
        key=f"batch_row_selection_{st.session_state['batch_editor_version']}",
        help="Select exactly one row to open it in the focused editor, or select one or more rows for deletion.",
    )
    action_cols = st.columns([1, 1, 2])
    if action_cols[0].button(
        "Edit selected row",
        disabled=row_action_active or len(selected_rows) != 1,
        use_container_width=True,
    ):
        st.session_state["batch_row_edit_index"] = selected_rows[0]
        st.session_state["batch_row_edit_version"] += 1
        st.session_state["batch_editor_version"] += 1
        st.rerun()
    if action_cols[1].button(
        "Delete selected row(s)",
        disabled=row_action_active or not selected_rows,
        use_container_width=True,
    ):
        st.session_state["batch_rows_pending_delete"] = selected_rows
        st.session_state["batch_editor_version"] += 1
        st.rerun()
    action_cols[2].caption(
        "Grid changes are retained in this session and immediately update the batch outputs."
    )

    if pending_delete:
        valid_positions = sorted(
            {position for position in pending_delete if 0 <= position < len(st.session_state["batch_grid"])}
        )
        labels = [row_labels[position] for position in valid_positions]
        st.warning(
            f"Delete {len(valid_positions)} selected row(s)? "
            + "; ".join(labels)
            + ". This changes the batch grid but does not delete the original Assessor registry records."
        )
        confirm_cols = st.columns([1, 1, 2])
        if confirm_cols[0].button(
            "Confirm deletion",
            type="primary",
            use_container_width=True,
        ):
            remaining = st.session_state["batch_grid"].drop(index=valid_positions).reset_index(drop=True)
            st.session_state["batch_grid"] = (
                remaining if not remaining.empty else pd.DataFrame([empty_batch_row()])
            )
            st.session_state["batch_rows_pending_delete"] = []
            st.session_state["batch_row_notice"] = (
                f"Deleted {len(valid_positions)} selected batch row(s)."
            )
            st.session_state["registry_batch_loaded_count"] = 0
            st.session_state["batch_editor_version"] += 1
            st.rerun()
        if confirm_cols[1].button("Cancel deletion", use_container_width=True):
            st.session_state["batch_rows_pending_delete"] = []
            st.session_state["batch_editor_version"] += 1
            st.rerun()

    row_edit_index = st.session_state.get("batch_row_edit_index")
    if row_edit_index is not None:
        if not 0 <= row_edit_index < len(st.session_state["batch_grid"]):
            st.session_state["batch_row_edit_index"] = None
            st.warning("The selected row is no longer available. Select another row to edit.")
        else:
            selected_label = row_labels[row_edit_index]
            with st.expander(f"Focused row editor · {selected_label}", expanded=True):
                st.caption(
                    "Modify the selected record below, then save it back to the batch grid. "
                    "Cancel leaves the existing row unchanged."
                )
                focused_row = st.data_editor(
                    st.session_state["batch_grid"].iloc[[row_edit_index]].reset_index(drop=True),
                    column_config=column_config,
                    column_order=editor_column_order,
                    num_rows="fixed",
                    use_container_width=True,
                    hide_index=True,
                    key=f"batch_focused_editor_{st.session_state['batch_row_edit_version']}",
                )
                edit_cols = st.columns([1, 1, 2])
                if edit_cols[0].button(
                    "Save row changes",
                    type="primary",
                    use_container_width=True,
                ):
                    updated_grid = st.session_state["batch_grid"].copy()
                    for column in updated_grid.columns:
                        if column in focused_row.columns:
                            updated_grid.at[row_edit_index, column] = focused_row.iloc[0][column]
                    st.session_state["batch_grid"] = updated_grid
                    st.session_state["batch_row_edit_index"] = None
                    st.session_state["batch_row_notice"] = (
                        f"Saved changes to {selected_label}."
                    )
                    st.session_state["registry_batch_loaded_count"] = 0
                    st.session_state["batch_editor_version"] += 1
                    st.rerun()
                if edit_cols[1].button("Cancel row editing", use_container_width=True):
                    st.session_state["batch_row_edit_index"] = None
                    st.session_state["batch_editor_version"] += 1
                    st.rerun()

    return st.session_state["batch_grid"]


def batch_pdf_intake() -> None:
    with st.expander("Batch PDF intake and draft evidence scoring", expanded=False):
        st.caption("Upload multiple articles that share an endpoint configuration. Draft scores are appended to the batch grid and must be reviewer-verified.")
        synthesis_path_label = st.selectbox(
            "Default synthesis endpoint and testing strategy",
            list(SYNTHESIS_PATH_LABELS),
            key="batch_pdf_synthesis_path",
        )
        synthesis_path = SYNTHESIS_PATHS_BY_KEY[SYNTHESIS_PATH_LABELS[synthesis_path_label]]
        endpoint_key = synthesis_path.endpoint_key
        pathway = ENDPOINTS_BY_KEY[endpoint_key].default_pathway
        uploaded_pdfs = st.file_uploader(
            "Upload article PDFs",
            type=["pdf"],
            accept_multiple_files=True,
            key="batch_pdf_uploads",
        )
        if uploaded_pdfs and st.button("Extract PDFs and append draft rows", type="primary"):
            existing_hashes = set(st.session_state["batch_pdf_hashes"])
            new_rows: list[dict[str, Any]] = []
            evidence_rows: list[dict[str, Any]] = []
            errors: list[str] = []
            for uploaded in uploaded_pdfs:
                pdf_bytes = uploaded.getvalue()
                file_hash = hashlib.sha256(pdf_bytes).hexdigest()
                if file_hash in existing_hashes:
                    continue
                try:
                    extracted = extract_pdf_text(pdf_bytes)
                    row = empty_batch_row()
                    row["Study ID"] = Path(uploaded.name).stem
                    row["Synthesis path"] = synthesis_path_label
                    row["Endpoint"] = ENDPOINTS_BY_KEY[endpoint_key].label
                    row["Measurement pathway"] = PATHWAY_LABELS[pathway]
                    if not extracted["scanned_or_empty"]:
                        draft = suggest_scores(
                            extracted["pages"], pathway, endpoint_key,
                            synthesis_path.verification_design,
                        )
                        for criterion in CRITERIA:
                            if criterion_is_applicable(criterion.code, pathway, endpoint_key):
                                row[criterion.code] = draft[criterion.code].score
                    new_rows.append(row)
                    evidence_rows.append(
                        {
                            "Document": uploaded.name,
                            "Pages": extracted["page_count"],
                            "Extracted characters": extracted["character_count"],
                            "Status": "Needs OCR/manual entry" if extracted["scanned_or_empty"] else "Draft suggestions added",
                            "SHA-256": file_hash,
                        }
                    )
                    existing_hashes.add(file_hash)
                except Exception as exc:
                    errors.append(f"{uploaded.name}: {exc}")
            if new_rows:
                current = st.session_state["batch_grid"]
                if len(current) == 1 and not str(current.iloc[0].get("Study ID") or "").strip():
                    current = current.iloc[0:0]
                st.session_state["batch_grid"] = pd.concat(
                    [current, pd.DataFrame(new_rows)], ignore_index=True
                )
                st.session_state["batch_editor_version"] += 1
                st.session_state["batch_pdf_hashes"] = list(existing_hashes)
                st.session_state["batch_pdf_evidence"].extend(evidence_rows)
                st.success(f"Added {len(new_rows)} PDF-derived draft row(s). Verify every score in the grid.")
            elif not errors:
                st.info("No new PDFs were added; duplicate files are ignored by SHA-256 hash.")
            for error in errors:
                st.error(error)

        if st.session_state["batch_pdf_evidence"]:
            evidence_frame = pd.DataFrame(st.session_state["batch_pdf_evidence"])
            st.dataframe(
                evidence_frame.drop(columns=["SHA-256"]),
                use_container_width=True,
                hide_index=True,
            )
            st.download_button(
                "Download PDF intake audit",
                evidence_frame.to_csv(index=False).encode("utf-8-sig"),
                "DARE-Arbo_PDF_intake_audit.csv",
                "text/csv",
            )


def calculate_batch(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    details: list[dict[str, Any]] = []
    long_items: list[dict[str, Any]] = []
    reverse_design = {label: key for key, label in VERIFICATION_DESIGNS.items()}
    for _, row in frame.iterrows():
        study_id = str(row.get("Study ID") or "").strip()
        if not study_id:
            continue
        synthesis_path_key = SYNTHESIS_PATH_LABELS.get(row.get("Synthesis path"), "")
        synthesis_path = SYNTHESIS_PATHS_BY_KEY.get(synthesis_path_key)
        if synthesis_path:
            endpoint_key = synthesis_path.endpoint_key
            pathway = ENDPOINTS_BY_KEY[endpoint_key].default_pathway
            design_key = synthesis_path.verification_design
            path_profile = assay_path_profile(synthesis_path_key)
        else:
            endpoint_key = ENDPOINT_LABELS.get(row.get("Endpoint"), "other")
            pathway = PATHWAYS_BY_LABEL.get(row.get("Measurement pathway"), ENDPOINTS_BY_KEY[endpoint_key].default_pathway)
            design_key = reverse_design.get(row.get("Verification design"))
            path_profile = {
                "endpoint_structure": str(row.get("Endpoint structure") or ""),
                "primary_assay_role": str(row.get("Assay role") or ""),
                "confirmation_role": str(row.get("Confirmation role") or ""),
                "confirmation_coverage": str(row.get("Confirmation coverage") or ""),
            }
        raw_scores = {criterion.code: row.get(criterion.code) for criterion in CRITERIA}
        scores = {
            code: None if pd.isna(value) else int(value)
            for code, value in raw_scores.items()
        }
        q4_fallback_method = str(row.get("Sampling/recruitment method") or "").strip()
        q4_fallback_score = Q4_USER_METHOD_SCORES.get(q4_fallback_method)
        if scores.get("Q4") is None and q4_fallback_score is not None:
            scores["Q4"] = q4_fallback_score
        selected_classification = classify_synthesis(endpoint_key, design_key)
        path_counts_for_suggestions: dict[str, Any] | None = None
        if synthesis_path:
            path_counts = {
                field_name: row.get(label)
                for field_name, (label, _) in PATH_COUNT_FIELDS.items()
            }
            path_counts["numerator_provenance"] = {
                label: key for key, label in NUMERATOR_PROVENANCE.items()
            }.get(row.get("Numerator provenance"), "unclear")
            path_counts_for_suggestions = path_counts
            path_audit = audit_synthesis_path_counts(synthesis_path_key, path_counts)
            effective_path = SYNTHESIS_PATHS_BY_KEY[path_audit["effective_synthesis_path_key"]]
            count_audit = {
                **path_audit,
                "complete_counts": path_audit["estimand_ready"],
                "effective_verification_design": effective_path.verification_design,
                "effective_tier": effective_path.summary_estimator,
                "endpoint_test_positivity": (
                    path_audit.get("verification_subset_positivity")
                    if path_audit.get("verification_subset_positivity") is not None
                    else path_audit["estimate"]
                ),
                "synthesis_denominator_n": path_audit["denominator_n"],
                "prevalence": path_audit["estimate"],
                "screen_negative_n": path_audit.get("retested_negative_stratum_n"),
            }
            classification = classify_synthesis(endpoint_key, effective_path.verification_design)
        else:
            count_audit = audit_endpoint_counts(
                endpoint_key, design_key, row.get("Source population N"),
                row.get("Endpoint tested N"), row.get("Endpoint positive n"),
                row.get("Screen-positive n"), row.get("Planned verification N"),
                row.get("Screen-negative n"), row.get("Verified screen-negative N"),
                row.get("Positive among verified screen-negative n"),
                {label: key for key, label in NUMERATOR_PROVENANCE.items()}.get(row.get("Numerator provenance"), "unclear"),
                row.get("Reference-positive N"), row.get("Reference-positive detected n"),
                row.get("Reference-negative N"), row.get("Reference-negative correctly negative n"),
            )
            classification = classify_synthesis(endpoint_key, count_audit["effective_verification_design"])
        structured_drafts = suggest_scores(
            [], pathway, endpoint_key, design_key,
            synthesis_path_key=synthesis_path_key or None,
            counts=path_counts_for_suggestions,
            target_population=str(row.get("Target population") or ""),
            target_population_category=str(row.get("Target-population category") or ""),
            achieved_sample_representation=str(row.get("Achieved sample representation") or ""),
            sampling_frame_description=str(row.get("Sampling frame / source population") or ""),
            sampling_frame_category=str(row.get("Sampling-frame category") or ""),
            sampling_recruitment_method=q4_fallback_method,
            endpoint_procedure_consistency=str(row.get("Endpoint procedure consistency") or ""),
            primary_assay=str(row.get("Primary assay") or ""),
            confirmatory_assay=str(row.get("Confirmatory assay") or ""),
        )
        for code in ("Q1", "Q3", "Q6a", "Q6c", "Q7", "Q8"):
            if scores.get(code) is None and structured_drafts[code].score is not None:
                scores[code] = structured_drafts[code].score
        if scores.get("Q6b") is None:
            scores["Q6b"] = (
                1 if str(row.get("Assay validation / QC") or "").startswith("Validated") else 0
            )
        if scores.get("Q10") is None and criterion_is_applicable("Q10", pathway, endpoint_key):
            scores["Q10"] = (
                1 if str(row.get("Assay-performance correction") or "").startswith("Explicit") else 0
            )
        if scores.get("Q5") is None and count_audit.get("q5_suggested_score") is not None:
            scores["Q5"] = count_audit["q5_suggested_score"]
        result = score_assessment(scores, pathway, endpoint_key)
        selection_flags = [
            code for code in ("Q1", "Q3", "Q4", "Q5") if result["scores"].get(code) == 0
        ]
        measurement_flags = [
            code for code in ("Q6a", "Q6b", "Q6c", "Q7") if result["scores"].get(code) == 0
        ]
        normalized_batch_target = normalize_target_population_category(
            str(row.get("Target-population category") or ""),
            str(row.get("Target population") or ""),
        ) or ""
        normalized_batch_frame = normalize_sampling_frame_category(
            str(row.get("Sampling-frame category") or ""),
            str(row.get("Sampling frame / source population") or ""),
        ) or ""
        batch_frame_alignment = assess_target_frame_alignment(
            normalized_batch_target, normalized_batch_frame
        )
        detail = {
            "Study ID": study_id,
            "Citation or DOI": row.get("Citation or DOI", ""),
            "Virus": row.get("Virus", ""),
            "Target population": row.get("Target population", ""),
            "Target-population category": normalized_batch_target,
            "Achieved sample representation": row.get("Achieved sample representation", ""),
            "Sampling frame / source population": row.get("Sampling frame / source population", ""),
            "Sampling-frame category": normalized_batch_frame,
            "Target-frame matrix status": batch_frame_alignment["status"],
            "Target-frame matrix rationale": batch_frame_alignment["rationale"],
            "Sampling/recruitment method": q4_fallback_method,
            "Endpoint": ENDPOINTS_BY_KEY[endpoint_key].label,
            "Primary assay": row.get("Primary assay", ""),
            "Confirmatory assay": row.get("Confirmatory assay", ""),
            "Endpoint structure": path_profile.get("endpoint_structure", ""),
            "Assay role": path_profile.get("primary_assay_role") or classification["assay_role"],
            "Confirmation role": path_profile.get("confirmation_role", ""),
            "Confirmation coverage": path_profile.get("confirmation_coverage", ""),
            "Endpoint procedure consistency": row.get("Endpoint procedure consistency", ""),
            "Testing strategy": synthesis_path.testing_strategy if synthesis_path else row.get("Testing/verification population", ""),
            "Assay type": synthesis_path.assay_type if synthesis_path else row.get("Endpoint-defining assay", ""),
            "Summary estimator": count_audit.get("summary_estimator") if synthesis_path else row.get("Summary estimator", ""),
            "Numerator provenance": row.get("Numerator provenance", ""),
            "Measurement pathway": pathway,
            "Selected synthesis tier": synthesis_path.summary_estimator if synthesis_path else selected_classification["tier"],
            "Synthesis tier": count_audit["effective_tier"] if synthesis_path else classification["tier"],
            "Effective synthesis tier": count_audit["effective_tier"],
            "Synthesis eligibility": classification["eligibility"],
            "Numerator rule": synthesis_path.numerator if synthesis_path else classification["numerator_rule"],
            "Denominator rule": synthesis_path.denominator if synthesis_path else classification["denominator_rule"],
            "False-positive rule": synthesis_path.false_positives if synthesis_path else "",
            "False positives n": count_audit.get("false_positives_n"),
            "False negatives n": count_audit.get("false_negatives_n"),
            "Total recruited": count_audit.get("total_recruited_n"),
            "Total tested": count_audit.get("primary_tested_n"),
            "Source population N": count_audit["source_population_n"],
            "Planned verification N": count_audit["planned_verification_n"],
            "Endpoint tested N": count_audit["endpoint_tested_n"],
            "Endpoint positive n": count_audit["endpoint_positive_n"],
            "Screen-positive n": count_audit["screen_positive_n"],
            "Screen-negative n": count_audit["screen_negative_n"],
            "Endpoint-test positivity": count_audit["endpoint_test_positivity"],
            "Verification-subset positivity": count_audit.get("verification_subset_positivity"),
            "Synthesis denominator N": count_audit["synthesis_denominator_n"],
            "Synthesis prevalence": count_audit["prevalence"],
            "Tested / recruited completion": count_audit.get("recruitment_testing_rate"),
            "Staged planned-pathway completion": count_audit.get("planned_pathway_completion_rate"),
            "Q5 limiting completion": count_audit["completion_rate"],
            "Planned-pathway completion": count_audit["completion_rate"],
            "Q5 completion basis": count_audit.get("q5_basis", ""),
            "Q5 count-based suggestion": count_audit["q5_suggested_score"],
            "Estimator ready": count_audit["estimand_ready"],
            "Missing estimator quantities": " | ".join(count_audit["missing_required_fields"]),
            "Validation sensitivity": count_audit["sensitivity"],
            "Validation specificity": count_audit["specificity"],
            "Counts valid": count_audit["valid"],
            "Counts complete": count_audit["complete_counts"],
            "Count audit": " | ".join(count_audit["issues"]),
            "DARE total": result["total"],
            "Applicable maximum": result["maximum"],
            "Complete": result["complete"],
            "Study design score": sum(result["domains"][domain]["score"] for domain in DOMAINS[:2]),
            "Study design maximum": sum(result["domains"][domain]["maximum"] for domain in DOMAINS[:2]),
            "Assay score": result["domains"]["Laboratory assay"]["score"],
            "Assay maximum": result["domains"]["Laboratory assay"]["maximum"],
            "Reporting score": result["domains"]["Reporting outcome"]["score"],
            "Reporting maximum": result["domains"]["Reporting outcome"]["maximum"],
            "Selection-domain zero items": ", ".join(selection_flags),
            "Measurement-domain zero items": ", ".join(measurement_flags),
            "Validation notes": " | ".join(result["validation"]),
        }
        for criterion in CRITERIA:
            detail[criterion.code] = result["scores"][criterion.code]
            if criterion_is_applicable(criterion.code, pathway, endpoint_key):
                value = result["scores"][criterion.code]
                long_items.append(
                    {
                        "Study ID": study_id,
                        "Criterion": criterion.code,
                        "Score": value,
                        "Maximum": criterion.maximum,
                        "Attainment": None if value is None else 100 * value / criterion.maximum,
                    }
                )
        details.append(detail)
    return pd.DataFrame(details), pd.DataFrame(long_items)


def appraiser_page() -> None:
    hero()
    st.markdown("Combine assessments, enter batches, inspect domain patterns, and export synthesis-ready tables. DARE scores remain endpoint-specific and continuous.")

    st.subheader("Import completed Assessor outputs or DARE workbook")
    import_files = st.file_uploader(
        "Upload one or more DARE-Arbo CSV, JSON, or XLSX files",
        type=["csv", "json", "xlsx", "xls"],
        accept_multiple_files=True,
        key="appraiser_imports",
    )
    if import_files and st.button("Add imported records to registry"):
        records, errors = import_records(import_files)
        st.session_state["registry"].extend(records)
        if records:
            st.success(f"Added {len(records)} record(s).")
        for error in errors:
            st.error(error)

    registry = st.session_state.get("registry", [])
    if registry:
        with st.expander(f"In-session Assessor registry ({len(registry)} records)", expanded=True):
            registry_df = pd.DataFrame(registry)
            preferred = [column for column in ["study_id", "virus", "target_population_category", "sampling_frame_category", "endpoint_label", "synthesis_tier", "dare_total", "dare_applicable_maximum", "dare_complete"] if column in registry_df.columns]
            st.dataframe(registry_df[preferred] if preferred else registry_df, use_container_width=True, hide_index=True)
            registry_cols = st.columns([1, 1, 1.45, 1])
            registry_cols[0].download_button("Download registry CSV", registry_df.to_csv(index=False).encode("utf-8-sig"), "DARE-Arbo_registry.csv", "text/csv", use_container_width=True)
            registry_cols[1].download_button("Download registry JSON", json.dumps(registry, ensure_ascii=False, indent=2, default=str).encode("utf-8"), "DARE-Arbo_registry.json", "application/json", use_container_width=True)
            registry_cols[2].button(
                "Generate batch appraisal",
                on_click=generate_batch_from_registry,
                help="Replace the current batch grid with all records in this registry and calculate the outputs below.",
                use_container_width=True,
            )
            if registry_cols[3].button("Clear registry", use_container_width=True):
                st.session_state["registry"] = []
                st.rerun()

    loaded_count = st.session_state.get("registry_batch_loaded_count", 0)
    if loaded_count:
        st.success(f"Batch grid populated from {loaded_count} in-session Assessor record(s). Review the rows, then use the generated outputs below.")

    batch_pdf_intake()
    batch = batch_editor()
    details, long_items = calculate_batch(batch)
    st.subheader("Batch appraisal outputs")
    if details.empty:
        st.info("Add at least one Study ID to calculate batch outputs.")
        return

    st.dataframe(details, use_container_width=True, hide_index=True)
    incomplete_count = int((~details["Complete"]).sum())
    if incomplete_count:
        st.warning(f"{incomplete_count} batch row(s) are incomplete. Missing applicable items contribute zero to the displayed draft totals.")

    if not long_items.empty:
        heatmap = (
            alt.Chart(long_items)
            .mark_rect(stroke="white", strokeWidth=1)
            .encode(
                x=alt.X("Criterion:N", sort=[criterion.code for criterion in CRITERIA], title=None),
                y=alt.Y("Study ID:N", title=None),
                color=alt.Color(
                    "Attainment:Q",
                    scale=alt.Scale(domain=[0, 50, 100], range=["#e45b16", "#f2d9c8", "#18864b"]),
                    title="Point attainment (%)",
                ),
                tooltip=["Study ID", "Criterion", "Score", "Maximum"],
            )
            .properties(height=max(130, 36 * details.shape[0]))
        )
        st.altair_chart(heatmap, use_container_width=True)
        st.caption("Colors show descriptive point attainment within each criterion. They are not DARE low/unclear/high risk categories.")

    export_cols = st.columns(3)
    export_cols[0].download_button("Download batch details", details.to_csv(index=False).encode("utf-8-sig"), "DARE-Arbo_batch_details.csv", "text/csv", use_container_width=True)
    summary_columns = [
        "Study ID", "Citation or DOI", "Virus", "Target population", "Target-population category",
        "Sampling frame / source population", "Sampling-frame category", "Target-frame matrix status", "Target-frame matrix rationale", "Sampling/recruitment method", "Endpoint",
        "Endpoint structure", "Primary assay", "Confirmatory assay", "Assay role", "Confirmation role", "Confirmation coverage", "Testing strategy", "Assay type",
        "Summary estimator", "Numerator provenance",
        "Synthesis tier", "Effective synthesis tier", "Synthesis eligibility",
        "Numerator rule", "Denominator rule", "False-positive rule",
        "Total recruited", "Total tested", "Tested / recruited completion",
        "Planned verification N", "Endpoint tested N", "Endpoint positive n", "Endpoint-test positivity",
        "Synthesis denominator N", "Synthesis prevalence",
        "False positives n", "False negatives n",
        "Staged planned-pathway completion", "Q5 limiting completion", "Q5 completion basis",
        "Planned-pathway completion", "Estimator ready", "Missing estimator quantities",
        "Validation sensitivity", "Validation specificity",
        "Counts valid", "Counts complete", "DARE total", "Applicable maximum",
        "Complete", "Selection-domain zero items", "Measurement-domain zero items",
    ]
    export_cols[1].download_button("Download synthesis summary", details[summary_columns].to_csv(index=False).encode("utf-8-sig"), "DARE-Arbo_synthesis_summary.csv", "text/csv", use_container_width=True)
    export_cols[2].download_button("Download batch JSON", details.to_json(orient="records", indent=2, force_ascii=False).encode("utf-8"), "DARE-Arbo_batch_details.json", "application/json", use_container_width=True)


def study_designer_page() -> None:
    hero()
    st.markdown(
        "Design one study-virus-estimand pathway that explicitly connects population coverage, assay architecture, estimator-ready counts, and reproducible reporting. This planner supports protocol development; it is not a completed risk-of-bias appraisal."
    )

    st.subheader("1. Define the surveillance objective")
    objective_left, objective_right = st.columns([1, 1.15])
    with objective_left:
        study_title = st.text_input(
            "Planned study title",
            key="designer_study_title",
            placeholder="e.g., Community DENV seroprevalence survey",
        )
        country_or_setting = st.text_input(
            "Country or study setting",
            key="designer_country_or_setting",
            placeholder="e.g., Ghana; Greater Accra sentinel network",
        )
        design_context = st.selectbox(
            "Design context",
            ("Surveillance study", "Other study design"),
            key="designer_design_context",
            help="This controls whether the downloaded report uses the surveillance-specific or general DARE-Arbo study-design title.",
        )
        virus_coverage = st.selectbox(
            "Virus coverage",
            ("Single virus", "Multiplex / multiple viruses"),
            key="designer_virus_coverage",
            help="Multiplex plans retain a distinct study-virus-estimand record and result for every virus target.",
        )
        if virus_coverage == "Single virus":
            virus = st.text_input(
                "Arbovirus",
                key="designer_virus",
                placeholder="e.g., DENV",
            )
            viruses = [virus.strip()] if virus.strip() else []
        else:
            selected_viruses = st.multiselect(
                "Arboviruses included",
                DESIGNER_ARBOVIRUS_OPTIONS,
                key="designer_viruses",
                placeholder="Select all virus targets in the panel",
            )
            other_viruses = st.text_input(
                "Other arbovirus target(s)",
                key="designer_other_viruses",
                placeholder="Comma-separated names not listed above",
            )
            viruses = list(dict.fromkeys(
                selected_viruses
                + [value.strip() for value in other_viruses.split(",") if value.strip()]
            ))
            virus = "; ".join(viruses)
        target_population = st.text_area(
            "Target population",
            key="designer_target_population",
            placeholder="Define geography, age, clinical/exposure status, and intended inference.",
            height=105,
        )
        target_population_category = st.selectbox(
            "Target-population category",
            options=[""] + TARGET_POPULATION_CATEGORY_OPTIONS,
            format_func=lambda value: "Classify the planned target" if not value else value,
            key="designer_target_population_category",
            help="The Designer judges representation and frame coverage against this explicit study-virus-estimand target, not against a national population by default.",
        )
        surveillance_mode = st.selectbox(
            "Surveillance mode",
            (
                "Active surveillance",
                "Passive surveillance",
                "Hybrid active + passive surveillance",
            ),
            key="designer_surveillance_mode",
            help="Hybrid designs can retain separate active/passive estimates, an explicitly adjusted combined estimator, or both.",
        )
        surveillance_architecture = st.selectbox(
            "Surveillance architecture",
            (
                "Community cross-sectional survey",
                "Repeated cross-sectional surveillance",
                "Sentinel facility surveillance",
                "National laboratory surveillance",
                "Prospective cohort surveillance",
                "Outbreak investigation",
                "One Health surveillance",
                "Assay-validation study",
            ),
            key="designer_surveillance_architecture",
        )
        active_stream_definition = ""
        passive_stream_definition = ""
        stream_estimator_mode = surveillance_mode
        stream_integration_plan = True
        if surveillance_mode == "Hybrid active + passive surveillance":
            active_stream_definition = st.text_area(
                "Active surveillance source/population",
                key="designer_active_stream",
                placeholder="e.g., scheduled community recruitment and specimen collection",
                height=80,
            )
            passive_stream_definition = st.text_area(
                "Passive surveillance source/population",
                key="designer_passive_stream",
                placeholder="e.g., routine presentation to sentinel health facilities",
                height=80,
            )
            stream_estimator_mode = st.selectbox(
                "Active/passive analysis",
                (
                    "Separate active and passive estimates",
                    "Combined estimator with source/stream adjustment",
                    "Separate and combined estimates",
                ),
                key="designer_stream_estimator_mode",
            )
            stream_integration_plan = st.checkbox(
                "Prespecify stream-specific eligibility, denominators, overlap handling, and any weighting/integration",
                key="designer_stream_integration_plan",
            )
    with objective_right:
        endpoint_design = st.selectbox(
            "Endpoint design",
            ("Single endpoint", "Mixed endpoints"),
            key="designer_endpoint_design",
            help="Every selected biological endpoint/testing strategy remains a separate estimand with its own assay, numerator, denominator, and estimator.",
        )
        endpoint_key = st.selectbox(
            "Primary synthesis endpoint",
            list(SYNTHESIS_ENDPOINT_ORDER),
            format_func=lambda key: ENDPOINTS_BY_KEY[key].label,
            key="designer_endpoint",
        )
        designer_path_options = [
            path.key for path in SYNTHESIS_PATHS_BY_ENDPOINT[endpoint_key]
        ]
        if st.session_state.get("designer_path") not in designer_path_options:
            st.session_state["designer_path"] = designer_path_options[0]
        synthesis_path_key = st.selectbox(
            "Testing strategy",
            designer_path_options,
            format_func=lambda key: SYNTHESIS_PATHS_BY_KEY[key].testing_strategy,
            key="designer_path",
        )
        synthesis_path = SYNTHESIS_PATHS_BY_KEY[synthesis_path_key]
        additional_path_keys: list[str] = []
        if endpoint_design == "Mixed endpoints":
            all_path_keys = [
                path.key
                for ordered_endpoint in SYNTHESIS_ENDPOINT_ORDER
                for path in SYNTHESIS_PATHS_BY_ENDPOINT[ordered_endpoint]
                if path.key != synthesis_path_key
            ]
            current_additional = st.session_state.get("designer_additional_paths", [])
            if not isinstance(current_additional, list):
                current_additional = []
            valid_additional = [
                value for value in current_additional if value in all_path_keys
            ]
            if current_additional != valid_additional:
                st.session_state["designer_additional_paths"] = valid_additional
            additional_path_keys = st.multiselect(
                "Additional endpoint/testing pathways",
                all_path_keys,
                format_func=lambda key: (
                    f"{ENDPOINTS_BY_KEY[SYNTHESIS_PATHS_BY_KEY[key].endpoint_key].label} — "
                    f"{SYNTHESIS_PATHS_BY_KEY[key].testing_strategy}"
                ),
                key="designer_additional_paths",
                placeholder="Add one or more endpoint-specific pathways",
            )
        selected_path_keys = [synthesis_path_key] + additional_path_keys
        planned_sample_size = st.number_input(
            "Planned recruited sample size",
            min_value=0,
            step=1,
            key="designer_sample_size",
            help="Use 0 while the sample-size calculation is still pending.",
        )
        st.markdown(
            f"**Primary summary estimator:** {synthesis_path.summary_estimator}  \n"
            f"**Primary numerator:** {synthesis_path.numerator}  \n"
            f"**Primary denominator:** {synthesis_path.denominator}"
        )
        if len(selected_path_keys) > 1:
            with st.expander("Selected endpoint-specific analysis pathways", expanded=True):
                for path_key in selected_path_keys:
                    selected_path = SYNTHESIS_PATHS_BY_KEY[path_key]
                    selected_endpoint = ENDPOINTS_BY_KEY[selected_path.endpoint_key]
                    st.markdown(
                        f"- **{selected_endpoint.label}** — {selected_path.testing_strategy}; "
                        f"{selected_path.numerator} / {selected_path.denominator}; "
                        f"{selected_path.summary_estimator}"
                    )

    st.subheader("2. Balance the three planning pillars")
    design_tab, assay_tab, reporting_tab = st.tabs(
        ("Study design", "Assay design", "Standard reporting")
    )
    with design_tab:
        sampling_frame_category = st.selectbox(
            "Sampling-frame category",
            options=[""] + SAMPLING_FRAME_CATEGORY_OPTIONS,
            format_func=lambda value: "Select the operational source frame" if not value else value,
            key="designer_sampling_frame_category",
            help="Identify the list, registry, network, roster, enumeration system, or operational specimen source from which eligible units can enter.",
        )
        target_frame_alignment = assess_target_frame_alignment(
            target_population_category, sampling_frame_category
        )
        if target_frame_alignment["status"] == "default-aligned":
            st.success(target_frame_alignment["rationale"])
        elif target_frame_alignment["status"] == "unresolved":
            st.info(target_frame_alignment["rationale"])
        else:
            st.warning(target_frame_alignment["rationale"])
        sampling_frame_coverage_justification = st.text_area(
            "Sampling-frame coverage and catchment justification",
            key="designer_sampling_frame_coverage_justification",
            placeholder="Explain geography, service catchment, eligibility coverage, source-system completeness, and any known undercoverage.",
            height=86,
            help="Required when the Target-Frame Matrix does not identify a default structural match. The matrix supports—but never replaces—study-specific judgement.",
        )
        sampling_recruitment_method = st.selectbox(
            "Sampling and recruitment method",
            options=[""] + list(Q4_USER_METHOD_SCORES),
            format_func=lambda value: "Select the planned method" if not value else value,
            key="designer_sampling_method",
        )
        design_cols = st.columns(2)
        sampling_frame_defined = bool(sampling_frame_category) and (
            target_frame_alignment["status"] == "default-aligned"
            or bool(sampling_frame_coverage_justification.strip())
        )
        eligibility_defined = design_cols[0].checkbox(
            "Eligibility and exclusion criteria are prespecified",
            key="designer_eligibility",
        )
        sample_size_rationale = design_cols[1].checkbox(
            "Sample-size rationale accounts for precision/design effects",
            key="designer_sample_size_rationale",
        )
        nonresponse_plan = design_cols[0].checkbox(
            "Nonresponse, missing specimens, and pathway completeness will be tracked",
            key="designer_nonresponse",
        )

    with assay_tab:
        pathway_assays: dict[str, dict[str, str]] = {}

        def pathway_assay_controls(path_key: str) -> dict[str, str]:
            designer_assays = ASSAY_OPTIONS_BY_SYNTHESIS_PATH[path_key]
            designer_primary_options = list(designer_assays["primary"])
            primary_key = f"designer_primary_assay_{path_key}"
            current_primary = str(st.session_state.get(primary_key) or "")
            if current_primary not in designer_primary_options:
                st.session_state[primary_key] = ""
            primary_choice = st.selectbox(
                "Primary / screening assay",
                options=[""] + designer_primary_options,
                format_func=lambda value: "Select one assay" if not value else value,
                key=primary_key,
            )
            primary_other = ""
            if primary_choice == "Other primary assay (describe)":
                primary_other = st.text_input(
                    "Describe the other primary assay",
                    key=f"designer_primary_assay_other_{path_key}",
                )
            primary_value = primary_other.strip() or primary_choice

            designer_confirmatory_options = list(designer_assays["confirmatory"])
            confirmatory_key = f"designer_confirmatory_assays_{path_key}"
            current_confirmatory = st.session_state.get(confirmatory_key, [])
            if not isinstance(current_confirmatory, list):
                current_confirmatory = []
            valid_confirmatory = [
                value for value in current_confirmatory
                if value in designer_confirmatory_options
            ]
            if current_confirmatory != valid_confirmatory:
                st.session_state[confirmatory_key] = valid_confirmatory
            confirmatory_choices = st.multiselect(
                "Confirmatory assay(s)",
                options=designer_confirmatory_options,
                key=confirmatory_key,
                disabled=not designer_confirmatory_options,
                placeholder=(
                    "Select one or more methods"
                    if designer_confirmatory_options
                    else "Not required by the selected strategy"
                ),
            )
            confirmatory_other = ""
            if "Other confirmatory assay (describe)" in confirmatory_choices:
                confirmatory_other = st.text_input(
                    "Describe the other confirmatory assay",
                    key=f"designer_confirmatory_assay_other_{path_key}",
                )
            resolved_confirmatory = [
                confirmatory_other.strip() or choice
                if choice == "Other confirmatory assay (describe)"
                else choice
                for choice in confirmatory_choices
            ]
            return {
                "primary_assay": primary_value,
                "confirmatory_assay": "; ".join(resolved_confirmatory),
            }

        for selected_path_key in selected_path_keys:
            selected_path = SYNTHESIS_PATHS_BY_KEY[selected_path_key]
            selected_endpoint = ENDPOINTS_BY_KEY[selected_path.endpoint_key]
            if len(selected_path_keys) > 1:
                with st.expander(
                    f"{selected_endpoint.label} — {selected_path.testing_strategy}",
                    expanded=selected_path_key == synthesis_path_key,
                ):
                    pathway_assays[selected_path_key] = pathway_assay_controls(
                        selected_path_key
                    )
            else:
                pathway_assays[selected_path_key] = pathway_assay_controls(
                    selected_path_key
                )

        primary_assay = "; ".join(
            dict.fromkeys(
                values["primary_assay"]
                for values in pathway_assays.values()
                if values["primary_assay"]
            )
        )
        confirmatory_assay = "; ".join(
            dict.fromkeys(
                values["confirmatory_assay"]
                for values in pathway_assays.values()
                if values["confirmatory_assay"]
            )
        )
        assay_cols = st.columns(2)
        specimen_timing = assay_cols[0].checkbox(
            "Specimen type, collection timing, storage, and transport are specified",
            key="designer_specimen_timing",
        )
        validation_controls = assay_cols[1].checkbox(
            "Validation, calibration, positive/negative controls, and QC are specified",
            key="designer_validation_controls",
        )
        cross_reactivity_plan = assay_cols[0].checkbox(
            "Cross-reactivity and sensitivity/specificity limitations are addressed",
            key="designer_cross_reactivity",
        )
        testing_denominator_plan = assay_cols[1].checkbox(
            "Planned testing/retesting denominators and verification strata are specified",
            key="designer_testing_denominator",
        )
        endpoint_specific_assay_plan = True
        if len(selected_path_keys) > 1:
            endpoint_specific_assay_plan = st.checkbox(
                "Keep specimen flow, assay decisions, and verification rules distinguishable for every endpoint pathway",
                key="designer_endpoint_specific_assay_plan",
            )
        multiplex_assay_plan = True
        if len(viruses) > 1:
            multiplex_assay_plan = st.checkbox(
                "Prespecify virus-specific targets, cutoffs, cross-reactivity handling, controls, and performance characteristics",
                key="designer_multiplex_assay_plan",
            )

    with reporting_tab:
        reporting_cols = st.columns(2)
        numerator_denominator_reporting = reporting_cols[0].checkbox(
            "Report observed endpoint numerator and denominator",
            key="designer_report_counts",
        )
        flow_reporting = reporting_cols[1].checkbox(
            "Report participant/specimen flow and verification-stratum counts",
            key="designer_report_flow",
        )
        assay_reporting = reporting_cols[0].checkbox(
            "Report assay manufacturer, protocol, cutoffs, controls, and interpretation",
            key="designer_report_assay",
        )
        missingness_reporting = reporting_cols[1].checkbox(
            "Report nonresponse, exclusions, missing specimens, and incomplete testing",
            key="designer_report_missingness",
        )
        estimator_reporting = reporting_cols[0].checkbox(
            "Report estimator, weights, standardization, and assay adjustment",
            key="designer_report_estimator",
        )
        reproducibility_reporting = reporting_cols[1].checkbox(
            "Share protocol, codebook, analysis code, or machine-readable outputs",
            key="designer_report_reproducibility",
        )
        mixed_endpoint_reporting_plan = True
        if len(selected_path_keys) > 1:
            mixed_endpoint_reporting_plan = st.checkbox(
                "Report endpoint-specific flows, numerators, denominators, estimates, and missingness separately",
                key="designer_mixed_endpoint_reporting",
            )
        multiplex_reporting_plan = True
        if len(viruses) > 1:
            multiplex_reporting_plan = st.checkbox(
                "Report virus-specific results, indeterminate results, and co-detections without collapsing targets",
                key="designer_multiplex_reporting",
            )

    plan = {
        "study_title": study_title,
        "country_or_setting": country_or_setting,
        "report_scope": "surveillance" if design_context == "Surveillance study" else "study",
        "virus": virus,
        "viruses": viruses,
        "virus_coverage": virus_coverage,
        "target_population": target_population,
        "target_population_category": target_population_category,
        "surveillance_mode": surveillance_mode,
        "surveillance_architecture": surveillance_architecture,
        "active_stream_definition": active_stream_definition,
        "passive_stream_definition": passive_stream_definition,
        "stream_estimator_mode": stream_estimator_mode,
        "stream_integration_plan": stream_integration_plan,
        "endpoint_design": endpoint_design,
        "endpoint_key": endpoint_key,
        "synthesis_path_key": synthesis_path_key,
        "synthesis_path_keys": selected_path_keys,
        "testing_strategy": synthesis_path.testing_strategy,
        "summary_estimator": synthesis_path.summary_estimator,
        "sampling_recruitment_method": sampling_recruitment_method,
        "sampling_frame_category": sampling_frame_category,
        "sampling_frame_coverage_justification": sampling_frame_coverage_justification,
        "planned_sample_size": int(planned_sample_size),
        "sampling_frame_defined": sampling_frame_defined,
        "eligibility_defined": eligibility_defined,
        "sample_size_rationale": sample_size_rationale,
        "nonresponse_plan": nonresponse_plan,
        "primary_assay": primary_assay,
        "confirmatory_assay": confirmatory_assay,
        "pathway_assays": pathway_assays,
        "specimen_timing": specimen_timing,
        "validation_controls": validation_controls,
        "cross_reactivity_plan": cross_reactivity_plan,
        "testing_denominator_plan": testing_denominator_plan,
        "endpoint_specific_assay_plan": endpoint_specific_assay_plan,
        "multiplex_assay_plan": multiplex_assay_plan,
        "numerator_denominator_reporting": numerator_denominator_reporting,
        "flow_reporting": flow_reporting,
        "assay_reporting": assay_reporting,
        "missingness_reporting": missingness_reporting,
        "estimator_reporting": estimator_reporting,
        "reproducibility_reporting": reproducibility_reporting,
        "mixed_endpoint_reporting_plan": mixed_endpoint_reporting_plan,
        "multiplex_reporting_plan": multiplex_reporting_plan,
        "reporting_output": "Virus-, endpoint-, and stream-specific flow counts, assay details, estimators, missingness, co-detections, and reproducibility materials",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    readiness = dict(evaluate_study_design_plan(plan))
    # Streamlit can hot-reload this page while retaining an older imported
    # dare_arbo module. Keep the page usable until the process is restarted.
    if "analysis_units" not in readiness:
        if surveillance_mode == "Passive surveillance":
            planned_streams = ["Passive surveillance"]
        elif surveillance_mode == "Hybrid active + passive surveillance":
            if stream_estimator_mode.startswith("Combined"):
                planned_streams = ["Hybrid adjusted"]
            elif stream_estimator_mode.startswith("Separate and combined"):
                planned_streams = [
                    "Active surveillance",
                    "Passive surveillance",
                    "Hybrid adjusted",
                ]
            else:
                planned_streams = ["Active surveillance", "Passive surveillance"]
        else:
            planned_streams = ["Active surveillance"]
        planned_viruses = viruses or ["Arbovirus not specified"]
        readiness["analysis_units"] = [
            {
                "surveillance_stream": stream,
                "virus": planned_virus,
                "endpoint": ENDPOINTS_BY_KEY[
                    SYNTHESIS_PATHS_BY_KEY[path_key].endpoint_key
                ].label,
                "testing_strategy": SYNTHESIS_PATHS_BY_KEY[path_key].testing_strategy,
                "numerator": SYNTHESIS_PATHS_BY_KEY[path_key].numerator,
                "denominator": SYNTHESIS_PATHS_BY_KEY[path_key].denominator,
                "summary_estimator": SYNTHESIS_PATHS_BY_KEY[path_key].summary_estimator,
            }
            for stream in planned_streams
            for planned_virus in planned_viruses
            for path_key in selected_path_keys
        ]
    readiness.setdefault("analysis_unit_count", len(readiness["analysis_units"]))

    st.subheader("3. Planning balance and design figure")
    metric_cols = st.columns(5)
    for column, pillar in zip(metric_cols[:3], readiness["pillars"]):
        result = readiness["pillars"][pillar]
        column.metric(pillar, f"{result['percentage']:.0f}%")
    metric_cols[3].metric("Coverage floor", f"{readiness['balance_floor']:.0f}%")
    metric_cols[4].metric("Analysis outputs", readiness["analysis_unit_count"])
    st.caption(
        "Percentages describe completion/strength of the listed planning elements. They are not DARE-Arbo risk-of-bias scores and should not be interpreted as study-quality categories."
    )
    for pillar, result in readiness["pillars"].items():
        st.progress(result["percentage"] / 100, text=f"{pillar}: {result['percentage']:.0f}% planning coverage")
    if readiness["recommendations"]:
        with st.expander("Planning priorities", expanded=True):
            for recommendation in readiness["recommendations"]:
                st.markdown(f"- {recommendation}")
    with st.expander("Planned study–virus–estimand outputs", expanded=False):
        st.caption(
            "Each row retains its own endpoint-defining assay pathway, observed numerator, denominator, and estimator. Hybrid streams are separate only when the selected analysis plan requests separate estimates."
        )
        st.dataframe(
            pd.DataFrame(readiness["analysis_units"])[
                [
                    "surveillance_stream",
                    "virus",
                    "endpoint",
                    "testing_strategy",
                    "numerator",
                    "denominator",
                    "summary_estimator",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

    presentation_colors = overview_color_controls()
    branding_logo = LOGO_PATH.read_bytes() if LOGO_PATH.exists() else None
    design_png = render_study_design_png(
        plan,
        presentation_colors=presentation_colors,
        branding_logo=branding_logo,
    )
    report_model = build_designer_report_model(plan, readiness)
    report_pdf: bytes | None = None
    try:
        with st.spinner("Preparing report…"):
            report_pdf = render_surveillance_design_report_pdf(
                plan,
                readiness=readiness,
                design_png=design_png,
                presentation_colors=presentation_colors,
                branding_logo=branding_logo,
                partner_logo=(
                    prepared_square_logo(SYNERGY_LOGO_PATH.read_bytes())
                    if SYNERGY_LOGO_PATH.exists()
                    else None
                ),
            )
    except Exception:
        st.error("Report could not be generated. Your study design has not been changed.")
    st.image(design_png, caption="DARE-Arbo surveillance study-design flow", width="stretch")
    with st.expander("Written protocol-oriented design summary", expanded=False):
        for paragraph_text in report_model["narrative"].values():
            st.write(paragraph_text)
    raw_name = study_title or "Study"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("_") or "Study"
    report_prefix = (
        "DARE-Arbo_Surveillance_Study_Design"
        if "Surveillance" in report_model["reportTitle"]
        else "DARE-Arbo_Study_Design"
    )
    report_filename = f"{report_prefix}_{safe_name}_{date.today().isoformat()}.pdf"
    download_cols = st.columns(3)
    download_cols[0].download_button(
        "Download study design report",
        data=report_pdf or b"",
        file_name=report_filename,
        mime="application/pdf",
        width="stretch",
        disabled=report_pdf is None,
    )
    download_cols[1].download_button(
        "Download study-design flow PNG",
        data=design_png,
        file_name=f"{safe_name}_flow.png",
        mime="image/png",
        width="stretch",
    )
    download_cols[2].download_button(
        "Download study-design plan JSON",
        data=json.dumps(
            {**plan, "planning_readiness": readiness, "designer_report_model": report_model},
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
        file_name=f"{safe_name}_plan.json",
        mime="application/json",
        width="stretch",
    )
    st.caption(
        "The PDF records the current design and any unresolved planning priorities. "
        "Regenerate it after edits before protocol handoff."
    )


def framework_page() -> None:
    hero()
    st.markdown("The implementation below follows the audited DARE-Arbo guide in this workspace. It separates endpoint classification from methodological scoring.")
    st.subheader("Scoring structure")
    rows = []
    for criterion in CRITERIA:
        rows.append(
            {
                "Domain": criterion.domain,
                "Item": criterion.code,
                "Criterion": criterion.label,
                "Range": f"0-{criterion.maximum}",
                "Prior exposure / IgM / NS1": "Applicable",
                "Mixed serology / neutralization / confirmed direct / assay performance / other": "N/A" if criterion.code == "Q10" else "Applicable",
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.info("Prior-exposure / binding-antibody prevalence, IgM-defined presumptive recent infection, and NS1-defined presumptive active infection: maximum 19 with Q10 scored 0–1. Mixed/inseparable serology, neutralizing-antibody prevalence, confirmed direct detection, assay-performance, and other explicitly defined endpoints: maximum 18 with Q10 recorded as N/A.")

    st.subheader("Endpoint-specific synthesis paths")
    path_rows = [
        {
            "Synthesis endpoint": ENDPOINTS_BY_KEY[path.endpoint_key].label,
            "Summary estimator": path.summary_estimator,
            "Testing strategy": path.testing_strategy,
            "Assay type": path.assay_type,
            "Numerator": path.numerator,
            "Denominator": path.denominator,
            "False positives": path.false_positives,
        }
        for endpoint_key in SYNTHESIS_ENDPOINT_ORDER
        for path in SYNTHESIS_PATHS_BY_ENDPOINT[endpoint_key]
    ]
    st.dataframe(pd.DataFrame(path_rows), use_container_width=True, hide_index=True)

    st.subheader("Interpretation guardrails")
    st.markdown(
        """
        - Assess one study–virus–estimand at a time; define target population → biological endpoint → observed numerator → endpoint-defining assay/algorithm → testing/verification population → matched denominator → summary estimator before scoring.
        - Do not combine IgG, IgM, NS1, PCR, and neutralization evidence into one score. If IgM/IgG results are inseparable, retain a mixed/other serologic endpoint rather than inferring either component.
        - Define the chain before scoring: target population → biological endpoint → endpoint-defining assay → testing/verification population → numerator/denominator → summary estimator.
        - Use the 2026-09-10 Design Library for Q1-Q3: classify the target and operational source frame separately, then use the Target-Frame Matrix as decision support. A legitimate restricted target is not penalized for lacking national coverage, and the matrix never replaces study-specific catchment and coverage judgement.
        - Use the Design Library recruitment classes for Q4 and trace every consequential stage from frame → sites → people/specimens → endpoint population. The weakest consequential stage governs; multiple sites or random laboratory subsampling alone do not upgrade source recruitment.
        - Q5 calculates Total tested / Total recruited and, when staged testing applies, actual completed / planned. The lower applicable percentage determines the score. Planned representative subsampling is not missingness when the planned subset is completed.
        - Q8 requires every observed quantity used by the estimator, including verification-stratum counts for staged or weighted designs. Keep posterior/reconstructed expected positives distinct from observed numerators.
        - Treat reference-positive/reference-negative validation samples as ancillary: they may support Q6a/Q6b and adjustment parameters but are not prevalence endpoints and do not automatically earn Q6c.
        - Random molecular testing of screening-positive and screening-negative subsets is a two-phase validation/adjustment design; never report its raw subset proportion as population molecular prevalence.
        - Retain Tier B only when the complete planned screen-positive verification set was actually tested; Tier B population prevalence uses the full screening-algorithm entry denominator. Otherwise reclassify to selected/unresolved Tier D unless representative Tier C verification is explicitly supported.
        - Tier C requires a predefined representative/random verification subset and all counts needed for its weighted or screen-conditioned estimator. Keep its raw verification-subset proportion separate from population prevalence.
        - For Q6, distinguish intrinsic validity (Q6a), validation/standardization/QC (Q6b), and independent orthogonal confirmation (Q6c). A linked high-specificity method earns Q6c=2 when systematically applied to the complete relevant positive/eligible set and Q6c=1 when limited to a predefined representative/random or selected subset; unlinked or ancillary assay-validation samples do not automatically earn Q6c.
        - Record endpoint structure, primary assay role, endpoint testing population, confirmation role, and confirmation coverage as separate variables. Repetition of the same assay or platform is not independent confirmation.
        - For Q7, judge the endpoint-defining procedure. Uneven optional supporting confirmation does not reduce an otherwise consistent primary endpoint; a staged endpoint requires consistent completion of every required step.
        - Do not infer unreported methods. Retain audit comments with every non-obvious score.
        - A high assay score cannot conceptually compensate for severe selection bias, and representativeness cannot compensate for an invalid assay.
        - Use domain scores, the endpoint/tier classification, and the continuous total together in sensitivity and subgroup analyses.
        """
    )


def about_page() -> None:
    hero()
    st.subheader("What this implementation provides")
    st.markdown(
        """
        - Single-document PDF intake with page-aware, rules-based evidence suggestions.
        - A surveillance Study Designer that balances study design, assay design, and reporting plans and exports a structured PDF report, presentation-ready flow PNG, and JSON protocol plan.
        - Manual reviewer verification for all 12 DARE-Arbo criteria and audit comments.
        - Endpoint-specific applicability, maxima, serologic/direct/neutralization pathway classification, and observed-count auditing.
        - Multi-study batch entry, descriptive criterion heatmaps, and CSV/JSON exports.
        - A testable scoring engine that is independent of the user interface.
        """
    )
    st.warning("PDF suggestions are deliberately conservative and must not be treated as autonomous risk-of-bias judgments. Image-only PDFs need OCR. Tables, supplements, and nuanced target-population claims still require reviewer inspection.")
    st.subheader("Privacy and deployment")
    st.markdown(
        """
        The app processes uploaded PDFs in memory and does not persist them by default. For public deployment, use a controlled Streamlit host, HTTPS, an access policy appropriate to the documents being reviewed, and a private storage/database layer only if persistent projects are required.

        Local launch:

        ```powershell
        python -m pip install -r requirements.txt
        streamlit run streamlit_app.py
        ```
        """
    )
    st.caption(f"Framework source: {GUIDE_VERSION}")


def main() -> None:
    configure_page()
    initialize_state()
    page = sidebar()
    if page == "Assessor":
        assessor_page()
    elif page == "Appraiser":
        appraiser_page()
    elif page == "DARE-Arbo Designer":
        study_designer_page()
    elif page == "Framework":
        framework_page()
    else:
        about_page()


if __name__ == "__main__":
    main()
