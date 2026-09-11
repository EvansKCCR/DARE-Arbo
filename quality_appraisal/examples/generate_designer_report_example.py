"""Generate a deterministic example report from a complete Designer fixture."""

from __future__ import annotations

from pathlib import Path
import sys


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dare_arbo import (  # noqa: E402
    build_designer_report_model,
    evaluate_study_design_plan,
    render_study_design_png,
    render_surveillance_design_report_pdf,
)


EXAMPLE_PLAN = {
    "study_title": "District DENV sentinel surveillance protocol",
    "country_or_setting": "Ghana; district sentinel facility network",
    "report_scope": "surveillance",
    "synthesis_path_key": "confirmed_active_tier_b",
    "synthesis_path_keys": ["confirmed_active_tier_b"],
    "virus": "DENV",
    "viruses": ["DENV"],
    "virus_coverage": "Single virus",
    "target_population": "General febrile population presenting to participating sentinel facilities",
    "target_population_category": "General febrile population",
    "sampling_frame_category": "Sentinel Site / Network",
    "sampling_frame_coverage_justification": (
        "Participating sites define a documented district catchment and enumerate every eligible febrile presentation during surveillance hours."
    ),
    "sampling_recruitment_method": "Multistage probability sampling",
    "surveillance_mode": "Passive surveillance",
    "surveillance_architecture": "Sentinel facility surveillance",
    "stream_estimator_mode": "Passive surveillance",
    "stream_integration_plan": True,
    "endpoint_design": "Single endpoint",
    "planned_sample_size": 800,
    "eligibility_defined": True,
    "sample_size_rationale": True,
    "nonresponse_plan": True,
    "pathway_assays": {
        "confirmed_active_tier_b": {
            "primary_assay": "NS1 antigen RDT",
            "confirmatory_assay": "Real-time RT-qPCR",
        }
    },
    "specimen_timing": True,
    "validation_controls": True,
    "cross_reactivity_plan": True,
    "testing_denominator_plan": True,
    "endpoint_specific_assay_plan": True,
    "multiplex_assay_plan": True,
    "numerator_denominator_reporting": True,
    "flow_reporting": True,
    "assay_reporting": True,
    "missingness_reporting": True,
    "estimator_reporting": True,
    "reproducibility_reporting": True,
    "mixed_endpoint_reporting_plan": True,
    "multiplex_reporting_plan": True,
    "created_at": "2026-09-11T09:00:00+00:00",
}


def main() -> Path:
    readiness = evaluate_study_design_plan(EXAMPLE_PLAN)
    model = build_designer_report_model(EXAMPLE_PLAN, readiness)
    if model["planningCoverage"]["overallStatus"] != "Planning framework complete":
        raise RuntimeError("The example fixture must remain a complete Designer plan.")

    kccr_logo = (APP_DIR / "Logo.jpeg").read_bytes()
    synergy_logo = (APP_DIR / "Synergy_NGS2025.png").read_bytes()
    flow = render_study_design_png(EXAMPLE_PLAN, branding_logo=kccr_logo)
    report = render_surveillance_design_report_pdf(
        EXAMPLE_PLAN,
        readiness=readiness,
        design_png=flow,
        branding_logo=kccr_logo,
        partner_logo=synergy_logo,
    )
    output_path = (
        APP_DIR
        / "output"
        / "pdf"
        / "DARE-Arbo_Surveillance_Study_Design_District_DENV_Protocol_2026-09-11.pdf"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(report)
    return output_path


if __name__ == "__main__":
    print(main())
