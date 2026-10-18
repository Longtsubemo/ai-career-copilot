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


def test_fixture_has_six_distinct_synthetic_scenarios() -> None:
    cases = load_cases()

    assert len(cases) == 6
    assert len({case.id for case in cases}) == len(cases)
    assert {case.scenario for case in cases} == {
        "strong_match",
        "partial_match",
        "missing_essential_skills",
        "unrelated_role",
        "unsupported_qualification",
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
        ),
    )["passed"] is True
    assert evaluate_case(
        case,
        valid_result(
            matched_skills=["Python", "Hugging Face Transformers"],
            missing_required_skills=[],
            experience_match=MatchSignal.matched,
            education_match=MatchSignal.mismatched,
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
            strengths=["Python and Transformers experience is supported."],
            gaps=["Education and employment dates are not provided."],
            rationale="Relevant skills are shown; education and experience duration remain unclear.",
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
