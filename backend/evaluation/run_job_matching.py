"""Explicitly run the synthetic Gemini job-matching evaluation.

Run from ``backend/`` with ``python -m evaluation.run_job_matching
--confirm-provider-calls``. This makes one Gemini call per case and stops at
the first provider or structured-output failure; there is no retry loop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.job_matching.contract import MatchResult
from app.agents.job_matching.prompt import PROMPT_VERSION, SYSTEM_INSTRUCTION
from app.clients.gemini import GeminiClient, GeminiError
from app.core.config import get_settings

from evaluation.job_matching import (
    build_case_prompt,
    evaluate_benchmark,
    load_cases,
)

DEFAULT_OUTPUT_DIR = Path("artifacts/job-matching-evaluations")


def _prompt_hash(user_prompt: str) -> str:
    combined = SYSTEM_INSTRUCTION + "\0" + user_prompt
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _reserve_result_file(output_dir: Path, run_started: datetime) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"job-matching-{run_started.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    target = output_dir / filename
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(
                {"run_started_at": run_started.isoformat(), "status": "running"},
                indent=2,
            )
            + "\n"
        )
    return target


def _write_reserved_result(target: Path, payload: dict[str, Any]) -> None:
    """Finalize this run's reserved file; never choose or replace another path."""
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with target.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(serialized)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run six synthetic job-matching cases through the configured Gemini model."
    )
    parser.add_argument(
        "--confirm-provider-calls",
        action="store_true",
        help="Required acknowledgement: this command sends one Gemini request per case.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.confirm_provider_calls:
        _parser().error(
            "provider calls are disabled by default; pass --confirm-provider-calls "
            "to run the six-case synthetic evaluation"
        )

    try:
        settings = get_settings()
    except Exception as exc:
        print(
            f"Application settings could not be loaded ({type(exc).__name__}); "
            "check the backend environment configuration.",
            file=sys.stderr,
        )
        return 2
    if not settings.gemini_api_key or not settings.gemini_api_key.strip():
        print(
            "Missing Gemini configuration. Set CAREER_COPILOT_GEMINI_API_KEY "
            "in the backend environment before explicitly running this evaluation.",
            file=sys.stderr,
        )
        return 2

    cases = load_cases()
    client = GeminiClient(settings.gemini_model, api_key=settings.gemini_api_key)
    run_started = datetime.now(timezone.utc)
    try:
        result_path = _reserve_result_file(DEFAULT_OUTPUT_DIR, run_started)
    except FileExistsError:
        print(
            "A result file already exists for this run timestamp; no existing result was overwritten.",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(
            f"Could not reserve the benchmark result file ({type(exc).__name__}); "
            "no provider calls were made.",
            file=sys.stderr,
        )
        return 2

    outputs: dict[str, MatchResult] = {}
    rows: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None

    for case in cases:
        prompt = build_case_prompt(case)
        try:
            result = client.generate_structured(
                prompt=prompt,
                schema=MatchResult,
                system_instruction=SYSTEM_INSTRUCTION,
            )
        except GeminiError as exc:
            # GeminiError can contain provider response details. Retain only
            # its type, never raw text, prompts, credentials or resume content.
            failure = {"case_id": case.id, "error_type": type(exc).__name__}
            rows.append(
                {
                    "case_id": case.id,
                    "prompt_version": PROMPT_VERSION,
                    "prompt_sha256": _prompt_hash(prompt),
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }
            )
            break

        outputs[case.id] = result
        rows.append(
            {
                "case_id": case.id,
                "prompt_version": PROMPT_VERSION,
                "prompt_sha256": _prompt_hash(prompt),
                "status": "succeeded",
                "structured_output": result.model_dump(mode="json"),
            }
        )

    benchmark = evaluate_benchmark(cases, outputs)
    finished = datetime.now(timezone.utc)
    payload = {
        "run_started_at": run_started.isoformat(),
        "run_finished_at": finished.isoformat(),
        "model": settings.gemini_model,
        "prompt_version": PROMPT_VERSION,
        "status": "failed" if failure else "completed",
        "failure": failure,
        "results": rows,
        "deterministic_evaluation": benchmark,
    }
    try:
        _write_reserved_result(result_path, payload)
    except OSError as exc:
        print(
            f"Could not finalize the reserved benchmark result ({type(exc).__name__}); "
            f"the run artifact remains at {result_path}.",
            file=sys.stderr,
        )
        return 2

    print(f"Results written to {result_path}")
    if failure:
        print(
            f"Evaluation stopped at case {failure['case_id']} ({failure['error_type']}; "
            "provider request or structured-output failure); no retry was attempted.",
            file=sys.stderr,
        )
        return 1
    if not benchmark["passed"]:
        failed_checks = sum(
            not row["passed"] for row in benchmark["case_results"]
        ) + sum(not row["passed"] for row in benchmark["ranking_checks"])
        print(
            f"Evaluation completed with {failed_checks} failed deterministic rubric check(s). "
            "Human review is also required.",
            file=sys.stderr,
        )
        return 1
    print("Deterministic checks passed; review free-text claims and score ordering manually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
