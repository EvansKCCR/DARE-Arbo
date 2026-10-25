"""Shared, conservative document intelligence for DARE-Arbo PDF workflows.

This module deliberately contains no Streamlit code and no appraisal scoring
rules.  It parses a document once, preserves source evidence and provenance,
and exposes study-virus-endpoint candidates to both the Assessor and Appraiser.
Scoring remains the responsibility of :mod:`dare_arbo`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import re
from typing import Any, BinaryIO, Iterable, Mapping, Sequence


ENGINE_VERSION = "2026.10.24.1"
UNRESOLVED = "Could not be established from the article"


@dataclass(frozen=True)
class SourceLocation:
    document: str
    page: int
    section: str = "Unclassified section"
    table_or_figure: str = ""


@dataclass
class EvidenceItem:
    field_id: str
    value: Any = UNRESOLVED
    normalized_value: Any = None
    evidence_text: str = ""
    location: SourceLocation | None = None
    source_span: str = ""
    confidence: str = "UNRESOLVED"
    extraction_method: str = "rules-based shared document intelligence"
    inference_level: str = "UNRESOLVED"
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    scope: str = "endpoint"
    original_extraction: Any = None
    user_override: Any = None

    def __post_init__(self) -> None:
        if self.original_extraction is None:
            self.original_extraction = self.value

    @property
    def final_value(self) -> Any:
        return self.user_override if self.user_override is not None else self.value

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["final_value"] = self.final_value
        return payload


@dataclass(frozen=True)
class TextSpan:
    document: str
    page: int
    section: str
    text: str


@dataclass
class EndpointCandidate:
    candidate_id: str
    virus: str
    endpoint_key: str
    endpoint_label: str
    synthesis_path_key: str
    fields: dict[str, EvidenceItem]
    q_evidence: dict[str, list[EvidenceItem]]
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "virus": self.virus,
            "endpoint_key": self.endpoint_key,
            "endpoint_label": self.endpoint_label,
            "synthesis_path_key": self.synthesis_path_key,
            "fields": {key: value.to_dict() for key, value in self.fields.items()},
            "q_evidence": {
                key: [item.to_dict() for item in values]
                for key, values in self.q_evidence.items()
            },
            "conflicts": self.conflicts,
        }


@dataclass
class DocumentEvidenceModel:
    document_id: str
    sources: list[dict[str, Any]]
    article_fields: dict[str, EvidenceItem]
    candidates: list[EndpointCandidate]
    evidence_index: list[dict[str, Any]]
    pages: list[str]
    provenance: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self, include_pages: bool = False) -> dict[str, Any]:
        payload = {
            "document_id": self.document_id,
            "sources": self.sources,
            "article_fields": {
                key: value.to_dict() for key, value in self.article_fields.items()
            },
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "evidence_index": self.evidence_index,
            "provenance": self.provenance,
            "warnings": self.warnings,
        }
        if include_pages:
            payload["pages"] = self.pages
        return payload


SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Methods", re.compile(r"\b(?:methods?|materials and methods)\b", re.I)),
    ("Study population", re.compile(r"\b(?:study population|participants?|eligibility|enrolment|enrollment)\b", re.I)),
    ("Study setting", re.compile(r"\b(?:study setting|study site|setting)\b", re.I)),
    ("Sampling / recruitment", re.compile(r"\b(?:sampling|recruitment|selection)\b", re.I)),
    ("Laboratory methods", re.compile(r"\b(?:laboratory|serolog|molecular|assay|diagnostic)\b", re.I)),
    ("Statistical analysis", re.compile(r"\b(?:statistical analysis|data analysis)\b", re.I)),
    ("Results", re.compile(r"\bresults?\b", re.I)),
    ("Table", re.compile(r"\btable\s+[s]?\d+\b", re.I)),
    ("Figure", re.compile(r"\b(?:figure|fig\.)\s+[s]?\d+\b", re.I)),
    ("Supplementary material", re.compile(r"\b(?:supplement|appendix)\b", re.I)),
)

VIRUS_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DENV", ("denv", "dengue virus", "dengue")),
    ("CHIKV", ("chikv", "chikungunya virus", "chikungunya")),
    ("ZIKV", ("zikv", "zika virus", "zika")),
    ("YFV", ("yfv", "yellow fever virus", "yellow fever")),
    ("WNV", ("wnv", "west nile virus", "west nile")),
    ("JEV", ("jev", "japanese encephalitis virus", "japanese encephalitis")),
    ("RVFV", ("rvfv", "rift valley fever virus", "rift valley fever")),
    ("ONNV", ("onnv", "o'nyong-nyong", "onyong nyong")),
    ("MAYV", ("mayv", "mayaro virus", "mayaro")),
    ("CCHFV", ("cchfv", "crimean-congo", "crimean congo")),
)

ENDPOINT_PATTERNS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("prior_exposure", "Prior exposure / antibody seroprevalence", ("igg", "antibody seroprevalence", "seroprevalence", "prior exposure"), "serology_apparent"),
    ("presumptive_igm", "Presumptive recent infection - IgM", ("igm", "recent infection"), "igm_apparent"),
    ("presumptive_ns1", "Presumptive active infection - NS1 antigen", ("ns1", "nonstructural protein 1", "antigen positive"), "ns1_apparent"),
    ("neutralizing_antibody", "Neutralizing-antibody prevalence", ("prnt", "neutralization", "neutralising", "microneutralization", "plaque reduction"), "neutralization_universal"),
    ("confirmed_active", "Confirmed active infection - direct viral detection", ("rt-pcr", "rt-qpcr", "pcr", "naat", "virus isolation", "viral rna", "sequencing"), "confirmed_active_universal"),
    ("assay_performance", "Assay performance endpoint", ("sensitivity", "specificity", "assay validation", "reference positive", "reference negative", "false positive", "false negative"), "serology_validation"),
)

ASSAY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("IgG ELISA", ("igg elisa", "elisa igg")),
    ("IgM ELISA", ("igm elisa", "elisa igm")),
    ("NS1 antigen assay", ("ns1 antigen", "ns1 assay", "ns1 rapid")),
    ("ELISA", ("elisa",)),
    ("Rapid diagnostic test", ("rapid diagnostic test", " rdt", "immunochromatographic")),
    ("IIFA/IFA", ("iifa", "immunofluorescence", " ifa")),
    ("CLIA", ("clia", "chemilumines")),
    ("PRNT", ("prnt", "plaque reduction neutralization", "plaque reduction neutralisation")),
    ("Microneutralization", ("microneutralization", "microneutralisation")),
    ("RT-qPCR", ("rt-qpcr", "qrt-pcr", "real-time rt-pcr", "real time rt-pcr")),
    ("RT-PCR/PCR", ("rt-pcr", "polymerase chain reaction", " pcr")),
    ("Virus isolation", ("virus isolation", "viral culture")),
    ("Sequencing", ("sequencing", "sequence-confirmed", "genotyping")),
)

Q_TERMS: Mapping[str, tuple[str, ...]] = {
    "Q1": ("aged", "age range", "male", "female", "sex", "gender", "eligibility"),
    "Q2": ("participants", "patients", "specimens", "serum", "plasma", "whole blood"),
    "Q3": ("registry", "register", "sentinel", "household", "facility", "hospital", "clinic", "cohort", "catchment"),
    "Q4": ("random", "consecutive", "convenience", "purposive", "census", "universal", "recruited", "enrolled", "selected"),
    "Q5": ("eligible", "tested", "missing", "excluded", "invalid", "indeterminate", "nonresponse", "insufficient volume"),
    "Q6a": ("assay", "elisa", "pcr", "prnt", "ns1", "igm", "igg", "specimen"),
    "Q6b": ("validated", "manufacturer", "sensitivity", "specificity", "cutoff", "control", "quality control", "reference protocol"),
    "Q6c": ("confirm", "prnt", "sequencing", "virus isolation", "second target", "orthogonal"),
    "Q7": ("all samples", "all specimens", "subset", "algorithm", "available specimens", "severity", "titre", "titer"),
    "Q8": ("positive", "tested", "prevalence", "numerator", "denominator"),
    "Q9": ("age-standard", "sex-standard", "survey weight", "weighted prevalence", "standardized prevalence", "standardised prevalence"),
    "Q10": ("rogan-gladen", "rogan gladen", "true prevalence", "corrected prevalence", "assay-adjusted", "bayesian adjustment", "latent class", "measurement error"),
}


def read_pdf(source: bytes | bytearray | BinaryIO, name: str = "article.pdf", kind: str = "main") -> dict[str, Any]:
    """Read a PDF once and return a page-aware source document."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PDF extraction requires the 'pypdf' package.") from exc

    raw = bytes(source) if isinstance(source, (bytes, bytearray)) else source.read()
    reader = PdfReader(BytesIO(raw))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValueError("The PDF is encrypted and cannot be read without a password.") from exc
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    metadata = {
        str(key).lstrip("/").lower(): str(value).strip()
        for key, value in (reader.metadata or {}).items()
        if value not in (None, "")
    }
    return {
        "name": name,
        "kind": kind,
        "sha256": sha256(raw).hexdigest(),
        "pages": pages,
        "metadata": metadata,
        "page_count": len(pages),
        "character_count": sum(len(page) for page in pages),
        "scanned_or_empty": not any(page.strip() for page in pages),
    }


def _section_for(text: str, current: str) -> str:
    for label, pattern in SECTION_PATTERNS:
        if pattern.search(text[:180]):
            return label
    return current


def build_spans(sources: Sequence[Mapping[str, Any]]) -> list[TextSpan]:
    """Split all source pages into traceable spans while retaining document identity."""
    spans: list[TextSpan] = []
    for source in sources:
        current_section = "Unclassified section"
        for page_number, page in enumerate(source.get("pages", []), start=1):
            lines = [re.sub(r"\s+", " ", line).strip() for line in str(page).splitlines()]
            for line in lines:
                if not line:
                    continue
                current_section = _section_for(line, current_section)
                for sentence in re.split(r"(?<=[.!?])\s+|\s*[•]\s*", line):
                    sentence = sentence.strip()
                    if len(sentence) >= 18:
                        spans.append(TextSpan(str(source.get("name", "article.pdf")), page_number, current_section, sentence[:1200]))
    return spans


def _ranked_spans(spans: Sequence[TextSpan], terms: Iterable[str], limit: int = 3, virus: str = "") -> list[TextSpan]:
    terms = tuple(term.lower() for term in terms)
    virus_terms = next((items for label, items in VIRUS_PATTERNS if label == virus), ())
    ranked: list[tuple[int, int, TextSpan]] = []
    priorities = {"Methods": 5, "Study population": 5, "Sampling / recruitment": 5, "Laboratory methods": 5, "Statistical analysis": 4, "Results": 4, "Table": 4, "Figure": 3, "Supplementary material": 4, "Unclassified section": 1}
    for span in spans:
        lowered = span.text.lower()
        hits = sum(term in lowered for term in terms)
        if not hits:
            continue
        virus_bonus = 2 if virus_terms and any(term in lowered for term in virus_terms) else 0
        ranked.append((hits + virus_bonus, priorities.get(span.section, 1), span))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2].page, len(item[2].text)))
    return [span for _, _, span in ranked[:limit]]


def _item(field_id: str, span: TextSpan | None, value: Any, normalized: Any = None, *, inference: str = "DIRECT", confidence: str = "HIGH", scope: str = "endpoint", notes: str = "") -> EvidenceItem:
    if span is None:
        return EvidenceItem(field_id=field_id, value=UNRESOLVED, confidence="UNRESOLVED", inference_level="UNRESOLVED", notes=notes, scope=scope)
    return EvidenceItem(
        field_id=field_id,
        value=value,
        normalized_value=normalized if normalized is not None else value,
        evidence_text=span.text,
        location=SourceLocation(span.document, span.page, span.section),
        source_span=span.text,
        confidence=confidence,
        inference_level=inference,
        notes=notes,
        scope=scope,
    )


def _first_classification(spans: Sequence[TextSpan], vocabulary: Sequence[tuple[str, tuple[str, ...]]], field_id: str, scope: str = "article") -> EvidenceItem:
    matches: list[tuple[int, TextSpan, str]] = []
    for span in spans:
        lowered = span.text.lower()
        for label, terms in vocabulary:
            hits = sum(term in lowered for term in terms)
            if hits:
                matches.append((hits, span, label))
    if not matches:
        return _item(field_id, None, UNRESOLVED, scope=scope)
    matches.sort(key=lambda row: (-row[0], row[1].page))
    _, span, label = matches[0]
    return _item(field_id, span, label, label, inference="DERIVED", confidence="MODERATE", scope=scope)


TARGET_VOCABULARY = (
    ("Suspected arbovirus population", ("suspected arbovirus", "suspected dengue", "suspected chikungunya", "suspected zika")),
    ("Non-malarial febrile population", ("non-malarial febrile", "malaria-negative febrile", "malaria negative febrile")),
    ("General febrile population", ("febrile patients", "acute febrile illness", "fever patients", "presenting with fever")),
    ("Non-febrile patients", ("non-febrile", "nonfebrile", "afebrile patients")),
    ("General outpatients", ("general outpatients", "outpatient population", "clinic attendees")),
    ("Inpatient clinical population", ("inpatients", "hospitalized patients", "hospitalised patients")),
    ("Pregnant women / antenatal populations", ("pregnant women", "antenatal", "anc attendees")),
    ("Blood donors", ("blood donors", "blood donor")),
    ("Occupationally / zoonotically exposed populations", ("occupationally exposed", "zoonotically exposed", "farmers", "herders", "slaughterhouse", "veterinarians")),
    ("Community or general populations", ("general population", "community residents", "household survey", "population-based")),
    ("Other explicitly defined population", ("defined cohort", "clinical cohort", "pediatric patients", "paediatric patients")),
)

FRAME_VOCABULARY = (
    ("Inpatient Registry", ("inpatient registry", "admission register", "ward register")),
    ("Outpatient Clinic Registry", ("outpatient registry", "clinic register", "outpatient register")),
    ("General Lab Register", ("laboratory register", "lab register")),
    ("Specialised Clinic Register", ("specialist clinic", "specialised clinic", "specialized clinic")),
    ("Blood Donor Registry", ("blood donor registry", "blood bank register", "donor register")),
    ("Archived Specimen Frame", ("archived specimens", "stored specimens", "biobank", "residual specimens")),
    ("Sentinel Site / Network", ("sentinel site", "sentinel hospital", "sentinel network")),
    ("Surveillance / Outbreak Setting", ("surveillance system", "outbreak investigation", "outbreak setting")),
    ("Occupational / Exposure Frame", ("employee roster", "staff list", "occupational cohort")),
    ("Population / Household Enumeration", ("household enumeration", "household list", "census list", "population register")),
    ("Parent Cohort / Research Roster", ("parent cohort", "research roster", "cohort roster")),
    ("Health-facility frame, service unspecified", ("hospital attendees", "facility attendees", "health facility")),
)


def _detect_recruitment(spans: Sequence[TextSpan]) -> dict[str, EvidenceItem]:
    fields: dict[str, EvidenceItem] = {}
    stages = {
        "site_selection": ("sentinel", "sites were selected", "facilities were selected", "purposive facilities", "randomly selected facilities"),
        "participant_selection": ("consecutive", "convenience", "randomly selected participants", "random sample", "all eligible", "census", "volunteer", "purposive", "enrolled", "recruited"),
        "laboratory_subsampling": ("random subset", "selected subset", "subsample", "specimens were selected", "positive samples were retested", "negative samples were retested"),
    }
    for field_id, terms in stages.items():
        matches = _ranked_spans(spans, terms, 1)
        if not matches:
            fields[field_id] = _item(field_id, None, UNRESOLVED, scope="article" if field_id != "laboratory_subsampling" else "endpoint")
            continue
        span = matches[0]
        lowered = span.text.lower()
        normalized = next((term for term in terms if term in lowered), "described selection stage")
        fields[field_id] = _item(field_id, span, normalized, normalized, inference="DERIVED", confidence="MODERATE", scope="article" if field_id != "laboratory_subsampling" else "endpoint")
    return fields


def _detect_assays(spans: Sequence[TextSpan], endpoint_terms: Sequence[str], virus: str) -> list[tuple[str, TextSpan]]:
    relevant = _ranked_spans(spans, tuple(endpoint_terms) + tuple(term for _, terms in ASSAY_PATTERNS for term in terms), 30, virus)
    virus_terms = next((items for label, items in VIRUS_PATTERNS if label == virus), ())
    other_virus_terms = tuple(
        term for label, items in VIRUS_PATTERNS if label != virus for term in items
    )
    if virus_terms:
        relevant = [
            span for span in relevant
            if any(term in span.text.lower() for term in virus_terms)
            or not any(term in span.text.lower() for term in other_virus_terms)
        ]
    found: list[tuple[str, TextSpan]] = []
    for span in relevant:
        lowered = span.text.lower()
        for label, terms in ASSAY_PATTERNS:
            if any(term in lowered for term in terms) and label not in {name for name, _ in found}:
                found.append((label, span))
    return found


def _count_candidates(spans: Sequence[TextSpan], endpoint_terms: Sequence[str], virus: str) -> list[dict[str, Any]]:
    relevant = _ranked_spans(spans, tuple(endpoint_terms) + ("positive", "tested", "prevalence"), 40, virus)
    virus_terms = next((items for label, items in VIRUS_PATTERNS if label == virus), ())
    other_virus_terms = tuple(
        term for label, items in VIRUS_PATTERNS if label != virus for term in items
    )
    if virus_terms:
        relevant = [
            span for span in relevant
            if any(term in span.text.lower() for term in virus_terms)
            or not any(term in span.text.lower() for term in other_virus_terms)
        ]
    candidates: list[dict[str, Any]] = []
    patterns = (
        re.compile(r"\b(\d{1,7})\s*/\s*(\d{1,7})\b"),
        re.compile(r"\b(\d{1,7})\s+(?:of|out of)\s+(\d{1,7})\b", re.I),
    )
    for span in relevant:
        for pattern in patterns:
            for match in pattern.finditer(span.text):
                numerator, denominator = int(match.group(1)), int(match.group(2))
                if denominator <= 0 or numerator > denominator:
                    continue
                candidates.append({"numerator": numerator, "denominator": denominator, "span": span, "matched": match.group(0)})
    return candidates


def _confirmation(spans: Sequence[TextSpan], assays: Sequence[tuple[str, TextSpan]], virus: str) -> tuple[EvidenceItem, EvidenceItem, EvidenceItem]:
    confirmation_terms = ("confirmed by", "confirmation", "retested", "verified", "all positive", "subset of positive", "random subset", "selected positive")
    matches = _ranked_spans(spans, confirmation_terms, 5, virus)
    confirmatory_names = [name for name, _ in assays if name in {"PRNT", "Microneutralization", "RT-qPCR", "RT-PCR/PCR", "Virus isolation", "Sequencing"}]
    if not matches or not confirmatory_names:
        unresolved = _item("confirmatory_assay", None, UNRESOLVED)
        return unresolved, _item("confirmation_coverage", None, UNRESOLVED), _item("confirmation_role", None, UNRESOLVED)
    span = matches[0]
    lowered = span.text.lower()
    if any(term in lowered for term in ("all positive", "all screen-positive", "all screening-positive", "every positive")):
        coverage = "All screening-positive specimens"
        confidence = "HIGH"
    elif any(term in lowered for term in ("random subset", "representative subset", "predefined subset")):
        coverage = "Representative/random subset"
        confidence = "HIGH"
    elif any(term in lowered for term in ("subset", "selected", "available specimens", "sufficient volume")):
        coverage = "Selected subset"
        confidence = "MODERATE"
    else:
        coverage = UNRESOLVED
        confidence = "LOW"
    role = "Endpoint-linked confirmation" if any(term in lowered for term in ("confirmed positive", "confirmed infection", "confirmed seropositivity")) else "Ancillary or unresolved confirmation role"
    return (
        _item("confirmatory_assay", span, "; ".join(confirmatory_names), confirmatory_names, inference="DERIVED", confidence="MODERATE"),
        _item("confirmation_coverage", span, coverage, coverage, inference="DERIVED", confidence=confidence),
        _item("confirmation_role", span, role, role, inference="DERIVED", confidence="MODERATE"),
    )


def _path_for(endpoint_key: str, coverage: str, confirmation_role: str) -> str:
    linked = confirmation_role == "Endpoint-linked confirmation"
    if endpoint_key == "prior_exposure" and linked:
        if coverage == "All screening-positive specimens":
            return "serology_tier_b"
        if coverage == "Representative/random subset":
            return "serology_tier_c"
        return "serology_tier_d"
    if endpoint_key == "confirmed_active" and linked:
        if coverage == "All screening-positive specimens":
            return "confirmed_active_tier_b"
        if coverage == "Representative/random subset":
            return "confirmed_active_tier_c"
        return "confirmed_active_tier_d"
    return {
        "prior_exposure": "serology_apparent",
        "presumptive_igm": "igm_apparent",
        "presumptive_ns1": "ns1_apparent",
        "neutralizing_antibody": "neutralization_universal",
        "confirmed_active": "confirmed_active_universal",
        "assay_performance": "serology_validation",
    }.get(endpoint_key, "serology_apparent")


def _q_evidence(spans: Sequence[TextSpan], virus: str, endpoint_terms: Sequence[str]) -> dict[str, list[EvidenceItem]]:
    result: dict[str, list[EvidenceItem]] = {}
    for code, terms in Q_TERMS.items():
        matches = _ranked_spans(spans, terms, 8, virus)
        if code == "Q9":
            matches = [
                span for span in matches
                if not (
                    re.search(r"\b(?:risk[- ]factor|logistic|cox|outcome)\s+(?:model|regression)\b", span.text, re.I)
                    and not re.search(r"\b(?:standardized|standardised|weighted)\s+prevalence\b", span.text, re.I)
                )
            ]
        if code == "Q10":
            matches = [
                span for span in matches
                if not re.search(
                    r"\b(?:no|not|without|did\s+not|was\s+not|were\s+not)\b[^.;]{0,45}"
                    r"\b(?:corrected prevalence|true prevalence|assay-adjusted|rogan[- ]gladen|bayesian adjustment|latent class|measurement error)\b",
                    span.text,
                    re.I,
                )
            ]
        matches = matches[:3]
        result[code] = [
            _item(f"{code}_evidence_{index}", span, span.text, span.text, scope="endpoint")
            for index, span in enumerate(matches, start=1)
        ]
    return result


def extract_document_model(sources: Sequence[Mapping[str, Any]], rule_version: str = "") -> DocumentEvidenceModel:
    """Create one auditable evidence model from pre-read main/supplement sources."""
    if not sources:
        raise ValueError("At least one source document is required.")
    spans = build_spans(sources)
    combined_pages: list[str] = []
    for source in sources:
        combined_pages.extend(str(page) for page in source.get("pages", []))
    combined_text = " ".join(span.text.lower() for span in spans)
    document_id = sources[0].get("sha256", "") or sha256("\n".join(combined_pages).encode("utf-8")).hexdigest()

    target = _first_classification(spans, TARGET_VOCABULARY, "target_population", "article")
    frame = _first_classification(spans, FRAME_VOCABULARY, "sampling_frame", "article")
    recruitment = _detect_recruitment(spans)
    article_fields = {"target_population": target, "sampling_frame": frame, **recruitment}

    viruses = [label for label, terms in VIRUS_PATTERNS if any(term in combined_text for term in terms)]
    if not viruses:
        viruses = ["Virus unresolved"]
    random_two_strata_validation = bool(
        re.search(r"random(?:ly)?\s+(?:selected\s+)?(?:subset[s]?\s+of\s+)?(?:screening[- ]|primary[- ])?positive", combined_text)
        and re.search(r"random(?:ly)?\s+(?:selected\s+)?(?:subset[s]?\s+of\s+)?(?:screening[- ]|primary[- ])?negative", combined_text)
    )
    endpoints = []
    for definition in ENDPOINT_PATTERNS:
        endpoint_key, _, terms, _ = definition
        if not any(term in combined_text for term in terms):
            continue
        if endpoint_key == "neutralizing_antibody":
            independent_neutralization_endpoint = bool(
                re.search(r"neutraliz(?:ing|ing-antibody|ation|ation-antibody)\s+(?:antibody\s+)?prevalence", combined_text)
                or re.search(r"(?:all|every|each)\s+(?:participants?|samples?|specimens?).{0,80}(?:prnt|neutraliz)", combined_text)
                or re.search(r"(?:prnt|neutraliz)[^.;]{0,80}\b\d{1,7}\s*/\s*\d{1,7}\b", combined_text)
            )
            if not independent_neutralization_endpoint:
                continue
        if endpoint_key == "assay_performance" and not (
            random_two_strata_validation
            or ("sensitivity" in combined_text and "specificity" in combined_text)
            or "assay validation" in combined_text
        ):
            continue
        endpoints.append(definition)
    if not endpoints:
        endpoints = [("other", "Other explicitly defined endpoint", (), "serology_apparent")]

    candidates: list[EndpointCandidate] = []
    for virus in viruses:
        virus_terms = next((terms for label, terms in VIRUS_PATTERNS if label == virus), ())
        for endpoint_key, endpoint_label, endpoint_terms, default_path in endpoints:
            linked_spans = [
                span for span in spans
                if (not virus_terms or any(term in span.text.lower() for term in virus_terms))
                and (not endpoint_terms or any(term in span.text.lower() for term in endpoint_terms))
            ]
            if len(viruses) > 1 and endpoint_terms and not linked_spans:
                continue
            assays = _detect_assays(spans, endpoint_terms, virus)
            primary = assays[0] if assays else None
            confirmation_assay, confirmation_coverage, confirmation_role = _confirmation(spans, assays, virus)
            counts = _count_candidates(spans, endpoint_terms, virus)
            fields: dict[str, EvidenceItem] = {
                "virus": _item("virus", linked_spans[0] if linked_spans else (spans[0] if spans else None), virus, virus, inference="DERIVED", confidence="MODERATE"),
                "endpoint": _item("endpoint", linked_spans[0] if linked_spans else None, endpoint_label, endpoint_key, inference="DERIVED", confidence="MODERATE"),
                "primary_assay": _item("primary_assay", primary[1] if primary else None, primary[0] if primary else UNRESOLVED, primary[0] if primary else None, inference="DERIVED", confidence="MODERATE"),
                "confirmatory_assay": confirmation_assay,
                "confirmation_coverage": confirmation_coverage,
                "confirmation_role": confirmation_role,
                "target_population": target,
                "sampling_frame": frame,
                **recruitment,
            }
            conflicts: list[dict[str, Any]] = []
            if counts:
                selected = counts[0]
                fields["numerator"] = _item("numerator", selected["span"], selected["numerator"], selected["numerator"], notes="Observed count extracted from an endpoint-relevant n/N expression; reviewer confirmation required.")
                fields["denominator"] = _item("denominator", selected["span"], selected["denominator"], selected["denominator"], notes="Observed count extracted from an endpoint-relevant n/N expression; reviewer confirmation required.")
                pairs = sorted({(row["numerator"], row["denominator"]) for row in counts})
                if len(pairs) > 1:
                    conflict = {
                        "field": "endpoint_numerator_denominator",
                        "candidates": [{"numerator": n, "denominator": d} for n, d in pairs],
                        "reason": "Multiple endpoint-relevant n/N expressions were detected across the article bundle.",
                        "resolution_status": "needs_review",
                    }
                    conflicts.append(conflict)
                    fields["numerator"].conflicts.append(conflict)
                    fields["denominator"].conflicts.append(conflict)
                    fields["numerator"].confidence = "LOW"
                    fields["denominator"].confidence = "LOW"
            else:
                fields["numerator"] = _item("numerator", None, UNRESOLVED)
                fields["denominator"] = _item("denominator", None, UNRESOLVED)
            path = _path_for(endpoint_key, str(confirmation_coverage.final_value), str(confirmation_role.final_value))
            if endpoint_key == "assay_performance" and any(
                name in {"RT-qPCR", "RT-PCR/PCR", "Virus isolation", "Sequencing"}
                for name, _ in assays
            ):
                path = "molecular_validation"
            if path == "serology_apparent" and endpoint_key != "prior_exposure":
                path = default_path
            candidate_id = f"{virus}-{endpoint_key}-{len(candidates) + 1}"
            candidates.append(EndpointCandidate(candidate_id, virus, endpoint_key, endpoint_label, path, fields, _q_evidence(spans, virus, endpoint_terms), conflicts))

    warnings: list[str] = []
    if any(source.get("scanned_or_empty") for source in sources):
        warnings.append("One or more source files contained no extractable text and require OCR or manual review.")
    return DocumentEvidenceModel(
        document_id=str(document_id),
        sources=[{key: value for key, value in source.items() if key != "pages"} for source in sources],
        article_fields=article_fields,
        candidates=candidates,
        evidence_index=[
            {
                "chunk_id": f"{span.document}:{span.page}:{index}",
                "document": span.document,
                "page": span.page,
                "section": span.section,
                "text": span.text,
                "table_or_figure": (
                    span.section if span.section in {"Table", "Figure"} else ""
                ),
                "untrusted_source": True,
            }
            for index, span in enumerate(spans, start=1)
        ],
        pages=combined_pages,
        provenance={
            "extraction_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "extraction_engine": "DARE-Arbo shared document intelligence",
            "extraction_engine_version": ENGINE_VERSION,
            "dare_arbo_rule_version": rule_version,
            "parsed_once": True,
        },
        warnings=warnings,
    )


def parse_pdf_bundle(main_pdf: bytes, main_name: str, supplements: Sequence[tuple[str, bytes]] = (), rule_version: str = "") -> DocumentEvidenceModel:
    """Read one article plus optional supplements and extract a shared model."""
    sources = [read_pdf(main_pdf, main_name, "main")]
    sources.extend(read_pdf(raw, name, "supplement") for name, raw in supplements)
    return extract_document_model(sources, rule_version=rule_version)

