from __future__ import annotations

import json
import socket

import pytest

from evaluation.compare_job_matching import (
    ComparisonInputError,
    compare_artifacts,
    main,
)
from evaluation.job_matching import load_cases

STRONG = "strong-retrieval-engineer"
PARTIAL = "partial-data-platform"
ESSENTIAL = "missing-essential-ml"
UNRELATED = "unrelated-accounting"


def _row(
    case_id: str,
    *,
    score: object = 70,
    verdict: str | None = "partial_match",
    confidence: object = 0.8,
    status: str = "succeeded",
    signals: dict[str, str] | None = None,
    prompt_version: str = "job-matching-v2",
    extra_output: dict | None = None,
) -> dict:
    row = {"case_id": case_id, "status": status, "prompt_version": prompt_version}
    if status == "succeeded":
        output = {
            "score": score,
            "verdict": verdict,
            "confidence": confidence,
            "experience_match": "matched",
            "education_match": "unclear",
        }
        output.update(signals or {})
        output.update(extra_output or {})
        row["structured_output"] = output
    return row


def _evaluation(
    *,
    missing: list[str] | None = None,
    case_results: list[dict] | None = None,
    ranking_checks: list[dict] | None = None,
) -> dict:
    return {
        "complete": not missing,
        "passed": not missing,
        "missing_case_ids": missing or [],
        "case_results": case_results or [],
        "ranking_checks": ranking_checks or [],
    }


def _artifact(
    rows: list[dict],
    *,
    model: str | None = "gemini-3.8-flash",
    prompt_version: str | None = "job-matching-v2",
    status: str = "completed",
    evaluation: dict | None = None,
    failure: dict | None = None,
) -> dict:
    return {
        "run_started_at": "2026-10-18T20:00:00+00:00",
        "run_finished_at": "2026-10-18T20:01:00+00:00",
        "model": model,
        "prompt_version": prompt_version,
        "status": status,
        "failure": failure,
        "results": rows,
        "deterministic_evaluation": evaluation
        if evaluation is not None
        else _evaluation(),
    }


def _write(path, artifact: dict) -> None:
    path.write_text(json.dumps(artifact), encoding="utf-8")


def test_compare_matches_exact_case_ids_when_artifact_order_differs(tmp_path) -> None:
    baseline = tmp_path / "baseline.json"
    comparison = tmp_path / "comparison.json"
    _write(
        baseline,
        _artifact([_row(STRONG, score=70), _row(PARTIAL, score=40)]),
    )
    _write(
        comparison,
        _artifact([_row(PARTIAL, score=45), _row(STRONG, score=75)]),
    )

    report = compare_artifacts(baseline, comparison)

    assert "strong-retrieval-engineer" in report
    assert "partial-data-platform" in report
    assert "observed score delta (comparison - baseline): +5" in report
    assert report.count("observed score delta (comparison - baseline): +5") == 2
    assert (
        "prompt=job-matching-v2; model=gemini-3.8-flash; "
        "verdict=partial_match; score=70"
    ) in report
    assert (
        "prompt=job-matching-v2; model=gemini-3.8-flash; "
        "verdict=partial_match; score=45"
    ) in report
    assert "confidence=0.8" in report
    assert "signals=experience:matched,education:unclear" in report


def test_compare_reports_baseline_only_comparison_only_and_unattempted_cases(
    tmp_path,
) -> None:
    baseline = tmp_path / "baseline.json"
    comparison = tmp_path / "comparison.json"
    _write(
        baseline,
        _artifact(
            [_row(STRONG), _row(PARTIAL, status="failed")],
            status="failed",
            evaluation=_evaluation(
                missing=[ESSENTIAL],
                case_results=[{"case_id": STRONG, "passed": True, "checks": {"one": True}}],
            ),
            failure={
                "case_id": PARTIAL,
                "error_category": "provider_api_error",
                "http_status": 503,
                "provider_status": "UNAVAILABLE",
            },
        ),
    )
    _write(
        comparison,
        _artifact(
            [_row(STRONG), _row(ESSENTIAL), _row(UNRELATED)],
            evaluation=_evaluation(
                case_results=[
                    {"case_id": STRONG, "passed": True, "checks": {"one": True}},
                    {"case_id": ESSENTIAL, "passed": False, "checks": {"one": False}},
                    {"case_id": UNRELATED, "passed": True, "checks": {"one": True}},
                ]
            ),
        ),
    )

    report = compare_artifacts(baseline, comparison)

    assert "partial-data-platform (baseline only" in report
    assert "unrelated-accounting (comparison only" in report
    assert "baseline: status=unattempted" in report
    assert "status=failed (execution; no match verdict); prompt=job-matching-v2" in report
    assert "category=provider_api_error, HTTP=503, provider=UNAVAILABLE" in report
    assert "no match output" in report


def test_skipped_and_running_rows_are_not_matching_verdicts(tmp_path) -> None:
    baseline = tmp_path / "baseline.json"
    comparison = tmp_path / "comparison.json"
    _write(baseline, _artifact([_row(STRONG, status="skipped")], status="failed"))
    _write(comparison, {"status": "running", "results": []})

    report = compare_artifacts(baseline, comparison)

    assert "status=skipped; prompt=job-matching-v2" in report
    assert "status=running" in report
    assert "no match output" in report
    assert "verdict=poor_match" not in report


def test_compare_shows_prompt_model_and_ranking_status_differences(tmp_path) -> None:
    baseline = tmp_path / "baseline.json"
    comparison = tmp_path / "comparison.json"
    not_run = {
        "higher_case": STRONG,
        "lower_case": PARTIAL,
        "status": "not_run",
        "passed": False,
    }
    _write(
        baseline,
        _artifact(
            [_row(STRONG)],
            prompt_version="job-matching-v2",
            evaluation=_evaluation(ranking_checks=[not_run]),
        ),
    )
    _write(
        comparison,
        _artifact(
            [_row(STRONG, prompt_version="job-matching-v3")],
            model="gemini-3.7-flash",
            prompt_version="job-matching-v3",
            evaluation=_evaluation(
                ranking_checks=[{**not_run, "status": "checked", "passed": True}]
            ),
        ),
    )

    report = compare_artifacts(baseline, comparison)

    assert "WARNING: prompt versions differ" in report
    assert "WARNING: model identifiers differ" in report
    assert "Baseline automatic ranking checks: passed=0, failed=0, not_run=1" in report
    assert "Comparison automatic ranking checks: passed=1, failed=0, not_run=0" in report
    assert "strong-retrieval-engineer above partial-data-platform: not_run -> passed" in report
    assert "not calibrated probability" in report.casefold()


def test_missing_optional_fields_are_reported_without_inventing_values(tmp_path) -> None:
    baseline = tmp_path / "baseline.json"
    comparison = tmp_path / "comparison.json"
    _write(
        baseline,
        _artifact(
            [{"case_id": STRONG, "status": "succeeded", "structured_output": {"score": 52}}],
            model=None,
            prompt_version=None,
            evaluation=None,
        ),
    )
    _write(comparison, _artifact([_row(STRONG, score=None)]))

    report = compare_artifacts(baseline, comparison)

    assert "prompt=not recorded; model=not recorded" in report
    assert "verdict=not recorded" in report
    assert "confidence=not recorded" in report
    assert "signals=not recorded" in report
    assert "observed score delta: unavailable" in report
    assert "automatic checks=not recorded" in report


@pytest.mark.parametrize(
    "bad_score",
    ["seventy", True, 101, -1, float("inf")],
)
def test_invalid_score_values_are_rejected_safely(tmp_path, bad_score) -> None:
    baseline = tmp_path / "baseline.json"
    comparison = tmp_path / "comparison.json"
    _write(baseline, _artifact([_row(STRONG, score=bad_score)]))
    _write(comparison, _artifact([_row(STRONG)]))

    with pytest.raises(ComparisonInputError, match="baseline artifact is malformed"):
        compare_artifacts(baseline, comparison)


@pytest.mark.parametrize(
    "contents",
    [
        "not json",
        json.dumps({"format": "csv", "rows": []}),
        json.dumps({"status": []}),
        "[" * 1100 + "]" * 1100,
    ],
)
def test_malformed_or_unsupported_artifacts_fail_with_sanitized_errors(
    tmp_path, contents, capsys
) -> None:
    baseline = tmp_path / "baseline-secret-path.json"
    comparison = tmp_path / "comparison.json"
    baseline.write_text(contents, encoding="utf-8")
    _write(comparison, _artifact([_row(STRONG)]))

    with pytest.raises(ComparisonInputError) as exc:
        compare_artifacts(baseline, comparison)

    assert "baseline artifact is malformed" in str(exc.value)
    assert "baseline-secret-path" not in str(exc.value)
    assert main(["--baseline", str(baseline), "--comparison", str(comparison)]) == 2
    assert "baseline-secret-path" not in capsys.readouterr().err


def test_report_ignores_raw_provider_text_and_other_untrusted_fields(tmp_path) -> None:
    baseline = tmp_path / "baseline.json"
    comparison = tmp_path / "comparison.json"
    secret = "synthetic-provider-body-secret"
    credential = "AIzaSyntheticCredentialSecret"
    _write(
        baseline,
        _artifact(
            [_row(STRONG, status="failed")],
            model=credential,
            prompt_version=credential,
            status="failed",
            failure={
                "case_id": STRONG,
                "error_category": "provider_api_error",
                "http_status": 429,
                "provider_status": "RESOURCE_EXHAUSTED",
                "raw_body": secret,
                "message": secret,
            },
        ),
    )
    _write(
        comparison,
        _artifact(
            [_row(STRONG, extra_output={"rationale": secret, "strengths": [secret]})]
        ),
    )

    report = compare_artifacts(baseline, comparison)

    assert "category=provider_api_error, HTTP=429, provider=RESOURCE_EXHAUSTED" in report
    assert secret not in report
    assert "raw_body" not in report
    assert "message=" not in report
    assert credential not in report


def test_comparison_cli_is_offline_and_requires_two_explicit_paths(
    tmp_path, monkeypatch, capsys
) -> None:
    baseline = tmp_path / "baseline.json"
    comparison = tmp_path / "comparison.json"
    _write(baseline, _artifact([_row(STRONG)]))
    _write(comparison, _artifact([_row(STRONG, score=75)]))
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: pytest.fail("comparison must not open a network socket"),
    )

    assert main(["--baseline", str(baseline), "--comparison", str(comparison)]) == 0
    output = capsys.readouterr().out
    assert "offline" in output
    assert "observed score delta (comparison - baseline): +5" in output

    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as help_exc:
        main(["--help"])
    assert help_exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--baseline PATH" in help_text
    assert "--comparison PATH" in help_text


def test_comparison_rejects_case_ids_outside_the_current_benchmark(tmp_path) -> None:
    baseline = tmp_path / "baseline.json"
    comparison = tmp_path / "comparison.json"
    known = {case.id for case in load_cases()}
    secret_id = "not-a-benchmark-secret-marker"
    assert secret_id not in known
    _write(baseline, _artifact([_row(secret_id)]))
    _write(comparison, _artifact([_row(STRONG)]))

    with pytest.raises(ComparisonInputError) as exc:
        compare_artifacts(baseline, comparison)

    assert secret_id not in str(exc.value)
