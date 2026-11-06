from __future__ import annotations

import json

import pytest

from app.agents.job_matching.contract import MatchResult, MatchSignal, MatchVerdict
from app.agents.job_matching.prompt import PROMPT_VERSION
from evaluation.job_matching import (
    build_case_prompt,
    evaluate_benchmark,
    evaluate_case,
    load_cases,
)


def valid_result(**overrides) -> MatchResult:
    payload = {
        "score": 70,
        "verdict": "partial_match",
        "matched_skills": ["Python", "SQL"],
        "missing_required_skills": ["Kubernetes"],
        "missing_preferred_skills": [],
        "strengths": ["Python and SQL are supported by the resume."],
        "gaps": ["Kubernetes is not evidenced."],
        "experience_match": "matched",
        "education_match": "unclear",
        "location_match": "matched",
        "seniority_match": "matched",
        "rationale": "Python and SQL match; Kubernetes is not evidenced.",
        "confidence": 0.8,
    }
    payload.update(overrides)
    return MatchResult.model_validate(payload)


def test_fixture_has_ten_distinct_synthetic_scenarios() -> None:
    cases = load_cases()

    assert len(cases) == 10
    assert len({case.id for case in cases}) == len(cases)
    assert {case.scenario for case in cases} == {
        "strong_match",
        "partial_match",
        "missing_essential_skills",
        "unrelated_role",
        "certification_not_mentioned",
        "certification_confirmed_absent",
        "preferred_skill_not_evidenced",
        "relevant_experience_gap",
        "missing_evidence",
        "transferable_experience",
    }
    assert all(case.candidate.name and case.candidate.resume_content for case in cases)
    assert all("Fictional" in case.job.company_name for case in cases)


def test_every_preferred_skill_is_excluded_from_required_gap_expectations() -> None:
    for case in load_cases():
        preferred = set(case.job.analysis.get("preferred_skills", []))
        excluded = set(case.expectations.must_not_be_missing_required)
        assert preferred <= excluded, case.id


def test_benchmark_prompts_use_case_job_and_resume_evidence() -> None:
    case = next(case for case in load_cases() if case.id == "strong-retrieval-engineer")
    prompt = build_case_prompt(case)

    assert case.job.title in prompt
    assert case.candidate.resume_content in prompt
    assert "JOB EVIDENCE" in prompt
    assert "CANDIDATE EVIDENCE" in prompt


def test_strong_case_requires_unclear_education_without_requirement_or_evidence() -> None:
    case = next(case for case in load_cases() if case.id == "strong-retrieval-engineer")
    assert case.job.analysis["education_requirements"] == []
    assert case.expectations.must_be_unclear == ["education_match"]

    result = valid_result(
        verdict=MatchVerdict.strong_match,
        matched_skills=["Python", "PyTorch", "Hugging Face Transformers"],
        missing_required_skills=[],
        education_match=MatchSignal.unclear,
    )
    assert evaluate_case(case, result)["passed"] is True

    unjustified_match = result.model_copy(
        update={"education_match": MatchSignal.matched}
    )
    evaluation = evaluate_case(case, unjustified_match)
    assert evaluation["checks"]["signal_unclear:education_match"] is False
    assert evaluation["passed"] is False


def test_generic_years_match_does_not_hide_missing_gpu_training_evidence() -> None:
    case = next(case for case in load_cases() if case.id == "missing-essential-ml")
    assert case.job.analysis["experience_years_min"] == 5
    assert case.candidate.years_of_experience == 5
    assert "Production GPU training experience" in case.job.analysis["qualifications"]
    assert case.expectations.must_be_matched == ["experience_match"]
    assert "production GPU training experience" in case.expectations.must_identify_gaps

    result = valid_result(
        verdict=MatchVerdict.poor_match,
        matched_skills=["Python"],
        missing_required_skills=["PyTorch", "CUDA", "distributed training"],
        experience_match=MatchSignal.matched,
        gaps=[
            "PyTorch is not evidenced.",
            "CUDA is not evidenced.",
            "Distributed training is not evidenced.",
            "Production GPU training experience is not evidenced.",
        ],
        rationale=(
            "The resume supports Python only; PyTorch, CUDA, distributed training, "
            "and production GPU training are not evidenced."
        ),
    )
    evaluation = evaluate_case(case, result)
    assert evaluation["checks"]["signal_matched:experience_match"] is True
    assert evaluation["checks"][
        "gap_identified:production GPU training experience"
    ] is True
    assert evaluation["passed"] is True

    missing_gpu_gap = result.model_copy(
        update={
            "gaps": [
                "PyTorch is not evidenced.",
                "CUDA is not evidenced.",
                "Distributed training is not evidenced.",
            ]
        }
    )
    evaluation = evaluate_case(case, missing_gpu_gap)
    assert evaluation["checks"][
        "gap_identified:production GPU training experience"
    ] is False
    assert evaluation["passed"] is False

    conflated_experience = result.model_copy(
        update={"experience_match": MatchSignal.mismatched}
    )
    assert evaluate_case(case, conflated_experience)["passed"] is False


@pytest.mark.parametrize(
    "omitted_gap",
    [
        "PyTorch",
        "CUDA",
        "distributed training",
        "production GPU training experience",
    ],
)
def test_each_expected_gap_must_be_in_gaps_not_only_rationale(
    omitted_gap: str,
) -> None:
    case = next(case for case in load_cases() if case.id == "missing-essential-ml")
    expected_gaps = case.expectations.must_identify_gaps
    result = valid_result(
        verdict=MatchVerdict.poor_match,
        matched_skills=["Python"],
        missing_required_skills=["PyTorch", "CUDA", "distributed training"],
        experience_match=MatchSignal.matched,
        gaps=[f"{gap} is not evidenced." for gap in expected_gaps if gap != omitted_gap],
        rationale=f"{omitted_gap} is not evidenced in the supplied resume.",
    )

    evaluation = evaluate_case(case, result)

    assert evaluation["checks"][f"gap_identified:{omitted_gap}"] is False
    assert all(
        evaluation["checks"][f"gap_identified:{gap}"]
        for gap in expected_gaps
        if gap != omitted_gap
    )
    assert evaluation["passed"] is False


@pytest.mark.parametrize(
    ("gpu_gap", "expected"),
    [
        ("No documented experience with production GPU training.", True),
        ("Production GPU training experience is missing.", True),
        ("No evidence of production GPU training experience.", True),
        ("Production GPU training was not documented.", True),
        ("GPU inference optimization experience is missing.", False),
        ("No experience deploying GPU inference pipelines.", False),
        ("The candidate lacks generic ML experience.", False),
        ("Pre-production GPU training experience is documented.", False),
        ("Pre-production GPU training experience is missing.", False),
    ],
)
def test_gpu_training_gap_matching_accepts_only_narrow_equivalents(
    gpu_gap: str, expected: bool
) -> None:
    case = next(case for case in load_cases() if case.id == "missing-essential-ml")
    result = valid_result(
        verdict=MatchVerdict.poor_match,
        matched_skills=["Python"],
        missing_required_skills=["PyTorch", "CUDA", "distributed training"],
        experience_match=MatchSignal.matched,
        gaps=[
            "PyTorch is not evidenced.",
            "CUDA is not evidenced.",
            "Distributed training is not evidenced.",
            gpu_gap,
        ],
        rationale="The resume supports Python and business reporting only.",
    )

    evaluation = evaluate_case(case, result)

    assert evaluation["checks"][
        "gap_identified:production GPU training experience"
    ] is expected


def test_one_gap_entry_cannot_satisfy_multiple_mandatory_gap_expectations() -> None:
    case = next(case for case in load_cases() if case.id == "missing-essential-ml")
    combined = valid_result(
        verdict=MatchVerdict.poor_match,
        matched_skills=["Python"],
        missing_required_skills=["PyTorch", "CUDA", "distributed training"],
        experience_match=MatchSignal.matched,
        gaps=[
            "PyTorch, CUDA, distributed training, and production GPU training "
            "experience are not evidenced."
        ],
        rationale="The supplied resume supports Python only.",
    )

    evaluation = evaluate_case(case, combined)
    gap_checks = [
        value
        for key, value in evaluation["checks"].items()
        if key.startswith("gap_identified:")
    ]

    assert sum(gap_checks) == 1
    assert evaluation["passed"] is False


@pytest.mark.parametrize(
    ("gap", "rationale"),
    [
        (
            "Production GPU training experience is supported.",
            "No evidence of production GPU training experience is supplied.",
        ),
        (
            "Production GPU training experience is not evidenced.",
            "The resume demonstrates production GPU training experience.",
        ),
        (
            "Production GPU training experience is not evidenced.",
            "The resume proves production GPU training experience.",
        ),
        (
            "Production GPU training experience is not evidenced.",
            "The resume demonstrates experience with production GPU training.",
        ),
        (
            "Production GPU training experience is not evidenced.",
            "Production GPU training experience is supported.",
        ),
    ],
)
def test_contradictory_gap_or_rationale_cannot_be_rescued(
    gap: str, rationale: str
) -> None:
    case = next(case for case in load_cases() if case.id == "missing-essential-ml")
    result = valid_result(
        verdict=MatchVerdict.poor_match,
        matched_skills=["Python"],
        missing_required_skills=["PyTorch", "CUDA", "distributed training"],
        experience_match=MatchSignal.matched,
        gaps=[
            "PyTorch is not evidenced.",
            "CUDA is not evidenced.",
            "Distributed training is not evidenced.",
            gap,
        ],
        rationale=rationale,
    )

    evaluation = evaluate_case(case, result)

    assert evaluation["checks"][
        "gap_identified:production GPU training experience"
    ] is False


def test_positive_gap_entry_cannot_be_rescued_by_another_negative_gap_entry() -> None:
    case = next(case for case in load_cases() if case.id == "missing-essential-ml")
    result = valid_result(
        verdict=MatchVerdict.poor_match,
        matched_skills=["Python"],
        missing_required_skills=["PyTorch", "CUDA", "distributed training"],
        experience_match=MatchSignal.matched,
        gaps=[
            "PyTorch is not evidenced.",
            "CUDA is not evidenced.",
            "Distributed training is not evidenced.",
            "Production GPU training experience is supported.",
            "No evidence of production GPU training experience is supplied.",
        ],
        rationale="The resume supports Python only.",
    )

    evaluation = evaluate_case(case, result)

    assert evaluation["checks"][
        "gap_identified:production GPU training experience"
    ] is False


def test_gpu_training_gap_alias_does_not_change_unrelated_gap_matching() -> None:
    case = next(case for case in load_cases() if case.id == "partial-data-platform")
    result = valid_result(
        gaps=["Kubernetes is not evidenced."],
        rationale="Python and SQL are supported by the supplied resume.",
    )
    gpu_alias_only = result.model_copy(
        update={
            "gaps": ["Experience with production GPU training is not documented."],
            "rationale": "Python and SQL are supported by the supplied resume.",
        }
    )

    assert evaluate_case(case, result)["checks"]["gap_identified:Kubernetes"] is True
    assert (
        evaluate_case(case, gpu_alias_only)["checks"]["gap_identified:Kubernetes"]
        is False
    )


def test_fixture_rejects_duplicate_ids_and_unknown_ranking_references(tmp_path) -> None:
    all_cases = [case.model_dump(mode="json") for case in load_cases()]
    duplicate_file = tmp_path / "duplicate.json"
    duplicate_file.write_text(json.dumps([all_cases[0], all_cases[0]]), encoding="utf-8")
    with pytest.raises(ValueError, match="IDs must be unique"):
        load_cases(duplicate_file)

    all_cases[0]["expectations"]["score_above"] = ["missing-case"]
    unknown_file = tmp_path / "unknown.json"
    unknown_file.write_text(json.dumps(all_cases), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown ranking case"):
        load_cases(unknown_file)


def test_fixture_rejects_a_missing_scenario(tmp_path) -> None:
    incomplete = [case.model_dump(mode="json") for case in load_cases()[:-1]]
    path = tmp_path / "incomplete.json"
    path.write_text(json.dumps(incomplete), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one case for each expected scenario"):
        load_cases(path)


def test_partial_case_requires_required_gap_and_supported_skills() -> None:
    case = next(case for case in load_cases() if case.id == "partial-data-platform")

    passed_result = valid_result(
        strengths=[
            "Operating services on virtual machines is relevant transferable "
            "experience for containerized platform operations."
        ],
        gaps=["Kubernetes experience is not listed in the resume."],
    )
    passed = evaluate_case(case, passed_result)
    assert passed["passed"] is True
    assert passed["checks"]["missing_required:Kubernetes"] is True
    assert passed["checks"]["transferable_acknowledged:virtual machines"] is True

    failed = evaluate_case(
        case,
        valid_result(
            missing_required_skills=[],
            matched_skills=["Python", "SQL", "Kubernetes"],
            strengths=["The candidate has Kubernetes experience."],
        ),
    )
    assert failed["passed"] is False
    assert failed["checks"]["not_positive_claimed:Kubernetes"] is False

    no_transfer = evaluate_case(
        case,
        passed_result.model_copy(
            update={"strengths": ["Virtual machine operations are documented."]}
        ),
    )
    assert no_transfer["checks"]["transferable_acknowledged:virtual machines"] is False

    unrelated_cue = evaluate_case(
        case,
        passed_result.model_copy(
            update={
                "strengths": [
                    "Virtual machine operations are documented; Kubernetes "
                    "experience is transferable."
                ]
            }
        ),
    )
    assert unrelated_cue["checks"]["transferable_acknowledged:virtual machines"] is False


def test_unsupported_qualification_must_not_be_a_positive_claim() -> None:
    case = next(
        case for case in load_cases() if case.id == "unsupported-cloud-qualification"
    )
    good = valid_result(
        verdict=MatchVerdict.partial_match,
        matched_skills=["Python", "Docker"],
        missing_required_skills=[],
        strengths=["Python and Docker experience is described."],
        gaps=["Google Professional Machine Learning Engineer certification is not evidenced."],
        rationale="Cloud project work is present, but the required certification is not evidenced.",
    )
    assert evaluate_case(case, good)["passed"] is True

    unsupported = good.model_copy(
        update={"strengths": ["Holds Google Professional Machine Learning Engineer certification."]}
    )
    result = evaluate_case(case, unsupported)
    assert result["checks"][
        "not_positive_claimed:Google Professional Machine Learning Engineer certification"
    ] is False

    certified_rationale = good.model_copy(
        update={
            "rationale": (
                "The candidate is certified as a Google Professional Machine Learning Engineer."
            )
        }
    )
    assert evaluate_case(case, certified_rationale)["checks"][
        "not_positive_claimed:Google Professional Machine Learning Engineer certification"
    ] is False

    unsupported_rationale = good.model_copy(
        update={
            "rationale": (
                "The candidate holds the Google Professional Machine Learning Engineer certification."
            )
        }
    )
    result = evaluate_case(case, unsupported_rationale)
    assert result["checks"][
        "not_positive_claimed:Google Professional Machine Learning Engineer certification"
    ] is False


def test_unmentioned_certification_requires_qualified_evidence_language() -> None:
    case = next(case for case in load_cases() if case.id == "unsupported-cloud-qualification")
    result = valid_result(
        matched_skills=["Python", "Docker", "Vertex AI"],
        missing_required_skills=[],
        missing_preferred_skills=[],
        strengths=["Python, Docker, and Vertex AI evidence is present."],
        gaps=[
            "The required Google Professional Machine Learning Engineer certification is not evidenced in the resume."
        ],
        rationale=(
            "Cloud project experience is documented, but the supplied resume does not establish "
            "whether the candidate holds the Google Professional Machine Learning Engineer certification."
        ),
    )

    evaluation = evaluate_case(case, result)
    assert evaluation["checks"][
        "not_evidenced:Google Professional Machine Learning Engineer certification"
    ] is True
    assert evaluation["checks"][
        "not_asserted_absent:Google Professional Machine Learning Engineer certification"
    ] is True

    lacks_evidence = result.model_copy(
        update={
            "rationale": (
                "The supplied resume lacks evidence of whether the candidate holds the Google Professional "
                "Machine Learning Engineer certification."
            )
        }
    )
    evidence_evaluation = evaluate_case(case, lacks_evidence)
    assert evidence_evaluation["checks"][
        "not_evidenced:Google Professional Machine Learning Engineer certification"
    ] is True
    assert evidence_evaluation["checks"][
        "not_asserted_absent:Google Professional Machine Learning Engineer certification"
    ] is True

    unrelated_positive = result.model_copy(
        update={
            "rationale": (
                "The candidate has strong Python experience, but the required Google Professional "
                "Machine Learning Engineer certification is not evidenced."
            )
        }
    )
    assert evaluate_case(case, unrelated_positive)["checks"][
        "not_positive_claimed:Google Professional Machine Learning Engineer certification"
    ] is True

    overstatement = result.model_copy(
        update={
            "gaps": [
                "The candidate lacks the required Google Professional Machine Learning Engineer certification."
            ],
            "rationale": "The candidate lacks the required Google Professional Machine Learning Engineer certification.",
        }
    )
    evaluation = evaluate_case(case, overstatement)
    assert evaluation["checks"][
        "not_evidenced:Google Professional Machine Learning Engineer certification"
    ] is False
    assert evaluation["checks"][
        "not_asserted_absent:Google Professional Machine Learning Engineer certification"
    ] is False


def test_explicitly_absent_certification_is_distinguished_from_omission() -> None:
    case = next(case for case in load_cases() if case.id == "certification-confirmed-absent")
    phrase = "AWS Certified Machine Learning Specialty certification"
    confirmed = valid_result(
        matched_skills=["Python", "Docker"],
        missing_required_skills=[],
        strengths=["Python and Docker experience is documented."],
        gaps=[f"The candidate explicitly states they do not hold the {phrase}."],
        rationale=f"The supplied resume explicitly states they do not hold the {phrase}.",
    )
    evaluation = evaluate_case(case, confirmed)
    assert evaluation["checks"][f"confirmed_absent:{phrase}"] is True

    merely_unmentioned = confirmed.model_copy(
        update={
            "gaps": [f"The {phrase} is not evidenced in the resume."],
            "rationale": f"The resume does not mention whether the candidate holds the {phrase}.",
        }
    )
    assert evaluate_case(case, merely_unmentioned)["checks"][
        f"confirmed_absent:{phrase}"
    ] is False


def test_preferred_skill_absence_is_not_reported_as_confirmed_inability() -> None:
    case = next(case for case in load_cases() if case.id == "preferred-skill-not-evidenced")
    result = valid_result(
        matched_skills=["Python"],
        missing_required_skills=[],
        missing_preferred_skills=["Terraform"],
        strengths=["Python API experience is documented."],
        gaps=["Terraform is preferred but not mentioned in the supplied resume."],
        rationale="Terraform is not evidenced; the resume does not establish hands-on use.",
    )
    evaluation = evaluate_case(case, result)
    assert evaluation["passed"] is True
    assert evaluation["checks"]["missing_preferred:Terraform"] is True
    assert evaluation["checks"]["not_evidenced:Terraform"] is True
    assert evaluation["checks"]["not_asserted_absent:Terraform"] is True

    wrongly_confirmed = result.model_copy(
        update={"gaps": ["The candidate lacks Terraform and cannot use it."]}
    )
    assert evaluate_case(case, wrongly_confirmed)["checks"][
        "not_asserted_absent:Terraform"
    ] is False

    misclassified = evaluate_case(
        case,
        result.model_copy(update={"missing_required_skills": ["Terraform"]}),
    )
    assert misclassified["checks"]["not_missing_required:Terraform"] is False
    assert misclassified["passed"] is False


@pytest.mark.parametrize(
    ("required_gap", "expected_not_missing"),
    [
        ("Terraform", False),
        ("Terraform experience", False),
        ("Terraform-related experience", False),
        ("Terraformer experience", True),
        ("IaC platform experience", True),
    ],
)
def test_preferred_exclusion_uses_skill_token_boundaries(
    required_gap: str, expected_not_missing: bool
) -> None:
    case = next(case for case in load_cases() if case.id == "preferred-skill-not-evidenced")
    result = valid_result(
        matched_skills=["Python"],
        missing_required_skills=[required_gap],
        missing_preferred_skills=["Terraform"],
        gaps=["Terraform is preferred but not mentioned in the supplied resume."],
    )

    evaluation = evaluate_case(case, result)

    assert evaluation["checks"]["not_missing_required:Terraform"] is expected_not_missing


def test_gpu_inference_is_transferable_but_does_not_satisfy_training_gap() -> None:
    case = next(case for case in load_cases() if case.id == "gpu-inference-without-training")
    assert "GPU inference" in case.candidate.resume_content
    assert "does not describe model training" in case.candidate.resume_content.casefold()

    result = valid_result(
        matched_skills=["Python", "PyTorch", "CUDA", "GPU inference optimization"],
        missing_required_skills=[],
        strengths=[
            "GPU inference optimization is relevant to this role and provides "
            "transferable experience for accelerated model systems."
        ],
        gaps=["Production GPU training experience is not evidenced."],
        rationale=(
            "Production GPU training experience is not evidenced, while GPU "
            "inference experience is relevant transferable background."
        ),
    )

    evaluation = evaluate_case(case, result)
    assert evaluation["passed"] is True
    assert evaluation["checks"]["transferable_acknowledged:GPU inference"] is True
    assert evaluation["checks"]["gap_identified:production GPU training experience"] is True
    assert evaluation["checks"]["not_positive_claimed:GPU training"] is True
    assert evaluation["checks"][
        "not_evidenced:production GPU training experience"
    ] is True

    no_transfer = evaluate_case(
        case,
        result.model_copy(
            update={
                "strengths": [
                    "GPU inference optimization is not relevant to the training requirement."
                ]
            }
        ),
    )
    assert no_transfer["checks"]["transferable_acknowledged:GPU inference"] is False

    unrelated_transfer = evaluate_case(
        case,
        result.model_copy(
            update={
                "strengths": [
                    "GPU inference is not relevant to training; Kubernetes "
                    "operations are transferable."
                ]
            }
        ),
    )
    assert unrelated_transfer["checks"]["transferable_acknowledged:GPU inference"] is False

    conflated = evaluate_case(
        case,
        result.model_copy(
            update={
                "strengths": [
                    "GPU inference optimization is relevant and demonstrates GPU training."
                ]
            }
        ),
    )
    assert conflated["checks"]["not_positive_claimed:GPU training"] is False


def test_negated_training_contrast_is_not_a_positive_training_claim() -> None:
    case = next(case for case in load_cases() if case.id == "gpu-inference-without-training")
    result = valid_result(
        matched_skills=["Python", "PyTorch", "CUDA", "GPU inference optimization"],
        missing_required_skills=[],
        strengths=["GPU inference is relevant, but it is not GPU training."],
        gaps=["Production GPU training experience is not evidenced."],
        rationale="Production GPU training is not evidenced in the supplied resume.",
    )

    evaluation = evaluate_case(case, result)

    assert evaluation["checks"]["not_positive_claimed:GPU training"] is True
    assert evaluation["checks"]["transferable_acknowledged:GPU inference"] is True


def test_total_tenure_does_not_satisfy_explicit_relevant_experience_requirement() -> None:
    case = next(case for case in load_cases() if case.id == "total-years-without-relevant-experience")
    result = valid_result(
        matched_skills=[],
        missing_required_skills=[],
        experience_match=MatchSignal.mismatched,
        gaps=["No relevant ML platform engineering experience is evidenced."],
        rationale=(
            "Eight years of accounting tenure are documented, but no relevant ML platform engineering experience is shown."
        ),
    )
    evaluation = evaluate_case(case, result)
    assert evaluation["checks"]["signal_mismatched:experience_match"] is True
    assert evaluation["checks"]["gap_identified:relevant ML platform engineering experience"] is True

    total_years_only = result.model_copy(update={"experience_match": MatchSignal.matched})
    assert evaluate_case(case, total_years_only)["checks"][
        "signal_mismatched:experience_match"
    ] is False


def test_missing_evidence_case_requires_unclear_signals() -> None:
    case = next(
        case for case in load_cases() if case.id == "missing-education-and-experience-evidence"
    )
    assert evaluate_case(
        case,
        valid_result(
            verdict=MatchVerdict.good_match,
            matched_skills=["Python", "Hugging Face Transformers"],
            missing_required_skills=[],
            experience_match=MatchSignal.unclear,
            education_match=MatchSignal.unclear,
            seniority_match=MatchSignal.unclear,
        ),
    )["passed"] is True
    assert evaluate_case(
        case,
        valid_result(
            matched_skills=["Python", "Hugging Face Transformers"],
            missing_required_skills=[],
            experience_match=MatchSignal.matched,
            education_match=MatchSignal.mismatched,
            seniority_match=MatchSignal.unclear,
        ),
    )["passed"] is False


def test_ranking_checks_only_declared_pairwise_constraints() -> None:
    cases = load_cases()
    outputs = {
        "strong-retrieval-engineer": valid_result(
            score=91,
            verdict=MatchVerdict.strong_match,
            matched_skills=["Python", "PyTorch", "Hugging Face Transformers"],
            missing_required_skills=[],
            education_match=MatchSignal.unclear,
            gaps=[],
            rationale="All core requirements are evidenced.",
        ),
        "partial-data-platform": valid_result(
            score=67,
            strengths=[
                "Operating virtual machines is relevant transferable experience "
                "for containerized services."
            ],
        ),
        "missing-essential-ml": valid_result(
            score=39,
            verdict=MatchVerdict.poor_match,
            matched_skills=["Python"],
            missing_required_skills=["PyTorch", "CUDA", "distributed training"],
            experience_match=MatchSignal.matched,
            strengths=["Python is supported by the resume."],
            gaps=[
                "PyTorch is not evidenced.",
                "CUDA is not evidenced.",
                "Distributed training is not evidenced.",
                "Production GPU training experience is not evidenced.",
            ],
            rationale="Only Python is supported among the central requirements.",
        ),
        "unrelated-accounting": valid_result(
            score=12,
            verdict=MatchVerdict.poor_match,
            matched_skills=[],
            missing_required_skills=["Python", "PyTorch", "model deployment"],
            strengths=[],
            gaps=[
                "Python is not evidenced.",
                "PyTorch is not evidenced.",
                "Model deployment is not evidenced.",
            ],
            rationale="The supplied experience concerns accounting, not ML infrastructure.",
        ),
        "unsupported-cloud-qualification": valid_result(
            score=61,
            verdict=MatchVerdict.partial_match,
            matched_skills=["Python", "Docker"],
            missing_required_skills=[],
            strengths=["Python and Docker are supported."],
            gaps=["Google Professional Machine Learning Engineer certification is not evidenced."],
            rationale="The resume does not establish the required certification.",
        ),
        "missing-education-and-experience-evidence": valid_result(
            score=74,
            verdict=MatchVerdict.good_match,
            matched_skills=["Python", "Hugging Face Transformers"],
            missing_required_skills=[],
            experience_match=MatchSignal.unclear,
            education_match=MatchSignal.unclear,
            seniority_match=MatchSignal.unclear,
            strengths=["Python and Transformers experience is supported."],
            gaps=["Education and employment dates are not provided."],
            rationale="Relevant skills are shown; education and experience duration remain unclear.",
        ),
        "certification-confirmed-absent": valid_result(
            score=60,
            matched_skills=["Python", "Docker"],
            missing_required_skills=[],
            strengths=["Python and Docker experience is documented."],
            gaps=[
                "The candidate explicitly states they do not hold the AWS Certified Machine Learning Specialty certification."
            ],
            rationale=(
                "The resume explicitly states they do not hold the AWS Certified Machine Learning Specialty certification."
            ),
        ),
        "preferred-skill-not-evidenced": valid_result(
            score=72,
            matched_skills=["Python"],
            missing_required_skills=[],
            missing_preferred_skills=["Terraform"],
            strengths=["Python API experience is documented."],
            gaps=["Terraform is preferred but not mentioned in the supplied resume."],
            rationale="Terraform is not evidenced in the supplied resume.",
        ),
        "total-years-without-relevant-experience": valid_result(
            score=22,
            matched_skills=[],
            missing_required_skills=[],
            experience_match=MatchSignal.mismatched,
            gaps=["No relevant ML platform engineering experience is evidenced."],
            rationale=(
                "Eight years of accounting tenure are documented, but no relevant ML platform engineering experience is shown."
            ),
        ),
        "gpu-inference-without-training": valid_result(
            matched_skills=["Python", "PyTorch", "CUDA", "GPU inference optimization"],
            missing_required_skills=[],
            strengths=[
                "GPU inference optimization is relevant to this role and offers "
                "transferable experience for accelerated model systems."
            ],
            gaps=["Production GPU training experience is not evidenced."],
            rationale=(
                "Production GPU training experience is not evidenced, while GPU "
                "inference experience is relevant transferable background."
            ),
        ),
    }

    evaluation = evaluate_benchmark(cases, outputs)
    checked = [row for row in evaluation["ranking_checks"] if row["status"] == "checked"]
    assert evaluation["complete"] is True
    assert checked
    assert all(row["passed"] for row in checked)
    assert evaluation["passed"] is True

    outputs["partial-data-platform"] = valid_result(score=20)
    assert evaluate_benchmark(cases, outputs)["passed"] is False


def test_missing_case_makes_benchmark_incomplete() -> None:
    cases = load_cases()
    evaluation = evaluate_benchmark(cases, {})

    assert evaluation["complete"] is False
    assert evaluation["passed"] is False
    assert evaluation["missing_case_ids"] == [case.id for case in cases]
    assert all(row["status"] == "not_run" for row in evaluation["ranking_checks"])


def test_case_expectations_do_not_add_score_cutoffs() -> None:
    cases = load_cases()
    assert all(not hasattr(case.expectations, "minimum_score") for case in cases)


def test_cli_requires_explicit_provider_call_acknowledgement(capsys) -> None:
    from evaluation.run_job_matching import main

    with pytest.raises(SystemExit) as exc:
        main([])

    assert exc.value.code == 2
    assert "--confirm-provider-calls" in capsys.readouterr().err

    with pytest.raises(SystemExit) as selected_exc:
        main(["--case-id", "strong-retrieval-engineer"])

    assert selected_exc.value.code == 2
    assert "--confirm-provider-calls" in capsys.readouterr().err


def test_cli_help_describes_the_configured_case_set(capsys) -> None:
    from evaluation.run_job_matching import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    help_text = " ".join(capsys.readouterr().out.split())
    assert exc.value.code == 0
    assert "Repeat --case-id" in help_text
    assert "Without this option, all configured cases run" in help_text
    assert "six-case" not in help_text


@pytest.mark.parametrize(
    "selected_ids",
    [
        ["missing-essential-ml"],
        ["unrelated-accounting", "strong-retrieval-engineer"],
    ],
)
def test_runner_executes_only_requested_cases_in_requested_order(
    monkeypatch, tmp_path, selected_ids
) -> None:
    from types import SimpleNamespace

    import evaluation.run_job_matching as runner

    class OfflineClient:
        def __init__(self, *_args, **_kwargs):
            self.calls = 0

        def generate_structured(self, **_kwargs):
            self.calls += 1
            return valid_result()

    client = OfflineClient()
    monkeypatch.setattr(
        runner,
        "get_settings",
        lambda: SimpleNamespace(
            gemini_api_key="synthetic-test-key", gemini_model="synthetic-model"
        ),
    )
    monkeypatch.setattr(runner, "GeminiClient", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(runner, "DEFAULT_OUTPUT_DIR", tmp_path)

    argv = ["--confirm-provider-calls"]
    for case_id in selected_ids:
        argv.extend(["--case-id", case_id])

    assert runner.main(argv) in {0, 1}
    artifact = json.loads(
        next(tmp_path.glob("job-matching-*.json")).read_text(encoding="utf-8")
    )
    assert client.calls == len(selected_ids)
    assert [row["case_id"] for row in artifact["results"]] == selected_ids
    assert artifact["deterministic_evaluation"]["complete"] is True
    assert artifact["prompt_version"] == PROMPT_VERSION == "job-matching-v4"
    assert all(row["prompt_version"] == PROMPT_VERSION for row in artifact["results"])


def test_runner_defaults_to_all_configured_cases(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    import evaluation.run_job_matching as runner

    class OfflineClient:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def generate_structured(self, **_kwargs):
            self.calls += 1
            return valid_result()

    client = OfflineClient()
    monkeypatch.setattr(
        runner,
        "get_settings",
        lambda: SimpleNamespace(
            gemini_api_key="synthetic-test-key", gemini_model="synthetic-model"
        ),
    )
    monkeypatch.setattr(runner, "GeminiClient", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(runner, "DEFAULT_OUTPUT_DIR", tmp_path)

    assert runner.main(["--confirm-provider-calls"]) in {0, 1}
    artifact = json.loads(
        next(tmp_path.glob("job-matching-*.json")).read_text(encoding="utf-8")
    )
    expected_ids = [case.id for case in load_cases()]
    assert client.calls == len(expected_ids) == 10
    assert [row["case_id"] for row in artifact["results"]] == expected_ids


@pytest.mark.parametrize(
    ("requested_ids", "message"),
    [
        (["unknown-case-secret-marker"], "not configured benchmark cases"),
        (["strong-retrieval-engineer", "strong-retrieval-engineer"], "Duplicate"),
    ],
)
def test_invalid_case_selection_stops_before_setup_or_provider_calls(
    monkeypatch, tmp_path, capsys, requested_ids, message
) -> None:
    import evaluation.run_job_matching as runner

    monkeypatch.setattr(
        runner,
        "get_settings",
        lambda: pytest.fail("settings must not be read for invalid case selection"),
    )
    monkeypatch.setattr(
        runner,
        "GeminiClient",
        lambda *_args, **_kwargs: pytest.fail("client must not be created"),
    )
    monkeypatch.setattr(
        runner,
        "_reserve_result_file",
        lambda *_args: pytest.fail("artifact must not be reserved"),
    )

    argv = ["--confirm-provider-calls"]
    for case_id in requested_ids:
        argv.extend(["--case-id", case_id])
    with pytest.raises(SystemExit) as exc:
        runner.main(argv)

    error = capsys.readouterr().err
    assert exc.value.code == 2
    assert message in error
    assert "unknown-case-secret-marker" not in error
    assert not list(tmp_path.iterdir())


def test_runner_stops_after_first_selected_case_failure_without_retry(
    monkeypatch, tmp_path
) -> None:
    from types import SimpleNamespace

    import evaluation.run_job_matching as runner
    from app.clients.gemini import GeminiError

    class FailOnSecondCallClient:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def generate_structured(self, **_kwargs):
            self.calls += 1
            if self.calls == 2:
                raise GeminiError(
                    error_category="provider_api_error",
                    http_status=503,
                    provider_status="UNAVAILABLE",
                )
            return valid_result()

    client = FailOnSecondCallClient()
    monkeypatch.setattr(
        runner,
        "get_settings",
        lambda: SimpleNamespace(
            gemini_api_key="synthetic-test-key", gemini_model="synthetic-model"
        ),
    )
    monkeypatch.setattr(runner, "GeminiClient", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(runner, "DEFAULT_OUTPUT_DIR", tmp_path)

    assert runner.main(
        [
            "--confirm-provider-calls",
            "--case-id",
            "strong-retrieval-engineer",
            "--case-id",
            "partial-data-platform",
            "--case-id",
            "missing-essential-ml",
        ]
    ) == 1
    artifact = json.loads(
        next(tmp_path.glob("job-matching-*.json")).read_text(encoding="utf-8")
    )
    assert client.calls == 2
    assert [row["case_id"] for row in artifact["results"]] == [
        "strong-retrieval-engineer",
        "partial-data-platform",
    ]
    assert artifact["status"] == "failed"
    assert artifact["failure"]["http_status"] == 503


def test_runner_reports_missing_key_without_creating_client(monkeypatch, capsys) -> None:
    from types import SimpleNamespace

    import evaluation.run_job_matching as runner

    monkeypatch.setattr(
        runner,
        "get_settings",
        lambda: SimpleNamespace(gemini_api_key="  ", gemini_model="synthetic-model"),
    )
    monkeypatch.setattr(
        runner,
        "GeminiClient",
        lambda *args, **kwargs: pytest.fail("client must not be created without a key"),
    )

    assert runner.main(["--confirm-provider-calls"]) == 2
    assert "CAREER_COPILOT_GEMINI_API_KEY" in capsys.readouterr().err


def test_runner_does_not_print_settings_exception_details(monkeypatch, capsys) -> None:
    import evaluation.run_job_matching as runner

    def fail_settings():
        raise ValueError("private configuration value")

    monkeypatch.setattr(runner, "get_settings", fail_settings)
    monkeypatch.setattr(
        runner,
        "GeminiClient",
        lambda *_args, **_kwargs: pytest.fail("client must not be created"),
    )

    assert runner.main(["--confirm-provider-calls"]) == 2
    error = capsys.readouterr().err
    assert "ValueError" in error
    assert "private configuration value" not in error


def test_runner_artifact_records_only_safe_gemini_error_metadata(
    monkeypatch, tmp_path, capsys
) -> None:
    from types import SimpleNamespace

    from google.genai.errors import APIError

    import evaluation.run_job_matching as runner
    from app.clients.gemini import GeminiClient

    api_key = "synthetic-api-key-secret"
    provider_message = "synthetic-provider-message-secret"
    response_body = "synthetic-response-body-secret"

    class FailingApi:
        calls = 0

        def generate_content(self, **_kwargs):
            self.calls += 1
            raise APIError(
                429,
                {
                    "error": {
                        "status": "RESOURCE_EXHAUSTED",
                        "message": provider_message,
                        "debug": response_body,
                        "credential": api_key,
                    }
                },
            )

    api = FailingApi()

    def build_offline_client(model, *, api_key: str):
        assert api_key == "synthetic-api-key-secret"
        return GeminiClient(model, api=api)

    monkeypatch.setattr(
        runner,
        "get_settings",
        lambda: SimpleNamespace(gemini_api_key=api_key, gemini_model="synthetic-model"),
    )
    monkeypatch.setattr(runner, "GeminiClient", build_offline_client)
    monkeypatch.setattr(runner, "DEFAULT_OUTPUT_DIR", tmp_path)

    assert runner.main(["--confirm-provider-calls"]) == 1
    assert api.calls == 1

    captured = capsys.readouterr()
    artifact_path = next(tmp_path.glob("job-matching-*.json"))
    artifact_text = artifact_path.read_text(encoding="utf-8")
    artifact = json.loads(artifact_text)
    failure = artifact["failure"]
    row = artifact["results"][0]

    assert artifact["status"] == "failed"
    assert failure["case_id"] == "strong-retrieval-engineer"
    assert failure["error_category"] == "provider_api_error"
    assert failure["http_status"] == 429
    assert failure["provider_status"] == "RESOURCE_EXHAUSTED"
    assert row["error_category"] == failure["error_category"]
    assert row["http_status"] == failure["http_status"]
    assert row["provider_status"] == failure["provider_status"]
    assert all(
        secret not in artifact_text + captured.out + captured.err
        for secret in (api_key, provider_message, response_body)
    )


def test_output_reservation_failure_stops_before_provider_generation(
    monkeypatch, capsys
) -> None:
    from types import SimpleNamespace

    import evaluation.run_job_matching as runner

    class NeverCalledClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def generate_structured(self, **kwargs):
            pytest.fail("provider call must not start without an output artifact")

    def fail_reservation(*_args):
        raise PermissionError("private detail")

    monkeypatch.setattr(
        runner,
        "get_settings",
        lambda: SimpleNamespace(gemini_api_key="synthetic-test-key", gemini_model="synthetic-model"),
    )
    monkeypatch.setattr(runner, "GeminiClient", NeverCalledClient)
    monkeypatch.setattr(runner, "_reserve_result_file", fail_reservation)

    assert runner.main(["--confirm-provider-calls"]) == 2
    error = capsys.readouterr().err
    assert "no provider calls were made" in error
    assert "private detail" not in error


def test_result_file_is_reserved_before_use_and_never_overwrites(tmp_path) -> None:
    from datetime import datetime, timezone

    from evaluation.run_job_matching import (
        _reserve_result_file,
        _write_reserved_result,
    )

    started = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    first = _reserve_result_file(tmp_path, started)
    assert json.loads(first.read_text(encoding="utf-8"))["status"] == "running"
    _write_reserved_result(first, {"run": 1})
    assert json.loads(first.read_text(encoding="utf-8")) == {"run": 1}

    with pytest.raises(FileExistsError):
        _reserve_result_file(tmp_path, started)
    assert json.loads(first.read_text(encoding="utf-8")) == {"run": 1}
