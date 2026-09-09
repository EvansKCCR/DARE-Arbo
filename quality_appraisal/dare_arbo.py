"""Core DARE-Arbo scoring, endpoint classification, and PDF evidence helpers.

The module is deliberately UI-independent so the scoring rules can be tested,
reused in batch pipelines, and audited without Streamlit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO
import html
import json
import math
import re
from typing import Any, BinaryIO, Iterable, Mapping


GUIDE_VERSION = (
    "DARE-Arbo endpoint-first application principle + Appraisal Library rules "
    "(workspace version, 2026-09-09)"
)


# Presentation themes are intentionally separate from the app interface theme.
# The classic multicolour palette is the default for exported study overviews.
OVERVIEW_COLOR_PRESETS: dict[str, dict[str, str]] = {
    "DARE-Arbo classic (default)": {
        "background": "#F7FAFC",
        "header": "#123B5D",
        "primary": "#0F766E",
        "secondary": "#2563EB",
        "text": "#172033",
        "success": "#15803D",
        "warning": "#D97706",
        "danger": "#DC2626",
    },
    "Obsidian and crimson": {
        "background": "#F7F7F8",
        "header": "#1E1E1F",
        "primary": "#DC2626",
        "secondary": "#525252",
        "text": "#121212",
        "success": "#404040",
        "warning": "#991B1B",
        "danger": "#DC2626",
    },
    "Accessible navy and gold": {
        "background": "#F8FAFC",
        "header": "#172554",
        "primary": "#0072B2",
        "secondary": "#CC79A7",
        "text": "#111827",
        "success": "#009E73",
        "warning": "#E69F00",
        "danger": "#D55E00",
    },
}

DEFAULT_OVERVIEW_COLORS = OVERVIEW_COLOR_PRESETS["DARE-Arbo classic (default)"]


def _hex_color(value: str, name: str) -> str:
    normalized = str(value).strip().upper()
    if not re.fullmatch(r"#[0-9A-F]{6}", normalized):
        raise ValueError(f"Presentation color '{name}' must use #RRGGBB notation.")
    return normalized


def _mix_hex(color: str, target: str, target_weight: float) -> str:
    """Blend two validated hex colors; target_weight is in the 0-1 range."""
    color_rgb = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
    target_rgb = tuple(int(target[index : index + 2], 16) for index in (1, 3, 5))
    mixed = tuple(
        round(source * (1 - target_weight) + destination * target_weight)
        for source, destination in zip(color_rgb, target_rgb)
    )
    return "#" + "".join(f"{component:02X}" for component in mixed)


def resolve_overview_colors(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the semantic and derived colors used by the PNG renderer."""
    theme = dict(DEFAULT_OVERVIEW_COLORS)
    if overrides:
        unknown = set(overrides) - set(theme)
        if unknown:
            raise ValueError(f"Unknown presentation color fields: {', '.join(sorted(unknown))}")
        theme.update(overrides)
    theme = {name: _hex_color(value, name) for name, value in theme.items()}
    white = "#FFFFFF"
    return {
        "obsidian": theme["header"],
        "charcoal": theme["header"],
        "navy": theme["header"],
        "ink": theme["text"],
        "muted": _mix_hex(theme["text"], white, 0.48),
        "silver": _mix_hex(theme["text"], white, 0.62),
        "teal": theme["primary"],
        "cyan": theme["secondary"],
        "teal_dark": _mix_hex(theme["primary"], "#000000", 0.28),
        "wine": _mix_hex(theme["primary"], "#000000", 0.34),
        "mint": _mix_hex(theme["primary"], white, 0.90),
        "paper": white,
        "line": _mix_hex(theme["text"], white, 0.46),
        "border": _mix_hex(theme["header"], white, 0.58),
        "gold": theme["warning"],
        "gold_pale": _mix_hex(theme["warning"], white, 0.84),
        "red": theme["danger"],
        "red_pale": _mix_hex(theme["danger"], white, 0.86),
        "green": theme["success"],
        "green_pale": _mix_hex(theme["success"], white, 0.84),
        "blue": theme["secondary"],
        "blue_pale": _mix_hex(theme["secondary"], white, 0.86),
        "purple": _mix_hex(theme["secondary"], theme["primary"], 0.48),
        "purple_pale": _mix_hex(_mix_hex(theme["secondary"], theme["primary"], 0.48), white, 0.86),
        "grey": _mix_hex(theme["text"], white, 0.91),
        "shadow": _mix_hex(theme["header"], white, 0.78),
        "background": theme["background"],
    }


def _load_presentation_font(image_font: Any, size: int, bold: bool = False) -> Any:
    """Load a scalable presentation font across Windows, WSL, and Linux hosts."""
    candidates = (
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/mnt/c/Windows/Fonts/arialbd.ttf" if bold else "/mnt/c/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return image_font.truetype(candidate, size=size)
        except OSError:
            continue
    try:
        return image_font.load_default(size=size)
    except TypeError:  # pragma: no cover - compatibility with older Pillow
        return image_font.load_default()


@dataclass(frozen=True)
class Criterion:
    code: str
    domain: str
    label: str
    maximum: int
    question: str
    rules: Mapping[int, str]


@dataclass(frozen=True)
class EvidenceSnippet:
    page: int
    text: str


@dataclass(frozen=True)
class Suggestion:
    score: int | None
    confidence: str
    rationale: str
    evidence: tuple[EvidenceSnippet, ...] = ()


@dataclass(frozen=True)
class EndpointDefinition:
    key: str
    label: str
    default_pathway: str
    interpretation: str


@dataclass(frozen=True)
class SynthesisPathDefinition:
    key: str
    endpoint_key: str
    summary_estimator: str
    testing_strategy: str
    assay_type: str
    numerator: str
    denominator: str
    false_positives: str
    verification_design: str
    required_counts: tuple[str, ...]


DOMAINS = (
    "Study population",
    "Population representativeness",
    "Laboratory assay",
    "Reporting outcome",
)


CRITERIA = (
    Criterion(
        "Q1",
        "Study population",
        "Representation of the defined target population",
        1,
        "Did the achieved sample include the age and sex groups expected for the estimand's explicitly defined target population, without a major imbalance?",
        {
            0: "The achieved sample clearly underrepresents or overrepresents important age or sex groups relative to the defined target; a special subgroup is generalized to a broader target; or age/sex information is insufficient.",
            1: "The achieved sample includes the age and sex groups expected for the defined target, with no major imbalance making it a poor representation. A legitimate local, clinical, pediatric, antenatal, occupational, outbreak, or other restricted target need not be nationally representative.",
        },
    ),
    Criterion(
        "Q2",
        "Study population",
        "Direct participant-derived data or specimens",
        1,
        "Was the endpoint measured from participants or participant-derived specimens belonging to the defined target/source population, rather than only from a proxy or aggregate source?",
        {
            0: "Only proxy respondents, aggregate reports, line lists, records, or another secondary source were used, or the link between stored specimens and their participant/source population is unclear.",
            1: "The endpoint was measured from directly collected participant data or participant-derived biological specimens. Clearly identified stored specimens can qualify when their source population is described; belonging to a special target population does not itself reduce Q2.",
        },
    ),
    Criterion(
        "Q3",
        "Population representativeness",
        "Sampling-frame representation",
        1,
        "Was the source list, facilities, communities, surveillance platform, biobank, registry, or recruitment setting suitable for identifying eligible members of this estimand's defined target population?",
        {
            0: "The frame excludes important members or settings within the defined target, is a narrow or selected frame used for broader inference, has an undefined archived-specimen source, or is insufficiently reported.",
            1: "The frame is suitable for the explicitly defined target population, including a coherent local, regional, facility, surveillance, occupational, antenatal, pediatric, outbreak, archived-specimen, animal, or One Health target. It need not cover the national population unless national inference is intended.",
        },
    ),
    Criterion(
        "Q4",
        "Population representativeness",
        "Sampling and recruitment method",
        3,
        "How were the endpoint-specific participants or specimens actually selected from the defined source population?",
        {
            0: "Sampling/recruitment is insufficiently reported for the endpoint-specific analytic population.",
            1: "Non-probability recruitment: convenience, volunteer, purposive, single-facility/consecutive, routine-service, case-series, outbreak, passive-surveillance, referral, available archived, secondary cohort/trial, or other selected sampling. Random retesting of a subset does not upgrade a non-probability source frame.",
            2: "Simple/systematic/random-number selection within a defined frame, random selection within a restricted population, or materially broadened multisite/multicommunity/structured surveillance capture without a complete probability design.",
            3: "Multistage, stratified, cluster-probability, probability-proportional-to-size, population-based probability sampling, census, universal inclusion, or documented complete/near-complete eligible capture.",
        },
    ),
    Criterion(
        "Q5",
        "Population representativeness",
        "Nonresponse, missingness, or completeness bias",
        2,
        "Were recruited participants tested, and—when staged testing was planned—was each planned testing/verification step completed?",
        {
            0: "The limiting relevant completion percentage is <70%, the required recruited/tested or planned-pathway counts are unreported, or missingness could materially bias the estimate.",
            1: "The limiting relevant completion percentage is 70% to <80%.",
            2: "The limiting relevant completion percentage is >=80%. Calculate Total tested / Total recruited and, when staged testing applies, actual completed / number expected under the planned pathway; use the lower applicable percentage. Planned representative subsampling is not missingness when the planned subset itself is completed.",
        },
    ),
    Criterion(
        "Q6a",
        "Laboratory assay",
        "Overall assay validity and reliability",
        4,
        "How strong is the evidence that the primary assay or algorithm validly measures the extracted endpoint?",
        {
            0: "The assay is inadequately described, inappropriate for the endpoint, or insufficient to establish the intended outcome.",
            1: "Screening-level testing with little or no performance information, validation, controls, or confirmation.",
            2: "A recognized single assay is appropriate and interpretable, but performance evidence, controls, or high-specificity confirmation are limited.",
            3: "Documented performance or complementary testing improves confidence, but confirmation is incomplete/subset-based or material specificity limits remain.",
            4: "A reference-standard/high-specificity method or robust validated algorithm provides high diagnostic certainty.",
        },
    ),
    Criterion(
        "Q6b",
        "Laboratory assay",
        "Validation or standardization before use",
        1,
        "Was the assay validated, standardized, or appropriately quality-controlled before or during use?",
        {
            0: "No clear manufacturer performance, validation, reference standardization, established protocol, or appropriate controls are reported.",
            1: "At least one acceptable validation/standardization indicator or appropriate assay-control strategy is reported.",
        },
    ),
    Criterion(
        "Q6c",
        "Laboratory assay",
        "Independent confirmation of the arbovirus result",
        2,
        "Was the primary result supported by an independent or more specific confirmatory method?",
        {
            0: "No independent confirmation beyond the primary endpoint-defining assay is reported, or confirmation is unlinked to the same virus, endpoint, or participant set.",
            1: "An orthogonal or additional method supports the same result but is less definitive, is applied to a predefined or selected subset, or does not cover the complete relevant eligible set.",
            2: "A high-specificity orthogonal method directly supports the same virus/endpoint and is systematically applied to the complete relevant positive or eligible set.",
        },
    ),
    Criterion(
        "Q7",
        "Laboratory assay",
        "Consistent collection and testing algorithm",
        1,
        "Was the endpoint-defining collection/testing algorithm applied consistently to every participant/specimen eligible for each planned step?",
        {
            0: "Materially different methods or inconsistent endpoint-defining subsets were used without a predefined rule.",
            1: "The same primary method or a uniformly applied predefined staged algorithm was used for all eligible samples. A clearly separate ancillary validation subset does not reduce this score.",
        },
    ),
    Criterion(
        "Q8",
        "Reporting outcome",
        "Appropriate numerator and denominator",
        1,
        "Are all quantities required by the declared estimator appropriate, internally consistent, observed, and reconstructable?",
        {
            0: "A required numerator, denominator, verification-stratum count, or weight is missing/ambiguous/inconsistent; or a posterior/reconstructed expected-positive quantity is substituted for an observed numerator.",
            1: "All observed counts and any verification-stratum quantities required by the actual estimator are clearly reported or reliably reconstructable for the same population, period, and endpoint.",
        },
    ),
    Criterion(
        "Q9",
        "Reporting outcome",
        "Formal age or sex correction",
        1,
        "Was the synthesized prevalence/risk estimate formally standardized, weighted, or model-adjusted for age and/or sex?",
        {
            0: "No formal correction; descriptive strata or association testing alone do not qualify.",
            1: "The synthesized estimate is standardized, weighted, or adjusted for age and/or sex.",
        },
    ),
    Criterion(
        "Q10",
        "Reporting outcome",
        "Correction for assay sensitivity and specificity",
        1,
        "For prior-exposure / binding-antibody prevalence, IgM-defined presumptive recent infection, or NS1-defined presumptive active infection, was the observed estimate corrected for imperfect assay sensitivity and specificity?",
        {
            0: "The observed/apparent prior-exposure, IgM, or NS1 prevalence estimate is not adjusted for sensitivity and specificity.",
            1: "The synthesized prior-exposure, IgM, or NS1 estimate uses explicit sensitivity/specificity adjustment or an equivalent measurement-error model.",
        },
    ),
)

CRITERIA_BY_CODE = {criterion.code: criterion for criterion in CRITERIA}


ENDPOINTS = (
    EndpointDefinition(
        "prior_exposure",
        "Prior exposure / antibody seroprevalence",
        "serologic",
        "IgG, total antibody, or another antibody endpoint interpreted as prior exposure.",
    ),
    EndpointDefinition(
        "presumptive_igm",
        "Presumptive recent infection - IgM",
        "serologic",
        "A distinct observed IgM-positive numerator is presumptive recent infection. It must not be merged with IgG. This is a test-adjusted endpoint, so Q10 applies.",
    ),
    EndpointDefinition(
        "presumptive_ns1",
        "Presumptive active infection - NS1 antigen",
        "serologic",
        "An observed NS1-positive numerator is a presumptive active-infection marker, not molecular confirmation. This is a test-adjusted endpoint, so Q10 applies.",
    ),
    EndpointDefinition(
        "mixed_serology",
        "Mixed / inseparable serologic evidence",
        "serologic",
        "Use only when IgM, IgG, or other serologic positives cannot be separated into endpoint-specific observed numerators. Do not infer a component endpoint; Q10 is N/A.",
    ),
    EndpointDefinition(
        "confirmed_active",
        "Confirmed active infection - direct viral detection",
        "direct_detection",
        "PCR/NAAT, virus isolation, or sequencing-supported direct detection of current infection.",
    ),
    EndpointDefinition(
        "neutralizing_antibody",
        "Neutralizing-antibody prevalence",
        "direct_detection",
        "PRNT, VNT, or microneutralization-positive prevalence is appraised separately from primary-assay seroprevalence; Q10 is N/A.",
    ),
    EndpointDefinition(
        "assay_performance",
        "Assay performance endpoint",
        "direct_detection",
        "Random confirmation of screening-positive and screening-negative samples estimates sensitivity, specificity, or misclassification; it is not a prevalence endpoint.",
    ),
    EndpointDefinition(
        "other",
        "Other explicitly defined endpoint",
        "serologic",
        "Define the observed biological numerator explicitly. Q10 is N/A unless the record is reclassified as primary-assay prior-exposure prevalence.",
    ),
)

ENDPOINTS_BY_KEY = {endpoint.key: endpoint for endpoint in ENDPOINTS}


VERIFICATION_DESIGNS = {
    "full_population": "All participants/specimens received the endpoint-defining test",
    "representative_subsample": "A predefined representative/random subsample received the endpoint-defining test",
    "all_screen_positive": "All planned screening-positive participants received verification",
    "representative_positive_subset": "A representative/random subset of screening-positive participants received verification",
    "subset_positive": "A selected/unspecified subset of screening-positive participants received verification",
    "selected_subsample": "A selected/nonrepresentative subsample received the endpoint-defining test",
    "two_phase_validation": "Random screening-positive and screening-negative subsets received molecular validation",
    "ancillary_validation": "Reference-positive/reference-negative samples were used only for assay validation",
    "mixed_two_phase": "Mixed positive/negative or otherwise unresolved verification design",
    "incomplete": "Denominator or linkage to the source population is incomplete",
}


Q10_APPLICABLE_ENDPOINTS = frozenset(
    {"prior_exposure", "presumptive_igm", "presumptive_ns1"}
)

NUMERATOR_PROVENANCE = {
    "observed": "Observed endpoint-positive count",
    "reconstructed_observed": "Observed count reconstructed from reported strata",
    "posterior_expected": "Posterior/model-derived expected positives",
    "unclear": "Unclear numerator provenance",
}


SYNTHESIS_PATHS = (
    SynthesisPathDefinition(
        "serology_apparent", "prior_exposure", "Apparent prevalence",
        "ELISA/RDT only",
        "ELISA, IIFA, RDT, CLIA, Luminex, or other primary serological assay",
        "Primary positive", "Total tested", "Not assessed", "full_population",
        ("total_tested_n", "primary_positive_n"),
    ),
    SynthesisPathDefinition(
        "serology_tier_b", "prior_exposure", "Tier B (confirmed seropositivity)",
        "Neutralization of all positive samples",
        "Primary serological assay + neutralization assay",
        "Confirmed positive", "Total tested (complete screening-algorithm entry population)",
        "Number retested − confirmed positive", "all_screen_positive",
        ("total_tested_n", "primary_positive_n", "number_retested_n", "confirmed_positive_n"),
    ),
    SynthesisPathDefinition(
        "serology_tier_c", "prior_exposure", "Tier C (estimated confirmed seropositivity)",
        "Neutralization of a predefined representative/random subset of positive samples",
        "Primary serological assay + neutralization assay",
        "Weighted/screen-conditioned confirmed positive", "Total tested plus screen-positive and verification-subset counts",
        "Number retested − confirmed positive", "representative_positive_subset",
        ("total_tested_n", "primary_positive_n", "number_retested_n", "confirmed_positive_n"),
    ),
    SynthesisPathDefinition(
        "serology_tier_d", "prior_exposure", "Tier D (selected/unresolved confirmation subset)",
        "Neutralization of a selected, convenience, or unspecified subset of positive samples",
        "Primary serological assay + neutralization assay",
        "Observed confirmed positive in selected subset", "Number retested (conditional subset only)",
        "Number retested − confirmed positive", "subset_positive",
        ("total_tested_n", "primary_positive_n", "number_retested_n", "confirmed_positive_n"),
    ),
    SynthesisPathDefinition(
        "neutralization_universal", "neutralizing_antibody", "Neutralizing antibody prevalence",
        "Universal neutralization testing", "Neutralization assay only (PRNT, cVNT, pVNT, MN, etc.)",
        "Neutralization positive", "Total tested", "Not assessed", "full_population",
        ("total_tested_n", "neutralization_positive_n"),
    ),
    SynthesisPathDefinition(
        "igm_only", "presumptive_igm", "Presumptive active infection prevalence",
        "Serological testing only", "IgM assay", "IgM positive", "Total tested",
        "Not assessed", "full_population", ("total_tested_n", "igm_positive_n"),
    ),
    SynthesisPathDefinition(
        "ns1_only", "presumptive_ns1", "Presumptive active infection prevalence",
        "Antigen testing only", "NS1 assay", "NS1 positive", "Total tested",
        "Not assessed", "full_population", ("total_tested_n", "ns1_positive_n"),
    ),
    SynthesisPathDefinition(
        "mixed_serology_only", "mixed_serology", "Mixed/other serologic positivity",
        "Inseparable combined serologic result",
        "Primary serological assay reporting a combined endpoint",
        "Combined serologic positive", "Total tested", "Not assessed", "full_population",
        ("total_tested_n", "mixed_serology_positive_n"),
    ),
    SynthesisPathDefinition(
        "confirmed_active_universal", "confirmed_active", "Primary confirmed-active dataset",
        "Universal molecular testing", "PCR/NAAT, virus isolation, or sequencing-supported detection",
        "Molecular positive", "Total tested", "Not assessed", "full_population",
        ("total_tested_n", "molecular_positive_n"),
    ),
    SynthesisPathDefinition(
        "confirmed_active_tier_b", "confirmed_active", "Tier B (confirmed active infection)",
        "Molecular testing of all screening positives",
        "Screening assay + PCR/NAAT, virus isolation, or sequencing",
        "Molecular positive among all screening positives", "Total tested (complete screening-algorithm entry population)",
        "Number retested − molecular positive", "all_screen_positive",
        ("total_tested_n", "primary_positive_n", "number_retested_n", "molecular_positive_n"),
    ),
    SynthesisPathDefinition(
        "confirmed_active_tier_c", "confirmed_active", "Tier C (estimated confirmed active infection)",
        "Molecular testing of a predefined representative/random subset of screening positives",
        "Screening assay + PCR/NAAT, virus isolation, or sequencing",
        "Weighted/screen-conditioned molecular positive", "Total tested plus screening-positive and verification-subset counts",
        "Number retested − molecular positive", "representative_positive_subset",
        ("total_tested_n", "primary_positive_n", "number_retested_n", "molecular_positive_n"),
    ),
    SynthesisPathDefinition(
        "confirmed_active_tier_d", "confirmed_active", "Tier D (selected/unresolved confirmation subset)",
        "Molecular testing of a selected, convenience, or unspecified subset of screening positives",
        "Screening assay + PCR/NAAT, virus isolation, or sequencing",
        "Observed molecular positive in selected subset", "Number retested (conditional subset only)",
        "Number retested − molecular positive", "subset_positive",
        ("total_tested_n", "primary_positive_n", "number_retested_n", "molecular_positive_n"),
    ),
    SynthesisPathDefinition(
        "serology_validation", "assay_performance", "Validation dataset",
        "Neutralization of random positive and negative samples",
        "Primary serological assay + neutralization assay",
        "Confirmed positive and confirmed negative results", "Number retested",
        "False positives and false negatives directly estimable", "two_phase_validation",
        ("number_retested_n", "retested_positive_stratum_n", "confirmed_positive_n", "retested_negative_stratum_n", "confirmed_negative_n"),
    ),
    SynthesisPathDefinition(
        "molecular_validation", "assay_performance", "Validation dataset",
        "Molecular testing of random screening-positive and screening-negative samples",
        "Screening assay + PCR/NAAT, virus isolation, or sequencing",
        "Molecularly confirmed status", "Number retested",
        "False positives and false negatives directly estimable", "two_phase_validation",
        ("number_retested_n", "retested_positive_stratum_n", "confirmed_positive_n", "retested_negative_stratum_n", "confirmed_negative_n"),
    ),
)

SYNTHESIS_PATHS_BY_KEY = {path.key: path for path in SYNTHESIS_PATHS}
SYNTHESIS_PATHS_BY_ENDPOINT: dict[str, tuple[SynthesisPathDefinition, ...]] = {
    endpoint.key: tuple(path for path in SYNTHESIS_PATHS if path.endpoint_key == endpoint.key)
    for endpoint in ENDPOINTS
}

PRIMARY_SEROLOGY_ASSAYS = (
    "IgG ELISA",
    "Total-antibody ELISA",
    "IgG rapid diagnostic test (RDT)",
    "Indirect immunofluorescence assay (IIFA)",
    "CLIA/CMIA",
    "Luminex/multiplex serology",
    "Hemagglutination-inhibition assay",
    "Other primary assay (describe)",
)
NEUTRALIZATION_ASSAYS = (
    "PRNT50",
    "PRNT80",
    "PRNT90",
    "FRNT50",
    "FRNT90",
    "Microneutralization assay",
    "Conventional virus-neutralization test (cVNT)",
    "Pseudovirus-neutralization test (pVNT)",
    "Other confirmatory assay (describe)",
)
MOLECULAR_ASSAYS = (
    "RT-PCR",
    "Real-time RT-qPCR",
    "Multiplex RT-PCR/NAAT",
    "Droplet digital PCR (ddPCR)",
    "LAMP/isothermal NAAT",
    "Virus isolation",
    "Second genomic target",
    "Sanger sequencing",
    "Next-generation/metagenomic sequencing",
    "Other confirmatory assay (describe)",
)
ACTIVE_SCREENING_ASSAYS = (
    "NS1 antigen ELISA",
    "NS1 antigen RDT",
    "IgM capture ELISA (MAC-ELISA)",
    "IgM rapid diagnostic test (RDT)",
    "Combined antigen/antibody RDT",
    "Clinical/syndromic screening definition",
    "Other primary assay (describe)",
)

ASSAY_OPTIONS_BY_SYNTHESIS_PATH: dict[str, dict[str, tuple[str, ...]]] = {
    "serology_apparent": {"primary": PRIMARY_SEROLOGY_ASSAYS, "confirmatory": NEUTRALIZATION_ASSAYS},
    "serology_tier_b": {"primary": PRIMARY_SEROLOGY_ASSAYS, "confirmatory": NEUTRALIZATION_ASSAYS},
    "serology_tier_c": {"primary": PRIMARY_SEROLOGY_ASSAYS, "confirmatory": NEUTRALIZATION_ASSAYS},
    "serology_tier_d": {"primary": PRIMARY_SEROLOGY_ASSAYS, "confirmatory": NEUTRALIZATION_ASSAYS},
    "neutralization_universal": {
        "primary": tuple(
            assay.replace("Other confirmatory assay", "Other primary assay")
            for assay in NEUTRALIZATION_ASSAYS
        ),
        "confirmatory": NEUTRALIZATION_ASSAYS,
    },
    "igm_only": {
        "primary": (
            "IgM capture ELISA (MAC-ELISA)", "IgM indirect ELISA",
            "IgM rapid diagnostic test (RDT)", "IgM IIFA", "IgM CLIA/CMIA",
            "Other primary assay (describe)",
        ),
        "confirmatory": MOLECULAR_ASSAYS,
    },
    "ns1_only": {
        "primary": (
            "NS1 antigen ELISA", "NS1 antigen RDT", "NS1 CLIA/CMIA",
            "Other primary assay (describe)",
        ),
        "confirmatory": MOLECULAR_ASSAYS,
    },
    "mixed_serology_only": {
        "primary": PRIMARY_SEROLOGY_ASSAYS,
        "confirmatory": tuple(dict.fromkeys((*NEUTRALIZATION_ASSAYS, *MOLECULAR_ASSAYS))),
    },
    "confirmed_active_universal": {
        "primary": tuple(
            assay.replace("Other confirmatory assay", "Other primary assay")
            for assay in MOLECULAR_ASSAYS
        ),
        "confirmatory": MOLECULAR_ASSAYS,
    },
    "confirmed_active_tier_b": {
        "primary": ACTIVE_SCREENING_ASSAYS,
        "confirmatory": MOLECULAR_ASSAYS,
    },
    "confirmed_active_tier_c": {
        "primary": ACTIVE_SCREENING_ASSAYS,
        "confirmatory": MOLECULAR_ASSAYS,
    },
    "confirmed_active_tier_d": {
        "primary": ACTIVE_SCREENING_ASSAYS,
        "confirmatory": MOLECULAR_ASSAYS,
    },
    "serology_validation": {
        "primary": PRIMARY_SEROLOGY_ASSAYS,
        "confirmatory": NEUTRALIZATION_ASSAYS,
    },
    "molecular_validation": {
        "primary": ACTIVE_SCREENING_ASSAYS,
        "confirmatory": MOLECULAR_ASSAYS,
    },
}

CONFIRMATORY_REQUIRED_SYNTHESIS_PATHS = frozenset(
    {
        "serology_tier_b",
        "serology_tier_c",
        "serology_tier_d",
        "confirmed_active_tier_b",
        "confirmed_active_tier_c",
        "confirmed_active_tier_d",
        "serology_validation",
        "molecular_validation",
    }
)


def criterion_is_applicable(
    code: str, pathway: str, endpoint_key: str | None = None
) -> bool:
    """Return whether a criterion applies to the chosen measurement pathway."""
    if code == "Q10":
        if endpoint_key:
            return endpoint_key in Q10_APPLICABLE_ENDPOINTS
        return pathway == "serologic"
    return True


def applicable_maximum(pathway: str, endpoint_key: str | None = None) -> int:
    if pathway not in {"serologic", "direct_detection"}:
        raise ValueError(f"Unknown measurement pathway: {pathway}")
    return sum(
        criterion.maximum
        for criterion in CRITERIA
        if criterion_is_applicable(criterion.code, pathway, endpoint_key)
    )


def validate_scores(
    scores: Mapping[str, Any], pathway: str, endpoint_key: str | None = None
) -> list[str]:
    problems: list[str] = []
    for criterion in CRITERIA:
        value = scores.get(criterion.code)
        if not criterion_is_applicable(criterion.code, pathway, endpoint_key):
            if value not in (None, "", "N/A", "NA"):
                problems.append(
                    f"{criterion.code} must be N/A for the selected synthesis endpoint."
                )
            continue
        if value in (None, ""):
            problems.append(f"{criterion.code} is not scored.")
            continue
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            problems.append(f"{criterion.code} has an invalid score: {value!r}.")
            continue
        if numeric < 0 or numeric > criterion.maximum:
            problems.append(
                f"{criterion.code} must be between 0 and {criterion.maximum}, not {numeric}."
            )
    return problems


def score_assessment(
    scores: Mapping[str, Any], pathway: str, endpoint_key: str | None = None
) -> dict[str, Any]:
    """Calculate endpoint-specific item, domain, and total scores."""
    normalized: dict[str, int | None] = {}
    for criterion in CRITERIA:
        if not criterion_is_applicable(criterion.code, pathway, endpoint_key):
            normalized[criterion.code] = None
            continue
        value = scores.get(criterion.code)
        if value in (None, ""):
            normalized[criterion.code] = None
        else:
            try:
                numeric = int(value)
                normalized[criterion.code] = (
                    numeric if 0 <= numeric <= criterion.maximum else None
                )
            except (TypeError, ValueError):
                normalized[criterion.code] = None

    domain_results: dict[str, dict[str, int]] = {}
    for domain in DOMAINS:
        domain_items = [
            criterion
            for criterion in CRITERIA
            if criterion.domain == domain
            and criterion_is_applicable(criterion.code, pathway, endpoint_key)
        ]
        domain_results[domain] = {
            "score": sum(normalized[item.code] or 0 for item in domain_items),
            "maximum": sum(item.maximum for item in domain_items),
            "completed": sum(normalized[item.code] is not None for item in domain_items),
            "items": len(domain_items),
        }

    maximum = applicable_maximum(pathway, endpoint_key)
    scored_count = sum(value is not None for value in normalized.values())
    applicable_count = sum(
        criterion_is_applicable(criterion.code, pathway, endpoint_key)
        for criterion in CRITERIA
    )
    total = sum(value or 0 for value in normalized.values())
    return {
        "scores": normalized,
        "domains": domain_results,
        "total": total,
        "maximum": maximum,
        "percentage": (100 * total / maximum) if maximum else 0.0,
        "scored_count": scored_count,
        "applicable_count": applicable_count,
        "complete": scored_count == applicable_count,
        "validation": validate_scores(scores, pathway, endpoint_key),
    }


def render_assessment_png(
    metadata: Mapping[str, Any],
    scores: Mapping[str, Any],
    pathway: str,
    classification: Mapping[str, str],
    presentation_colors: Mapping[str, str] | None = None,
    branding_logo: bytes | None = None,
) -> bytes:
    """Render a self-contained PNG overview of one endpoint assessment.

    Colors indicate descriptive point attainment only. The final panel does not
    convert the continuous DARE-Arbo score into an unofficial risk category.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - declared runtime dependency
        raise RuntimeError("PNG rendering requires the 'Pillow' package.") from exc

    width, height = 1800, 1950
    colors = resolve_overview_colors(presentation_colors)
    canvas = Image.new("RGB", (width, height), colors["background"])
    draw = ImageDraw.Draw(canvas)

    def load_font(size: int, bold: bool = False):
        return _load_presentation_font(ImageFont, size, bold)

    # Streamlit scales the complete overview to the available browser width.
    # Presentation-sized source type keeps every label readable after scaling.
    title_font = load_font(70, bold=True)
    subtitle_font = load_font(32)
    section_font = load_font(35, bold=True)
    box_font = load_font(30, bold=True)
    body_font = load_font(30)
    endpoint_font = load_font(27)
    small_font = load_font(27)
    score_font = load_font(82, bold=True)

    def wrapped_lines(text: str, font: Any, max_width: int) -> list[str]:
        words = str(text).split()
        if not words:
            return [""]
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def centered_text(
        box: tuple[int, int, int, int],
        text: str,
        font: Any,
        fill: str,
        padding: int = 16,
    ) -> None:
        x1, y1, x2, y2 = box
        lines = wrapped_lines(text, font, max(20, x2 - x1 - 2 * padding))
        line_height = max(18, draw.textbbox((0, 0), "Ag", font=font)[3] + 5)
        total_height = line_height * len(lines)
        y = y1 + ((y2 - y1) - total_height) / 2
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            x = x1 + ((x2 - x1) - (bbox[2] - bbox[0])) / 2
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height

    def rounded_box(
        box: tuple[int, int, int, int],
        text: str,
        selected: bool = False,
        fill: str | None = None,
        outline: str | None = None,
        font: Any = box_font,
    ) -> None:
        background = fill or (colors["teal"] if selected else colors["paper"])
        border = outline or (colors["teal"] if selected else colors["border"])
        shadow_box = (box[0] + 5, box[1] + 7, box[2] + 5, box[3] + 7)
        draw.rounded_rectangle(shadow_box, radius=18, fill=colors["shadow"])
        draw.rounded_rectangle(box, radius=18, fill=background, outline=border, width=4 if selected else 2)
        centered_text(box, text, font, "#FFFFFF" if selected else colors["ink"])

    def arrow(start: tuple[float, float], end: tuple[float, float], fill: str = colors["line"]) -> None:
        draw.line((start, end), fill=fill, width=4)
        angle = math.atan2(end[1] - start[1], end[0] - start[0])
        size = 13
        points = [
            end,
            (
                end[0] - size * math.cos(angle - math.pi / 6),
                end[1] - size * math.sin(angle - math.pi / 6),
            ),
            (
                end[0] - size * math.cos(angle + math.pi / 6),
                end[1] - size * math.sin(angle + math.pi / 6),
            ),
        ]
        draw.polygon(points, fill=fill)

    def poly_arrow(points: list[tuple[float, float]], fill: str = colors["line"]) -> None:
        draw.line(points, fill=fill, width=4, joint="curve")
        arrow(points[-2], points[-1], fill=fill)

    def branch(
        source: tuple[float, float],
        targets: list[tuple[float, float]],
        trunk_y: float,
        fill: str = colors["line"],
    ) -> None:
        draw.line((source, (source[0], trunk_y)), fill=fill, width=4)
        draw.line(
            ((min(target[0] for target in targets), trunk_y), (max(target[0] for target in targets), trunk_y)),
            fill=fill,
            width=4,
        )
        for target in targets:
            arrow((target[0], trunk_y), target, fill=fill)

    def merge(
        sources: list[tuple[float, float]],
        target: tuple[float, float],
        trunk_y: float,
        fill: str = colors["line"],
    ) -> None:
        for source in sources:
            draw.line((source, (source[0], trunk_y)), fill=fill, width=4)
        draw.line(
            ((min(source[0] for source in sources), trunk_y), (max(source[0] for source in sources), trunk_y)),
            fill=fill,
            width=4,
        )
        arrow((target[0], trunk_y), target, fill=fill)

    endpoint_key = str(metadata.get("endpoint_key") or "other")
    if endpoint_key not in ENDPOINTS_BY_KEY:
        endpoint_key = "other"
    result = score_assessment(scores, pathway, endpoint_key)
    endpoint_label = str(classification.get("endpoint") or metadata.get("endpoint_label") or "Endpoint not specified")

    # Header band and study metadata.
    draw.rectangle((0, 0, width, 190), fill=colors["charcoal"])
    draw.rectangle((0, 186, width, 190), fill=colors["teal"])
    draw.text((72, 28), "Arboviral Risk of Bias Assessor", font=title_font, fill="#FFFFFF")
    draw.text((74, 118), "DARE-Arbo | endpoint-specific quality overview", font=subtitle_font, fill="#FFFFFF")
    if branding_logo:
        try:
            logo = Image.open(BytesIO(branding_logo)).convert("RGBA")
            alpha_bounds = logo.getchannel("A").getbbox()
            if alpha_bounds:
                logo = logo.crop(alpha_bounds)
            logo.thumbnail((132, 132), Image.Resampling.LANCZOS)
            logo_panel = (1588, 14, 1758, 174)
            draw.rounded_rectangle(
                logo_panel,
                radius=18,
                fill="#000000",
                outline=colors["cyan"],
                width=2,
            )
            logo_x = logo_panel[0] + (logo_panel[2] - logo_panel[0] - logo.width) // 2
            logo_y = logo_panel[1] + (logo_panel[3] - logo_panel[1] - logo.height) // 2
            canvas.paste(logo, (logo_x, logo_y), logo)
        except (OSError, ValueError):
            pass
    study_bits = [
        str(metadata.get("study_id") or "Study not named"),
        str(metadata.get("virus") or "Virus not specified"),
        str(metadata.get("assessment_date") or ""),
    ]
    meta_text = "  |  ".join(bit for bit in study_bits if bit)
    meta_width = min(1180, draw.textbbox((0, 0), meta_text, font=body_font)[2] + 46)
    draw.rounded_rectangle((68, 207, 68 + meta_width, 259), radius=18, fill=colors["paper"], outline=colors["border"], width=2)
    draw.text((88, 216), meta_text, font=body_font, fill=colors["muted"])

    entry_box = (690, 286, 1110, 378)
    rounded_box(entry_box, "Assessment entry")
    draw.text((72, 416), "1. Synthesis endpoint", font=section_font, fill=colors["ink"])

    endpoint_options = [endpoint.label for endpoint in ENDPOINTS]
    option_gap = 10
    option_width = (width - 144 - option_gap * (len(endpoint_options) - 1)) // len(endpoint_options)
    endpoint_boxes: list[tuple[int, int, int, int]] = []
    for index, option in enumerate(endpoint_options):
        x1 = 72 + index * (option_width + option_gap)
        box = (x1, 472, x1 + option_width, 618)
        endpoint_boxes.append(box)
        is_selected = option == endpoint_label
        display_option = re.sub(r"(?<=\w)-(?=\w)", "- ", option)
        rounded_box(
            box,
            display_option,
            selected=is_selected,
            fill=colors["navy"] if is_selected else colors["paper"],
            outline=colors["cyan"] if is_selected else colors["border"],
            font=endpoint_font,
        )

    branch(
        (900, entry_box[3]),
        [((box[0] + box[2]) / 2, box[1]) for box in endpoint_boxes],
        trunk_y=448,
    )

    selected_endpoint = next(
        (box for option, box in zip(endpoint_options, endpoint_boxes) if option == endpoint_label),
        endpoint_boxes[-1],
    )
    pathway_heading = (660, 690, 1140, 782)
    selected_endpoint_x = (selected_endpoint[0] + selected_endpoint[2]) / 2
    poly_arrow(
        [
            (selected_endpoint_x, selected_endpoint[3]),
            (selected_endpoint_x, 654),
            (900, 654),
            (900, pathway_heading[1]),
        ],
        fill=colors["teal"],
    )
    rounded_box(pathway_heading, "2. DARE measurement pathway")

    pathway_boxes = {
        "serologic": (340, 852, 820, 974),
        "direct_detection": (980, 852, 1460, 974),
    }
    serologic_pathway_label = (
        "Test-adjusted endpoint · Q10 applies · max 19"
        if endpoint_key in Q10_APPLICABLE_ENDPOINTS
        else "Other serologic endpoint · Q10 N/A · max 18"
    )
    for key, box in pathway_boxes.items():
        rounded_box(
            box,
            serologic_pathway_label if key == "serologic" else "Direct/reference endpoint · Q10 N/A · max 18",
            selected=pathway == key,
            fill=colors["teal"] if pathway == key else colors["paper"],
            outline=colors["cyan"] if pathway == key else colors["border"],
            font=body_font,
        )

    branch(
        (900, pathway_heading[3]),
        [((box[0] + box[2]) / 2, box[1]) for box in pathway_boxes.values()],
        trunk_y=820,
    )

    selected_pathway = pathway_boxes[pathway]
    domain_heading = (660, 1040, 1140, 1132)
    selected_pathway_x = (selected_pathway[0] + selected_pathway[2]) / 2
    poly_arrow(
        [
            (selected_pathway_x, selected_pathway[3]),
            (selected_pathway_x, 1008),
            (900, 1008),
            (900, domain_heading[1]),
        ],
        fill=colors["teal"],
    )
    rounded_box(domain_heading, "3. Domain appraisal")

    domain_gap = 22
    domain_width = (width - 144 - domain_gap * 3) // 4
    domain_boxes: list[tuple[int, int, int, int]] = []
    domain_accents = [colors["blue"], colors["purple"], colors["teal"], colors["gold"]]
    domain_tints = [colors["blue_pale"], colors["purple_pale"], colors["mint"], colors["gold_pale"]]
    for index, domain in enumerate(DOMAINS):
        x1 = 72 + index * (domain_width + domain_gap)
        box = (x1, 1202, x1 + domain_width, 1528)
        domain_boxes.append(box)
        values = result["domains"][domain]
        shadow_box = (box[0] + 5, box[1] + 7, box[2] + 5, box[3] + 7)
        draw.rounded_rectangle(shadow_box, radius=20, fill=colors["shadow"])
        draw.rounded_rectangle(box, radius=20, fill=colors["paper"], outline=colors["border"], width=2)
        draw.rounded_rectangle((box[0], box[1], box[2], box[1] + 14), radius=7, fill=domain_accents[index])
        title_panel = (box[0] + 20, box[1] + 22, box[2] - 20, box[1] + 92)
        draw.rounded_rectangle(title_panel, radius=13, fill=domain_tints[index])
        centered_text(title_panel, domain, box_font, colors["ink"], padding=10)
        score_text = f"{values['score']} / {values['maximum']}"
        score_bbox = draw.textbbox((0, 0), score_text, font=section_font)
        draw.text((box[0] + (domain_width - (score_bbox[2] - score_bbox[0])) / 2, box[1] + 112), score_text, font=section_font, fill=domain_accents[index])
        bar = (box[0] + 28, box[1] + 166, box[2] - 28, box[1] + 190)
        draw.rounded_rectangle(bar, radius=12, fill=colors["grey"])
        fraction = values["score"] / values["maximum"] if values["maximum"] else 0
        if fraction > 0:
            draw.rounded_rectangle((bar[0], bar[1], bar[0] + int((bar[2] - bar[0]) * fraction), bar[3]), radius=12, fill=domain_accents[index])

        domain_items = [criterion for criterion in CRITERIA if criterion.domain == domain]
        chip_x, chip_y = box[0] + 28, box[1] + 218
        for criterion in domain_items:
            value = result["scores"][criterion.code]
            if value is None:
                chip_fill, chip_text = colors["grey"], f"{criterion.code} N/A" if not criterion_is_applicable(criterion.code, pathway, endpoint_key) else f"{criterion.code} -"
            elif value == 0:
                chip_fill, chip_text = colors["red_pale"], f"{criterion.code} {value}/{criterion.maximum}"
            elif value == criterion.maximum:
                chip_fill, chip_text = colors["green_pale"], f"{criterion.code} {value}/{criterion.maximum}"
            else:
                chip_fill, chip_text = colors["gold_pale"], f"{criterion.code} {value}/{criterion.maximum}"
            chip_w = 112 if len(chip_text) < 8 else 134
            if chip_x + chip_w > box[2] - 24:
                chip_x = box[0] + 28
                chip_y += 48
            draw.rounded_rectangle((chip_x, chip_y, chip_x + chip_w, chip_y + 38), radius=14, fill=chip_fill)
            centered_text((chip_x, chip_y, chip_x + chip_w, chip_y + 38), chip_text, small_font, colors["ink"], padding=5)
            chip_x += chip_w + 8
    branch(
        (900, domain_heading[3]),
        [((box[0] + box[2]) / 2, box[1]) for box in domain_boxes],
        trunk_y=1168,
    )

    final_box = (240, 1602, 1560, 1818)
    merge(
        [((box[0] + box[2]) / 2, box[3]) for box in domain_boxes],
        (900, final_box[1]),
        trunk_y=1565,
    )
    draw.rounded_rectangle((final_box[0] + 7, final_box[1] + 9, final_box[2] + 7, final_box[3] + 9), radius=27, fill=colors["shadow"])
    draw.rounded_rectangle(final_box, radius=27, fill=colors["paper"], outline=colors["teal"], width=4)

    ring_box = (286, 1620, 466, 1800)
    draw.ellipse(ring_box, outline=colors["grey"], width=16)
    score_fraction = result["total"] / result["maximum"] if result["maximum"] else 0
    draw.arc(ring_box, start=-90, end=-90 + int(360 * score_fraction), fill=colors["cyan"], width=16)
    ring_font = load_font(40, bold=True)
    centered_text(ring_box, f"{result['total']}/{result['maximum']}", ring_font, colors["navy"], padding=8)
    draw.text((500, 1623), "DARE-Arbo score", font=box_font, fill=colors["ink"])
    status = "Assessment complete" if result["complete"] else f"Draft · {result['scored_count']}/{result['applicable_count']} items scored"
    draw.text((500, 1670), status, font=body_font, fill=colors["teal"] if result["complete"] else colors["gold"])
    centered_text((486, 1714, 760, 1790), "Continuous score\nNo official risk band", small_font, colors["muted"])
    draw.line((790, 1624, 790, 1798), fill=colors["border"], width=2)
    centered_text((818, 1622, 1192, 1692), str(classification.get("tier") or "Not tiered"), section_font, colors["navy"])
    centered_text((818, 1692, 1192, 1796), str(classification.get("analysis_role") or "Retain as a distinct endpoint"), small_font, colors["muted"])
    draw.rounded_rectangle((1212, 1632, 1518, 1788), radius=17, fill=colors["mint"], outline="#FCA5A5", width=2)
    centered_text((1228, 1646, 1502, 1712), pathway.replace("_", " ").title(), box_font, colors["teal_dark"])
    centered_text((1228, 1712, 1502, 1770), f"Applicable maximum: {result['maximum']}", small_font, colors["muted"])

    draw.text((72, 1853), "ITEM ATTAINMENT", font=small_font, fill=colors["navy"])
    legend_items = [
        (colors["green_pale"], "Maximum points"),
        (colors["gold_pale"], "Partial points"),
        (colors["red_pale"], "Zero points"),
        (colors["grey"], "Unscored / N/A"),
    ]
    legend_x = 300
    for fill, label in legend_items:
        draw.rounded_rectangle((legend_x, 1848, legend_x + 28, 1876), radius=8, fill=fill)
        draw.text((legend_x + 38, 1849), label, font=small_font, fill=colors["muted"])
        legend_x += 340
    draw.text((72, 1903), "Descriptive colors show point attainment only | not official risk-of-bias categories", font=small_font, fill=colors["muted"])

    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True, dpi=(180, 180))
    return output.getvalue()


def _study_design_path_keys(plan: Mapping[str, Any]) -> list[str]:
    """Return ordered, unique synthesis paths for a single- or mixed-endpoint plan."""
    raw_paths = plan.get("synthesis_path_keys")
    if isinstance(raw_paths, str):
        candidates = [raw_paths]
    elif isinstance(raw_paths, Iterable):
        candidates = [str(value) for value in raw_paths]
    else:
        candidates = []
    primary = str(plan.get("synthesis_path_key") or "")
    if primary:
        candidates.insert(0, primary)
    path_keys = list(dict.fromkeys(value for value in candidates if value))
    unknown = [value for value in path_keys if value not in SYNTHESIS_PATHS_BY_KEY]
    if unknown:
        raise ValueError(f"Unknown synthesis path(s): {', '.join(unknown)}")
    if not path_keys:
        raise ValueError("At least one synthesis path is required.")
    return path_keys


def _study_design_viruses(plan: Mapping[str, Any]) -> list[str]:
    """Return ordered virus targets, retaining legacy single-virus plans."""
    raw_viruses = plan.get("viruses")
    if isinstance(raw_viruses, str):
        candidates = re.split(r"[;,]", raw_viruses)
    elif isinstance(raw_viruses, Iterable):
        candidates = [str(value) for value in raw_viruses]
    else:
        candidates = []
    if not candidates:
        candidates = re.split(r"[;,]", str(plan.get("virus") or ""))
    viruses = list(dict.fromkeys(value.strip() for value in candidates if value.strip()))
    return viruses or ["Arbovirus not specified"]


def _study_design_surveillance_outputs(plan: Mapping[str, Any]) -> list[str]:
    """Return the active/passive stream outputs that require distinct estimates."""
    mode = str(plan.get("surveillance_mode") or "Active surveillance")
    if mode.startswith("Passive"):
        return ["Passive surveillance"]
    if not mode.startswith("Hybrid"):
        return ["Active surveillance"]
    estimator_mode = str(
        plan.get("stream_estimator_mode") or "Separate active and passive estimates"
    )
    if estimator_mode.startswith("Combined"):
        return ["Hybrid adjusted"]
    if estimator_mode.startswith("Separate and combined"):
        return ["Active surveillance", "Passive surveillance", "Hybrid adjusted"]
    return ["Active surveillance", "Passive surveillance"]


def evaluate_study_design_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize planning coverage across design, assay, and reporting pillars."""
    path_keys = _study_design_path_keys(plan)
    synthesis_path_key = path_keys[0]
    viruses = _study_design_viruses(plan)
    surveillance_outputs = _study_design_surveillance_outputs(plan)
    mixed_endpoints = len(path_keys) > 1
    multiplex = len(viruses) > 1
    hybrid_surveillance = str(plan.get("surveillance_mode") or "").startswith("Hybrid")
    pathway_assays = plan.get("pathway_assays")
    if not isinstance(pathway_assays, Mapping):
        pathway_assays = {}

    primary_selected = True
    confirmatory_selected = True
    confirmatory_required = False
    for index, path_key in enumerate(path_keys):
        path_assays = pathway_assays.get(path_key, {})
        if not isinstance(path_assays, Mapping):
            path_assays = {}
        primary_value = path_assays.get("primary_assay")
        confirmatory_value = path_assays.get("confirmatory_assay")
        if index == 0:
            primary_value = primary_value or plan.get("primary_assay")
            confirmatory_value = confirmatory_value or plan.get("confirmatory_assay")
        primary_selected = primary_selected and bool(primary_value)
        path_requires_confirmation = path_key in CONFIRMATORY_REQUIRED_SYNTHESIS_PATHS
        confirmatory_required = confirmatory_required or path_requires_confirmation
        confirmatory_selected = confirmatory_selected and bool(
            confirmatory_value or not path_requires_confirmation
        )
    sampling_method = str(plan.get("sampling_recruitment_method") or "")
    q4_strength = Q4_USER_METHOD_SCORES.get(sampling_method)
    sampling_fraction = 0.0 if q4_strength is None else q4_strength / 3
    elements: dict[str, dict[str, float]] = {
        "Study design": {
            "Target population explicitly defined": float(bool(str(plan.get("target_population") or "").strip())),
            "Sampling frame aligned with the target": float(bool(plan.get("sampling_frame_defined"))),
            "Sampling/recruitment strength": sampling_fraction,
            "Eligibility criteria prespecified": float(bool(plan.get("eligibility_defined"))),
            "Sample-size rationale documented": float(bool(plan.get("sample_size_rationale"))),
            "Nonresponse/completeness plan documented": float(bool(plan.get("nonresponse_plan"))),
        },
        "Assay design": {
            "One endpoint-defining primary assay selected per pathway": float(primary_selected),
            "Required confirmation/verification specified": float(
                confirmatory_selected or not confirmatory_required
            ),
            "Specimen type and collection timing specified": float(bool(plan.get("specimen_timing"))),
            "Validation and quality-control procedures specified": float(bool(plan.get("validation_controls"))),
            "Cross-reactivity and assay-performance plan specified": float(bool(plan.get("cross_reactivity_plan"))),
            "Planned testing/retesting denominator specified": float(bool(plan.get("testing_denominator_plan"))),
        },
        "Standard reporting": {
            "Observed numerator and denominator prespecified": float(bool(plan.get("numerator_denominator_reporting"))),
            "Participant/specimen flow and verification strata reported": float(bool(plan.get("flow_reporting"))),
            "Assay manufacturer, protocol, cutoffs, and controls reported": float(bool(plan.get("assay_reporting"))),
            "Missingness, exclusions, and nonresponse reported": float(bool(plan.get("missingness_reporting"))),
            "Estimator, weighting, and adjustment methods reported": float(bool(plan.get("estimator_reporting"))),
            "Protocol, codebook, or analysis materials shareable": float(bool(plan.get("reproducibility_reporting"))),
        },
    }
    if hybrid_surveillance:
        elements["Study design"][
            "Active/passive source populations and integration are prespecified"
        ] = float(bool(plan.get("stream_integration_plan")))
    if mixed_endpoints:
        elements["Assay design"][
            "Endpoint-specific testing pathways remain distinguishable"
        ] = float(bool(plan.get("endpoint_specific_assay_plan")))
        elements["Standard reporting"][
            "Endpoint-specific flows, numerators, denominators, and estimates are reported"
        ] = float(bool(plan.get("mixed_endpoint_reporting_plan")))
    if multiplex:
        elements["Assay design"][
            "Virus-specific performance, cross-reactivity, and interpretation are prespecified"
        ] = float(bool(plan.get("multiplex_assay_plan")))
        elements["Standard reporting"][
            "Virus-specific results and co-detections are reported separately"
        ] = float(bool(plan.get("multiplex_reporting_plan")))
    pillar_results: dict[str, dict[str, Any]] = {}
    for pillar, values in elements.items():
        earned = sum(values.values())
        maximum = len(values)
        pillar_results[pillar] = {
            "earned": earned,
            "maximum": maximum,
            "percentage": round(100 * earned / maximum, 1),
            "missing": [label for label, value in values.items() if value < 1],
        }
    balance_floor = min(result["percentage"] for result in pillar_results.values())
    recommendations: list[str] = []
    for pillar, result in pillar_results.items():
        if result["missing"]:
            recommendations.append(
                f"{pillar}: complete " + "; ".join(result["missing"][:3])
            )
    if q4_strength in (0, 1):
        recommendations.append(
            "Study design: consider probability-based, structured multisite, census, or documented complete capture when compatible with the target population."
        )
    if confirmatory_required and not confirmatory_selected:
        recommendations.append(
            "Assay design: select the confirmatory/verification method required by the chosen testing strategy."
        )
    if any(
        SYNTHESIS_PATHS_BY_KEY[path_key].endpoint_key == "assay_performance"
        for path_key in path_keys
    ):
        recommendations.append(
            "Interpretation: this pathway produces an assay-validation dataset, not a population prevalence endpoint."
        )
    analysis_units = [
        {
            "virus": virus,
            "surveillance_stream": stream,
            "endpoint_key": SYNTHESIS_PATHS_BY_KEY[path_key].endpoint_key,
            "endpoint": ENDPOINTS_BY_KEY[SYNTHESIS_PATHS_BY_KEY[path_key].endpoint_key].label,
            "synthesis_path_key": path_key,
            "testing_strategy": SYNTHESIS_PATHS_BY_KEY[path_key].testing_strategy,
            "summary_estimator": SYNTHESIS_PATHS_BY_KEY[path_key].summary_estimator,
            "numerator": SYNTHESIS_PATHS_BY_KEY[path_key].numerator,
            "denominator": SYNTHESIS_PATHS_BY_KEY[path_key].denominator,
        }
        for stream in surveillance_outputs
        for virus in viruses
        for path_key in path_keys
    ]
    return {
        "pillars": pillar_results,
        "balance_floor": balance_floor,
        "recommendations": recommendations,
        "confirmatory_required": confirmatory_required,
        "all_required_assays_selected": primary_selected and confirmatory_selected,
        "sampling_method_score": q4_strength,
        "synthesis_path_keys": path_keys,
        "viruses": viruses,
        "surveillance_outputs": surveillance_outputs,
        "analysis_units": analysis_units,
        "analysis_unit_count": len(analysis_units),
        "mixed_endpoints": mixed_endpoints,
        "virus_multiplexing": multiplex,
        "hybrid_surveillance": hybrid_surveillance,
    }


def render_study_design_png(
    plan: Mapping[str, Any],
    presentation_colors: Mapping[str, str] | None = None,
    branding_logo: bytes | None = None,
) -> bytes:
    """Render a high-resolution DARE-Arbo surveillance study-design flow figure."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Study-design PNG rendering requires Pillow.") from exc

    path_keys = _study_design_path_keys(plan)
    synthesis_path_key = path_keys[0]
    path = SYNTHESIS_PATHS_BY_KEY[synthesis_path_key]
    readiness = evaluate_study_design_plan(plan)
    viruses = readiness["viruses"]
    surveillance_outputs = readiness["surveillance_outputs"]
    colors = resolve_overview_colors(presentation_colors)
    width, height = 2000, 1750
    canvas = Image.new("RGB", (width, height), colors["background"])
    draw = ImageDraw.Draw(canvas)

    def font(size: int, bold: bool = False):
        return _load_presentation_font(ImageFont, size, bold)

    # The figure is scaled to the available Streamlit column. These larger
    # source sizes remain legible after browser-side downscaling and also
    # produce presentation-ready downloaded graphics.
    title_font = font(66, True)
    subtitle_font = font(32)
    heading_font = font(34, True)
    body_font = font(29)
    small_font = font(25)
    metric_font = font(43, True)

    def wrap(text: str, selected_font: Any, max_width: int) -> list[str]:
        words = str(text or "Not specified").split()
        if not words:
            return ["Not specified"]
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textbbox((0, 0), candidate, font=selected_font)[2] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def centered_lines(box: tuple[int, int, int, int], text: str, selected_font: Any, fill: str) -> None:
        lines = wrap(text, selected_font, box[2] - box[0] - 36)
        line_height = draw.textbbox((0, 0), "Ag", font=selected_font)[3] + 7
        y = box[1] + ((box[3] - box[1]) - line_height * len(lines)) / 2
        for line in lines:
            line_width = draw.textbbox((0, 0), line, font=selected_font)[2]
            draw.text((box[0] + (box[2] - box[0] - line_width) / 2, y), line, font=selected_font, fill=fill)
            y += line_height

    def arrow(y1: int, y2: int, x: int | None = None) -> None:
        x = width // 2 if x is None else x
        draw.line((x, y1, x, y2 - 12), fill=colors["line"], width=4)
        draw.polygon(((x, y2), (x - 12, y2 - 18), (x + 12, y2 - 18)), fill=colors["line"])

    def flow_box(
        box: tuple[int, int, int, int],
        label: str,
        detail: str,
        accent: str,
        detail_font: Any = body_font,
    ) -> None:
        draw.rounded_rectangle(
            (box[0] + 5, box[1] + 6, box[2] + 5, box[3] + 6),
            radius=17,
            fill=colors["shadow"],
        )
        draw.rounded_rectangle(
            box,
            radius=17,
            fill=colors["paper"],
            outline=colors["border"],
            width=2,
        )
        draw.rectangle((box[0], box[1], box[0] + 12, box[3]), fill=accent)
        draw.text((box[0] + 38, box[1] + 15), label, font=heading_font, fill=colors["ink"])
        detail_y = box[1] + 65
        detail_step = draw.textbbox((0, 0), "Ag", font=detail_font)[3] + 7
        for detail_line in wrap(detail, detail_font, box[2] - box[0] - 82)[:2]:
            draw.text((box[0] + 38, detail_y), detail_line, font=detail_font, fill=colors["muted"])
            detail_y += detail_step

    draw.rectangle((0, 0, width, 176), fill=colors["charcoal"])
    draw.rectangle((0, 172, width, 176), fill=colors["teal"])
    draw.text((70, 35), "DARE-Arbo Surveillance Study Designer", font=title_font, fill="#FFFFFF")
    draw.text((72, 108), "Conceptual planning flow | study design + assay design + standard reporting", font=subtitle_font, fill="#FFFFFF")
    if branding_logo:
        try:
            logo = Image.open(BytesIO(branding_logo)).convert("RGBA")
            bounds = logo.getchannel("A").getbbox()
            if bounds:
                logo = logo.crop(bounds)
            logo.thumbnail((135, 135), Image.Resampling.LANCZOS)
            canvas.paste(logo, (width - logo.width - 72, 20), logo)
        except (OSError, ValueError):
            pass

    study_title = str(plan.get("study_title") or "Untitled surveillance study")
    scope_labels = []
    if readiness["hybrid_surveillance"]:
        scope_labels.append("active + passive")
    else:
        scope_labels.append(surveillance_outputs[0].replace(" surveillance", "").lower())
    scope_labels.append("mixed endpoints" if readiness["mixed_endpoints"] else "single endpoint")
    scope_labels.append("multiplex viruses" if readiness["virus_multiplexing"] else "single virus")
    draw.text((72, 205), study_title, font=heading_font, fill=colors["ink"])
    draw.text((72, 242), " | ".join(scope_labels), font=body_font, fill=colors["muted"])

    pathway_assays = plan.get("pathway_assays")
    if not isinstance(pathway_assays, Mapping):
        pathway_assays = {}
    assay_summaries: list[str] = []
    for index, path_key in enumerate(path_keys):
        selected = pathway_assays.get(path_key, {})
        if not isinstance(selected, Mapping):
            selected = {}
        primary_assay = str(
            selected.get("primary_assay")
            or (plan.get("primary_assay") if index == 0 else "")
            or "primary assay pending"
        )
        confirmatory_assay = str(
            selected.get("confirmatory_assay")
            or (plan.get("confirmatory_assay") if index == 0 else "")
            or "no confirmation required/pending"
        )
        endpoint_label = ENDPOINTS_BY_KEY[SYNTHESIS_PATHS_BY_KEY[path_key].endpoint_key].label
        assay_summaries.append(f"{endpoint_label}: {primary_assay} -> {confirmatory_assay}")

    node_x1, node_x2 = 245, 1755
    node_height, gap = 142, 24
    first_y = 292
    target_box = (node_x1, first_y, node_x2, first_y + node_height)
    flow_box(
        target_box,
        "1. Target population and surveillance frame",
        f"{plan.get('target_population') or 'Define target population'} | {plan.get('surveillance_architecture') or 'Select surveillance architecture'}",
        colors["navy"],
    )

    branch_y1 = first_y + node_height + gap
    branch_y2 = branch_y1 + node_height
    branch_gap = 30
    branch_width = (node_x2 - node_x1 - branch_gap) // 2
    stream_box = (node_x1, branch_y1, node_x1 + branch_width, branch_y2)
    virus_box = (stream_box[2] + branch_gap, branch_y1, node_x2, branch_y2)
    center_y = target_box[3] + 13
    stream_center = (stream_box[0] + stream_box[2]) // 2
    virus_center = (virus_box[0] + virus_box[2]) // 2
    draw.line((width // 2, target_box[3], width // 2, center_y), fill=colors["line"], width=4)
    draw.line((stream_center, center_y, virus_center, center_y), fill=colors["line"], width=4)
    arrow(center_y, branch_y1, stream_center)
    arrow(center_y, branch_y1, virus_center)
    flow_box(
        stream_box,
        "2A. Surveillance stream(s)",
        " + ".join(surveillance_outputs),
        colors["navy"],
    )
    flow_box(
        virus_box,
        "2B. Virus target(s)",
        ", ".join(viruses),
        colors["teal"],
    )

    full_nodes = (
        (
            "3. Sampling and recruitment",
            str(plan.get("sampling_recruitment_method") or "Select sampling/recruitment method"),
            colors["navy"],
        ),
        (
            "4. Endpoint-specific assay pathways",
            " | ".join(assay_summaries),
            colors["teal"],
        ),
        (
            "5. Separate study-virus-estimand outputs",
            f"{readiness['analysis_unit_count']} analysis output(s); retain observed numerator, denominator, verification strata, and estimator for each.",
            colors["cyan"],
        ),
        (
            "6. Standard reporting and reproducible outputs",
            str(plan.get("reporting_output") or "Virus-, endpoint-, and stream-specific flows, assay details, estimators, missingness, and shareable materials"),
            colors["cyan"],
        ),
    )
    merge_y = branch_y2 + 13
    draw.line((stream_center, branch_y2, stream_center, merge_y), fill=colors["line"], width=4)
    draw.line((virus_center, branch_y2, virus_center, merge_y), fill=colors["line"], width=4)
    draw.line((stream_center, merge_y, virus_center, merge_y), fill=colors["line"], width=4)
    next_y = branch_y2 + gap
    arrow(merge_y, next_y)
    for index, (label, detail, accent) in enumerate(full_nodes):
        y1 = next_y + index * (node_height + gap)
        y2 = y1 + node_height
        flow_box((node_x1, y1, node_x2, y2), label, detail, accent)
        if index < len(full_nodes) - 1:
            arrow(y2 + 3, y2 + gap - 2)

    pillar_y1, pillar_y2 = 1300, 1680
    pillar_gap = 28
    pillar_width = (width - 144 - pillar_gap * 2) // 3
    pillar_accents = (colors["navy"], colors["teal"], colors["cyan"])
    for index, ((pillar, result), accent) in enumerate(zip(readiness["pillars"].items(), pillar_accents)):
        x1 = 72 + index * (pillar_width + pillar_gap)
        x2 = x1 + pillar_width
        draw.rounded_rectangle((x1, pillar_y1, x2, pillar_y2), radius=18, fill=colors["paper"], outline=colors["border"], width=2)
        draw.rectangle((x1, pillar_y1, x2, pillar_y1 + 9), fill=accent)
        draw.text((x1 + 28, pillar_y1 + 28), pillar, font=heading_font, fill=colors["ink"])
        draw.text((x1 + 28, pillar_y1 + 82), f"{result['percentage']:.0f}%", font=metric_font, fill=accent)
        bar = (x1 + 28, pillar_y1 + 144, x2 - 28, pillar_y1 + 170)
        draw.rounded_rectangle(bar, radius=12, fill=colors["grey"])
        completed_x = bar[0] + round((bar[2] - bar[0]) * result["percentage"] / 100)
        if completed_x > bar[0]:
            draw.rounded_rectangle((bar[0], bar[1], completed_x, bar[3]), radius=12, fill=accent)
        missing = result["missing"][:2]
        if missing:
            draw.text((x1 + 28, pillar_y1 + 198), "Next planning priorities", font=small_font, fill=colors["muted"])
            cursor_y = pillar_y1 + 238
            for item in missing:
                lines = wrap("• " + item, small_font, pillar_width - 56)
                for line in lines[:2]:
                    draw.text((x1 + 28, cursor_y), line, font=small_font, fill=colors["ink"])
                    cursor_y += 31
                cursor_y += 7
        else:
            draw.text((x1 + 28, pillar_y1 + 215), "All listed planning elements documented.", font=small_font, fill=colors["green"])

    draw.text(
        (72, 1712),
        f"Planning coverage floor across the three pillars: {readiness['balance_floor']:.0f}% | This is a design aid, not a completed DARE-Arbo risk-of-bias score.",
        font=small_font,
        fill=colors["muted"],
    )
    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True, dpi=(180, 180))
    return output.getvalue()


def render_surveillance_design_report_pdf(
    plan: Mapping[str, Any],
    readiness: Mapping[str, Any] | None = None,
    design_png: bytes | None = None,
    presentation_colors: Mapping[str, str] | None = None,
    branding_logo: bytes | None = None,
    partner_logo: bytes | None = None,
) -> bytes:
    """Build a branded, protocol-ready surveillance study design report PDF."""
    try:
        from reportlab.lib import colors as pdf_colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Image as PdfImage,
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:  # pragma: no cover - dependency shown by UI
        raise RuntimeError(
            "Surveillance report generation requires the 'reportlab' package."
        ) from exc

    evaluated = dict(readiness or evaluate_study_design_plan(plan))
    theme = resolve_overview_colors(presentation_colors)
    if design_png is None:
        design_png = render_study_design_png(
            plan,
            presentation_colors=presentation_colors,
            branding_logo=branding_logo,
        )

    def clean(value: Any) -> str:
        text = str(value if value not in (None, "") else "Not specified")
        replacements = {
            "\u2013": "-", "\u2014": "-", "\u2011": "-", "\u2212": "-",
            "\u2192": "->", "\u2265": ">=", "\u2264": "<=", "\u00d7": "x",
        }
        for source, target in replacements.items():
            text = text.replace(source, target)
        return html.escape(text)

    navy = pdf_colors.HexColor(theme["navy"])
    primary = pdf_colors.HexColor(theme["teal"])
    secondary = pdf_colors.HexColor(theme["cyan"])
    ink = pdf_colors.HexColor(theme["ink"])
    muted = pdf_colors.HexColor(theme["muted"])
    pale = pdf_colors.HexColor(theme["mint"])
    border = pdf_colors.HexColor(theme["border"])
    background = pdf_colors.HexColor(theme["background"])
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.68 * inch,
        bottomMargin=0.76 * inch,
        title="DARE-Arbo Surveillance Study Design Report",
        author="DARE-Arbo Designer",
        subject="Protocol-oriented arbovirus surveillance study design",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="DareTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=22, leading=26, textColor=navy, alignment=TA_LEFT,
        spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="DareSubtitle", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9.5, leading=13, textColor=muted, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="DareHeading", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=13, leading=16, textColor=navy, spaceBefore=10, spaceAfter=7,
    ))
    styles.add(ParagraphStyle(
        name="DareBody", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=8.8, leading=12.2, textColor=ink, spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="DareSmall", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=7.4, leading=9.6, textColor=ink,
    ))
    styles.add(ParagraphStyle(
        name="DareClosing", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=8.2, leading=10.4, textColor=ink, spaceAfter=0,
    ))
    styles.add(ParagraphStyle(
        name="DareSmallWhite", parent=styles["BodyText"], fontName="Helvetica-Bold",
        fontSize=7.4, leading=9.6, textColor=pdf_colors.white,
    ))
    styles.add(ParagraphStyle(
        name="DareWhite", parent=styles["BodyText"], fontName="Helvetica-Bold",
        fontSize=9, leading=12, textColor=pdf_colors.white, alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        name="DareUnit", parent=styles["Heading3"], fontName="Helvetica-Bold",
        fontSize=9.4, leading=12, textColor=primary, spaceAfter=4, keepWithNext=True,
    ))

    def paragraph(value: Any, style: str = "DareBody") -> Any:
        return Paragraph(clean(value), styles[style])

    def section(title: str) -> Any:
        return Paragraph(clean(title), styles["DareHeading"])

    def key_value_table(rows: list[tuple[str, Any]], widths: tuple[float, float] = (1.8, 5.25)) -> Any:
        table = Table(
            [[paragraph(label, "DareSmall"), paragraph(value, "DareSmall")] for label, value in rows],
            colWidths=[widths[0] * inch, widths[1] * inch],
            hAlign="LEFT",
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), pale),
            ("TEXTCOLOR", (0, 0), (-1, -1), ink),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.45, border),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return table

    def logo_flowable(data: bytes | None) -> Any:
        if not data:
            return Spacer(0.68 * inch, 0.68 * inch)
        try:
            return PdfImage(BytesIO(data), width=0.68 * inch, height=0.68 * inch, kind="proportional")
        except Exception:
            return Spacer(0.68 * inch, 0.68 * inch)

    title_block = Table(
        [[
            logo_flowable(branding_logo),
            Paragraph(
                "DARE-Arbo Surveillance Study Design Report",
                styles["DareTitle"],
            ),
            logo_flowable(partner_logo),
        ]],
        colWidths=[0.78 * inch, 5.48 * inch, 0.78 * inch],
    )
    title_block.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    created_at = str(plan.get("created_at") or "")
    report_status = (
        "Planning framework complete"
        if float(evaluated.get("balance_floor") or 0) >= 100
        else "Planning priorities remain"
    )
    status_table = Table(
        [[
            Paragraph(clean(report_status), styles["DareWhite"]),
            Paragraph(
                clean(f"Coverage floor: {float(evaluated.get('balance_floor') or 0):.0f}% | Analysis outputs: {evaluated.get('analysis_unit_count', len(evaluated.get('analysis_units', [])))}"),
                styles["DareWhite"],
            ),
        ]],
        colWidths=[2.25 * inch, 4.8 * inch],
    )
    status_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), primary),
        ("BOX", (0, 0), (-1, -1), 0.8, primary),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))

    story: list[Any] = [
        title_block,
        Paragraph(
            clean(
                "Protocol-oriented summary generated by the DARE-Arbo Designer. "
                "Planning coverage is not a completed risk-of-bias assessment."
            ),
            styles["DareSubtitle"],
        ),
        status_table,
        section("1. Surveillance objective and study architecture"),
        key_value_table([
            ("Planned study title", plan.get("study_title")),
            ("Target population", plan.get("target_population")),
            ("Virus coverage", plan.get("virus_coverage")),
            ("Arbovirus target(s)", plan.get("virus") or "; ".join(plan.get("viruses") or [])),
            ("Surveillance mode", plan.get("surveillance_mode")),
            ("Surveillance architecture", plan.get("surveillance_architecture")),
            ("Planned recruited sample size", plan.get("planned_sample_size") or "Pending"),
            ("Sampling and recruitment", plan.get("sampling_recruitment_method")),
            ("Endpoint design", plan.get("endpoint_design")),
            ("Generated", created_at or "Current session"),
        ]),
    ]
    if plan.get("surveillance_mode") == "Hybrid active + passive surveillance":
        story.extend([
            Spacer(1, 7),
            key_value_table([
                ("Active surveillance source", plan.get("active_stream_definition")),
                ("Passive surveillance source", plan.get("passive_stream_definition")),
                ("Stream analysis", plan.get("stream_estimator_mode")),
                ("Integration prespecified", "Yes" if plan.get("stream_integration_plan") else "No"),
            ]),
        ])

    pillar_rows = [[paragraph("Planning pillar", "DareSmallWhite"), paragraph("Coverage", "DareSmallWhite"), paragraph("Outstanding elements", "DareSmallWhite")]]
    for pillar, values in evaluated.get("pillars", {}).items():
        missing = values.get("missing") or []
        pillar_rows.append([
            paragraph(pillar, "DareSmall"),
            paragraph(f"{float(values.get('percentage') or 0):.0f}%", "DareSmall"),
            paragraph("; ".join(missing) if missing else "Complete", "DareSmall"),
        ])
    pillar_table = Table(pillar_rows, colWidths=[1.65 * inch, 0.85 * inch, 4.55 * inch], repeatRows=1)
    pillar_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), navy),
        ("TEXTCOLOR", (0, 0), (-1, 0), pdf_colors.white),
        ("GRID", (0, 0), (-1, -1), 0.45, border),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [pdf_colors.white, background]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([section("2. Planning balance"), pillar_table])
    recommendations = list(evaluated.get("recommendations") or [])
    story.append(Spacer(1, 6))
    story.append(Paragraph("Planning priorities", styles["DareUnit"]))
    if recommendations:
        for item in recommendations:
            story.append(Paragraph(f"- {clean(item)}", styles["DareBody"]))
    else:
        story.append(paragraph("No outstanding planning elements were identified by the Designer checklist."))

    story.extend([PageBreak(), section("3. Surveillance design flow")])
    try:
        story.append(PdfImage(BytesIO(design_png), width=7.05 * inch, height=6.17 * inch))
    except Exception:
        story.append(paragraph("The design flow image could not be embedded; use the separately downloadable PNG."))
    story.append(Spacer(1, 5))
    story.append(Paragraph(
        clean("The figure links the declared population, surveillance stream, endpoint-specific assay pathway, and reporting output."),
        styles["DareSubtitle"],
    ))

    story.append(section("4. Study-virus-estimand analysis plan"))
    analysis_units = list(evaluated.get("analysis_units") or [])
    if analysis_units:
        for index, unit in enumerate(analysis_units, start=1):
            path_key = str(unit.get("synthesis_path_key") or "")
            path_assays = (plan.get("pathway_assays") or {}).get(path_key, {})
            unit_title = (
                f"Analysis unit {index}: {unit.get('surveillance_stream', 'Stream')} | "
                f"{unit.get('virus', 'Virus')} | {unit.get('endpoint', 'Endpoint')}"
            )
            block = [
                Paragraph(clean(unit_title), styles["DareUnit"]),
                key_value_table([
                    ("Testing strategy", unit.get("testing_strategy")),
                    ("Primary / screening assay", path_assays.get("primary_assay") or plan.get("primary_assay")),
                    ("Confirmatory assay(s)", path_assays.get("confirmatory_assay") or "Not required / not selected"),
                    ("Observed numerator", unit.get("numerator")),
                    ("Estimator denominator", unit.get("denominator")),
                    ("Summary estimator", unit.get("summary_estimator")),
                ]),
                Spacer(1, 8),
            ]
            story.extend(block)
    else:
        story.append(paragraph("No complete study-virus-estimand analysis unit has been generated."))

    reporting_checks = [
        ("Observed endpoint numerator and denominator", plan.get("numerator_denominator_reporting")),
        ("Participant/specimen and verification-stratum flow", plan.get("flow_reporting")),
        ("Assay manufacturer, protocol, cutoffs, controls, and interpretation", plan.get("assay_reporting")),
        ("Nonresponse, exclusions, missing specimens, and incomplete testing", plan.get("missingness_reporting")),
        ("Estimator, weights, standardization, and assay adjustment", plan.get("estimator_reporting")),
        ("Protocol, codebook, analysis code, or machine-readable outputs", plan.get("reproducibility_reporting")),
    ]
    if len(plan.get("synthesis_path_keys") or []) > 1:
        reporting_checks.append(("Endpoint-specific results retained separately", plan.get("mixed_endpoint_reporting_plan")))
    if len(plan.get("viruses") or []) > 1:
        reporting_checks.append(("Virus-specific and co-detection reporting", plan.get("multiplex_reporting_plan")))
    report_table = Table(
        [[paragraph("Reporting commitment", "DareSmallWhite"), paragraph("Status", "DareSmallWhite")]]
        + [[paragraph(label, "DareSmall"), paragraph("Planned" if selected else "Not yet planned", "DareSmall")] for label, selected in reporting_checks],
        colWidths=[5.55 * inch, 1.5 * inch],
        repeatRows=1,
    )
    report_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), navy),
        ("TEXTCOLOR", (0, 0), (-1, 0), pdf_colors.white),
        ("GRID", (0, 0), (-1, -1), 0.45, border),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [pdf_colors.white, background]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([
        section("5. Standard reporting commitments"),
        report_table,
        section("6. Protocol handoff note"),
        paragraph(
            "This report documents a planned design. It does not replace a protocol, statistical analysis plan, ethics review, laboratory SOP, or completed DARE-Arbo risk-of-bias assessment. "
            "Before implementation, resolve every planning priority and verify that each virus-endpoint-stream analysis unit has a matched observed numerator, denominator, assay pathway, and estimator.",
            "DareClosing",
        ),
    ])

    def header_footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        page_width, page_height = A4
        canvas.setFillColor(navy)
        canvas.rect(0, page_height - 0.27 * inch, page_width, 0.27 * inch, fill=1, stroke=0)
        canvas.setFillColor(primary)
        canvas.rect(0, page_height - 0.31 * inch, page_width, 0.04 * inch, fill=1, stroke=0)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(muted)
        canvas.drawString(0.55 * inch, 0.31 * inch, "DARE-Arbo surveillance study design report")
        canvas.drawRightString(page_width - 0.55 * inch, 0.31 * inch, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    return output.getvalue()


def classify_synthesis(endpoint_key: str, verification_design: str | None) -> dict[str, str]:
    """Classify assay role and population measurement pathway independently."""
    endpoint = ENDPOINTS_BY_KEY[endpoint_key]
    base = {
        "endpoint": endpoint.label,
        "tier": "Not tiered",
        "assay_role": "Primary endpoint-defining assay",
        "measurement_pathway": VERIFICATION_DESIGNS.get(
            verification_design or "", "Unresolved testing/verification population"
        ),
        "analysis_role": "Retain as a distinct endpoint",
        "caution": endpoint.interpretation,
        "eligibility": "Review endpoint-specific eligibility",
        "numerator_rule": "Use the observed positives produced by the declared endpoint-defining assay or algorithm.",
        "denominator_rule": "Use the population required by the declared estimator.",
    }
    if verification_design not in VERIFICATION_DESIGNS:
        return {
            **base,
            "tier": "Unresolved",
            "assay_role": "Unresolved",
            "analysis_role": "Define the testing/verification population before synthesis",
            "caution": "The study-virus-estimand and the relationship between its tested group and target population are not specified.",
            "eligibility": "Exclude from primary pooling until resolved",
        }
    if verification_design == "ancillary_validation":
        return {
            **base,
            "tier": "Ancillary assay-validation evidence",
            "assay_role": "Ancillary validation; not endpoint-defining",
            "analysis_role": "Supports Q6a/Q6b and may supply sensitivity/specificity",
            "caution": "Reference-positive/reference-negative samples are not prevalence endpoints and do not automatically earn Q6c.",
            "eligibility": "Do not enter as a prevalence estimate",
            "numerator_rule": "Record observed validation true-positive and true-negative results separately.",
            "denominator_rule": "Use reference-positive and reference-negative validation denominators; never a study prevalence denominator.",
        }
    if verification_design == "two_phase_validation":
        return {
            **base,
            "tier": "Ancillary two-phase assay-validation/adjustment design",
            "assay_role": "Ancillary validation/adjustment",
            "analysis_role": "Use stratum-specific results for validation or an explicit two-phase adjusted estimator",
            "caution": "The raw molecular proportion in sampled screening-positive and screening-negative strata is not population molecular prevalence.",
            "eligibility": "Do not pool the raw molecular subset proportion as prevalence",
            "numerator_rule": "Keep observed confirmatory results separate within screening-positive and screening-negative verification strata.",
            "denominator_rule": "Retain total stratum sizes and tested counts for both strata; use only an explicit two-phase estimator.",
        }

    endpoint_numerators = {
        "prior_exposure": "Observed binding-antibody positives for a primary-assay endpoint, or observed confirmed positives for an explicitly staged endpoint.",
        "presumptive_igm": "Observed IgM-positive count only; do not combine with IgG.",
        "presumptive_ns1": "Observed NS1-positive count only; analyse separately from IgM and molecular detection.",
        "mixed_serology": "Observed combined serologic positives only; do not reconstruct an IgM- or IgG-specific numerator.",
        "confirmed_active": "Observed PCR/NAAT, isolation, or sequencing-supported positives.",
        "neutralizing_antibody": "Observed neutralization-positive count.",
        "assay_performance": "Observed validation outcomes by reference-status stratum.",
        "other": "Observed positives for the explicitly declared biological endpoint.",
    }
    denominator_rules = {
        "full_population": "Use everyone who received the endpoint-defining test.",
        "representative_subsample": "Use the predefined representative endpoint-tested sample, with weights when required by the estimator.",
        "all_screen_positive": "For complete staged Tier B prevalence, use everyone entering the screening algorithm (Total tested); every screening-positive participant requiring confirmation must be completed.",
        "representative_positive_subset": "Use Total tested, Screen-positive N, Number retested, and observed confirmed-positive n in the explicit weighted/screen-conditioned estimator; keep the raw verified-subset proportion separate.",
        "subset_positive": "Use Number retested only for the conditional selected-subset proportion; do not label it population prevalence.",
        "selected_subsample": "Use the selected endpoint-tested subset and label its conditional target population.",
        "mixed_two_phase": "Use a validated stratum-specific estimator or leave population prevalence unresolved.",
        "incomplete": "No valid prevalence denominator is available.",
    }
    if verification_design == "incomplete":
        tier = "Tier E - incomplete/non-analyzable"
        role = "Exclude from prevalence synthesis until counts and linkage are resolved"
        assay_role = "Unresolved endpoint-defining pathway"
    elif verification_design == "mixed_two_phase":
        tier = "Tier D - mixed or unresolved verification"
        role = "Exclude unless the actual estimator and verification strata are reconstructable"
        assay_role = "Unresolved staged algorithm"
    elif verification_design == "all_screen_positive":
        tier = "Tier B - complete staged confirmation"
        role = "Separate Tier B algorithm-confirmed prevalence dataset"
        assay_role = "Endpoint-defining staged screening-confirmation algorithm"
    elif verification_design == "representative_positive_subset":
        tier = "Tier C - representative-subset weighted/screen-conditioned estimand"
        role = "Separate Tier C weighted or explicitly conditional dataset"
        assay_role = "Endpoint-defining staged algorithm with representative verification"
    elif verification_design == "subset_positive":
        tier = "Tier D - selected/unspecified verification subset"
        role = "Conditional/sensitivity analysis only; exclude from unrestricted population prevalence pooling"
        assay_role = "Endpoint-defining staged algorithm with selected/unresolved verification"
    elif endpoint_key in {"confirmed_active", "neutralizing_antibody"}:
        tier = "Tier A - primary direct/reference-standard assay"
        assay_role = "Primary endpoint-defining direct/reference-standard assay"
        if verification_design == "full_population":
            role = "Primary population endpoint dataset"
        elif verification_design == "representative_subsample":
            role = "Primary representative-subsample dataset if representativeness is defensible"
        else:
            role = "Selected-sample analysis only; Tier A assay role does not establish population representativeness"
    else:
        tier = "Primary-assay endpoint"
        assay_role = "Primary endpoint-defining assay"
        role = (
            "Primary endpoint dataset"
            if verification_design == "full_population"
            else "Endpoint-specific subset dataset; interpret only for its declared target"
        )
    return {
        **base,
        "tier": tier,
        "assay_role": assay_role,
        "analysis_role": role,
        "eligibility": role,
        "numerator_rule": endpoint_numerators[endpoint_key],
        "denominator_rule": denominator_rules[verification_design],
    }


def audit_endpoint_counts(
    endpoint_key: str,
    verification_design: str | None,
    source_population_n: Any = None,
    endpoint_tested_n: Any = None,
    endpoint_positive_n: Any = None,
    screen_positive_n: Any = None,
    planned_verification_n: Any = None,
    screen_negative_n: Any = None,
    verification_negative_tested_n: Any = None,
    verification_negative_positive_n: Any = None,
    numerator_provenance: str = "observed",
    reference_positive_n: Any = None,
    reference_positive_detected_n: Any = None,
    reference_negative_n: Any = None,
    reference_negative_correct_n: Any = None,
) -> dict[str, Any]:
    """Audit estimator-specific observed counts without inventing denominators."""
    fields = {
        "source_population_n": source_population_n,
        "endpoint_tested_n": endpoint_tested_n,
        "endpoint_positive_n": endpoint_positive_n,
        "screen_positive_n": screen_positive_n,
        "planned_verification_n": planned_verification_n,
        "screen_negative_n": screen_negative_n,
        "verification_negative_tested_n": verification_negative_tested_n,
        "verification_negative_positive_n": verification_negative_positive_n,
        "reference_positive_n": reference_positive_n,
        "reference_positive_detected_n": reference_positive_detected_n,
        "reference_negative_n": reference_negative_n,
        "reference_negative_correct_n": reference_negative_correct_n,
    }
    values: dict[str, int | None] = {}
    issues: list[str] = []
    for name, raw in fields.items():
        if raw in (None, "") or (isinstance(raw, float) and math.isnan(raw)):
            values[name] = None
            continue
        try:
            numeric = float(raw)
            if not math.isfinite(numeric) or not numeric.is_integer():
                raise ValueError
            number = int(numeric)
        except (TypeError, ValueError):
            issues.append(f"{name} must be an observed integer.")
            values[name] = None
            continue
        if number < 0:
            issues.append(f"{name} must be a non-negative observed integer.")
        values[name] = number

    source_n = values["source_population_n"]
    tested_n = values["endpoint_tested_n"]
    positive_n = values["endpoint_positive_n"]
    screen_n = values["screen_positive_n"]
    planned_n = values["planned_verification_n"]
    screen_negative = values["screen_negative_n"]
    negative_tested = values["verification_negative_tested_n"]
    negative_positive = values["verification_negative_positive_n"]
    warnings: list[str] = []
    if source_n == 0:
        issues.append("source_population_n must be greater than zero when supplied.")
    if tested_n == 0:
        issues.append("endpoint_tested_n must be greater than zero when supplied.")
    for denominator_name in (
        "planned_verification_n", "screen_negative_n", "verification_negative_tested_n",
        "reference_positive_n", "reference_negative_n",
    ):
        if values[denominator_name] == 0:
            issues.append(f"{denominator_name} must be greater than zero when supplied.")
    if positive_n is not None and tested_n is None:
        issues.append("An endpoint-positive numerator requires an endpoint-tested denominator.")
    if positive_n is not None and tested_n is not None and positive_n > tested_n:
        issues.append("Endpoint positives cannot exceed the endpoint-tested denominator.")
    if tested_n is not None and source_n is not None and tested_n > source_n:
        issues.append("Endpoint tested N cannot exceed the declared source population N.")
    if screen_n is not None and source_n is not None and screen_n > source_n:
        issues.append("Screen-positive N cannot exceed the source population N.")
    if screen_negative is not None and source_n is not None and screen_negative > source_n:
        issues.append("Screen-negative N cannot exceed the source population N.")
    if screen_n is not None and screen_negative is not None and source_n is not None and screen_n + screen_negative > source_n:
        issues.append("Screen-positive and screen-negative counts cannot exceed the source population N.")
    if planned_n is not None and source_n is not None and planned_n > source_n:
        issues.append("Planned verification N cannot exceed the source population N.")
    if tested_n is not None and planned_n is not None and tested_n > planned_n:
        issues.append("Endpoint tested N cannot exceed planned verification N.")
    if negative_positive is not None and negative_tested is None:
        issues.append("A screening-negative-stratum numerator requires its tested denominator.")
    if negative_positive is not None and negative_tested is not None and negative_positive > negative_tested:
        issues.append("Positive results in the screening-negative verification stratum cannot exceed those tested.")
    if values["reference_positive_detected_n"] is not None and values["reference_positive_n"] is not None and values["reference_positive_detected_n"] > values["reference_positive_n"]:
        issues.append("Reference-positive detections cannot exceed the reference-positive denominator.")
    if values["reference_negative_correct_n"] is not None and values["reference_negative_n"] is not None and values["reference_negative_correct_n"] > values["reference_negative_n"]:
        issues.append("Correctly negative validation results cannot exceed the reference-negative denominator.")
    if verification_design == "full_population" and tested_n is not None and source_n is not None and tested_n != source_n:
        issues.append("Full-population testing is selected, but endpoint tested N differs from source population N.")
    effective_design = verification_design
    if verification_design == "all_screen_positive":
        expected_n = planned_n if planned_n is not None else screen_n
        if screen_n is not None and planned_n is not None and planned_n != screen_n:
            issues.append("Tier B requires the complete planned screening-positive set, so planned verification N must equal screen-positive N.")
        if tested_n is not None and expected_n is not None and tested_n != expected_n:
            warnings.append("Tier B requires every planned screening-positive participant; this record is reclassified to Tier D because verification is incomplete.")
            effective_design = "subset_positive"
    if verification_design == "subset_positive" and tested_n is not None and screen_n is not None and tested_n >= screen_n:
        issues.append("Subset verification is selected, but endpoint tested N is not smaller than screen-positive N.")

    if verification_design == "representative_positive_subset" and tested_n is not None and screen_n is not None and tested_n >= screen_n:
        warnings.append("The representative-positive-subset design usually has fewer verified than screen-positive participants; confirm the selected design.")
    if numerator_provenance not in NUMERATOR_PROVENANCE:
        issues.append("Numerator provenance is invalid or missing.")
    elif numerator_provenance not in {"observed", "reconstructed_observed"}:
        issues.append("Q8 requires an observed numerator; posterior/model-derived expected positives remain distinct.")

    classification = classify_synthesis(endpoint_key, effective_design)
    complete_counts = tested_n is not None and positive_n is not None
    required: list[str] = ["endpoint_tested_n", "endpoint_positive_n"]
    if verification_design == "all_screen_positive":
        required += ["screen_positive_n", "planned_verification_n"]
    elif verification_design == "representative_positive_subset":
        required += ["screen_positive_n"]
    elif verification_design == "two_phase_validation":
        required += ["source_population_n", "screen_positive_n", "screen_negative_n", "verification_negative_tested_n", "verification_negative_positive_n"]
    elif verification_design == "ancillary_validation":
        required = ["reference_positive_n", "reference_positive_detected_n", "reference_negative_n", "reference_negative_correct_n"]
    missing_required = [name for name in required if values.get(name) is None]

    synthesis_denominator_n: int | None = tested_n
    prevalence: float | None = None
    if verification_design == "all_screen_positive" and effective_design == "all_screen_positive":
        synthesis_denominator_n = source_n
        if positive_n is not None and source_n:
            prevalence = positive_n / source_n
    elif verification_design == "representative_positive_subset":
        synthesis_denominator_n = source_n
        if source_n and screen_n is not None and tested_n and positive_n is not None:
            prevalence = (screen_n / source_n) * (positive_n / tested_n)
    elif verification_design not in {"two_phase_validation", "ancillary_validation"}:
        if tested_n and positive_n is not None:
            prevalence = positive_n / tested_n

    completion_expected_n = planned_n
    if completion_expected_n is None and verification_design == "full_population":
        completion_expected_n = source_n
    if completion_expected_n is None and verification_design == "all_screen_positive":
        completion_expected_n = screen_n
    completion_rate = (
        tested_n / completion_expected_n
        if tested_n is not None and completion_expected_n
        else None
    )
    q5_suggested_score = None
    if completion_rate is not None:
        q5_suggested_score = 2 if completion_rate >= 0.8 else 1 if completion_rate >= 0.7 else 0

    sensitivity = None
    specificity = None
    if values["reference_positive_n"] and values["reference_positive_detected_n"] is not None:
        sensitivity = values["reference_positive_detected_n"] / values["reference_positive_n"]
    if values["reference_negative_n"] and values["reference_negative_correct_n"] is not None:
        specificity = values["reference_negative_correct_n"] / values["reference_negative_n"]
    estimator_ready = not missing_required and numerator_provenance in {"observed", "reconstructed_observed"} and not issues
    return {
        **values,
        "valid": not issues,
        "complete_counts": complete_counts,
        "synthesis_counts_complete": estimator_ready,
        "estimand_ready": estimator_ready,
        "required_fields": required,
        "missing_required_fields": missing_required,
        "issues": issues,
        "warnings": warnings,
        "effective_verification_design": effective_design,
        "effective_tier": classification["tier"],
        "numerator_provenance": numerator_provenance,
        "endpoint_test_positivity": (positive_n / tested_n) if complete_counts and tested_n else None,
        "verification_subset_positivity": (positive_n / tested_n) if complete_counts and tested_n else None,
        "synthesis_denominator_n": synthesis_denominator_n,
        "prevalence": prevalence,
        "completion_expected_n": completion_expected_n,
        "completion_rate": completion_rate,
        "q5_suggested_score": q5_suggested_score,
        "sensitivity": sensitivity,
        "specificity": specificity,
        "numerator_rule": classification["numerator_rule"],
        "denominator_rule": classification["denominator_rule"],
    }


def audit_synthesis_path_counts(
    synthesis_path_key: str, counts: Mapping[str, Any]
) -> dict[str, Any]:
    """Audit the pathway-specific numerator, denominator, and misclassification counts."""
    if synthesis_path_key not in SYNTHESIS_PATHS_BY_KEY:
        raise ValueError(f"Unknown synthesis path: {synthesis_path_key}")
    path = SYNTHESIS_PATHS_BY_KEY[synthesis_path_key]
    numerator_provenance = str(counts.get("numerator_provenance") or "observed")
    count_names = set(path.required_counts) | {
        "total_recruited_n", "total_tested_n", "planned_retest_n"
    }
    values: dict[str, int | None] = {}
    issues: list[str] = []
    warnings: list[str] = []
    for name in count_names:
        raw = counts.get(name)
        if raw in (None, "") or (isinstance(raw, float) and math.isnan(raw)):
            values[name] = None
            continue
        try:
            number = float(raw)
            if not math.isfinite(number) or not number.is_integer() or number < 0:
                raise ValueError
            values[name] = int(number)
        except (TypeError, ValueError):
            values[name] = None
            issues.append(f"{name} must be a non-negative whole-number count.")

    missing = [name for name in path.required_counts if values.get(name) is None]
    positive_field_by_path = {
        "serology_apparent": "primary_positive_n",
        "mixed_serology_only": "mixed_serology_positive_n",
        "neutralization_universal": "neutralization_positive_n",
        "igm_only": "igm_positive_n",
        "ns1_only": "ns1_positive_n",
        "confirmed_active_universal": "molecular_positive_n",
        "serology_tier_b": "confirmed_positive_n",
        "serology_tier_c": "confirmed_positive_n",
        "serology_tier_d": "confirmed_positive_n",
        "confirmed_active_tier_b": "molecular_positive_n",
        "confirmed_active_tier_c": "molecular_positive_n",
        "confirmed_active_tier_d": "molecular_positive_n",
    }
    universal_paths = {
        "serology_apparent", "neutralization_universal", "igm_only", "ns1_only",
        "confirmed_active_universal", "mixed_serology_only",
    }
    tier_b_paths = {"serology_tier_b", "confirmed_active_tier_b"}
    tier_c_paths = {"serology_tier_c", "confirmed_active_tier_c"}
    tier_d_paths = {"serology_tier_d", "confirmed_active_tier_d"}
    validation_paths = {"serology_validation", "molecular_validation"}

    numerator_n: int | None = None
    denominator_n: int | None = None
    false_positives: int | None = None
    false_negatives: int | None = None
    sensitivity: float | None = None
    specificity: float | None = None
    effective_path_key = synthesis_path_key

    total_recruited_n = values.get("total_recruited_n")
    total_tested_n = values.get("total_tested_n")
    if total_recruited_n == 0:
        issues.append("Total recruited must be greater than zero when supplied.")
    if total_tested_n == 0:
        issues.append("Total tested must be greater than zero when supplied.")
    if (
        total_recruited_n is not None
        and total_tested_n is not None
        and total_tested_n > total_recruited_n
    ):
        issues.append("Total tested cannot exceed Total recruited.")

    if synthesis_path_key in universal_paths:
        denominator_n = values.get("total_tested_n")
        numerator_n = values.get(positive_field_by_path[synthesis_path_key])
        if denominator_n == 0:
            issues.append("Total tested must be greater than zero.")
        if numerator_n is not None and denominator_n is not None and numerator_n > denominator_n:
            issues.append(f"{path.numerator} cannot exceed Total tested.")
    elif synthesis_path_key in tier_b_paths | tier_c_paths | tier_d_paths:
        verification_denominator_n = values.get("number_retested_n")
        numerator_n = values.get(positive_field_by_path[synthesis_path_key])
        primary_positive_n = values.get("primary_positive_n")
        if verification_denominator_n == 0:
            issues.append("Number retested must be greater than zero.")
        if numerator_n is not None and verification_denominator_n is not None and numerator_n > verification_denominator_n:
            issues.append(f"{path.numerator} cannot exceed Number retested.")
        if verification_denominator_n is not None and primary_positive_n is not None and verification_denominator_n > primary_positive_n:
            issues.append("Number retested cannot exceed Primary positive.")
        if synthesis_path_key in tier_b_paths and verification_denominator_n is not None and primary_positive_n is not None and verification_denominator_n != primary_positive_n:
            effective_path_key = (
                "serology_tier_d" if synthesis_path_key == "serology_tier_b"
                else "confirmed_active_tier_d"
            )
            warnings.append("Tier B requires the complete planned screening-positive verification set. This record is reclassified to Tier D because Number retested differs from Primary positive.")
        denominator_n = (
            values.get("total_tested_n")
            if synthesis_path_key in tier_b_paths | tier_c_paths
            else verification_denominator_n
        )
        if effective_path_key in tier_d_paths:
            denominator_n = verification_denominator_n
        if numerator_n is not None and verification_denominator_n is not None:
            false_positives = verification_denominator_n - numerator_n
    else:
        denominator_n = values.get("number_retested_n")
        positive_stratum_n = values.get("retested_positive_stratum_n")
        negative_stratum_n = values.get("retested_negative_stratum_n")
        confirmed_positive_n = values.get("confirmed_positive_n")
        confirmed_negative_n = values.get("confirmed_negative_n")
        numerator_n = confirmed_positive_n
        if denominator_n == 0:
            issues.append("Number retested must be greater than zero.")
        if denominator_n is not None and positive_stratum_n is not None and negative_stratum_n is not None and denominator_n != positive_stratum_n + negative_stratum_n:
            issues.append("Number retested must equal retested positive-stratum N plus retested negative-stratum N.")
        if confirmed_positive_n is not None and positive_stratum_n is not None and confirmed_positive_n > positive_stratum_n:
            issues.append("Confirmed positive cannot exceed the retested positive stratum.")
        if confirmed_negative_n is not None and negative_stratum_n is not None and confirmed_negative_n > negative_stratum_n:
            issues.append("Confirmed negative cannot exceed the retested negative stratum.")
        if confirmed_positive_n is not None and positive_stratum_n is not None:
            false_positives = positive_stratum_n - confirmed_positive_n
        if confirmed_negative_n is not None and negative_stratum_n is not None:
            false_negatives = negative_stratum_n - confirmed_negative_n
        if confirmed_positive_n is not None and false_negatives is not None and confirmed_positive_n + false_negatives:
            sensitivity = confirmed_positive_n / (confirmed_positive_n + false_negatives)
        if confirmed_negative_n is not None and false_positives is not None and confirmed_negative_n + false_positives:
            specificity = confirmed_negative_n / (confirmed_negative_n + false_positives)

    recruitment_testing_rate = (
        total_tested_n / total_recruited_n
        if total_tested_n is not None and total_recruited_n
        else None
    )
    planned_for_q5: int | None = None
    actual_for_q5: int | None = None
    staged_path = synthesis_path_key in tier_b_paths | tier_c_paths | tier_d_paths | validation_paths
    if synthesis_path_key in tier_b_paths:
        planned_for_q5 = values.get("primary_positive_n")
        actual_for_q5 = values.get("number_retested_n")
    elif synthesis_path_key in tier_c_paths | tier_d_paths | validation_paths:
        planned_for_q5 = values.get("planned_retest_n")
        actual_for_q5 = values.get("number_retested_n")
    planned_pathway_completion_rate = (
        min(actual_for_q5 / planned_for_q5, 1.0)
        if actual_for_q5 is not None and planned_for_q5
        else None
    )
    if (
        actual_for_q5 is not None
        and planned_for_q5 is not None
        and actual_for_q5 > planned_for_q5
    ):
        warnings.append(
            "Actual completed testing exceeds the planned number; Q5 completion is capped at 100%."
        )

    required_q5_rates = [recruitment_testing_rate]
    if staged_path:
        required_q5_rates.append(planned_pathway_completion_rate)
    q5_counts_complete = all(rate is not None for rate in required_q5_rates)
    q5_completion_rate = min(required_q5_rates) if q5_counts_complete else None
    q5_suggested_score = None
    if q5_completion_rate is not None:
        q5_suggested_score = (
            2 if q5_completion_rate >= 0.8
            else 1 if q5_completion_rate >= 0.7
            else 0
        )
    if recruitment_testing_rate is None:
        warnings.append(
            "Total recruited and Total tested are both required to calculate the primary Q5 completion percentage."
        )
    if staged_path and planned_pathway_completion_rate is None:
        warnings.append(
            "The planned testing/verification denominator and actual completed count are required for the staged Q5 completion percentage."
        )
    q5_basis_parts: list[str] = []
    if recruitment_testing_rate is not None:
        q5_basis_parts.append(
            f"Total tested / Total recruited = {total_tested_n}/{total_recruited_n} "
            f"({100 * recruitment_testing_rate:.1f}%)"
        )
    if planned_pathway_completion_rate is not None:
        q5_basis_parts.append(
            f"planned-pathway completion = {actual_for_q5}/{planned_for_q5} "
            f"({100 * planned_pathway_completion_rate:.1f}%)"
        )
    q5_basis = "; ".join(q5_basis_parts)
    if q5_completion_rate is not None and len(q5_basis_parts) > 1:
        q5_basis += f"; limiting percentage = {100 * q5_completion_rate:.1f}%"
    if numerator_provenance not in {"observed", "reconstructed_observed"}:
        issues.append("Q8 requires observed numerator counts; posterior/model-derived expected positives must remain separate.")

    effective_path = SYNTHESIS_PATHS_BY_KEY[effective_path_key]
    estimator_ready = (
        not missing
        and numerator_provenance in {"observed", "reconstructed_observed"}
        and not issues
    )
    estimate = None
    verification_subset_positivity = None
    if effective_path_key in tier_b_paths | tier_c_paths | tier_d_paths:
        verification_n = values.get("number_retested_n")
        if verification_n and numerator_n is not None:
            verification_subset_positivity = numerator_n / verification_n
    if synthesis_path_key not in validation_paths and estimator_ready:
        if effective_path_key in tier_c_paths:
            screened_n = values.get("total_tested_n")
            screen_positive_n = values.get("primary_positive_n")
            verification_n = values.get("number_retested_n")
            if screened_n and screen_positive_n is not None and verification_n and numerator_n is not None:
                estimate = (screen_positive_n / screened_n) * (numerator_n / verification_n)
        elif denominator_n:
            estimate = numerator_n / denominator_n if numerator_n is not None else None
    return {
        **values,
        "synthesis_path_key": synthesis_path_key,
        "effective_synthesis_path_key": effective_path_key,
        "endpoint_key": path.endpoint_key,
        "summary_estimator": effective_path.summary_estimator,
        "testing_strategy": path.testing_strategy,
        "assay_type": path.assay_type,
        "numerator_label": path.numerator,
        "denominator_label": path.denominator,
        "false_positive_rule": path.false_positives,
        "numerator_n": numerator_n,
        "denominator_n": denominator_n,
        "estimate": estimate,
        "verification_subset_positivity": verification_subset_positivity,
        "false_positives_n": false_positives,
        "false_negatives_n": false_negatives,
        "sensitivity": sensitivity,
        "specificity": specificity,
        "required_fields": list(path.required_counts),
        "missing_required_fields": missing,
        "estimand_ready": estimator_ready,
        "valid": not issues,
        "issues": issues,
        "warnings": warnings,
        "total_recruited_n": total_recruited_n,
        "recruitment_testing_rate": recruitment_testing_rate,
        "planned_pathway_completion_rate": planned_pathway_completion_rate,
        "q5_counts_complete": q5_counts_complete,
        "q5_completion_rate": q5_completion_rate,
        "q5_basis": q5_basis,
        # Backward-compatible alias used by existing exports and callers.
        "completion_rate": q5_completion_rate,
        "q5_suggested_score": q5_suggested_score,
        "numerator_provenance": numerator_provenance,
        "source_population_n": total_recruited_n,
        "primary_tested_n": total_tested_n,
        "endpoint_tested_n": denominator_n,
        "verification_tested_n": values.get("number_retested_n"),
        "endpoint_positive_n": numerator_n,
        "screen_positive_n": values.get("primary_positive_n"),
        "planned_verification_n": planned_for_q5,
        "prevalence": estimate,
    }


def extract_pdf_text(source: bytes | bytearray | BinaryIO) -> dict[str, Any]:
    """Extract page-aware text from a PDF without persisting the document."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency error shown in UI
        raise RuntimeError("PDF extraction requires the 'pypdf' package.") from exc

    stream: BinaryIO
    if isinstance(source, (bytes, bytearray)):
        stream = BytesIO(source)
    else:
        stream = source
    reader = PdfReader(stream)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValueError("The PDF is encrypted and cannot be read without a password.") from exc
    pages: list[str] = []
    for page in reader.pages:
        pages.append((page.extract_text() or "").strip())
    raw_metadata = reader.metadata or {}
    metadata = {
        str(key).lstrip("/").lower(): str(value).strip()
        for key, value in raw_metadata.items()
        if value not in (None, "")
    }
    return {
        "pages": pages,
        "metadata": metadata,
        "page_count": len(pages),
        "character_count": sum(len(page) for page in pages),
        "scanned_or_empty": not any(page.strip() for page in pages),
    }


DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)


def extract_citation_or_doi(
    pages: Iterable[str], metadata: Mapping[str, Any] | None = None
) -> dict[str, str]:
    """Return a DOI when available, otherwise a conservative PDF-derived citation."""
    metadata = {str(key).lower().lstrip("/"): str(value).strip() for key, value in (metadata or {}).items() if value not in (None, "")}
    page_list = list(pages)
    search_text = " ".join(metadata.values()) + " " + " ".join(page_list)
    doi_candidates: list[str] = []
    for match in DOI_PATTERN.finditer(search_text):
        candidate = match.group(0).rstrip(".,;:")
        while candidate.endswith((")", "]", "}")):
            pairs = {")": "(", "]": "[", "}": "{"}
            closer = candidate[-1]
            if candidate.count(pairs[closer]) >= candidate.count(closer):
                break
            candidate = candidate[:-1]
        if candidate and candidate.lower() not in {item.lower() for item in doi_candidates}:
            doi_candidates.append(candidate)
    if doi_candidates:
        return {
            "value": doi_candidates[0],
            "kind": "DOI",
            "confidence": "high",
            "source": "PDF metadata/text",
        }

    def clean(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip(" .;,\t\r\n")

    title = clean(metadata.get("title", ""))
    author = clean(metadata.get("author", ""))
    if title.lower() in {"", "untitled", "microsoft word", "adobe acrobat"}:
        title = ""

    first_page = page_list[0] if page_list else ""
    year_match = re.search(r"\b(?:19|20)\d{2}\b", metadata.get("creationdate", ""))
    if not year_match:
        year_match = re.search(r"\b(?:19|20)\d{2}\b", first_page[:5000])
    year = year_match.group(0) if year_match else ""

    source = "PDF metadata"
    confidence = "moderate"
    if not title and first_page:
        excluded = re.compile(
            r"^(?:abstract|introduction|keywords?|doi\b|https?://|www\.|copyright|received|accepted|volume\b|issue\b)",
            re.IGNORECASE,
        )
        candidates = []
        for position, raw_line in enumerate(first_page.splitlines()[:24]):
            line = clean(raw_line)
            word_count = len(line.split())
            if 5 <= word_count <= 40 and 25 <= len(line) <= 300 and not excluded.search(line):
                alpha_ratio = sum(character.isalpha() for character in line) / max(len(line), 1)
                if alpha_ratio >= 0.55:
                    candidates.append((position, line))
        if candidates:
            title = candidates[0][1]
            source = "first-page text"
            confidence = "low"

    if title:
        citation_parts = [part for part in (author, title, year) if part]
        return {
            "value": ". ".join(citation_parts).rstrip(".") + ".",
            "kind": "Citation",
            "confidence": confidence,
            "source": source,
        }
    return {
        "value": "",
        "kind": "Not found",
        "confidence": "none",
        "source": "PDF metadata/text",
    }


def _sentences_by_page(pages: Iterable[str]) -> list[EvidenceSnippet]:
    snippets: list[EvidenceSnippet] = []
    for page_number, page_text in enumerate(pages, start=1):
        cleaned = re.sub(r"\s+", " ", page_text).strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\s*[•]\s*", cleaned):
            sentence = sentence.strip()
            if len(sentence) >= 24:
                snippets.append(EvidenceSnippet(page_number, sentence[:700]))
    return snippets


POPULATION_TARGET_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "General febrile population",
        ("general febrile population", "febrile population", "febrile patients", "acute febrile illness", "fever patients"),
    ),
    (
        "Community or general populations",
        ("community or general population", "general population", "community population", "community residents", "district residents", "population-based", "household survey", "residents"),
    ),
    (
        "Occupationally or zoonotically exposed populations",
        ("occupationally exposed", "zoonotically exposed", "occupational population", "high-exposure", "livestock contact", "animal contact", "vector exposure", "healthcare workers", "farmers", "herders", "slaughterhouse workers", "veterinarians", "military personnel"),
    ),
    (
        "Suspected arbovirus population",
        ("suspected arbovirus population", "suspected arbovirus", "suspected dengue", "suspected chikungunya", "suspected zika", "arbovirus-suspected", "suspected cases"),
    ),
    ("Blood donors", ("blood donors", "blood donor", "blood-donor")),
    (
        "Other or mixed populations",
        ("other or mixed population", "mixed populations", "mixed population", "multiple population groups", "heterogeneous population"),
    ),
    (
        "Pregnant women / antenatal populations",
        ("pregnant women", "pregnant participants", "pregnancy cohort", "antenatal", "prenatal", "postpartum"),
    ),
    (
        "Non-malarial febrile population",
        ("non-malarial febrile population", "non-malarial febrile", "non-malarial fever", "malaria-negative febrile", "malaria negative febrile"),
    ),
    (
        "Other defined clinical cohorts",
        ("defined clinical cohort", "clinical cohort", "hospitalized patients", "hospitalised patients", "inpatients", "pediatric patients", "paediatric patients", "adult patients", "people living with hiv", "hiv-positive participants", "diagnostic evaluation", "cases and controls"),
    ),
    (
        "Archived or residual specimen populations",
        ("archived specimen population", "archived specimens", "stored specimens", "residual specimens", "residual samples", "biobank", "serotheque"),
    ),
    (
        "Non-febrile patients",
        ("non-febrile patients", "nonfebrile patients", "afebrile patients", "patients without fever"),
    ),
    (
        "General outpatients",
        ("general outpatients", "outpatient population", "outpatients", "ambulatory patients", "clinic attendees"),
    ),
)

LEGACY_TARGET_CATEGORY_MAP = {
    "National population": "Community or general populations",
    "Regional or state population": "Community or general populations",
    "Local community population": "Community or general populations",
    "General population": "Community or general populations",
    "Febrile-patient population": "General febrile population",
    "Pediatric febrile population": "General febrile population",
    "Occupational population": "Occupationally or zoonotically exposed populations",
    "High-exposure population": "Occupationally or zoonotically exposed populations",
    "One Health population": "Occupationally or zoonotically exposed populations",
    "Animal population": "Occupationally or zoonotically exposed populations",
    "Antenatal or pregnant population": "Pregnant women / antenatal populations",
    "Blood-donor population": "Blood donors",
    "Archived-specimen population": "Archived or residual specimen populations",
    "Surveillance-case population": "Suspected arbovirus population",
    "Adult clinical population": "Other defined clinical cohorts",
    "Hospitalized clinical population": "Other defined clinical cohorts",
    "People living with HIV": "Other defined clinical cohorts",
    "Outbreak population": "Other defined clinical cohorts",
    "Diagnostic-evaluation population": "Other defined clinical cohorts",
}

APPRAISAL_LIBRARY_RULE_SOURCE = (
    "DARE_Arbo_Library_with_Decision_Tree.xlsx: Appraisal Library; "
    "Q1 Q3 Q4 Rules; Quick Decision Matrix; Worked Examples"
)

Q1_AGE_EVIDENCE_TERMS = (
    "age group", "age groups", "age distribution", "age range", "aged ",
    "median age", "mean age", "children and adults", "pediatric", "paediatric",
    "adolescents", "years old",
)

Q1_SEX_EVIDENCE_TERMS = (
    "both sexes", "men and women", "male and female", "sex distribution",
    "gender distribution", " sex", "gender", "male", "female",
)

Q3_FRAME_EVIDENCE_TERMS = (
    "sampling frame", "source population", "household list", "household register",
    "census list", "census area", "enumeration area", "population register",
    "list of eligible", "all eligible attendees", "eligible attendees during",
    "hospital-attendee frame", "facility network", "hospital network",
    "health centre network", "health center network",
    "sentinel sites", "surveillance sites", "case notifications", "case-report frame",
    "line list", "registry", "cohort roster", "staff list", "workforce list",
    "farm list", "herd list", "abattoir list", "biobank", "serotheque",
    "parent study", "original cohort",
)

Q3_EXPLICIT_UNDERCOVERAGE_TERMS = (
    "does not cover", "did not cover", "under-cover", "undercover", "inadequate frame",
    "selected only", "only stored sera", "only stored serum", "adequate volume only",
    "available specimens only", "unclear frame", "unknown frame",
)

Q4_SCORE_TERMS: dict[int, tuple[str, ...]] = {
    3: (
        "multistage probability", "multi-stage probability", "two-stage probability",
        "three-stage probability", "multistage cluster", "cluster-probability",
        "probability proportional to size", "pps cluster", "stratified random", "community census",
        "census of", "universal inclusion of eligible", "all eligible participants were included",
        "all eligible participants were invited", "all eligible specimens were analysed",
        "all eligible specimens were analyzed", "complete case ascertainment",
        "all eligible staff were included", "all eligible staff included",
        "near-complete capture", "near complete capture", "national population-based survey",
        "regional population-based survey",
    ),
    2: (
        "simple random sampling", "systematic random sampling", "random-number",
        "random number generator", "random household selection", "households were randomly selected",
        "villages were randomly selected", "communities were randomly selected",
        "randomly selected from a defined", "randomly selected from the eligible",
        "randomly selected from multiple", "predefined multisite recruitment",
        "materially broadened multisite", "facilities spanning all", "structured multisite surveillance",
    ),
    1: (
        "convenience sample", "convenience sampling", "convenience recruitment", "recruited by convenience",
        "volunteer recruitment", "self-selected", "purposive sampling", "purposively selected",
        "consecutive recruitment", "consecutively recruited", "consecutive enrollment",
        "consecutive enrolment", "consecutive consenting", "routine service attendance",
        "case series", "case-definition-based", "outbreak convenience", "passive surveillance",
        "diagnostic referral", "referred specimens", "available archived specimens",
        "selected based on availability", "available consenting participants",
        "non-random community", "nonrandom community", "field case-finding",
        "referral-based", "non-probability sampling", "nonprobability sampling",
    ),
}

Q4_USER_METHOD_SCORES: dict[str, int | None] = {
    "Insufficiently described": 0,
    "Convenience recruitment": 1,
    "Volunteer recruitment": 1,
    "Purposive sampling": 1,
    "Facility-based convenience recruitment": 1,
    "Consecutive single-site recruitment": 1,
    "Outbreak convenience sampling": 1,
    "Passive surveillance": 1,
    "Diagnostic referral sample": 1,
    "Available archived specimens": 1,
    "Secondary cohort/trial sample": 1,
    "Non-random community recruitment": 1,
    "Simple random sampling": 2,
    "Systematic random sampling": 2,
    "Random sampling within a restricted population": 2,
    "Random household/community selection": 2,
    "Multisite recruitment": 2,
    "Multicommunity recruitment": 2,
    "Structured multisite surveillance": 2,
    "Multistage probability sampling": 3,
    "Cluster-probability sampling": 3,
    "Stratified random sampling": 3,
    "Probability-proportional-to-size sampling": 3,
    "National or regional population-based survey": 3,
    "Census of the defined population": 3,
    "Universal inclusion of eligible participants": 3,
    "Universal inclusion of eligible specimens": 3,
    "Complete surveillance case ascertainment": 3,
    "Mixed probability and non-probability sampling": None,
    "Mixed endpoint-specific recruitment pathways": None,
}


def _controlled_target_category(text: str) -> str | None:
    lowered = text.lower()
    matches = [
        (len(term), -position, label)
        for position, (label, terms) in enumerate(POPULATION_TARGET_CATEGORIES)
        for term in terms
        if term in lowered
    ]
    return max(matches)[2] if matches else None


def normalize_target_population_category(
    value: str | None, target_population: str = ""
) -> str | None:
    """Return a current controlled category, migrating legacy labels when needed."""
    category = str(value or "").strip()
    current_labels = {label for label, _ in POPULATION_TARGET_CATEGORIES}
    if category in current_labels:
        return category
    if category in LEGACY_TARGET_CATEGORY_MAP:
        return LEGACY_TARGET_CATEGORY_MAP[category]
    return _controlled_target_category(f"{category} {target_population}".strip())


EVIDENCE_TERMS: dict[str, tuple[str, ...]] = {
    "Q1": ("target population", "age", "sex", "gender", "male", "female"),
    "Q2": ("serum", "plasma", "blood", "specimen", "sample collected", "participant", "questionnaire", "interview"),
    "Q3": ("sampling frame", "source population", "community", "facility", "district", "region", "surveillance", "registry", "biobank"),
    "Q4": ("random", "stratified", "cluster", "multistage", "consecutive", "convenience", "census", "universal", "recruit"),
    "Q5": ("response rate", "completeness", "missing", "excluded", "attrition", "loss to follow-up"),
    "Q6a": ("elisa", "pcr", "prnt", "neutralization", "assay", "naat", "virus isolation", "sequencing"),
    "Q6b": ("validated", "validation", "control", "manufacturer", "sensitivity", "specificity", "reference standard"),
    "Q6c": ("confirm", "prnt", "neutralization", "second target", "sequencing", "genotyping", "virus isolation"),
    "Q7": ("all samples", "all specimens", "same method", "algorithm", "screen-positive", "subset"),
    "Q8": ("numerator", "denominator", "prevalence", "positive", "tested", "n ="),
    "Q9": ("age-standard", "sex-standard", "weighted", "adjusted for age", "adjusted for sex"),
    "Q10": ("true prevalence", "sensitivity and specificity", "rogan", "gladen", "measurement error", "assay-adjusted"),
}


def _evidence_for(code: str, snippets: list[EvidenceSnippet], limit: int = 3) -> tuple[EvidenceSnippet, ...]:
    terms = EVIDENCE_TERMS[code]
    ranked: list[tuple[int, EvidenceSnippet]] = []
    for snippet in snippets:
        lowered = snippet.text.lower()
        hits = sum(term in lowered for term in terms)
        if hits:
            ranked.append((hits, snippet))
    ranked.sort(key=lambda item: (-item[0], item[1].page, len(item[1].text)))
    return tuple(snippet for _, snippet in ranked[:limit])


def suggest_scores(
    pages: Iterable[str],
    pathway: str,
    endpoint_key: str | None = None,
    verification_design: str | None = None,
    synthesis_path_key: str | None = None,
    counts: Mapping[str, Any] | None = None,
    target_population: str = "",
    target_population_category: str = "",
    achieved_sample_representation: str = "",
    sampling_frame_description: str = "",
    sampling_recruitment_method: str = "",
    endpoint_procedure_consistency: str = "",
    primary_assay: str = "",
    confirmatory_assay: str = "",
) -> dict[str, Suggestion]:
    """Create conservative, fully inspectable suggestions from extracted PDF text.

    These suggestions are evidence-finding aids, not automated final judgments.
    """
    snippets = _sentences_by_page(pages)
    full_text = " ".join(snippet.text for snippet in snippets).lower()
    suggestions: dict[str, Suggestion] = {}

    def has_any(*terms: str) -> bool:
        return any(term in full_text for term in terms)

    def has_affirmative(*terms: str) -> bool:
        """Detect a term while rejecting simple nearby negation in the same sentence."""
        for snippet in snippets:
            lowered = snippet.text.lower()
            for term in terms:
                for match in re.finditer(re.escape(term), lowered):
                    prefix = lowered[max(0, match.start() - 55) : match.start()]
                    if not re.search(
                        r"\b(?:no|not|without|neither|nor|did\s+not|was\s+not|were\s+not|wasn't|weren't)\b[^.;:]{0,45}$",
                        prefix,
                    ):
                        return True
        return False

    def add(code: str, score: int | None, confidence: str, rationale: str) -> None:
        suggestions[code] = Suggestion(
            score=score,
            confidence=confidence,
            rationale=rationale,
            evidence=_evidence_for(code, snippets),
        )

    population_context = f"{target_population_category} {target_population} {full_text}".strip()
    target_category = (
        normalize_target_population_category(
            target_population_category, target_population
        )
        or _controlled_target_category(population_context)
    )
    target_label = target_category or "target population not clearly classified"
    target_text = f"{target_population_category} {target_population}".strip().lower()
    target_is_defined = bool(target_text or target_category)
    age_evidence = any(term in full_text for term in Q1_AGE_EVIDENCE_TERMS) or bool(
        re.search(r"\b(?:aged?\s*)?\d{1,3}\s*(?:-|–|to)\s*\d{1,3}\s*(?:years?|yrs?)\b", full_text)
    )
    sex_evidence = any(term in full_text for term in Q1_SEX_EVIDENCE_TERMS)
    broad_target = target_category == "Community or general populations"
    target_is_attendee_specific = any(
        term in target_text
        for term in (
            "attendee", "attending", "outpatient", "inpatient", "named hospital",
            "named clinic", "antenatal", "blood donor", "occupational", "staff",
            "suspected", "referred", "prior cohort",
        )
    ) or has_any(
        "eligible febrile attendees", "febrile hospital attendees", "hospital-attendee target",
        "patients attending the participating hospital", "outpatient population",
    )
    target_claims_broad_geography = broad_target or any(
        term in target_text
        for term in (
            "all febrile city residents", "all febrile residents", "city residents",
            "district residents", "regional population", "national population",
            "general population",
        )
    ) or has_any(
        "all febrile city residents", "all febrile residents", "city residents",
        "district residents", "regional population", "national population",
    )
    restricted_achieved = has_any(
        "single hospital", "one hospital", "one referral hospital", "single centre",
        "single center", "single clinic", "blood donors", "pregnant women",
        "healthcare workers", "adult-only", "children only", "archived specimens",
        "outbreak-positive specimens",
    )
    explicit_imbalance = has_any(
        "underrepresented", "overrepresented", "substantial imbalance",
        "predominantly male", "predominantly female", "mostly male", "mostly female",
        "systematically excluded",
    )
    explicit_adequacy = has_any(
        "representative of the target population", "no substantial imbalance",
        "included both sexes", "included multiple age groups", "across age groups",
    )
    target_scope_mismatch = (
        target_claims_broad_geography and restricted_achieved and not target_is_attendee_specific
    )
    achieved_fallback = achieved_sample_representation.strip().lower()
    if not target_is_defined:
        q1, q1_conf = 0, "low"
        q1_class = "insufficient target definition"
        q1_reason = "The article-outcome target population was not clear enough to judge achieved representation."
    elif achieved_fallback.startswith("adequate") and not (age_evidence and sex_evidence):
        q1, q1_conf = 1, "low"
        q1_class = "reviewer fallback: adequate within target"
        q1_reason = (
            f"The reviewer identified adequate age/sex representation within the defined {target_label}; "
            "the PDF did not contain enough extractable evidence, so verify before finalizing."
        )
    elif achieved_fallback.startswith(("partial", "poor", "insufficient", "not reported")) and not (age_evidence and sex_evidence):
        q1, q1_conf = 0, "low"
        q1_class = "reviewer fallback: partial/poor/unjudgeable"
        q1_reason = f"The reviewer fallback did not establish adequate age/sex representation within the defined {target_label}."
    elif not age_evidence or not sex_evidence:
        q1, q1_conf = 0, "low"
        q1_class = "insufficient age/sex evidence"
        q1_reason = (
            f"The achieved sample could not be judged as adequately representative of the {target_label}: "
            "both relevant age and sex representation were not established."
        )
    elif explicit_imbalance or target_scope_mismatch:
        q1, q1_conf = 0, "high" if target_scope_mismatch else "moderate"
        q1_class = "poor representation or target-scope mismatch"
        q1_reason = (
            f"The achieved sample does not adequately represent the defined {target_label} because a major age/sex "
            "imbalance or a restricted sample generalized to a broader target was detected."
        )
    else:
        q1, q1_conf = 1, "high" if explicit_adequacy else "moderate"
        q1_class = "adequate age/sex representation within the defined target"
        q1_reason = (
            f"The expected age and sex groups were represented without a detected major imbalance for the defined {target_label}. "
            "A local, clinical, donor, antenatal, or occupational target is judged against that target rather than a national population."
        )
    add(
        "Q1", q1, q1_conf,
        f"Appraisal Library / Quick Decision Matrix classification: {q1_class}. {q1_reason}",
    )

    participant_specimen = has_any(
        "serum", "plasma", "whole blood", "blood sample", "specimen collected",
        "participants were enrolled", "participants were interviewed", "questionnaire was administered",
    )
    archived_source_defined = has_any(
        "specimens from participants", "stored specimens from", "archived specimens from",
        "source population", "original cohort",
    )
    archived_only_unclear = has_any("archived specimens", "stored specimens", "biobank", "serotheque") and not archived_source_defined
    secondary_only = has_any(
        "line list", "aggregate report", "medical records", "registry data",
        "routine records", "surveillance reports",
    ) and not participant_specimen
    q2_positive = participant_specimen and not archived_only_unclear
    add(
        "Q2",
        1 if q2_positive else 0,
        "high" if q2_positive or secondary_only or archived_only_unclear else "low",
        (
            f"Participant-derived data/specimens linked to the {target_label} were detected."
            if q2_positive
            else "Only a proxy/secondary source was detected, or stored specimens were not clearly linked to their participant/source population."
        ),
    )

    entered_frame = sampling_frame_description.strip().lower()
    combined_frame_text = f"{full_text} {entered_frame}".strip()
    population_enumeration_frame = any(
        term in combined_frame_text
        for term in (
            "enumeration area", "household list", "household register", "census list",
            "complete resident list", "population register", "gps points", "cohort roster",
        )
    )
    facility_frame = any(
        term in combined_frame_text
        for term in (
            "hospital attendees", "clinic attendees", "eligible attendees", "outpatient services",
            "hospital-attendee frame",
            "participating health centre", "participating health center", "participating facility",
            "participating hospital", "participating clinic", "referral hospital", "antenatal clinic",
            "delivery service", "blood bank", "donor centre", "donor center",
        )
    )
    multisite_frame = bool(
        re.search(
            r"\b(?:two|three|four|five|six|seven|eight|nine|ten|\d+|multiple|several)\s+"
            r"(?:relevant\s+)?(?:hospitals?|health centres?|health centers?|facilities|sites)\b",
            combined_frame_text,
        )
    ) or any(term in combined_frame_text for term in ("facility network", "hospital network", "sentinel sites"))
    archived_source_frame = any(
        term in combined_frame_text
        for term in ("parent study", "original cohort", "specimens from participants", "source population")
    )
    frame_evidence = any(term in full_text for term in Q3_FRAME_EVIDENCE_TERMS) or any(
        term in full_text
        for term in (
            "hospital attendees", "clinic attendees", "participating hospital",
            "participating clinic", "participating facility", "participating health centre",
            "participating health center", "referral hospital", "antenatal clinic",
            "donor centre", "donor center", "complete staff", "eight relevant hospitals",
        )
    )
    entered_frame_evidence = bool(entered_frame) and (
        any(term in entered_frame for term in Q3_FRAME_EVIDENCE_TERMS)
        or any(term in entered_frame for term in ("community", "household", "facility", "hospital", "clinic", "district", "region", "surveillance", "cohort", "census"))
    )
    explicit_undercoverage = any(term in combined_frame_text for term in Q3_EXPLICIT_UNDERCOVERAGE_TERMS)
    single_facility_frame = any(
        term in combined_frame_text
        for term in ("single hospital", "one hospital", "one referral hospital", "single clinic", "single facility", "selected facility")
    )
    facility_frame_mismatch = facility_frame and not target_is_attendee_specific and target_category in {
        "Community or general populations", "General febrile population"
    }
    narrow_frame_for_broader_target = single_facility_frame and target_claims_broad_geography and not target_is_attendee_specific
    named_community_without_enumeration = any(
        term in combined_frame_text for term in ("named communities", "selected communities")
    ) and not population_enumeration_frame
    archived_availability_subset = (
        target_category == "Archived or residual specimen populations" or archived_only_unclear
    ) and any(term in combined_frame_text for term in ("only stored sera", "only stored serum", "adequate volume", "available specimens")) and not archived_source_frame
    frame_mismatch = any(
        (
            explicit_undercoverage,
            narrow_frame_for_broader_target,
            facility_frame_mismatch,
            named_community_without_enumeration,
            archived_availability_subset,
        )
    )
    pdf_supported_frame = frame_evidence and not frame_mismatch
    entered_frame_supported = entered_frame_evidence and not frame_mismatch
    q3_positive = target_is_defined and (pdf_supported_frame or entered_frame_supported)
    if pdf_supported_frame:
        q3_confidence = "moderate"
        q3_reason = (
            f"The reported source list/frame reasonably covers the defined {target_label}. Frame coverage is judged against the stated target, not an automatically national population."
        )
    elif entered_frame_supported:
        q3_confidence = "low"
        q3_reason = (
            f"Provisional fallback from the reviewer-entered sampling frame/source population: {sampling_frame_description.strip()}. "
            "The target-population label alone is not used to award Q3; verify frame coverage before finalizing."
        )
    else:
        q3_confidence = "high" if narrow_frame_for_broader_target or archived_availability_subset else "moderate" if facility_frame or frame_mismatch else "low"
        q3_reason = (
            f"The source frame was not described well enough or excludes important parts of the defined {target_label}. Recruitment quality cannot repair an under-covering frame."
        )
    add(
        "Q3",
        1 if q3_positive else 0,
        q3_confidence,
        f"Appraisal Library / Quick Decision Matrix classification. {q3_reason}",
    )

    q4_signal_terms = (
        "sampling", "selected", "selection", "recruited", "recruitment", "enrolled",
        "enrolment", "included", "invited", "eligible", "census", "universal",
        "hospital", "clinic", "facility", "site", "household", "cluster", "stratified",
    )
    q4_sentences: list[str] = []
    for snippet in snippets:
        sentence = snippet.text.lower()
        if not any(term in sentence for term in q4_signal_terms):
            continue
        validation_subset_sentence = any(
            term in sentence
            for term in (
                "random subset of positive", "random subset of negative",
                "randomly selected positive and negative", "random positive and negative samples",
                "selected for laboratory validation", "assay validation subset",
            )
        )
        source_selection_in_same_sentence = any(
            term in sentence
            for term in (
                "participants were recruited", "participants were enrolled", "patients were enrolled",
                "households were selected", "sites were selected", "facilities were selected",
            )
        )
        if validation_subset_sentence and not source_selection_in_same_sentence:
            continue
        q4_sentences.append(sentence)
    q4_text = " ".join(q4_sentences)

    def q4_affirmative(*terms: str) -> bool:
        for term in terms:
            for match in re.finditer(re.escape(term), q4_text):
                prefix = q4_text[max(0, match.start() - 55) : match.start()]
                if not re.search(r"\b(?:no|not|without|did\s+not|was\s+not|were\s+not)\b[^.;:]{0,45}$", prefix):
                    return True
        return False

    q4_high = q4_affirmative(*Q4_SCORE_TERMS[3])
    q4_mid = q4_affirmative(*Q4_SCORE_TERMS[2])
    q4_low = q4_affirmative(*Q4_SCORE_TERMS[1])
    random_validation_subset = has_any(
        "random subset of positive", "random subset of negative",
        "randomly selected positive and negative", "random positive and negative samples",
        "random subsample from",
    )
    target_supports_multisite_capture = any(
        term in target_text for term in ("regional", "facility network", "hospital network", "multisite", "multiple facilities")
    )
    materially_broadened_multisite = multisite_frame and target_supports_multisite_capture
    explicit_non_probability = any(
        term in q4_text
        for term in ("without random selection", "no random selection", "non-probability", "nonprobability")
    )
    high_implementation_evidence = any(
        term in q4_text
        for term in (
            "probability proportional to size", "pps cluster", "random households",
            "random eligible", "strata", "clusters", "sampling stages",
            "all eligible participants were included", "all eligible participants were invited",
            "all eligible staff were included", "all eligible staff included",
            "universal inclusion", "census of",
        )
    )
    user_q4_score = Q4_USER_METHOD_SCORES.get(sampling_recruitment_method)
    user_q4_selected = sampling_recruitment_method in Q4_USER_METHOD_SCORES
    if q4_low:
        q4 = 1
        q4_reason = "Appraisal Library class 1: consequential convenience, consecutive, purposive, referral, volunteer, or otherwise selected recruitment was detected. Random laboratory subsampling does not upgrade participant/specimen recruitment."
        q4_conf = "high" if not (q4_mid or q4_high) else "moderate"
    elif q4_high:
        q4 = 3
        q4_reason = "Appraisal Library class 3: multistage/stratified/cluster probability sampling or documented census/universal eligible capture was detected."
        q4_conf = "high" if high_implementation_evidence else "moderate"
    elif q4_mid or materially_broadened_multisite:
        q4 = 2
        q4_reason = "Appraisal Library class 2: simple/systematic random selection or multisite capture that materially broadened the defined target was detected without a full multistage probability design or census."
        q4_conf = "moderate"
    elif explicit_non_probability:
        q4 = 1
        q4_reason = "Appraisal Library class 1: the reported endpoint-source enrollment explicitly lacked participant-level probability selection."
        q4_conf = "moderate"
    elif user_q4_selected and user_q4_score is not None:
        q4 = user_q4_score
        q4_reason = (
            f"Provisional fallback from the reviewer-entered sampling/recruitment method: "
            f"{sampling_recruitment_method}. The uploaded PDF did not provide classifiable Q4 evidence; verify the endpoint-specific recruitment pathway before finalizing."
        )
        q4_conf = "low"
    elif user_q4_selected:
        q4 = None
        q4_reason = (
            f"The reviewer selected '{sampling_recruitment_method}', which cannot be assigned one Q4 score without identifying the pathway that generated this endpoint's numerator and denominator."
        )
        q4_conf = "low"
    else:
        q4 = 0
        q4_reason = "Appraisal Library class 0: the selection stages and mechanisms that assembled the endpoint-specific analytic population could not be established. Design labels and random assay-validation subsets are insufficient."
        q4_conf = "low"
    add("Q4", q4, q4_conf, q4_reason)

    count_based_q5: dict[str, Any] | None = None
    if synthesis_path_key in SYNTHESIS_PATHS_BY_KEY and counts is not None:
        count_based_q5 = audit_synthesis_path_counts(synthesis_path_key, counts)
    if count_based_q5 and count_based_q5["q5_suggested_score"] is not None:
        add(
            "Q5",
            count_based_q5["q5_suggested_score"],
            "high",
            "Count-based Q5 calculation: " + count_based_q5["q5_basis"] + ". The lower applicable completion percentage determines the score.",
        )
    elif has_any("complete capture", "near-complete", "all eligible records", "all eligible specimens"):
        add("Q5", 2, "moderate", "Complete or near-complete eligible capture was reported.")
    else:
        completeness_sentences = [snippet.text for snippet in _evidence_for("Q5", snippets, 8)]
        completion_rates: list[float] = []
        for sentence in completeness_sentences:
            lowered = sentence.lower()
            for value in re.findall(r"(\d{1,3}(?:\.\d+)?)\s*%", sentence):
                percentage = float(value)
                if not 0 <= percentage <= 100:
                    continue
                if re.search(r"\b(?:missing|nonresponse|non-response|excluded|attrition|lost|loss)\b", lowered) and not re.search(r"\b(?:completion|response rate|tested)\b", lowered):
                    percentage = 100 - percentage
                completion_rates.append(percentage / 100)
            for actual, expected in re.findall(
                r"\b(\d+)\s*(?:of|/)\s*(\d+)\b[^.;]{0,50}\b(?:tested|completed|retested|verified)",
                lowered,
            ):
                if int(expected) > 0 and int(actual) <= int(expected):
                    completion_rates.append(int(actual) / int(expected))
        if completion_rates:
            rate = min(completion_rates)
            q5 = 2 if rate >= 0.8 else 1 if rate >= 0.7 else 0
            add("Q5", q5, "moderate", f"The limiting completion percentage detected near Q5 evidence was {100 * rate:g}%; verify the recruited, tested, and planned-pathway denominators.")
        else:
            missing_detail = ""
            if count_based_q5:
                missing_detail = " " + " ".join(count_based_q5["warnings"])
            add("Q5", None, "low", "No complete auditable Q5 calculation was available. Enter Total recruited and Total tested and, for staged pathways, actual and planned verification counts." + missing_detail)

    primary_assay_text = (primary_assay or full_text).lower()
    confirmation_text = confirmatory_assay.lower()

    def assay_text_has(text: str, *terms: str) -> bool:
        return any(term in text for term in terms)

    neutralization = assay_text_has(primary_assay_text, "prnt", "neutralization", "microneutralization", "vnt")
    molecular = assay_text_has(primary_assay_text, "pcr", "naat", "virus isolation", "sequencing")
    sequencing = assay_text_has(confirmation_text, "sequencing", "second genomic target", "second pcr target", "second target")
    elisa = assay_text_has(primary_assay_text, "elisa", "immunofluorescence", "iifa", "ifa", "immunoblot", "clia", "luminex")
    rapid = assay_text_has(primary_assay_text, "rapid diagnostic test", "immunochromatographic", "rdt")
    confirmation_neutralization = assay_text_has(confirmation_text, "prnt", "neutralization", "microneutralization", "vnt")
    confirmation_molecular = assay_text_has(confirmation_text, "pcr", "naat", "virus isolation", "sequencing", "genomic target")
    if not confirmatory_assay:
        confirmation_neutralization = has_affirmative("prnt", "plaque reduction neutralization", "virus neutralization", "microneutralization", " vnt")
        confirmation_molecular = has_affirmative(
            "confirmed by rt-pcr", "confirmation by pcr", "orthogonal molecular",
            "second genomic target", "second pcr target", "virus isolation", "sequencing"
        )
        sequencing = has_affirmative("sequencing", "genotyping", "second genomic target", "second pcr target", "second target")
    controls = has_affirmative("positive control", "negative control", "extraction control", "amplification control", "manufacturer", "validated", "validation", "reference protocol")
    subset = has_any("subset", "selected positive", "selected samples", "a proportion of")
    staged_endpoint_paths = {
        "serology_tier_b", "serology_tier_c", "serology_tier_d",
        "confirmed_active_tier_b", "confirmed_active_tier_c", "confirmed_active_tier_d",
    }
    staged_endpoint = synthesis_path_key in staged_endpoint_paths or verification_design in {
        "all_screen_positive", "representative_positive_subset", "subset_positive"
    }
    effective_verification_design = (
        SYNTHESIS_PATHS_BY_KEY[count_based_q5["effective_synthesis_path_key"]].verification_design
        if count_based_q5 and count_based_q5.get("effective_synthesis_path_key") in SYNTHESIS_PATHS_BY_KEY
        else verification_design
    )

    endpoint_is_molecular = endpoint_key == "confirmed_active" or (
        endpoint_key is None and pathway == "direct_detection"
    )
    endpoint_is_neutralization = endpoint_key == "neutralizing_antibody"
    endpoint_is_primary_serology = endpoint_key in {
        None, "prior_exposure", "presumptive_igm", "presumptive_ns1", "mixed_serology", "other"
    } and pathway == "serologic"

    if staged_endpoint and (confirmation_neutralization or confirmation_molecular):
        if effective_verification_design == "all_screen_positive":
            q6a, q6a_reason = 4, "A complete predefined screening-confirmation algorithm was identified as the endpoint-defining measurement procedure."
        elif effective_verification_design == "representative_positive_subset":
            q6a, q6a_reason = 3, "A predefined representative-subset screening-confirmation algorithm was identified; its validity depends on the explicit weighted or conditional estimand."
        else:
            q6a, q6a_reason = 2, "Confirmation was endpoint-defining but applied to a selected or unresolved subset, limiting the validity of the overall algorithm."
    elif endpoint_is_molecular and molecular:
        q6a = 4 if controls else 3
        q6a_reason = "Direct molecular/isolation testing was detected" + (" with validation/control evidence." if controls else ", but explicit validation/control evidence needs verification.")
    elif endpoint_is_neutralization and neutralization:
        q6a = 4
        q6a_reason = "A high-specificity neutralization method was detected as the endpoint-defining assay."
    elif elisa:
        q6a = 2
        q6a_reason = "A recognized primary serologic assay was detected. Supporting confirmation is considered separately under Q6c and does not inflate Q6a."
    elif rapid:
        q6a = 1
        q6a_reason = "A screening-level rapid assay was detected."
    else:
        q6a = 0
        q6a_reason = "No sufficiently described endpoint-defining assay was detected."
    add("Q6a", q6a, "moderate", q6a_reason)
    add("Q6b", 1 if controls else 0, "moderate" if controls else "low", "Validation, manufacturer performance, reference protocol, or assay controls were detected." if controls else "No explicit validation, standardization, or control evidence was detected.")

    ancillary_validation = verification_design == "ancillary_validation" or has_any(
        "reference-positive", "reference positive", "reference-negative", "reference negative",
        "assay validation panel", "validation samples"
    )
    if ancillary_validation:
        q6c, q6c_conf, q6c_reason = 0, "moderate", "A separate assay-validation set can support Q6a/Q6b but does not automatically independently confirm the study endpoint for Q6c."
    elif staged_endpoint and (confirmation_neutralization or confirmation_molecular):
        if effective_verification_design == "all_screen_positive":
            q6c, q6c_conf, q6c_reason = 2, "high", (
                "A high-specificity orthogonal confirmatory method is linked to the same staged endpoint and is applied to the complete relevant screening-positive/eligible set."
            )
        elif effective_verification_design == "representative_positive_subset":
            q6c, q6c_conf, q6c_reason = 1, "high", (
                "A high-specificity orthogonal confirmatory method is linked to the same staged endpoint and applied to a predefined representative/random subset. "
                "This is valid limited subset confirmation for Q6c, not complete systematic confirmation of the full relevant eligible set."
            )
        else:
            q6c, q6c_conf, q6c_reason = 1, "moderate", (
                "An orthogonal confirmatory method supports the same staged endpoint, but confirmation is limited to a selected, incomplete, or unresolved subset; complete systematic coverage is not established."
            )
    elif staged_endpoint:
        q6c, q6c_conf, q6c_reason = 0, "moderate", (
            "The staged pathway was selected, but no independent orthogonal confirmatory method linked to the same virus and endpoint was identified."
        )
    elif endpoint_is_molecular:
        if molecular and (sequencing or confirmation_molecular):
            q6c, q6c_conf, q6c_reason = 2, "high", "A molecular endpoint plus independent sequencing/second-target confirmation was detected."
        else:
            q6c, q6c_conf, q6c_reason = 0, "moderate", "A strong primary molecular assay does not itself earn confirmation points; no independent second method was detected."
    elif endpoint_is_neutralization:
        q6c, q6c_conf, q6c_reason = 0, "moderate", "Neutralization is the primary endpoint-defining assay here and does not confirm itself; no additional independent method was detected."
    elif confirmation_neutralization or confirmation_molecular:
        systematic_confirmation = has_any(
            "all positive samples were", "all positives were", "all eligible samples were",
            "systematically confirmed", "every positive sample",
        )
        q6c = 2 if systematic_confirmation and not subset else 1
        q6c_conf = "moderate"
        q6c_reason = "A supporting orthogonal confirmation method was detected" + (" and appears to have been systematically applied to the same endpoint and virus." if q6c == 2 else ", but complete systematic application to the same endpoint and virus was not established.")
    elif elisa and has_any("second serologic assay", "confirmatory elisa", "western blot"):
        q6c, q6c_conf, q6c_reason = 1, "moderate", "A supportive second serologic method was detected without high-specificity neutralization."
    else:
        q6c, q6c_conf, q6c_reason = 0, "low", "No independent confirmatory method was detected."
    add("Q6c", q6c, q6c_conf, q6c_reason)

    uniform = has_any(
        "all samples were tested", "all specimens were tested", "same method was applied",
        "applied to all samples", "applied to all specimens", "all positive samples were",
        "all screen-positive", "same assay was used",
    )
    predefined_subset = has_any("predefined", "random subset", "representative subset", "according to the algorithm", "all eligible")
    inconsistent = staged_endpoint and subset and not predefined_subset and not ancillary_validation
    consistency_fallback = endpoint_procedure_consistency.strip().lower()
    if consistency_fallback.startswith(("same", "consistent", "predefined staged")):
        q7_supported, q7_conf = True, "low"
        q7_reason = "Reviewer fallback indicates that the same primary procedure, or the complete predefined staged algorithm, was applied to everyone eligible at each required step."
    elif consistency_fallback.startswith(("materially", "inconsistent", "selective")):
        q7_supported, q7_conf = False, "low"
        q7_reason = "Reviewer fallback identifies materially inconsistent or selective endpoint-defining testing."
    elif count_based_q5 and synthesis_path_key in {"serology_tier_b", "confirmed_active_tier_b"} and count_based_q5["effective_synthesis_path_key"] != synthesis_path_key:
        q7_supported, q7_conf = False, "high"
        q7_reason = "The complete staged algorithm was not applied to every screening-positive participant requiring confirmation."
    elif staged_endpoint:
        q7_supported = (uniform or predefined_subset) and not inconsistent
        q7_conf = "moderate" if q7_supported or inconsistent else "low"
        q7_reason = "The predefined staged endpoint algorithm appears consistently applied to all eligible samples at each required step." if q7_supported else "Completion and consistent application of every required stage could not be established."
    else:
        q7_supported = uniform and not inconsistent
        q7_conf = "moderate" if q7_supported else "low"
        q7_reason = "The same primary endpoint-defining procedure appears to have been applied to all eligible samples. Optional or ancillary confirmation does not alter this judgment." if q7_supported else "Consistency of the primary endpoint-defining procedure could not be established."
    add("Q7", 1 if q7_supported else 0, q7_conf, q7_reason)

    if count_based_q5 is not None:
        q8_supported = bool(count_based_q5["estimand_ready"])
        missing_labels = ", ".join(count_based_q5["missing_required_fields"])
        count_issues = " ".join(count_based_q5["issues"])
        q8_reason = (
            "The endpoint-matched observed numerator, denominator, and all pathway-specific verification quantities are internally consistent and reconstructable."
            if q8_supported
            else "The estimator is not reconstructable from the entered observed counts. "
            + (f"Missing: {missing_labels}. " if missing_labels else "")
            + count_issues
        )
        add("Q8", 1 if q8_supported else 0, "high", q8_reason.strip())
    else:
        ratio_pattern = re.search(r"\b\d{1,7}\s*(?:/|of)\s*\d{1,7}\b", full_text)
        explicit_counts = has_any("number positive", "participants tested", "samples tested", "numerator", "denominator")
        staged_design = verification_design in {"all_screen_positive", "representative_positive_subset", "subset_positive", "two_phase_validation", "mixed_two_phase"}
        staged_counts = has_any("screen-positive", "screen positive") and has_any(
            "number retested", "confirmed positive", "molecular positive", "neutralization tested", "verification stratum", "sampling weight"
        )
        q8_supported = bool(ratio_pattern or explicit_counts) and (not staged_design or staged_counts)
        add("Q8", 1 if q8_supported else 0, "moderate" if q8_supported else "low", "The endpoint-matched observed numerator/denominator and required verification quantities appear reconstructable." if q8_supported else "All observed quantities required by the actual estimator were not clearly detected; generic prevalence numbers are insufficient for staged or weighted designs.")

    formal_adjustment = has_any("age-standardized", "age-standardised", "sex-standardized", "sex-standardised", "weighted prevalence", "adjusted for age", "adjusted for sex")
    add("Q9", 1 if formal_adjustment else 0, "high" if formal_adjustment else "moderate", "Formal age/sex standardization, weighting, or adjustment was detected." if formal_adjustment else "No formal age/sex correction of the synthesized estimate was detected.")

    if not criterion_is_applicable("Q10", pathway, endpoint_key):
        add("Q10", None, "high", "Q10 is not applicable to this endpoint under the current synthesis framework; the applicable maximum is 18.")
    else:
        performance_adjustment = has_any("true prevalence", "assay-adjusted", "adjusted for sensitivity and specificity", "corrected for sensitivity and specificity", "rogan-gladen", "measurement-error model")
        add("Q10", 1 if performance_adjustment else 0, "high" if performance_adjustment else "moderate", "Explicit assay-performance correction was detected." if performance_adjustment else "No explicit correction for assay sensitivity and specificity was detected.")

    return suggestions


def build_export_record(
    metadata: Mapping[str, Any],
    scores: Mapping[str, Any],
    comments: Mapping[str, str],
    pathway: str,
    classification: Mapping[str, str],
) -> dict[str, Any]:
    endpoint_key = str(metadata.get("endpoint_key") or "")
    if not endpoint_key:
        classification_label = str(classification.get("endpoint") or "")
        endpoint_key = next(
            (key for key, endpoint in ENDPOINTS_BY_KEY.items() if endpoint.label == classification_label),
            "other",
        )
    if endpoint_key not in ENDPOINTS_BY_KEY:
        endpoint_key = "other"
    result = score_assessment(scores, pathway, endpoint_key)
    synthesis_path_key = str(metadata.get("synthesis_path_key") or "")
    synthesis_path = SYNTHESIS_PATHS_BY_KEY.get(synthesis_path_key)
    if synthesis_path:
        path_audit = audit_synthesis_path_counts(synthesis_path_key, metadata)
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
        }
    else:
        count_audit = audit_endpoint_counts(
            endpoint_key,
            metadata.get("verification_design") or None,
            metadata.get("source_population_n"),
            metadata.get("endpoint_tested_n"),
            metadata.get("endpoint_positive_n"),
            metadata.get("screen_positive_n"),
            metadata.get("planned_verification_n"),
            metadata.get("screen_negative_n"),
            metadata.get("verification_negative_tested_n"),
            metadata.get("verification_negative_positive_n"),
            str(metadata.get("numerator_provenance") or "unclear"),
            metadata.get("reference_positive_n"),
            metadata.get("reference_positive_detected_n"),
            metadata.get("reference_negative_n"),
            metadata.get("reference_negative_correct_n"),
        )
    effective_classification = classify_synthesis(
        endpoint_key, count_audit["effective_verification_design"]
    )
    record: dict[str, Any] = {
        **dict(metadata),
        "guide_version": GUIDE_VERSION,
        "appraisal_library_rule_source": APPRAISAL_LIBRARY_RULE_SOURCE,
        "measurement_pathway": pathway,
        "dare_total": result["total"],
        "dare_applicable_maximum": result["maximum"],
        "dare_complete": result["complete"],
        "endpoint_classification": effective_classification.get("endpoint", ""),
        "selected_synthesis_tier": synthesis_path.summary_estimator if synthesis_path else classification.get("tier", ""),
        "synthesis_tier": count_audit["effective_tier"] if synthesis_path else effective_classification.get("tier", ""),
        "analysis_role": effective_classification.get("analysis_role", ""),
        "assay_role": effective_classification.get("assay_role", metadata.get("assay_role", "")),
        "testing_verification_pathway": effective_classification.get("measurement_pathway", ""),
        "classification_caution": effective_classification.get("caution", ""),
        "synthesis_eligibility": effective_classification.get("eligibility", ""),
        "numerator_rule": synthesis_path.numerator if synthesis_path else effective_classification.get("numerator_rule", ""),
        "denominator_rule": synthesis_path.denominator if synthesis_path else effective_classification.get("denominator_rule", ""),
        "endpoint_counts_valid": count_audit["valid"],
        "endpoint_counts_complete": count_audit["complete_counts"],
        "endpoint_count_issues": " | ".join(count_audit["issues"]),
        "endpoint_count_warnings": " | ".join(count_audit["warnings"]),
        "estimand_ready": count_audit["estimand_ready"],
        "missing_estimator_quantities": " | ".join(count_audit["missing_required_fields"]),
        "effective_verification_design": count_audit["effective_verification_design"],
        "effective_synthesis_tier": count_audit["effective_tier"],
        "endpoint_test_positivity": count_audit["endpoint_test_positivity"],
        "verification_subset_positivity": count_audit.get("verification_subset_positivity"),
        "synthesis_denominator_n": count_audit["synthesis_denominator_n"],
        "endpoint_observed_prevalence": count_audit["prevalence"],
        "recruitment_testing_completion_rate": count_audit.get("recruitment_testing_rate"),
        "staged_pathway_completion_rate": count_audit.get("planned_pathway_completion_rate"),
        "planned_pathway_completion_rate": count_audit["completion_rate"],
        "q5_completion_basis": count_audit.get("q5_basis", ""),
        "q5_count_based_suggestion": count_audit["q5_suggested_score"],
        "validation_sensitivity": count_audit["sensitivity"],
        "validation_specificity": count_audit["specificity"],
        "false_positives_n": count_audit.get("false_positives_n"),
        "false_negatives_n": count_audit.get("false_negatives_n"),
    }
    for domain, values in result["domains"].items():
        key = re.sub(r"[^a-z0-9]+", "_", domain.lower()).strip("_")
        record[f"domain_{key}_score"] = values["score"]
        record[f"domain_{key}_maximum"] = values["maximum"]
    for criterion in CRITERIA:
        record[f"{criterion.code}_score"] = result["scores"][criterion.code]
        record[f"{criterion.code}_comment"] = comments.get(criterion.code, "")
    return record


def record_to_json(record: Mapping[str, Any]) -> str:
    return json.dumps(dict(record), ensure_ascii=False, indent=2)


def criteria_as_dicts() -> list[dict[str, Any]]:
    return [asdict(criterion) for criterion in CRITERIA]
