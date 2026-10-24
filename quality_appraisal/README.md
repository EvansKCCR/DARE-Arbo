# About DARE-Arbo Framework
Design, Assay, and Reporting Evaluation for Arboviruses (DARE-Arbo) framework provides a structured approach for linking the epidemiological population, biological endpoint, assay pathway and estimand within a single appraisal framework. Its pilot evaluation demonstrates that systematic source-level discrepancy auditing can identify and resolve methodological ambiguities that conventional total scores may obscure. DARE-Arbo offers a scalable platform to improve transparency, comparability, and methodological harmonisation of arbovirus surveillance evidence, with particular relevance to African surveillance systems and evidence synthesis

# DARE-Arbo Assessor and Appraiser

This directory contains one consolidated Streamlit application that operationalizes the audited DARE-Arbo framework for article-outcome appraisal and multi-study evidence synthesis.

## What is implemented
- **Assessor:** one PDF and one study-virus-estimand at a time, with controlled target and sampling-frame classification, target-frame decision support, endpoint-first assay roles, all 12 DARE-Arbo criteria, audit comments, Assessor ID plus automatic UTC start/completion timing, page-aware evidence suggestions, endpoint-specific applicability, synthesis-tier classification, and PNG/CSV/JSON exports.
- **Appraiser:** import completed Assessor outputs, preserve Assessor ID and timing audit fields, perform direct article-PDF appraisal through the shared Assessor/Appraiser document-intelligence layer, review separate study-virus-endpoint candidates before committing them, retain source excerpts/confidence/conflicts/user overrides, enter and manage studies in an editable batch grid, calculate endpoint-specific totals and domain scores, inspect a descriptive criterion heatmap, and export synthesis-ready tables.
- **DARE-Arbo Designer:** plan single or mixed endpoints, active/passive/hybrid surveillance, and single-virus or multiplex studies using the same target-frame, recruitment, assay-pathway, denominator, and reporting rules; download a flow PNG, JSON plan, and deterministic protocol-oriented study-design report.
- **Framework reference:** item ranges, serologic/direct-detection applicability, endpoint definitions, tier rules, and interpretation guardrails.
- **Comments and support:** every workspace page includes a non-persistent feedback form that opens a user-reviewed email draft addressed to `evansasamoahadu@gmail.com`.
- **Reusable engines:** scoring, validation, classification, evidence-to-score mapping, and serialization remain separated from the Streamlit interface in `dare_arbo.py`; PDF reading, source localization, endpoint separation, structured evidence, conflict detection, confidence, and provenance are shared through `document_intelligence.py`.

## Shared PDF document intelligence

The Assessor compatibility function `extract_pdf_text()` and the Appraiser direct-PDF workflow use the same reader in `document_intelligence.py`. The Appraiser parses a main article plus optional supplements once, then constructs separate candidate records for each detected virus and biological endpoint. It preserves three distinct layers: source evidence, DARE-Arbo interpretation, and draft appraisal score.

Every extracted field retains its original value, normalized value, evidence excerpt, document/page/section location, confidence, inference level, conflicts, extraction engine/version, source hash, rule version, and any later reviewer override. Missing evidence is represented as unresolved rather than as an affirmative negative. A reviewer must select an endpoint and may accept, edit, or mark fields unresolved before adding an editable batch draft. The complete evidence and provenance model can be downloaded as JSON.

## Expert scientific workspace interface

The Streamlit interface uses a desktop-first, responsive workspace architecture while preserving the validated rule engine and export schemas:

- **Home:** tool selection, workspace appearance, Framework access, and comments/support.
- **Assess:** Assessor and Appraiser are adjacent navigation destinations. Assessor uses directly navigable Endpoint, Study & evidence, Domain appraisal, and Review & export stages. Appraiser uses Import & registry, Rapid batch entry, and Results & exports stages.
- **Design:** the Designer retains one protocol state while linking Objective, Planning pillars, and Review & downloads.
- **Reference:** the Framework page presents endpoint applicability, criterion ranges, synthesis paths, and interpretation guardrails as read-only reference material.
- **System:** About & deployment retains the full DARE-Arbo banner and deployment/privacy guidance.

Working pages use a compact header and persistent context strip for Study, Virus, Endpoint, Pathway, and Status. The full brand banner is reserved for Home and About. Shared interface functions in `streamlit_app.py` render headers, context, workflow navigation, endpoint summaries, assay pathways, criteria, analysis-unit tables, readiness, status badges, and review summaries. Scientific definitions and scoring decisions remain in `dare_arbo.py`.

DARE-Arbo totals are continuous methodological-quality scores. The application does not invent low/moderate/high bands. Q10 applies to prior-exposure / antibody-seroprevalence, IgM, and NS1 endpoints, producing an applicable maximum of 19. Mixed/inseparable serology, neutralizing-antibody prevalence, confirmed direct detection, assay-performance, and other explicitly defined endpoints record Q10 as N/A and have an applicable maximum of 18.

## Designer report architecture

The Designer report keeps planning logic and presentation separate:

1. `evaluate_study_design_plan()` applies the existing Designer rules and constructs stream-virus-endpoint units.
2. `build_designer_report_model()` normalizes the completed session into a deterministic, JSON-serializable report model, assigns assay roles from each estimand, validates estimator readiness, and creates specific planning priorities.
3. `generate_designer_narrative()` produces controlled protocol-oriented prose without an LLM or external report service.
4. `render_designer_report_pdf()` renders the model with the existing ReportLab stack; `render_surveillance_design_report_pdf()` remains the compatibility entry point used by the Streamlit page.

Planning coverage in this report is a documentation-completeness indicator. It is not a risk-of-bias, methodological-validity, or study-quality score.


## PDF behavior and privacy

Uploaded PDFs are processed in memory and are not persisted by the app. Text-based PDFs work directly. Image-only/scanned articles require OCR before upload. Evidence suggestions use explicit, local rules and page-aware text matches; they are drafts that reviewers must verify against the article, tables, supplements, and stated target population.

## Primary framework sources in this directory

- `DARE_Arbo_Design_library.xlsx` - 2026-09-10 controlled target, frame, recruitment, Q1/Q3/Q4, matrix, and worked-example library.
- `DARE-Arbo_application_principle.docx` - endpoint-first assay roles, measurement pathways, confirmation coverage, and Q1-Q10 application rules.
- `DARE_Arbo_workbook.xlsx` - legacy audited study-level scoring structure and examples retained for compatibility.
- `DARE-Arbo_manuscript.docx` - framework rationale and methods description.

