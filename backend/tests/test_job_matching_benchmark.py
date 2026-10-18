from __future__ import annotations

import json

import pytest

from app.agents.job_matching.contract import MatchResult, MatchSignal, MatchVerdict
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


def test_fixture_has_nine_distinct_synthetic_scenarios() -> None:
    cases = load_cases()

    assert len(cases) == 9
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
    }
    assert all(case.candidate.name and case.candidate.resume_content for case in cases)
    assert all("Fictional" in case.job.company_name for case in cases)


def test_benchmark_prompts_use_case_job_and_resume_evidence() -> None:
    case = next(case for case in load_cases() if case.id == "strong-retrieval-engineer")
    prompt = build_case_prompt(case)

    assert case.job.title in prompt
    assert case.candidate.resume_content in prompt
    assert "JOB EVIDENCE" in prompt
    assert "CANDIDATE EVIDENCE" in prompt


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

    passed = evaluate_case(case, valid_result())
    assert passed["passed"] is True
    assert passed["checks"]["missing_required:Kubernetes"] is True

    failed = evaluate_case(
        case,
        valid_result(
            missing_required_skills=[],
            matched_skills=["Python", "SQL", "Kubernetes"],
        ),
    )
    assert failed["passed"] is False


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
    assert evaluation["checks"]["missing_preferred:Terraform"] is True
    assert evaluation["checks"]["not_evidenced:Terraform"] is True
    assert evaluation["checks"]["not_asserted_absent:Terraform"] is True

    wrongly_confirmed = result.model_copy(
        update={"gaps": ["The candidate lacks Terraform and cannot use it."]}
    )
    assert evaluate_case(case, wrongly_confirmed)["checks"][
        "not_asserted_absent:Terraform"
    ] is False


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
            gaps=[],
            rationale="All core requirements are evidenced.",
        ),
        "partial-data-platform": valid_result(score=67),
        "missing-essential-ml": valid_result(
            score=39,
            verdict=MatchVerdict.poor_match,
            matched_skills=["Python"],
            missing_required_skills=["PyTorch", "CUDA", "distributed training"],
            strengths=["Python is supported by the resume."],
            gaps=["PyTorch, CUDA, and distributed training are not evidenced."],
            rationale="Only Python is supported among the central requirements.",
        ),
        "unrelated-accounting": valid_result(
            score=12,
            verdict=MatchVerdict.poor_match,
            matched_skills=[],
            missing_required_skills=["Python", "PyTorch", "model deployment"],
            strengths=[],
            gaps=["Python, PyTorch, and model deployment are not evidenced."],
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
