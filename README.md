# About DARE-Arbo Framework
Design, Assay, and Reporting Evaluation for Arboviruses (DARE-Arbo) framework provides a structured approach for linking the epidemiological population, biological endpoint, assay pathway and estimand within a single appraisal framework. Its pilot evaluation demonstrates that systematic source-level discrepancy auditing can identify and resolve methodological ambiguities that conventional total scores may obscure. DARE-Arbo offers a scalable platform to improve transparency, comparability, and methodological harmonisation of arbovirus surveillance evidence, with particular relevance to African surveillance systems and evidence synthesis

# DARE-Arbo Assessor and Appraiser

This directory contains one consolidated Streamlit application that operationalizes the audited DARE-Arbo framework for article-outcome appraisal and multi-study evidence synthesis.

## What is implemented

- **Assessor:** one PDF and one study-virus-estimand at a time, with controlled target and sampling-frame classification, target-frame decision support, endpoint-first assay roles, all 12 DARE-Arbo criteria, audit comments, Assessor ID plus automatic UTC start/completion timing, page-aware evidence suggestions, endpoint-specific applicability, synthesis-tier classification, and PNG/CSV/JSON exports.
- **Appraiser:** import completed Assessor outputs, preserve Assessor ID and timing audit fields, upload multiple same-endpoint PDFs for draft evidence scoring, enter and manage studies in an editable batch grid, retain target/frame and assay-path provenance, calculate endpoint-specific totals and domain scores, inspect a descriptive criterion heatmap, and export synthesis-ready tables.
- **DARE-Arbo Designer:** plan single or mixed endpoints, active/passive/hybrid surveillance, and single-virus or multiplex studies using the same target-frame, recruitment, assay-pathway, denominator, and reporting rules; download a flow PNG, JSON plan, and deterministic protocol-oriented study-design report.
- **Framework reference:** item ranges, serologic/direct-detection applicability, endpoint definitions, tier rules, and interpretation guardrails.
- **Reusable engine:** scoring, validation, classification, PDF text extraction, evidence suggestions, and serialization are separated from the Streamlit interface in `dare_arbo.py`.

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


## Citation

- Adu, E.A., Amuasi, J.H., Alani, H. et al. (Sero)prevalence of (re)emerging major arbovirus infections in Africa: a systematic review and meta-analysis protocol. Syst Rev 15, 97 (2026). https://doi.org/10.1186/s13643-025-02879-z

## License

Please refer to the repository license for terms governing the use and redistribution of the source code and datasets.
