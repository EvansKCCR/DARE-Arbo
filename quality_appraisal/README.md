# DARE-Arbo Assessor and Appraiser

This directory contains one consolidated Streamlit application that operationalizes the audited DARE-Arbo framework for article-outcome appraisal and multi-study evidence synthesis.

## What is implemented

- **Assessor:** one PDF and one extracted endpoint at a time, with structured metadata, all 12 DARE-Arbo criteria, audit comments, page-aware evidence suggestions, endpoint-specific applicability, synthesis-tier classification, and PNG/CSV/JSON exports.
- **Appraiser:** import completed Assessor outputs, upload multiple same-endpoint PDFs for draft evidence scoring, enter studies in a batch grid, calculate endpoint-specific totals and domain scores, inspect a descriptive criterion heatmap, and export synthesis-ready tables.
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

## Test the rule engine

```powershell
python -m unittest discover -s tests -v
```

## PDF behavior and privacy

Uploaded PDFs are processed in memory and are not persisted by the app. Text-based PDFs work directly. Image-only/scanned articles require OCR before upload. Evidence suggestions use explicit, local rules and page-aware text matches; they are drafts that reviewers must verify against the article, tables, supplements, and stated target population.

The DARE-Arbo digital workspace is a pre-release, non-production implementation developed for methodological evaluation, usability testing, and refinement of the DARE-Arbo framework. It should not yet be treated as a validated production system or relied upon as the sole basis for formal risk-of-bias decisions. Automated evidence suggestions and draft scores are decision-support outputs and require reviewer verification against the source study. Features, scoring logic, interface components, and export formats may change during further testing and validation.

## Primary framework sources in this directory

- `Application_principle.docx` - audited scoring codebook and operational rules.
- `DARE_Arbo_Library_with_Decision_Tree.xlsx` - active-infection and neutralizing-antibody endpoint/tier harmonization.
- `DARE_Arbo_workbook.xlsx` - audited study-level scoring structure and examples.
