from __future__ import annotations

from io import BytesIO
from pathlib import Path
import unittest

from dare_arbo import (
    ASSAY_OPTIONS_BY_SYNTHESIS_PATH,
    CRITERIA,
    POPULATION_TARGET_CATEGORIES,
    SYNTHESIS_PATHS_BY_ENDPOINT,
    SYNTHESIS_PATHS_BY_KEY,
    Suggestion,
    applicable_maximum,
    audit_endpoint_counts,
    audit_synthesis_path_counts,
    build_export_record,
    classify_synthesis,
    criterion_is_applicable,
    extract_citation_or_doi,
    extract_pdf_text,
    evaluate_study_design_plan,
    render_assessment_png,
    render_study_design_png,
    render_surveillance_design_report_pdf,
    normalize_target_population_category,
    score_assessment,
    suggest_scores,
    validate_scores,
)


def maximum_scores(pathway: str, endpoint_key: str | None = None) -> dict[str, int | None]:
    return {
        criterion.code: (
            criterion.maximum
            if criterion_is_applicable(criterion.code, pathway, endpoint_key)
            else None
        )
        for criterion in CRITERIA
    }


class DareArboScoringTests(unittest.TestCase):
    def test_endpoint_specific_maxima(self) -> None:
        self.assertEqual(applicable_maximum("serologic"), 19)
        self.assertEqual(applicable_maximum("direct_detection"), 18)
        for endpoint in ("prior_exposure", "presumptive_igm", "presumptive_ns1"):
            self.assertEqual(applicable_maximum("serologic", endpoint), 19)
        for endpoint in (
            "mixed_serology", "neutralizing_antibody", "confirmed_active",
            "assay_performance", "other",
        ):
            self.assertEqual(applicable_maximum("serologic", endpoint), 18)

    def test_complete_serologic_assessment(self) -> None:
        result = score_assessment(maximum_scores("serologic"), "serologic")
        self.assertTrue(result["complete"])
        self.assertEqual(result["total"], 19)
        self.assertEqual(result["maximum"], 19)
        self.assertEqual(result["domains"]["Laboratory assay"]["maximum"], 8)

    def test_direct_detection_treats_q10_as_na(self) -> None:
        scores = maximum_scores("direct_detection")
        result = score_assessment(scores, "direct_detection")
        self.assertTrue(result["complete"])
        self.assertEqual(result["total"], 18)
        self.assertIsNone(result["scores"]["Q10"])

    def test_q10_numeric_value_is_invalid_for_direct_detection(self) -> None:
        scores = maximum_scores("direct_detection")
        scores["Q10"] = 0
        problems = validate_scores(scores, "direct_detection")
        self.assertIn("Q10 must be N/A for the selected synthesis endpoint.", problems)

    def test_invalid_item_does_not_inflate_total(self) -> None:
        scores = maximum_scores("serologic")
        scores["Q1"] = 7
        result = score_assessment(scores, "serologic", "prior_exposure")
        self.assertIsNone(result["scores"]["Q1"])
        self.assertEqual(result["total"], 18)
        self.assertFalse(result["complete"])

    def test_q10_is_endpoint_specific_not_simply_serologic(self) -> None:
        scores = maximum_scores("serologic")
        for endpoint in ("prior_exposure", "presumptive_igm", "presumptive_ns1"):
            result = score_assessment(scores, "serologic", endpoint)
            self.assertEqual(result["maximum"], 19)
            self.assertEqual(result["scores"]["Q10"], 1)
        for endpoint in (
            "mixed_serology", "neutralizing_antibody", "confirmed_active",
            "assay_performance", "other",
        ):
            result = score_assessment(scores, "serologic", endpoint)
            self.assertEqual(result["maximum"], 18)
            self.assertIsNone(result["scores"]["Q10"])

    def test_confirmed_active_tiers(self) -> None:
        tier_a = classify_synthesis("confirmed_active", "full_population")
        tier_c = classify_synthesis("confirmed_active", "subset_positive")
        self.assertTrue(tier_a["tier"].startswith("Tier A"))
        self.assertTrue(tier_c["tier"].startswith("Tier D"))
        self.assertIn("exclude from unrestricted population", tier_c["analysis_role"])

    def test_neutralization_incomplete_is_tier_e(self) -> None:
        result = classify_synthesis("neutralizing_antibody", "incomplete")
        self.assertTrue(result["tier"].startswith("Tier E"))

    def test_selected_serologic_subsample_is_not_population_prevalence(self) -> None:
        result = classify_synthesis("prior_exposure", "selected_subsample")
        self.assertEqual(result["tier"], "Primary-assay endpoint")
        self.assertIn("declared target", result["analysis_role"])
        self.assertIn("selected", result["measurement_pathway"].lower())

    def test_endpoint_count_audit_checks_tier_b_denominators(self) -> None:
        valid = audit_endpoint_counts("confirmed_active", "all_screen_positive", 500, 40, 12, 40, 40)
        self.assertTrue(valid["valid"])
        self.assertTrue(valid["estimand_ready"])
        self.assertAlmostEqual(valid["endpoint_test_positivity"], 0.3)
        self.assertAlmostEqual(valid["prevalence"], 12 / 500)
        self.assertEqual(valid["synthesis_denominator_n"], 500)
        invalid = audit_endpoint_counts("confirmed_active", "all_screen_positive", 500, 30, 12, 40, 40)
        self.assertTrue(invalid["valid"])
        self.assertEqual(invalid["effective_verification_design"], "subset_positive")
        self.assertTrue(invalid["effective_tier"].startswith("Tier D"))

    def test_representative_neutralization_subset_uses_weighted_estimand(self) -> None:
        audit = audit_endpoint_counts(
            "neutralizing_antibody",
            "representative_positive_subset",
            1000,
            50,
            20,
            200,
            50,
        )
        self.assertTrue(audit["estimand_ready"])
        self.assertAlmostEqual(audit["verification_subset_positivity"], 20 / 50)
        self.assertAlmostEqual(audit["prevalence"], (200 / 1000) * (20 / 50))
        self.assertIn("Tier C", audit["effective_tier"])

    def test_synthesis_hierarchy_matches_endpoint_strategy_codebook(self) -> None:
        self.assertEqual(
            [path.key for path in SYNTHESIS_PATHS_BY_ENDPOINT["prior_exposure"]],
            ["serology_apparent", "serology_tier_b", "serology_tier_c", "serology_tier_d"],
        )
        self.assertEqual(
            [path.key for path in SYNTHESIS_PATHS_BY_ENDPOINT["confirmed_active"]],
            ["confirmed_active_universal", "confirmed_active_tier_b", "confirmed_active_tier_c", "confirmed_active_tier_d"],
        )
        self.assertEqual(
            [path.key for path in SYNTHESIS_PATHS_BY_ENDPOINT["assay_performance"]],
            ["serology_validation", "molecular_validation"],
        )

    def test_tier_b_path_uses_algorithm_entry_denominator_and_reclassifies_incomplete_testing(self) -> None:
        complete = audit_synthesis_path_counts(
            "serology_tier_b",
            {
                "total_tested_n": 500,
                "primary_positive_n": 40,
                "number_retested_n": 40,
                "confirmed_positive_n": 12,
            },
        )
        self.assertTrue(complete["estimand_ready"])
        self.assertEqual(complete["denominator_n"], 500)
        self.assertEqual(complete["false_positives_n"], 28)
        self.assertAlmostEqual(complete["verification_subset_positivity"], 12 / 40)
        self.assertAlmostEqual(complete["estimate"], 12 / 500)

        incomplete = audit_synthesis_path_counts(
            "serology_tier_b",
            {
                "total_tested_n": 500,
                "primary_positive_n": 40,
                "number_retested_n": 30,
                "confirmed_positive_n": 12,
            },
        )
        self.assertEqual(incomplete["effective_synthesis_path_key"], "serology_tier_d")
        self.assertAlmostEqual(incomplete["estimate"], 12 / 30)

    def test_validation_path_reconstructs_false_results(self) -> None:
        audit = audit_synthesis_path_counts(
            "molecular_validation",
            {
                "number_retested_n": 100,
                "retested_positive_stratum_n": 60,
                "confirmed_positive_n": 50,
                "retested_negative_stratum_n": 40,
                "confirmed_negative_n": 36,
                "planned_retest_n": 100,
            },
        )
        self.assertTrue(audit["estimand_ready"])
        self.assertIsNone(audit["estimate"])
        self.assertEqual(audit["false_positives_n"], 10)
        self.assertEqual(audit["false_negatives_n"], 4)
        self.assertAlmostEqual(audit["sensitivity"], 50 / 54)
        self.assertAlmostEqual(audit["specificity"], 36 / 46)

    def test_active_and_serological_paths_use_biologically_correct_numerators(self) -> None:
        expected = {
            "igm_only": "IgM positive",
            "ns1_only": "NS1 positive",
            "confirmed_active_universal": "Molecular positive",
            "neutralization_universal": "Neutralization positive",
            "serology_apparent": "Primary positive",
            "mixed_serology_only": "Combined serologic positive",
        }
        for key, numerator in expected.items():
            self.assertEqual(SYNTHESIS_PATHS_BY_KEY[key].numerator, numerator)

    def test_assay_options_cover_every_endpoint_testing_strategy(self) -> None:
        self.assertEqual(
            set(ASSAY_OPTIONS_BY_SYNTHESIS_PATH),
            set(SYNTHESIS_PATHS_BY_KEY),
        )
        self.assertIn(
            "IgG ELISA",
            ASSAY_OPTIONS_BY_SYNTHESIS_PATH["serology_tier_b"]["primary"],
        )
        self.assertIn(
            "PRNT90",
            ASSAY_OPTIONS_BY_SYNTHESIS_PATH["serology_tier_b"]["confirmatory"],
        )
        self.assertIn(
            "Real-time RT-qPCR",
            ASSAY_OPTIONS_BY_SYNTHESIS_PATH["confirmed_active_tier_b"]["confirmatory"],
        )
        self.assertIn(
            "PRNT90",
            ASSAY_OPTIONS_BY_SYNTHESIS_PATH["serology_apparent"]["confirmatory"],
        )

    def test_two_phase_molecular_subset_is_not_raw_prevalence(self) -> None:
        audit = audit_endpoint_counts(
            "confirmed_active", "two_phase_validation", 1000, 40, 12, 200,
            40, 800, 40, 2,
        )
        self.assertTrue(audit["estimand_ready"])
        self.assertIsNone(audit["prevalence"])
        classification = classify_synthesis("confirmed_active", "two_phase_validation")
        self.assertIn("not population molecular prevalence", classification["caution"])

    def test_q5_uses_planned_pathway_and_q8_rejects_expected_numerator(self) -> None:
        audit = audit_endpoint_counts(
            "confirmed_active", "representative_subsample", 1000, 80, 12, None,
            100, numerator_provenance="posterior_expected",
        )
        self.assertAlmostEqual(audit["completion_rate"], 0.8)
        self.assertEqual(audit["q5_suggested_score"], 2)
        self.assertFalse(audit["estimand_ready"])
        self.assertTrue(any("observed numerator" in issue for issue in audit["issues"]))

    def test_ancillary_validation_calculates_performance_not_prevalence(self) -> None:
        audit = audit_endpoint_counts(
            "prior_exposure", "ancillary_validation",
            numerator_provenance="observed",
            reference_positive_n=100,
            reference_positive_detected_n=90,
            reference_negative_n=200,
            reference_negative_correct_n=190,
        )
        self.assertAlmostEqual(audit["sensitivity"], 0.9)
        self.assertAlmostEqual(audit["specificity"], 0.95)
        self.assertIsNone(audit["prevalence"])

    def test_rule_based_suggestions_are_endpoint_aware(self) -> None:
        pages = [
            "Participants were randomly selected from multiple sites. Serum was collected from all participants. "
            "All samples were tested by a validated ELISA with manufacturer controls. All positive samples were "
            "confirmed using PRNT90. The prevalence was 24 of 300. True prevalence was adjusted for sensitivity "
            "and specificity and weighted for age and sex."
        ]
        serologic = suggest_scores(pages, "serologic", "prior_exposure", "full_population")
        igm = suggest_scores(pages, "serologic", "presumptive_igm", "full_population")
        ns1 = suggest_scores(pages, "serologic", "presumptive_ns1", "full_population")
        mixed = suggest_scores(pages, "serologic", "mixed_serology", "full_population")
        direct = suggest_scores(pages, "direct_detection", "confirmed_active", "full_population")
        self.assertGreaterEqual(serologic["Q4"].score or 0, 2)
        self.assertEqual(serologic["Q6a"].score, 2)
        self.assertEqual(serologic["Q6c"].score, 2)
        self.assertEqual(serologic["Q10"].score, 1)
        self.assertEqual(igm["Q10"].score, 1)
        self.assertEqual(ns1["Q10"].score, 1)
        self.assertIsNone(mixed["Q10"].score)
        self.assertIsNone(direct["Q10"].score)

    def test_q5_uses_tested_recruited_and_limiting_staged_completion(self) -> None:
        audit = audit_synthesis_path_counts(
            "serology_tier_c",
            {
                "total_recruited_n": 1000,
                "total_tested_n": 900,
                "primary_positive_n": 140,
                "number_retested_n": 75,
                "confirmed_positive_n": 55,
                "planned_retest_n": 100,
            },
        )
        self.assertAlmostEqual(audit["recruitment_testing_rate"], 0.9)
        self.assertAlmostEqual(audit["planned_pathway_completion_rate"], 0.75)
        self.assertAlmostEqual(audit["q5_completion_rate"], 0.75)
        self.assertEqual(audit["q5_suggested_score"], 1)
        self.assertIn("limiting percentage", audit["q5_basis"])

    def test_q5_requires_both_components_for_a_staged_path(self) -> None:
        audit = audit_synthesis_path_counts(
            "confirmed_active_tier_c",
            {
                "total_recruited_n": 500,
                "total_tested_n": 450,
                "primary_positive_n": 80,
                "number_retested_n": 50,
                "molecular_positive_n": 12,
            },
        )
        self.assertAlmostEqual(audit["recruitment_testing_rate"], 0.9)
        self.assertIsNone(audit["planned_pathway_completion_rate"])
        self.assertIsNone(audit["q5_suggested_score"])

    def test_q5_rejects_more_tested_than_recruited(self) -> None:
        audit = audit_synthesis_path_counts(
            "igm_only",
            {
                "total_recruited_n": 90,
                "total_tested_n": 100,
                "igm_positive_n": 10,
            },
        )
        self.assertFalse(audit["valid"])
        self.assertTrue(any("cannot exceed Total recruited" in issue for issue in audit["issues"]))

    def test_population_dictionary_respects_restricted_target(self) -> None:
        pages = [
            "Febrile patients of both sexes and multiple age groups were recruited from the participating hospital. "
            "Serum specimens were collected directly from enrolled participants."
        ]
        draft = suggest_scores(
            pages,
            "serologic",
            "prior_exposure",
            "full_population",
            target_population="Febrile patients attending the participating hospital",
        )
        self.assertEqual(draft["Q1"].score, 1)
        self.assertEqual(draft["Q2"].score, 1)
        self.assertEqual(draft["Q3"].score, 1)

    def test_target_population_taxonomy_matches_updated_categories(self) -> None:
        self.assertEqual(
            [label for label, _ in POPULATION_TARGET_CATEGORIES],
            [
                "General febrile population",
                "Community or general populations",
                "Occupationally or zoonotically exposed populations",
                "Suspected arbovirus population",
                "Blood donors",
                "Other or mixed populations",
                "Pregnant women / antenatal populations",
                "Non-malarial febrile population",
                "Other defined clinical cohorts",
                "Archived or residual specimen populations",
                "Non-febrile patients",
                "General outpatients",
            ],
        )
        specific = suggest_scores(
            ["Participants included both sexes and multiple age groups."],
            "serologic", "prior_exposure", "full_population",
            target_population="Non-malarial febrile patients attending outpatient clinics",
        )
        self.assertIn("Non-malarial febrile population", specific["Q1"].rationale)
        self.assertEqual(
            normalize_target_population_category("Antenatal or pregnant population"),
            "Pregnant women / antenatal populations",
        )

    def test_population_dictionary_flags_restricted_sample_for_general_target(self) -> None:
        pages = [
            "Blood donors of both sexes across multiple age groups were recruited at a single hospital."
        ]
        draft = suggest_scores(
            pages,
            "serologic",
            "prior_exposure",
            "full_population",
            target_population="General population residents",
        )
        self.assertEqual(draft["Q1"].score, 0)
        self.assertEqual(draft["Q3"].score, 0)

    def test_q3_requires_explicit_frame_fallback_not_target_label(self) -> None:
        draft = suggest_scores(
            ["A validated ELISA was used to measure dengue IgG antibodies."],
            "serologic",
            "prior_exposure",
            "full_population",
            target_population="Residents of three named communities in the study district",
        )
        self.assertEqual(draft["Q3"].score, 0)
        self.assertEqual(draft["Q3"].confidence, "low")
        with_frame = suggest_scores(
            ["A validated ELISA was used to measure dengue IgG antibodies."],
            "serologic", "prior_exposure", "full_population",
            target_population="Residents of three named communities in the study district",
            sampling_frame_description="A household census list covering all three named communities",
        )
        self.assertEqual(with_frame["Q3"].score, 1)
        self.assertIn("sampling frame/source population", with_frame["Q3"].rationale)

    def test_q3_does_not_infer_without_pdf_frame_or_user_target(self) -> None:
        draft = suggest_scores(
            ["A validated ELISA was used to measure dengue IgG antibodies."],
            "serologic",
            "prior_exposure",
            "full_population",
        )
        self.assertEqual(draft["Q3"].score, 0)
        self.assertEqual(draft["Q3"].confidence, "low")

    def test_q4_dictionary_does_not_upgrade_random_validation_subset(self) -> None:
        pages = [
            "Participants were recruited by convenience at one clinic. A random subset of positive and negative "
            "samples was later selected for laboratory validation."
        ]
        draft = suggest_scores(pages, "serologic", "prior_exposure", "two_phase_validation")
        self.assertEqual(draft["Q4"].score, 1)

    def test_q4_falls_back_to_user_method_when_pdf_has_no_recruitment_evidence(self) -> None:
        draft = suggest_scores(
            ["A validated ELISA was used to test serum specimens."],
            "serologic",
            "prior_exposure",
            "full_population",
            sampling_recruitment_method="Simple random sampling",
        )
        self.assertEqual(draft["Q4"].score, 2)
        self.assertEqual(draft["Q4"].confidence, "low")
        self.assertIn("reviewer-entered sampling/recruitment method", draft["Q4"].rationale)

    def test_q4_pdf_evidence_takes_precedence_over_user_fallback(self) -> None:
        draft = suggest_scores(
            ["Participants were recruited by convenience at one clinic."],
            "serologic",
            "prior_exposure",
            "full_population",
            sampling_recruitment_method="Multistage probability sampling",
        )
        self.assertEqual(draft["Q4"].score, 1)
        self.assertEqual(draft["Q4"].confidence, "high")

    def test_q4_mixed_user_fallback_remains_unscored(self) -> None:
        draft = suggest_scores(
            ["A validated ELISA was used to test serum specimens."],
            "serologic",
            "prior_exposure",
            "full_population",
            sampling_recruitment_method="Mixed probability and non-probability sampling",
        )
        self.assertIsNone(draft["Q4"].score)

    def test_q4_dictionary_recognizes_universal_eligible_capture(self) -> None:
        pages = [
            "All eligible participants were included during the prespecified study period and serum was collected."
        ]
        draft = suggest_scores(pages, "serologic", "prior_exposure", "full_population")
        self.assertEqual(draft["Q4"].score, 3)

    def test_q1_q3_q4_match_appraisal_library_worked_examples(self) -> None:
        examples = (
            (
                "population household serosurvey",
                "Residents aged 5 years and older in selected ecological zones",
                "Participants of both sexes and all planned age groups were sampled from enumeration areas and household lists. "
                "PPS clusters, random households, and a random eligible resident were selected.",
                (1, 1, 3),
            ),
            (
                "single hospital generalized to city",
                "All febrile city residents",
                "Male and female patients across age groups were recruited at one referral hospital by consecutive consenting enrollment.",
                (0, 0, 1),
            ),
            (
                "hospital attendee target",
                "Eligible febrile attendees of the named hospital",
                "Male and female attendees across age groups entered through the hospital-attendee frame and were consecutively recruited.",
                (1, 1, 1),
            ),
            (
                "regional multisite clinical surveillance",
                "Febrile attendees in a regional facility network",
                "Both sexes and multiple age groups were represented across eight relevant hospitals and health centres. "
                "Eligible patients were enrolled without random selection.",
                (1, 1, 2),
            ),
            (
                "archived specimen subset",
                "Participants in a prior febrile cohort",
                "Only stored sera with adequate volume were included. The age and sex distribution and specimen selection method were not reported.",
                (0, 0, 0),
            ),
            (
                "complete occupational census",
                "All staff in two outbreak-affected occupational groups",
                "All age groups and both sexes were represented on complete staff lists. All eligible staff were included.",
                (1, 1, 3),
            ),
        )
        for name, target, text, expected in examples:
            with self.subTest(name=name):
                draft = suggest_scores(
                    [text], "serologic", "prior_exposure", "full_population",
                    target_population=target,
                )
                self.assertEqual(
                    (draft["Q1"].score, draft["Q3"].score, draft["Q4"].score),
                    expected,
                )

    def test_q4_random_assay_validation_subset_is_not_source_sampling(self) -> None:
        draft = suggest_scores(
            ["A random subset of screening-positive and screening-negative specimens was selected for laboratory assay validation."],
            "serologic", "assay_performance", "two_phase_validation",
            target_population="Eligible clinic attendees",
        )
        self.assertEqual(draft["Q4"].score, 0)

    def test_primary_molecular_assay_does_not_self_confirm(self) -> None:
        pages = [
            "All serum specimens were tested using a validated RT-qPCR protocol with positive, negative, "
            "extraction, and amplification controls. No sequencing or second target was performed."
        ]
        draft = suggest_scores(pages, "direct_detection", "confirmed_active", "full_population")
        self.assertEqual(draft["Q6a"].score, 4)
        self.assertEqual(draft["Q6b"].score, 1)
        self.assertEqual(draft["Q6c"].score, 0)

    def test_primary_neutralization_assay_does_not_self_confirm(self) -> None:
        pages = ["All participants were tested by PRNT90 using a validated reference protocol."]
        draft = suggest_scores(pages, "direct_detection", "neutralizing_antibody", "full_population")
        self.assertEqual(draft["Q6a"].score, 4)
        self.assertEqual(draft["Q6c"].score, 0)

    def test_complete_staged_endpoint_confirmation_scores_two_under_q6c(self) -> None:
        draft = suggest_scores(
            ["All screening-positive samples were confirmed by PRNT90."],
            "serologic", "prior_exposure", "all_screen_positive",
            synthesis_path_key="serology_tier_b",
            primary_assay="IgG ELISA",
            confirmatory_assay="PRNT90",
        )
        self.assertEqual(draft["Q6a"].score, 4)
        self.assertEqual(draft["Q6c"].score, 2)

    def test_representative_tier_c_neutralization_subset_scores_one_under_q6c(self) -> None:
        draft = suggest_scores(
            [
                "All participants were screened using IgG ELISA. A predefined representative random "
                "subset of screening-positive samples was independently confirmed by PRNT90."
            ],
            "serologic", "prior_exposure", "representative_positive_subset",
            synthesis_path_key="serology_tier_c",
            counts={
                "total_tested_n": 1000,
                "primary_positive_n": 200,
                "number_retested_n": 50,
                "confirmed_positive_n": 20,
                "planned_retest_n": 50,
                "numerator_provenance": "observed",
            },
            primary_assay="IgG ELISA",
            confirmatory_assay="PRNT90",
        )
        self.assertEqual(draft["Q6a"].score, 3)
        self.assertEqual(draft["Q6c"].score, 1)
        self.assertIn("predefined representative/random subset", draft["Q6c"].rationale)

    def test_staged_path_without_linked_confirmatory_method_scores_zero_under_q6c(self) -> None:
        draft = suggest_scores(
            ["A predefined representative subset of screening-positive samples was selected."],
            "serologic", "prior_exposure", "representative_positive_subset",
            synthesis_path_key="serology_tier_c",
            primary_assay="IgG ELISA",
        )
        self.assertEqual(draft["Q6c"].score, 0)

    def test_incomplete_tier_b_is_limited_confirmation_under_q6c(self) -> None:
        draft = suggest_scores(
            ["Screening-positive samples with available specimens were confirmed by PRNT90."],
            "serologic", "prior_exposure", "all_screen_positive",
            synthesis_path_key="serology_tier_b",
            counts={
                "total_tested_n": 500,
                "primary_positive_n": 40,
                "number_retested_n": 30,
                "confirmed_positive_n": 20,
                "planned_retest_n": 40,
                "numerator_provenance": "observed",
            },
            primary_assay="IgG ELISA",
            confirmatory_assay="PRNT90",
        )
        self.assertEqual(draft["Q6c"].score, 1)
        self.assertIn("selected, incomplete, or unresolved subset", draft["Q6c"].rationale)

    def test_ancillary_validation_panel_does_not_earn_q6c(self) -> None:
        draft = suggest_scores(
            ["Reference-positive and reference-negative validation samples were tested by PRNT90."],
            "serologic", "assay_performance", "ancillary_validation",
            synthesis_path_key="serology_validation",
            primary_assay="IgG ELISA",
            confirmatory_assay="PRNT90",
        )
        self.assertEqual(draft["Q6c"].score, 0)

    def test_q7_ignores_optional_supporting_subset_but_checks_staged_completion(self) -> None:
        primary = suggest_scores(
            ["The same ELISA was applied to all samples; a selected subset was later tested by PRNT."],
            "serologic", "prior_exposure", "full_population",
            synthesis_path_key="serology_apparent",
            primary_assay="IgG ELISA",
            confirmatory_assay="PRNT90",
        )
        self.assertEqual(primary["Q7"].score, 1)
        incomplete = suggest_scores(
            ["Positive samples were confirmed by PRNT90."],
            "serologic", "prior_exposure", "all_screen_positive",
            synthesis_path_key="serology_tier_b",
            counts={
                "total_tested_n": 500,
                "primary_positive_n": 40,
                "number_retested_n": 30,
                "confirmed_positive_n": 12,
                "numerator_provenance": "observed",
            },
            primary_assay="IgG ELISA",
            confirmatory_assay="PRNT90",
        )
        self.assertEqual(incomplete["Q7"].score, 0)

    def test_q8_uses_endpoint_specific_count_audit(self) -> None:
        complete = suggest_scores(
            [], "serologic", "prior_exposure", "representative_positive_subset",
            synthesis_path_key="serology_tier_c",
            counts={
                "total_tested_n": 1000,
                "primary_positive_n": 100,
                "number_retested_n": 50,
                "confirmed_positive_n": 30,
                "numerator_provenance": "observed",
            },
        )
        self.assertEqual(complete["Q8"].score, 1)
        incomplete = suggest_scores(
            [], "serologic", "prior_exposure", "representative_positive_subset",
            synthesis_path_key="serology_tier_c",
            counts={
                "total_tested_n": 1000,
                "primary_positive_n": 100,
                "confirmed_positive_n": 30,
                "numerator_provenance": "observed",
            },
        )
        self.assertEqual(incomplete["Q8"].score, 0)

    def test_primary_direct_assay_role_remains_tier_a_in_selected_subset(self) -> None:
        result = classify_synthesis("confirmed_active", "selected_subsample")
        self.assertTrue(result["tier"].startswith("Tier A"))
        self.assertIn("selected", result["measurement_pathway"].lower())
        self.assertIn("does not establish population representativeness", result["analysis_role"])

    def test_inseparable_serology_has_its_own_endpoint_and_count_path(self) -> None:
        path = SYNTHESIS_PATHS_BY_KEY["mixed_serology_only"]
        self.assertEqual(path.endpoint_key, "mixed_serology")
        audit = audit_synthesis_path_counts(
            path.key,
            {
                "total_tested_n": 200,
                "mixed_serology_positive_n": 25,
                "numerator_provenance": "observed",
            },
        )
        self.assertAlmostEqual(audit["estimate"], 25 / 200)
        result = score_assessment(maximum_scores("serologic"), "serologic", "mixed_serology")
        self.assertEqual(result["maximum"], 18)
        self.assertIsNone(result["scores"]["Q10"])

    def test_export_keeps_scores_comments_and_classification(self) -> None:
        scores = maximum_scores("serologic")
        record = build_export_record(
            {"study_id": "Example 2026"},
            scores,
            {"Q1": "Age and sex matched the stated community target."},
            "serologic",
            classify_synthesis("prior_exposure", None),
        )
        self.assertEqual(record["dare_total"], 19)
        self.assertIn("Q1 Q3 Q4 Rules", record["appraisal_library_rule_source"])
        self.assertEqual(record["Q1_comment"], "Age and sex matched the stated community target.")
        self.assertEqual(record["synthesis_tier"], "Unresolved")

    def test_blank_pdf_is_flagged_as_empty(self) -> None:
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf is not installed")
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        stream = BytesIO()
        writer.write(stream)
        result = extract_pdf_text(stream.getvalue())
        self.assertEqual(result["page_count"], 1)
        self.assertTrue(result["scanned_or_empty"])

    def test_doi_is_preferred_over_pdf_citation_metadata(self) -> None:
        result = extract_citation_or_doi(
            ["Example Article. Available at https://doi.org/10.1016/j.example.2026.001."],
            {"title": "Example Article", "author": "A. Researcher"},
        )
        self.assertEqual(result["kind"], "DOI")
        self.assertEqual(result["value"], "10.1016/j.example.2026.001")

    def test_pdf_metadata_builds_citation_when_doi_is_missing(self) -> None:
        result = extract_citation_or_doi(
            ["Published in 2026"],
            {"title": "Arbovirus surveillance in Exampleland", "author": "A. Researcher"},
        )
        self.assertEqual(result["kind"], "Citation")
        self.assertEqual(
            result["value"],
            "A. Researcher. Arbovirus surveillance in Exampleland. 2026.",
        )

    def test_pdf_extraction_preserves_document_metadata(self) -> None:
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf is not installed")
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.add_metadata({"/Title": "Metadata title", "/Author": "Metadata author"})
        stream = BytesIO()
        writer.write(stream)
        result = extract_pdf_text(stream.getvalue())
        self.assertEqual(result["metadata"]["title"], "Metadata title")
        self.assertEqual(result["metadata"]["author"], "Metadata author")

    def test_png_overview_is_valid_and_endpoint_specific(self) -> None:
        scores = maximum_scores("serologic")
        png = render_assessment_png(
            {
                "study_id": "Example et al., 2026",
                "virus": "DENV",
                "assessment_date": "2026-08-19",
                "endpoint_label": "Prior exposure / antibody seroprevalence",
            },
            scores,
            "serologic",
            classify_synthesis("prior_exposure", None),
        )
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 20_000)
        from PIL import Image

        image = Image.open(BytesIO(png))
        self.assertEqual(image.size, (1800, 1540))
        self.assertEqual(image.getpixel((10, 10)), (18, 59, 93))

    def test_png_overview_accepts_custom_presentation_colors(self) -> None:
        png = render_assessment_png(
            {
                "study_id": "Custom palette",
                "endpoint_label": "Prior exposure / antibody seroprevalence",
            },
            maximum_scores("serologic"),
            "serologic",
            classify_synthesis("prior_exposure", None),
            presentation_colors={
                "header": "#234567",
                "background": "#FAF0E6",
                "primary": "#7C3AED",
            },
        )
        from PIL import Image

        image = Image.open(BytesIO(png))
        self.assertEqual(image.getpixel((10, 10)), (35, 69, 103))
        self.assertEqual(image.getpixel((10, 200)), (250, 240, 230))

    def test_png_overview_rejects_invalid_presentation_color(self) -> None:
        with self.assertRaises(ValueError):
            render_assessment_png(
                {"endpoint_label": "Prior exposure / antibody seroprevalence"},
                maximum_scores("serologic"),
                "serologic",
                classify_synthesis("prior_exposure", None),
                presentation_colors={"header": "navy"},
            )

    def test_png_overview_accepts_branding_logo(self) -> None:
        logo_path = Path(__file__).resolve().parents[1] / "Synergy_NGS2025.png"
        png = render_assessment_png(
            {"endpoint_label": "Prior exposure / antibody seroprevalence"},
            maximum_scores("serologic"),
            "serologic",
            classify_synthesis("prior_exposure", None),
            branding_logo=logo_path.read_bytes(),
        )
        from PIL import Image

        image = Image.open(BytesIO(png))
        self.assertEqual(image.getpixel((1600, 20)), (0, 0, 0))

    def test_study_designer_balances_three_explicit_planning_pillars(self) -> None:
        plan = {
            "synthesis_path_key": "serology_tier_b",
            "target_population": "Residents aged 5 years and older in the study district",
            "sampling_recruitment_method": "Multistage probability sampling",
            "sampling_frame_defined": True,
            "eligibility_defined": True,
            "sample_size_rationale": True,
            "nonresponse_plan": True,
            "primary_assay": "IgG ELISA",
            "confirmatory_assay": "PRNT90",
            "specimen_timing": True,
            "validation_controls": True,
            "cross_reactivity_plan": True,
            "testing_denominator_plan": True,
            "numerator_denominator_reporting": True,
            "flow_reporting": True,
            "assay_reporting": True,
            "missingness_reporting": True,
            "estimator_reporting": True,
            "reproducibility_reporting": True,
        }
        readiness = evaluate_study_design_plan(plan)
        self.assertEqual(readiness["balance_floor"], 100)
        self.assertTrue(all(
            result["percentage"] == 100
            for result in readiness["pillars"].values()
        ))

        plan["confirmatory_assay"] = ""
        incomplete = evaluate_study_design_plan(plan)
        self.assertLess(incomplete["pillars"]["Assay design"]["percentage"], 100)
        self.assertTrue(any("confirmatory" in item.lower() for item in incomplete["recommendations"]))

    def test_study_designer_png_is_high_resolution_and_downloadable(self) -> None:
        png = render_study_design_png(
            {
                "synthesis_path_key": "confirmed_active_tier_b",
                "study_title": "Planned active-infection surveillance",
                "virus": "DENV",
                "target_population": "Febrile patients at sentinel facilities",
                "surveillance_architecture": "Sentinel facility surveillance",
                "sampling_recruitment_method": "Structured multisite surveillance",
                "primary_assay": "NS1 antigen RDT",
                "confirmatory_assay": "Real-time RT-qPCR; Virus isolation",
            }
        )
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 30_000)
        from PIL import Image

        image = Image.open(BytesIO(png))
        self.assertEqual(image.size, (2000, 1500))

    def test_surveillance_study_design_report_is_auditable_pdf(self) -> None:
        plan = {
            "study_title": "District arbovirus surveillance protocol",
            "synthesis_path_key": "confirmed_active_tier_b",
            "synthesis_path_keys": ["confirmed_active_tier_b", "serology_apparent"],
            "virus": "DENV; CHIKV",
            "viruses": ["DENV", "CHIKV"],
            "virus_coverage": "Multiplex / multiple viruses",
            "target_population": "General febrile population attending sentinel facilities",
            "surveillance_mode": "Hybrid active + passive surveillance",
            "surveillance_architecture": "Sentinel facility surveillance",
            "active_stream_definition": "Scheduled community fever screening",
            "passive_stream_definition": "Routine febrile presentations",
            "stream_estimator_mode": "Separate active and passive estimates",
            "stream_integration_plan": True,
            "endpoint_design": "Mixed endpoints",
            "planned_sample_size": 1200,
            "sampling_recruitment_method": "Structured multisite surveillance",
            "sampling_frame_defined": True,
            "eligibility_defined": True,
            "sample_size_rationale": True,
            "nonresponse_plan": True,
            "pathway_assays": {
                "confirmed_active_tier_b": {
                    "primary_assay": "NS1 antigen ELISA",
                    "confirmatory_assay": "Real-time RT-qPCR",
                },
                "serology_apparent": {
                    "primary_assay": "IgG ELISA",
                    "confirmatory_assay": "PRNT90",
                },
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
            "created_at": "2026-09-08T12:00:00+00:00",
        }
        readiness = evaluate_study_design_plan(plan)
        flow_png = render_study_design_png(plan)
        report = render_surveillance_design_report_pdf(
            plan, readiness=readiness, design_png=flow_png
        )
        self.assertTrue(report.startswith(b"%PDF"))
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(report))
        self.assertGreaterEqual(len(reader.pages), 3)
        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("DARE-Arbo Surveillance Study", extracted)
        self.assertIn("Design Report", extracted)
        self.assertIn("District arbovirus surveillance protocol", extracted)
        self.assertIn("Study-virus-estimand analysis plan", extracted)
        self.assertIn("Protocol handoff note", extracted)

    def test_study_designer_expands_hybrid_mixed_multiplex_analysis_units(self) -> None:
        plan = {
            "synthesis_path_key": "serology_apparent",
            "synthesis_path_keys": ["serology_apparent", "confirmed_active_universal"],
            "viruses": ["DENV", "CHIKV"],
            "surveillance_mode": "Hybrid active + passive surveillance",
            "stream_estimator_mode": "Separate active and passive estimates",
            "target_population": "Residents and presenting febrile patients in the district",
            "sampling_recruitment_method": "Multistage probability sampling",
            "sampling_frame_defined": True,
            "eligibility_defined": True,
            "sample_size_rationale": True,
            "nonresponse_plan": True,
            "stream_integration_plan": True,
            "pathway_assays": {
                "serology_apparent": {
                    "primary_assay": "Luminex/multiplex serology",
                    "confirmatory_assay": "",
                },
                "confirmed_active_universal": {
                    "primary_assay": "Multiplex RT-PCR/NAAT",
                    "confirmatory_assay": "",
                },
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
        }
        readiness = evaluate_study_design_plan(plan)
        self.assertTrue(readiness["hybrid_surveillance"])
        self.assertTrue(readiness["mixed_endpoints"])
        self.assertTrue(readiness["virus_multiplexing"])
        self.assertEqual(readiness["analysis_unit_count"], 8)
        self.assertEqual(
            {unit["surveillance_stream"] for unit in readiness["analysis_units"]},
            {"Active surveillance", "Passive surveillance"},
        )
        self.assertEqual(readiness["balance_floor"], 100)

        png = render_study_design_png(plan)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))


try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # The core engine can be tested without the UI dependency.
    AppTest = None


@unittest.skipIf(AppTest is None, "Streamlit testing support is not installed")
class StreamlitSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
        self.app = AppTest.from_file(str(app_path), default_timeout=60).run()

    def assert_no_app_exception(self) -> None:
        self.assertEqual(list(self.app.exception), [])

    def test_assessor_initial_view(self) -> None:
        self.assert_no_app_exception()
        labels = [element.value for element in self.app.subheader]
        self.assertIn("1. Define the extracted endpoint", labels)
        self.assertIn("4. Review and export", labels)

    def test_appraiser_and_framework_views(self) -> None:
        self.app.sidebar.radio[0].set_value("Appraiser").run()
        self.assert_no_app_exception()
        self.assertIn("Rapid structured batch entry", [element.value for element in self.app.subheader])
        self.app.sidebar.radio[0].set_value("Framework").run()
        self.assert_no_app_exception()
        self.assertIn("Scoring structure", [element.value for element in self.app.subheader])
        framework_notes = [element.value for element in self.app.info]
        self.assertTrue(
            any(
                "IgM-defined presumptive recent infection" in note
                and "NS1-defined presumptive active infection" in note
                and "maximum 19" in note
                for note in framework_notes
            )
        )

    def test_study_designer_view_generates_downloadable_flow(self) -> None:
        self.app.sidebar.radio[0].set_value("DARE-Arbo Designer").run()
        self.assert_no_app_exception()
        subheaders = [element.value for element in self.app.subheader]
        self.assertIn("1. Define the surveillance objective", subheaders)
        self.assertIn("3. Planning balance and design figure", subheaders)
        download_labels = [button.label for button in self.app.download_button]
        self.assertIn("Download surveillance study design report (PDF)", download_labels)
        self.assertIn("Download study-design flow PNG", download_labels)
        self.assertIn("Download study-design plan JSON", download_labels)

    def test_study_designer_exposes_hybrid_mixed_and_multiplex_options(self) -> None:
        self.app.sidebar.radio[0].set_value("DARE-Arbo Designer").run()
        self.assert_no_app_exception()
        selectboxes = {element.label: element for element in self.app.selectbox}
        self.assertIn("Surveillance mode", selectboxes)
        self.assertIn("Virus coverage", selectboxes)
        self.assertIn("Endpoint design", selectboxes)

        selectboxes["Virus coverage"].set_value("Multiplex / multiple viruses").run()
        self.assert_no_app_exception()
        self.assertIn("Arboviruses included", [field.label for field in self.app.multiselect])

        selectboxes = {element.label: element for element in self.app.selectbox}
        selectboxes["Endpoint design"].set_value("Mixed endpoints").run()
        self.assert_no_app_exception()
        self.assertIn(
            "Additional endpoint/testing pathways",
            [field.label for field in self.app.multiselect],
        )

        selectboxes = {element.label: element for element in self.app.selectbox}
        selectboxes["Surveillance mode"].set_value(
            "Hybrid active + passive surveillance"
        ).run()
        self.assert_no_app_exception()
        text_area_labels = [field.label for field in self.app.text_area]
        self.assertIn("Active surveillance source/population", text_area_labels)
        self.assertIn("Passive surveillance source/population", text_area_labels)

    def test_endpoint_change_updates_default_pathway(self) -> None:
        self.app.selectbox[0].set_value("confirmed_active").run()
        self.assert_no_app_exception()
        self.assertEqual(self.app.session_state["pathway_selector"], "direct_detection")

    def test_endpoint_strategy_controls_show_only_relevant_assessor_counts(self) -> None:
        labels = [field.label for field in self.app.text_input]
        self.assertIn("Total tested", labels)
        self.assertIn("Primary positive", labels)
        self.assertNotIn("Molecular positive", labels)

        self.app.selectbox[0].set_value("confirmed_active").run()
        self.assert_no_app_exception()
        self.app.selectbox[1].set_value("confirmed_active_tier_b").run()
        self.assert_no_app_exception()
        labels = [field.label for field in self.app.text_input]
        for expected in ("Total tested", "Primary positive", "Number retested", "Molecular positive"):
            self.assertIn(expected, labels)
        self.assertNotIn("IgM positive", labels)

    def test_assay_dropdowns_follow_endpoint_and_testing_strategy(self) -> None:
        primary = next(
            field for field in self.app.selectbox
            if field.label == "Primary / screening assay"
        )
        self.assertIn("IgG ELISA", primary.options)
        self.assertNotIn("NS1 antigen ELISA", primary.options)
        confirmatory = next(
            field for field in self.app.multiselect
            if field.label == "Confirmatory assay(s)"
        )
        self.assertIn("PRNT90", confirmatory.options)

        self.app.selectbox[0].set_value("confirmed_active").run()
        self.assert_no_app_exception()
        self.app.selectbox[1].set_value("confirmed_active_tier_b").run()
        self.assert_no_app_exception()
        primary = next(
            field for field in self.app.selectbox
            if field.label == "Primary / screening assay"
        )
        self.assertIn("NS1 antigen ELISA", primary.options)
        self.assertNotIn("IgG ELISA", primary.options)
        primary.set_value("NS1 antigen ELISA").run()
        self.assert_no_app_exception()
        confirmatory = next(
            field for field in self.app.multiselect
            if field.label == "Confirmatory assay(s)"
        )
        self.assertIn("Real-time RT-qPCR", confirmatory.options)
        confirmatory.set_value(["Real-time RT-qPCR", "Virus isolation"]).run()
        self.assert_no_app_exception()
        self.assertEqual(
            self.app.session_state["confirmatory_assay_choices"],
            ["Real-time RT-qPCR", "Virus isolation"],
        )

    def test_pending_doi_autofills_blank_citation_without_overwriting_reviewer_value(self) -> None:
        pending = {
            "value": "10.1234/dare.arbo.2026",
            "kind": "DOI",
            "confidence": "high",
            "source": "PDF metadata/text",
        }
        self.app.session_state["pending_citation_autofill"] = pending
        self.app.run()
        self.assert_no_app_exception()
        citation_field = next(field for field in self.app.text_input if field.label == "Citation or DOI")
        self.assertEqual(citation_field.value, pending["value"])

        citation_field.set_value("Reviewer-entered citation").run()
        self.app.session_state["pending_citation_autofill"] = {
            **pending,
            "value": "10.9999/should.not.overwrite",
        }
        self.app.run()
        self.assert_no_app_exception()
        citation_field = next(field for field in self.app.text_input if field.label == "Citation or DOI")
        self.assertEqual(citation_field.value, "Reviewer-entered citation")

    def test_use_suggestion_updates_score_through_callback(self) -> None:
        self.app.session_state["suggestions"] = {
            "Q1": Suggestion(
                score=1,
                confidence="high",
                rationale="Regression test for the suggestion callback.",
            )
        }
        self.app.run()
        self.assert_no_app_exception()
        use_buttons = [button for button in self.app.button if button.label == "Use suggestion"]
        self.assertEqual(len(use_buttons), 1)
        use_buttons[0].click().run()
        self.assert_no_app_exception()
        self.assertEqual(self.app.session_state["score_Q1"], 1)

    def test_reset_assessment_clears_instantiated_score_widgets(self) -> None:
        self.app.radio[0].set_value(1).run()
        self.assertEqual(self.app.session_state["score_Q1"], 1)
        reset_buttons = [button for button in self.app.button if button.label == "Reset assessment"]
        self.assertEqual(len(reset_buttons), 1)
        reset_buttons[0].click().run()
        self.assert_no_app_exception()
        self.assertIsNone(self.app.session_state["score_Q1"])

    def test_registry_generates_batch_appraisal_grid(self) -> None:
        registry_record = build_export_record(
            {
                "study_id": "Registry Study 2026",
                "virus": "DENV",
                "endpoint_key": "prior_exposure",
                "endpoint_label": "Prior exposure / antibody seroprevalence",
                "verification_design": "",
            },
            maximum_scores("serologic"),
            {},
            "serologic",
            classify_synthesis("prior_exposure", None),
        )
        self.app.session_state["registry"] = [registry_record]
        self.app.sidebar.radio[0].set_value("Appraiser").run()
        self.assert_no_app_exception()
        generate_buttons = [button for button in self.app.button if button.label == "Generate batch appraisal"]
        self.assertEqual(len(generate_buttons), 1)
        generate_buttons[0].click().run()
        self.assert_no_app_exception()
        batch = self.app.session_state["batch_grid"]
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch.iloc[0]["Study ID"], "Registry Study 2026")
        self.assertEqual(batch.iloc[0]["Q1"], 1)


if __name__ == "__main__":
    unittest.main()
