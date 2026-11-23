"""Compare existing synthetic job-matching evaluation artifacts offline."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from app.agents.job_matching.contract import MatchSignal, MatchVerdict
from evaluation.job_matching import load_cases

_MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
_RUN_STATUSES = frozenset({"completed", "failed", "running"})
_ROW_STATUSES = frozenset({"succeeded", "failed", "skipped", "not_run"})
_ERROR_CATEGORIES = frozenset(
    {
        "provider_api_error",
        "transport_error",
        "structured_output_error",
        "unknown_error",
    }
)
_PROVIDER_STATUSES = frozenset(
    {
        "ABORTED",
        "ALREADY_EXISTS",
        "CANCELLED",
        "DATA_LOSS",
        "DEADLINE_EXCEEDED",
        "FAILED_PRECONDITION",
        "INTERNAL",
        "INVALID_ARGUMENT",
        "NOT_FOUND",
        "OUT_OF_RANGE",
        "PERMISSION_DENIED",
        "RESOURCE_EXHAUSTED",
        "UNAUTHENTICATED",
        "UNAVAILABLE",
        "UNIMPLEMENTED",
        "UNKNOWN",
    }
)
_SIGNAL_FIELDS = (
    "experience_match",
    "education_match",
    "location_match",
    "seniority_match",
)
_MODEL_PATTERN = re.compile(r"gemini-[0-9]+(?:\.[0-9]+)*(?:-[a-z0-9]+)*\Z")
_PROMPT_PATTERN = re.compile(r"job-matching-v[0-9]+\Z")


class ComparisonInputError(ValueError):
    """An artifact cannot be safely interpreted by this comparison tool."""


def _invalid(label: str) -> ComparisonInputError:
    return ComparisonInputError(
        f"The {label} artifact is malformed or uses an unsupported evaluation format."
    )


def _safe_metadata_display(value: object, pattern: re.Pattern[str], missing: str) -> str:
    if value is None:
        return missing
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    return "[unrecognized]"


def _optional_number(
    value: object, *, minimum: float, maximum: float, label: str
) -> int | float | None:
    if value is None:
        return None
    if type(value) is int:
        if minimum <= value <= maximum:
            return value
        raise ValueError(label)
    if type(value) is not float or not math.isfinite(value):
        raise ValueError(label)
    if value < minimum or value > maximum:
        raise ValueError(label)
    return value


def _safe_diagnostics(value: object, label: str) -> dict[str, str | int | None] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid(label)

    category = value.get("error_category")
    if category is not None and not isinstance(category, str):
        raise _invalid(label)
    category = category if category in _ERROR_CATEGORIES else None

    status = value.get("http_status")
    if status is not None and (
        type(status) is not int or status < 100 or status > 599
    ):
        raise _invalid(label)

    provider_status = value.get("provider_status")
    if provider_status is not None and not isinstance(provider_status, str):
        raise _invalid(label)
    provider_status = (
        provider_status if provider_status in _PROVIDER_STATUSES else None
    )
    return {
        "error_category": category,
        "http_status": status,
        "provider_status": provider_status,
    }


def _project_output(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(label)

    output: dict[str, Any] = {}
    try:
        score = value.get("score")
        output["score"] = _optional_number(
            score, minimum=0, maximum=100, label=label
        )
        confidence = value.get("confidence")
        output["confidence"] = _optional_number(
            confidence, minimum=0, maximum=1, label=label
        )
    except ValueError:
        raise _invalid(label) from None

    verdict = value.get("verdict")
    if verdict is not None and (
        not isinstance(verdict, str)
        or verdict not in {item.value for item in MatchVerdict}
    ):
        raise _invalid(label)
    output["verdict"] = verdict

    signals: dict[str, str] = {}
    for field in _SIGNAL_FIELDS:
        signal = value.get(field)
        if signal is not None:
            if not isinstance(signal, str) or signal not in {
                item.value for item in MatchSignal
            }:
                raise _invalid(label)
            signals[field] = signal
    output["signals"] = signals
    return output


def _load_artifact(path: str | Path, label: str, known_case_ids: set[str]) -> dict[str, Any]:
    try:
        artifact_path = Path(path)
        if artifact_path.stat().st_size > _MAX_ARTIFACT_BYTES:
            raise _invalid(label)
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
    except ComparisonInputError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        RecursionError,
    ):
        raise _invalid(label) from None

    if (
        not isinstance(data, dict)
        or not isinstance(data.get("status"), str)
        or data["status"] not in _RUN_STATUSES
    ):
        raise _invalid(label)

    status = data["status"]
    raw_rows = data.get("results", [] if status == "running" else None)
    if not isinstance(raw_rows, list):
        raise _invalid(label)

    rows: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        if not isinstance(row, dict):
            raise _invalid(label)
        case_id = row.get("case_id")
        row_status = row.get("status")
        if (
            not isinstance(case_id, str)
            or case_id not in known_case_ids
            or not isinstance(row_status, str)
            or row_status not in _ROW_STATUSES
            or case_id in rows
        ):
            raise _invalid(label)

        output = None
        if "structured_output" in row:
            output = _project_output(row["structured_output"], label)
        elif row_status == "succeeded":
            raise _invalid(label)

        prompt = row.get("prompt_version", data.get("prompt_version"))
        if prompt is not None and not isinstance(prompt, str):
            raise _invalid(label)
        rows[case_id] = {
            "status": row_status,
            "prompt_raw": prompt,
            "output": output,
            "diagnostics": _safe_diagnostics(row, label),
        }

    raw_evaluation = data.get("deterministic_evaluation")
    if raw_evaluation is None:
        evaluation: dict[str, Any] = {
            "complete": None,
            "passed": None,
            "missing_case_ids": set(),
            "case_results": {},
            "ranking_checks": {},
        }
    elif not isinstance(raw_evaluation, dict):
        raise _invalid(label)
    else:
        complete = raw_evaluation.get("complete")
        passed = raw_evaluation.get("passed")
        if complete is not None and type(complete) is not bool:
            raise _invalid(label)
        if passed is not None and type(passed) is not bool:
            raise _invalid(label)

        missing_values = raw_evaluation.get("missing_case_ids", [])
        case_values = raw_evaluation.get("case_results", [])
        ranking_values = raw_evaluation.get("ranking_checks", [])
        if (
            not isinstance(missing_values, list)
            or not isinstance(case_values, list)
            or not isinstance(ranking_values, list)
        ):
            raise _invalid(label)
        missing_ids: set[str] = set()
        for case_id in missing_values:
            if (
                not isinstance(case_id, str)
                or case_id not in known_case_ids
                or case_id in missing_ids
            ):
                raise _invalid(label)
            missing_ids.add(case_id)

        case_results: dict[str, dict[str, Any]] = {}
        for case_result in case_values:
            if not isinstance(case_result, dict):
                raise _invalid(label)
            case_id = case_result.get("case_id")
            case_passed = case_result.get("passed")
            checks = case_result.get("checks")
            if (
                not isinstance(case_id, str)
                or case_id not in known_case_ids
                or case_id in case_results
                or type(case_passed) is not bool
            ):
                raise _invalid(label)
            if checks is not None and (
                not isinstance(checks, dict)
                or any(
                    not isinstance(key, str) or type(value) is not bool
                    for key, value in checks.items()
                )
            ):
                raise _invalid(label)
            case_results[case_id] = {"passed": case_passed, "checks": checks}

        ranking_checks: dict[tuple[str, str], str] = {}
        for check in ranking_values:
            if not isinstance(check, dict):
                raise _invalid(label)
            higher = check.get("higher_case")
            lower = check.get("lower_case")
            check_status = check.get("status")
            check_passed = check.get("passed")
            pair = (higher, lower)
            if (
                not isinstance(higher, str)
                or higher not in known_case_ids
                or not isinstance(lower, str)
                or lower not in known_case_ids
                or pair in ranking_checks
                or not isinstance(check_status, str)
                or check_status not in {"checked", "not_run"}
                or type(check_passed) is not bool
            ):
                raise _invalid(label)
            for score_field in ("higher_score", "lower_score"):
                if score_field in check:
                    try:
                        _optional_number(
                            check[score_field],
                            minimum=0,
                            maximum=100,
                            label=label,
                        )
                    except ValueError:
                        raise _invalid(label) from None
            if check_status == "not_run":
                ranking_checks[pair] = "not_run"
            elif check_passed:
                ranking_checks[pair] = "passed"
            else:
                ranking_checks[pair] = "failed"

        evaluation = {
            "complete": complete,
            "passed": passed,
            "missing_case_ids": missing_ids,
            "case_results": case_results,
            "ranking_checks": ranking_checks,
        }

    model = data.get("model")
    prompt = data.get("prompt_version")
    if model is not None and not isinstance(model, str):
        raise _invalid(label)
    if prompt is not None and not isinstance(prompt, str):
        raise _invalid(label)

    raw_failure = data.get("failure")
    failure_case_id = None
    if raw_failure is not None:
        if not isinstance(raw_failure, dict):
            raise _invalid(label)
        failure_case_id = raw_failure.get("case_id")
        if failure_case_id is not None and (
            not isinstance(failure_case_id, str)
            or failure_case_id not in known_case_ids
        ):
            raise _invalid(label)
    failure = _safe_diagnostics(raw_failure, label)
    return {
        "label": label,
        "status": status,
        "model_raw": model,
        "model": _safe_metadata_display(model, _MODEL_PATTERN, "not recorded"),
        "prompt_raw": prompt,
        "prompt": _safe_metadata_display(prompt, _PROMPT_PATTERN, "not recorded"),
        "failure": failure,
        "failure_case_id": failure_case_id,
        "rows": rows,
        "evaluation": evaluation,
    }


def _case_status(artifact: dict[str, Any], case_id: str) -> str | None:
    row = artifact["rows"].get(case_id)
    if row is not None:
        return row["status"]
    if case_id in artifact["evaluation"]["missing_case_ids"]:
        return "unattempted"
    return None


def _check_summary(artifact: dict[str, Any], case_id: str) -> str:
    result = artifact["evaluation"]["case_results"].get(case_id)
    if result is None:
        return "not recorded"
    checks = result["checks"]
    if checks is None:
        return "overall passed" if result["passed"] else "overall failed"
    passed_count = sum(checks.values())
    overall = "passed" if result["passed"] else "failed"
    return f"{passed_count}/{len(checks)} checks passed; overall {overall}"


def _check_comparison(baseline: dict[str, Any], comparison: dict[str, Any], case_id: str) -> str:
    before = baseline["evaluation"]["case_results"].get(case_id)
    after = comparison["evaluation"]["case_results"].get(case_id)
    if before is None or after is None or before["checks"] is None or after["checks"] is None:
        return "check-level differences unavailable"
    before_checks = before["checks"]
    after_checks = after["checks"]
    shared = before_checks.keys() & after_checks.keys()
    improved = sum(not before_checks[key] and after_checks[key] for key in shared)
    regressed = sum(before_checks[key] and not after_checks[key] for key in shared)
    unchanged = sum(before_checks[key] == after_checks[key] for key in shared)
    added = len(after_checks.keys() - before_checks.keys())
    removed = len(before_checks.keys() - after_checks.keys())
    return (
        f"check differences: improved={improved}, regressed={regressed}, "
        f"unchanged={unchanged}, added={added}, removed={removed}"
    )


def _output_summary(artifact: dict[str, Any], case_id: str) -> str:
    row = artifact["rows"].get(case_id)
    prompt = _safe_metadata_display(
        row["prompt_raw"] if row is not None else artifact["prompt_raw"],
        _PROMPT_PATTERN,
        artifact["prompt"],
    )
    status = _case_status(artifact, case_id) or "not represented"
    if status == "failed":
        status = "failed (execution; no match verdict)"
    header = (
        f"status={status}; prompt={prompt}; model={artifact['model']}"
    )
    if row is None or row["status"] != "succeeded" or row["output"] is None:
        details = "no match output"
        diagnostics = row["diagnostics"] if row is not None else None
        if (
            (diagnostics is None or not any(diagnostics.values()))
            and artifact["failure_case_id"] == case_id
        ):
            diagnostics = artifact["failure"]
        if diagnostics and any(value is not None for value in diagnostics.values()):
            safe = [
                f"category={diagnostics['error_category']}"
                if diagnostics["error_category"]
                else None
            ]
            if diagnostics["http_status"] is not None:
                safe.append(f"HTTP={diagnostics['http_status']}")
            if diagnostics["provider_status"]:
                safe.append(f"provider={diagnostics['provider_status']}")
            details += f"; diagnostics={', '.join(value for value in safe if value)}"
        return f"{header}; {details}"

    output = row["output"]
    values = [f"verdict={output['verdict'] or 'not recorded'}"]
    score = output["score"]
    confidence = output["confidence"]
    values.append(f"score={score:g}" if score is not None else "score=not recorded")
    values.append(
        f"confidence={confidence:g}" if confidence is not None else "confidence=not recorded"
    )
    signals = output["signals"]
    values.append(
        "signals=" + (
            ",".join(
                f"{field.removesuffix('_match')}:{signals[field]}"
                for field in _SIGNAL_FIELDS
                if field in signals
            )
            or "not recorded"
        )
    )
    values.append(f"automatic checks={_check_summary(artifact, case_id)}")
    return f"{header}; " + "; ".join(values)


def _ranking_summary(artifact: dict[str, Any]) -> str:
    checks = artifact["evaluation"]["ranking_checks"].values()
    counts = {status: 0 for status in ("passed", "failed", "not_run")}
    for status in checks:
        counts[status] += 1
    if not any(counts.values()):
        return "not recorded"
    return ", ".join(f"{status}={counts[status]}" for status in counts)


def compare_artifacts(baseline_path: str | Path, comparison_path: str | Path) -> str:
    """Validate and compare two artifacts by exact case ID; return safe text."""
    known_case_ids = {case.id for case in load_cases()}
    baseline = _load_artifact(baseline_path, "baseline", known_case_ids)
    comparison = _load_artifact(comparison_path, "comparison", known_case_ids)
    lines = [
        "Job-matching evaluation artifact comparison (offline)",
        f"Baseline: status={baseline['status']}; prompt={baseline['prompt']}; "
        f"model={baseline['model']}",
        f"Comparison: status={comparison['status']}; "
        f"prompt={comparison['prompt']}; model={comparison['model']}",
        "OBSERVATIONAL: score deltas are observed differences; they do not "
        "establish prompt/model causation or score calibration. Confidence is "
        "not calibrated probability.",
    ]
    if baseline["prompt_raw"] != comparison["prompt_raw"]:
        lines.append(
            "WARNING: prompt versions differ; this is not a controlled prompt comparison."
        )
    if baseline["model_raw"] != comparison["model_raw"]:
        lines.append(
            "WARNING: model identifiers differ; this is not a controlled model comparison."
        )

    all_ids = set(baseline["rows"]) | baseline["evaluation"]["missing_case_ids"]
    all_ids |= set(comparison["rows"]) | comparison["evaluation"]["missing_case_ids"]
    order = [case.id for case in load_cases() if case.id in all_ids]
    lines.extend(["", "Cases:"])
    for case_id in order:
        before_status = _case_status(baseline, case_id)
        after_status = _case_status(comparison, case_id)
        if before_status is not None and after_status is None:
            lines.append(f"- {case_id} (baseline only; comparison does not represent this case)")
        elif after_status is not None and before_status is None:
            lines.append(f"- {case_id} (comparison only; baseline does not represent this case)")
        else:
            lines.append(f"- {case_id}")
        lines.append(f"  baseline: {_output_summary(baseline, case_id)}")
        lines.append(f"  comparison: {_output_summary(comparison, case_id)}")

        before = baseline["rows"].get(case_id, {}).get("output")
        after = comparison["rows"].get(case_id, {}).get("output")
        before_row = baseline["rows"].get(case_id, {})
        after_row = comparison["rows"].get(case_id, {})
        if (
            before is not None
            and after is not None
            and before_row.get("status") == "succeeded"
            and after_row.get("status") == "succeeded"
            and before["score"] is not None
            and after["score"] is not None
        ):
            delta = after["score"] - before["score"]
            lines.append(f"  observed score delta (comparison - baseline): {delta:+g}")
        else:
            lines.append(
                "  observed score delta: unavailable "
                "(both cases need valid numeric scores)"
            )
        if before is not None and after is not None and before["verdict"] != after["verdict"]:
            lines.append("  verdict changed; this is an observed output difference only")
        if before_status == "succeeded" and after_status == "succeeded":
            lines.append(f"  {_check_comparison(baseline, comparison, case_id)}")

    lines.extend(
        [
            "",
            "Deterministic evaluation: "
            f"baseline complete={baseline['evaluation']['complete']}, "
            f"passed={baseline['evaluation']['passed']}; "
            f"comparison complete={comparison['evaluation']['complete']}, "
            f"passed={comparison['evaluation']['passed']}",
            f"Baseline automatic ranking checks: {_ranking_summary(baseline)}",
            f"Comparison automatic ranking checks: {_ranking_summary(comparison)}",
        ]
    )
    shared_pairs = (
        baseline["evaluation"]["ranking_checks"].keys()
        & comparison["evaluation"]["ranking_checks"].keys()
    )
    if shared_pairs:
        lines.append("Shared ranking-check status comparisons:")
        for higher, lower in sorted(shared_pairs):
            before = baseline["evaluation"]["ranking_checks"][(higher, lower)]
            after = comparison["evaluation"]["ranking_checks"][(higher, lower)]
            lines.append(f"- {higher} above {lower}: {before} -> {after}")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two existing job-matching evaluation artifacts offline."
    )
    parser.add_argument(
        "--baseline",
        required=True,
        metavar="PATH",
        help="Path to the baseline JSON artifact.",
    )
    parser.add_argument(
        "--comparison",
        required=True,
        metavar="PATH",
        help="Path to the comparison JSON artifact.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = compare_artifacts(args.baseline, args.comparison)
    except ComparisonInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
