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
import json
import re
from typing import Any, BinaryIO, Iterable, Mapping, Sequence


ENGINE_VERSION = "2026.11.26.1"
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
    ("NAAT", ("naat", "nucleic acid amplification")),
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


ASSAY_ENDPOINT_MARKERS = {
    "prior_exposure": r"\bigg\b",
    "presumptive_igm": r"\bigm\b",
    "presumptive_ns1": r"\b(?:ns1|nonstructural protein 1)\b",
    "neutralizing_antibody": r"\b(?:prnt\d*|(?:micro)?neutraliz\w*|neutralis\w*|plaque reduction)\b",
    "confirmed_active": r"\b(?:(?:rt[- ]?)?q?pcr|naat|viral rna|virus isolation|sequencing)\b",
}
NEUTRALIZATION_ASSAYS = {"PRNT", "Microneutralization"}
MOLECULAR_ASSAYS = {"RT-qPCR", "RT-PCR/PCR", "Virus isolation", "Sequencing", "NAAT"}
_NUMBER = r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d{1,7})(?![\w.,]\d)"
_PAIR = re.compile(_NUMBER + r"\s*(?:/|\bout of\b|\bof\b)\s*" + _NUMBER + r"(?!\d|\s*[/%])", re.I)
_OBSERVED_OUTCOME = re.compile(r"\b(?:positive|positivity|seropositive|seroprevalence|prevalence|detected|confirmed)\b", re.I)
_NON_OBSERVED = re.compile(r"\b(?:posterior|expected positives?|model[- ](?:derived|predicted)|simulated|adjusted prevalence|predicted positives?)\b", re.I)
_PLAN = re.compile(r"\b(?:plan(?:ned)?|intend(?:ed)?|scheduled|were to be|would be|targeted for|protocol specified)\b", re.I)
_VERIFIED = re.compile(r"\b(?:retested|underwent|received|completed|were verified|were (?:subsequently )?(?:re-?tested|confirmed)|was performed|confirmed by|tested by|tested using)\b", re.I)
_SCREEN_POS = r"(?:(?:screen(?:ing)?|primary|elisa|igg|igm|ns1)[- ]+)?positiv(?:e|es)"
_SUBSET = re.compile(r"\b(?:not\s+(?:all|every|each)|subset|some (?:of the )?" + _SCREEN_POS + r"|selected (?:positive|specimens|samples)|available specimens|when (?:samples|specimens) were available|sufficient volume|only\s+\d+|\bexcept\b)\b", re.I)
_RANDOM_SUBSET = re.compile(r"\b(?:random(?:ly selected)?|representative)[- ]+subset\b|\b(?:subset|sample)\b.{0,45}\b(?:randomly selected|representative)\b", re.I)
_ALL_POS = re.compile(r"\b(?:all|every|each)\s+(?:the\s+)?" + _SCREEN_POS + r"\b", re.I)
_STAGED = re.compile(r"\b(?:(?:screen(?:ing)?|primary)[- ]+positiv(?:e|es)|subset of (?:the )?positive|retested|verif(?:ied|ication)|confirmatory|confirmation|validation (?:subset|set|sample|panel))\b", re.I)
_STRATUM = re.compile(r"\b(?:elisa|igg|igm|ns1)[- ]+positiv(?:e|es)\s+(?:specimens?|samples?|participants?|patients?|sera)\b", re.I)
_TWO_STRATA = re.compile(r"\b(?:screen(?:ing)?[- ]+)?positiv(?:e|es)\b.{0,35}\band\b.{0,25}\b(?:screen(?:ing)?[- ]+)?negativ(?:e|es)\b", re.I)


def _virus_mentions(text: str) -> set[str]:
    return {
        name for name, terms in VIRUS_PATTERNS
        if any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I) for term in terms)
    }


def _endpoint_mentions(text: str) -> list[tuple[int, str]]:
    return sorted((hit.start(), endpoint) for endpoint, pattern in ASSAY_ENDPOINT_MARKERS.items()
                  for hit in re.finditer(pattern, text, re.I))


def _pair_value(match: re.Match[str]) -> tuple[int, int]:
    return int(match[1].replace(",", "")), int(match[2].replace(",", ""))


def _verification_pair(span: TextSpan) -> tuple[int, int] | None:
    """Extract actual retested / screen-positive eligible, not assay positives."""
    for match in _PAIR.finditer(span.text):
        tail = span.text[match.end():match.end() + 125]
        if re.match(r"\s+(?:of the\s+)?" + _SCREEN_POS
                    + r"\b(?:\s+(?:specimens?|samples?|participants?|patients?|sera))?\s+(?:were\s+)?(?:actually\s+)?(?:retested|tested|underwent|received|were\s+verified)\b", tail, re.I):
            if not _PLAN.search(span.text[max(0, match.start() - 30):match.end() + 18]):
                return _pair_value(match)
    return None


def _count_candidates(
    spans: Sequence[TextSpan], endpoint_key: str, virus: str, single_virus: bool,
    staged: bool = False,
) -> list[dict[str, Any]]:
    """Return observed outcome pairs with an explicit, unambiguous local link."""
    candidates: list[dict[str, Any]] = []
    marker_key = "neutralizing_antibody" if staged and endpoint_key == "prior_exposure" else endpoint_key
    if marker_key not in ASSAY_ENDPOINT_MARKERS:
        return candidates  # Validation needs an explicit stratum-specific contingency table.
    for span in spans:
        text = span.text
        if not _OBSERVED_OUTCOME.search(text) or _NON_OBSERVED.search(text) or _PLAN.search(text):
            continue
        mentions = _virus_mentions(text)
        if (mentions and mentions != {virus}) or (not mentions and not single_virus):
            continue
        matches = list(_PAIR.finditer(text))
        markers = _endpoint_mentions(text[:matches[0].start()] if len(matches) > 1 else text) if matches else []
        keys = [key for _, key in markers]
        if len(matches) > 1:
            # Map parallel marker/result lists only when the authors explicitly
            # say "respectively" and every marker has one corresponding pair.
            if not re.search(r"\brespectively\b", text, re.I) or len(keys) != len(matches) or len(set(keys)) != len(keys):
                continue
            selected = [match for key, match in zip(keys, matches) if key == marker_key]
        elif matches and set(keys) == {marker_key}:
            selected = matches
        elif matches and staged and marker_key in keys and _STAGED.search(text):
            # The screen marker may appear earlier in conditional results.
            selected = matches if keys[-1] == marker_key else []
        else:
            continue
        for match in selected:
            numerator, denominator = _pair_value(match)
            if (denominator <= 0 or numerator > denominator or _verification_pair(span)
                or _TWO_STRATA.search(text) or text[:match.start()].rstrip().endswith("/")):
                continue
            if not staged and (_STAGED.search(text) or (_STRATUM.search(text) and len(set(keys)) > 1)):
                continue  # Not direct prevalence in the population tested.
            if staged and not _STAGED.search(text):
                continue
            candidates.append({"numerator": numerator, "denominator": denominator, "span": span, "matched": match.group(0)})
    return candidates


def _confirmation(
    spans: Sequence[TextSpan], endpoint_key: str, virus: str, single_virus: bool,
) -> dict[str, EvidenceItem]:
    fields = {name: _item(name, None, UNRESOLVED) for name in (
        "confirmatory_assay", "confirmation_coverage", "confirmation_role",
        "verification_expected", "verification_completed",
    )}
    possible_assays = (
        MOLECULAR_ASSAYS if endpoint_key == "confirmed_active" else
        NEUTRALIZATION_ASSAYS if endpoint_key == "prior_exposure" else set()
    )
    if not possible_assays:
        return fields
    linked: list[tuple[TextSpan, list[str]]] = []
    for span in spans:
        mentions = _virus_mentions(span.text)
        if (mentions and mentions != {virus}) or (not mentions and not single_virus):
            continue
        names = [name for name, terms in ASSAY_PATTERNS if name in possible_assays
                 and any(term in span.text.lower() for term in terms)]
        if "RT-qPCR" in names and "RT-PCR/PCR" in names:
            names.remove("RT-PCR/PCR")
        if names and (
            _STAGED.search(span.text) or _STRATUM.search(span.text)
            or re.search(r"\b(?:some (?:of the )?positives?|(?:all|every) positives?)\b", span.text, re.I)
        ):
            linked.append((span, names))
    if not linked:
        return fields
    assay_span, names = next(((span, names) for span, names in linked if not _PLAN.search(span.text)), linked[0])
    fields["confirmatory_assay"] = _item("confirmatory_assay", assay_span, "; ".join(names), names,
                                         inference="DERIVED", confidence="LOW" if _PLAN.search(assay_span.text) else "MODERATE")
    observed = [(span, names) for span, names in linked if not _PLAN.search(span.text)]
    if any(_TWO_STRATA.search(span.text) and not _ALL_POS.search(span.text) for span, _ in linked):
        fields["confirmation_role"] = _item(
            "confirmation_role", assay_span, "Ancillary positive/negative assay validation",
            inference="DERIVED", confidence="MODERATE",
        )
        return fields
    # An unlabelled actual/eligible pair can supplement an unambiguous single-virus pathway.
    completion = []
    competing_stage = "neutralizing_antibody" if endpoint_key == "confirmed_active" else "confirmed_active"
    other_family_staged = any(
        any(key == competing_stage for _, key in _endpoint_mentions(span.text))
        and (_STAGED.search(span.text) or _STRATUM.search(span.text)) for span in spans
    )
    for span in spans:
        pair = _verification_pair(span)
        if not pair or not 0 <= pair[0] <= pair[1] or not pair[1]:
            continue
        if any(span is item[0] for item in linked):
            completion.append((span, pair))
        elif (
            single_virus
            and all(key not in {"confirmed_active", "neutralizing_antibody"} for _, key in _endpoint_mentions(span.text))
            and not other_family_staged
            and _virus_mentions(span.text) in (set(), {virus})
        ):
            completion.append((span, pair))
    complete = next((span for span, _ in observed if _ALL_POS.search(span.text)
                     and _VERIFIED.search(span.text) and not _SUBSET.search(span.text)
                     and not re.search(r"\b(?:none|never|not)\b", span.text, re.I)), None)
    partial = next((span for span, _ in observed if _SUBSET.search(span.text)), None)
    random = next((span for span, _ in observed if _RANDOM_SUBSET.search(span.text) and _VERIFIED.search(span.text)
                   and not re.search(r"\b(?:no|not|never)\b", span.text, re.I)), None)
    if completion:
        pairs = {pair for _, pair in completion}
        if len(pairs) == 1:
            span, (actual, expected) = completion[0]
            if 0 <= actual <= expected and expected > 0:
                fields["verification_completed"] = _item("verification_completed", span, actual)
                fields["verification_expected"] = _item("verification_expected", span, expected)
                if actual < expected:
                    partial = span
                elif not partial:
                    complete = span
        else:
            partial = completion[0][0]  # Contradictory completion figures cannot establish Tier B.
    if random and not complete:
        coverage, source, confidence = "Representative/random subset", random, "MODERATE"
    elif partial:
        coverage, source, confidence = "Selected subset", partial, "MODERATE"
    elif complete:
        coverage, source, confidence = "All screening-positive specimens", complete, "HIGH"
    else:
        coverage, source, confidence = UNRESOLVED, assay_span, "UNRESOLVED"
    fields["confirmation_coverage"] = _item("confirmation_coverage", source, coverage, inference="DERIVED", confidence=confidence)
    # Staged molecular outcomes are a distinct endpoint, but a neutralization
    # test only replaces binding-antibody positivity when the result defines that estimand.
    role_span = next((span for span, _ in observed if re.search(
        r"\b(?:confirmed (?:infection|seropositivity|positive)|positivity (?:was )?defined)\b", span.text, re.I)), None)
    staged = (
        endpoint_key == "confirmed_active" and any(
            _ALL_POS.search(span.text) or _STRATUM.search(span.text)
            or re.search(r"\bscreen(?:ing)?[- ]+positiv", span.text, re.I) for span, _ in linked
        )
    ) or role_span is not None
    role = "Endpoint-linked confirmation" if staged else "Ancillary or unresolved confirmation role"
    fields["confirmation_role"] = _item("confirmation_role", role_span or assay_span, role,
                                         inference="DERIVED", confidence="MODERATE")
    return fields


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
    bundle_parts = [(source.get("kind"), source.get("name"), source.get("sha256") or
                     sha256("\n".join(source.get("pages", [])).encode("utf-8")).hexdigest())
                    for source in sources]
    document_id = sha256(json.dumps(bundle_parts, ensure_ascii=False).encode("utf-8")).hexdigest()

    target = _first_classification(spans, TARGET_VOCABULARY, "target_population", "article")
    frame = _first_classification(spans, FRAME_VOCABULARY, "sampling_frame", "article")
    recruitment = _detect_recruitment(spans)
    article_fields = {"target_population": target, "sampling_frame": frame, **recruitment}

    viruses = [label for label, _ in VIRUS_PATTERNS if label in _virus_mentions(combined_text)]
    if not viruses:
        viruses = ["Virus unresolved"]
    single_virus = len(viruses) == 1 and viruses[0] != "Virus unresolved"
    random_two_strata_validation = bool(
        any(_TWO_STRATA.search(span.text) and re.search(r"\brandom\w*|validation\b", span.text, re.I) for span in spans)
        or (re.search(r"random(?:ly)?\s+(?:selected\s+)?(?:subset[s]?\s+of\s+)?(?:screening[- ]|primary[- ])?positive", combined_text)
        and re.search(r"random(?:ly)?\s+(?:selected\s+)?(?:subset[s]?\s+of\s+)?(?:screening[- ]|primary[- ])?negative", combined_text)
        )
    )
    endpoints = []
    for definition in ENDPOINT_PATTERNS:
        endpoint_key, _, terms, _ = definition
        if not any(term in combined_text for term in terms) and not (endpoint_key == "assay_performance" and random_two_strata_validation):
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
            confirmation = _confirmation(spans, endpoint_key, virus, single_virus)
            if (endpoint_key == "confirmed_active" and
                confirmation["confirmation_role"].value == "Ancillary positive/negative assay validation"
                and not _count_candidates(spans, endpoint_key, virus, single_virus)):
                continue
            path = _path_for(endpoint_key, str(confirmation["confirmation_coverage"].final_value),
                             str(confirmation["confirmation_role"].final_value))
            staged = path.endswith(("_tier_b", "_tier_c", "_tier_d"))
            # Use evidence that links the assay to the endpoint, not a different
            # assay merely mentioned in the same paper.
            assay_keys = {
                "IgG ELISA": {"prior_exposure"}, "IgM ELISA": {"presumptive_igm"},
                "NS1 antigen assay": {"presumptive_ns1"},
                **{name: {"confirmed_active"} for name in MOLECULAR_ASSAYS},
                **{name: {"neutralizing_antibody"} for name in NEUTRALIZATION_ASSAYS},
            }
            eligible_assays = [(name, span) for name, span in assays
                               if (not _virus_mentions(span.text) or _virus_mentions(span.text) == {virus})
                               and (endpoint_key in assay_keys.get(name, set()) or
                                    {key for _, key in _endpoint_mentions(span.text)} == {endpoint_key})]
            if staged and endpoint_key == "confirmed_active":
                screen_assays = [(name, span) for name, span in assays
                                 if name not in MOLECULAR_ASSAYS | NEUTRALIZATION_ASSAYS
                                 and re.search(r"\bscreen(?:ed|ing)\b", span.text, re.I)
                                 and (_virus_mentions(span.text) == {virus} or
                                      (single_virus and not _virus_mentions(span.text)))]
                primary = screen_assays[0] if screen_assays else None
            else:
                primary = eligible_assays[0] if eligible_assays else None
            counts = _count_candidates(spans, endpoint_key, virus, single_virus, staged)
            screen_counts: list[dict[str, Any]] = []
            if staged:
                screen_endpoints = (
                    {"prior_exposure"} if endpoint_key == "prior_exposure" else
                    {key for _, key in _endpoint_mentions(primary[1].text)} & {"prior_exposure", "presumptive_igm", "presumptive_ns1"}
                    if primary else set()
                )
                if len(screen_endpoints) == 1:
                    screen_counts = _count_candidates(spans, next(iter(screen_endpoints)), virus, single_virus)
            fields: dict[str, EvidenceItem] = {
                "virus": _item("virus", linked_spans[0] if linked_spans else (spans[0] if spans else None), virus, virus, inference="DERIVED", confidence="MODERATE"),
                "endpoint": _item("endpoint", linked_spans[0] if linked_spans else None, endpoint_label, endpoint_key, inference="DERIVED", confidence="MODERATE"),
                "primary_assay": _item("primary_assay", primary[1] if primary else None, primary[0] if primary else UNRESOLVED, primary[0] if primary else None, inference="DERIVED", confidence="MODERATE"),
                **confirmation,
                "target_population": target,
                "sampling_frame": frame,
                **recruitment,
            }
            conflicts: list[dict[str, Any]] = []
            for pairs_source, n_field, d_field, group in (
                (counts, "numerator", "denominator", "endpoint_numerator_denominator"),
                (screen_counts, "screen_positive", "screen_tested", "screening_numerator_denominator"),
            ):
                if group.startswith("screening") and not staged:
                    continue
                pairs = {(row["numerator"], row["denominator"]) for row in pairs_source}
                if len(pairs) == 1:
                    selected = pairs_source[0]
                    scope_note = (
                        "Observed result among retested screening positives; not a population estimator."
                        if staged and group.startswith("endpoint") else
                        "Observed counts linked to this endpoint result; reviewer confirmation required."
                    )
                    fields[n_field] = _item(n_field, selected["span"], selected["numerator"], notes=scope_note)
                    fields[d_field] = _item(d_field, selected["span"], selected["denominator"], notes=scope_note)
                else:
                    fields[n_field] = _item(n_field, None, UNRESOLVED)
                    fields[d_field] = _item(d_field, None, UNRESOLVED)
                if len(pairs) > 1:
                    conflict = {
                        "field": group,
                        "candidates": [{"numerator": row["numerator"], "denominator": row["denominator"],
                                        "evidence_text": row["span"].text,
                                        "location": asdict(SourceLocation(row["span"].document, row["span"].page, row["span"].section))}
                                       for row in pairs_source],
                        "reason": "Multiple observed n/N pairs could correspond to the same endpoint or stage; no pair was selected.",
                        "resolution_status": "needs_review",
                    }
                    conflicts.append(conflict)
                    for field_id in (n_field, d_field):
                        fields[field_id].conflicts.append(conflict)
                        fields[field_id].confidence = "LOW"
                if staged and group.startswith("endpoint") and len(pairs) == 1 and not isinstance(fields["verification_completed"].value, int):
                    fields["verification_completed"] = _item("verification_completed", selected["span"], selected["denominator"])
            if staged:
                completed = fields["verification_completed"]
                tested = completed.value
                planned = fields["screen_positive"].value
                disputed_verification = False
                if isinstance(tested, int) and isinstance(fields["denominator"].value, int) and tested != fields["denominator"].value:
                    disputed_verification = True
                    options = [
                        {"denominator": item.value, "evidence_text": item.evidence_text, "location": asdict(item.location)}
                        for item in (fields["verification_completed"], fields["denominator"]) if item.location
                    ]
                    conflict = {
                        "field": "verification_denominator",
                        "candidates": options,
                        "reason": "The reported retested count and endpoint result denominator differ; their relationship requires review.",
                        "resolution_status": "needs_review",
                    }
                    conflicts.append(conflict)
                    for field_id in ("numerator", "denominator", "verification_completed"):
                        fields[field_id] = EvidenceItem(field_id, conflicts=[conflict], confidence="LOW")
                if isinstance(tested, int) and isinstance(planned, int) and tested < planned and path.endswith("_tier_b"):
                    fields["confirmation_coverage"] = EvidenceItem(
                        "confirmation_coverage", "Selected subset", evidence_text=completed.evidence_text,
                        location=completed.location, confidence="MODERATE", inference_level="DERIVED",
                        notes="The completed verification denominator is smaller than the observed screening-positive set.",
                    )
                    path = _path_for(endpoint_key, "Selected subset", "Endpoint-linked confirmation")
                elif disputed_verification and path.endswith("_tier_b"):
                    fields["confirmation_coverage"] = EvidenceItem(
                        "confirmation_coverage", evidence_text=completed.evidence_text, location=completed.location,
                        notes="Conflicting verification denominators do not establish complete verification.",
                    )
                    path = _path_for(endpoint_key, UNRESOLVED, "Endpoint-linked confirmation")
            if endpoint_key == "assay_performance" and any(name in MOLECULAR_ASSAYS for name, _ in assays):
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
            "main_document_sha256": sources[0].get("sha256") or bundle_parts[0][2],
            "parsed_once": True,
        },
        warnings=warnings,
    )


def parse_pdf_bundle(main_pdf: bytes, main_name: str, supplements: Sequence[tuple[str, bytes]] = (), rule_version: str = "") -> DocumentEvidenceModel:
    """Read one article plus optional supplements and extract a shared model."""
    sources = [read_pdf(main_pdf, main_name, "main")]
    sources.extend(read_pdf(raw, name, "supplement") for name, raw in supplements)
    return extract_document_model(sources, rule_version=rule_version)

