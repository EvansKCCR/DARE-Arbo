# DARE-Arbo Assessor and Appraiser

This directory contains one consolidated Streamlit application that operationalizes the audited DARE-Arbo framework for article-outcome appraisal and multi-study evidence synthesis.

## What is implemented

- **Assessor:** one PDF and one study-virus-estimand at a time, with controlled target and sampling-frame classification, target-frame decision support, endpoint-first assay roles, all 12 DARE-Arbo criteria, audit comments, page-aware evidence suggestions, endpoint-specific applicability, synthesis-tier classification, and PNG/CSV/JSON exports.
- **Appraiser:** import completed Assessor outputs, upload multiple same-endpoint PDFs for draft evidence scoring, enter studies in a batch grid, retain target/frame and assay-path provenance, calculate endpoint-specific totals and domain scores, inspect a descriptive criterion heatmap, and export synthesis-ready tables.
- **DARE-Arbo Designer:** plan single or mixed endpoints, active/passive/hybrid surveillance, and single-virus or multiplex studies using the same target-frame, recruitment, assay-pathway, denominator, and reporting rules; download a flow PNG, JSON plan, and deterministic protocol-oriented study-design report.
- **Framework reference:** item ranges, serologic/direct-detection applicability, endpoint definitions, tier rules, and interpretation guardrails.
- **Reusable engine:** scoring, validation, classification, PDF text extraction, evidence suggestions, and serialization are separated from the Streamlit interface in `dare_arbo.py`.

DARE-Arbo totals are continuous methodological-quality scores. The application does not invent low/moderate/high bands. Q10 applies to prior-exposure / antibody-seroprevalence, IgM, and NS1 endpoints, producing an applicable maximum of 19. Mixed/inseparable serology, neutralizing-antibody prevalence, confirmed direct detection, assay-performance, and other explicitly defined endpoints record Q10 as N/A and have an applicable maximum of 18.

## Run locally

From this directory:

```powershell
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

The legacy entry points `risk_bias_assesor.py` and `risk_of_bias_scoring.py` now open the same consolidated application.

## Streamlit Cloud deployment

Deploy `quality_appraisal/streamlit_app.py` and keep `quality_appraisal/dare_arbo.py` in the same repository directory. These two files form a versioned interface/core pair and must be committed and deployed together. After updating either file, reboot the Streamlit Cloud app so its Python process cannot retain an older imported core module. The current compatible core API is `2026.09.11.1`; a mismatched deployment now shows an explicit synchronization message instead of an opaque import failure.

## Designer report architecture

The Designer report keeps planning logic and presentation separate:

1. `evaluate_study_design_plan()` applies the existing Designer rules and constructs stream-virus-endpoint units.
2. `build_designer_report_model()` normalizes the completed session into a deterministic, JSON-serializable report model, assigns assay roles from each estimand, validates estimator readiness, and creates specific planning priorities.
3. `generate_designer_narrative()` produces controlled protocol-oriented prose without an LLM or external report service.
4. `render_designer_report_pdf()` renders the model with the existing ReportLab stack; `render_surveillance_design_report_pdf()` remains the compatibility entry point used by the Streamlit page.

Planning coverage in this report is a documentation-completeness indicator. It is not a risk-of-bias, methodological-validity, or study-quality score.

## Test the rule engine

```powershell
python -m unittest discover -s tests -v
```

## PDF behavior and privacy

Uploaded PDFs are processed in memory and are not persisted by the app. Text-based PDFs work directly. Image-only/scanned articles require OCR before upload. Evidence suggestions use explicit, local rules and page-aware text matches; they are drafts that reviewers must verify against the article, tables, supplements, and stated target population.

## Primary framework sources in this directory

- `DARE_Arbo_Design_library.xlsx` - 2026-09-10 controlled target, frame, recruitment, Q1/Q3/Q4, matrix, and worked-example library.
- `DARE-Arbo_application_principle.docx` - endpoint-first assay roles, measurement pathways, confirmation coverage, and Q1-Q10 application rules.
- `DARE_Arbo_workbook.xlsx` - legacy audited study-level scoring structure and examples retained for compatibility.
- `DARE-Arbo_manuscript.docx` - framework rationale and methods description.
