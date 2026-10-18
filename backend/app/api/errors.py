"""HTTP mapping of domain failures.

Two paths reach this module. Services raise exceptions directly while a
route runs, and the Agent Runtime records handler failures on the
``agent_runs.error`` column. Neither path ever returns a stack trace to a
client: a recorded traceback is reduced to the exception class and the
first line of its message, and an exception nobody recognized is reported
as an opaque 500. Unexpected server errors are still raised so the server
logs them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.agents.runtime import (
    DuplicateSuccessError,
    InvalidRunInputError,
    InvalidRunStateError,
    InvalidTransitionError,
)
from app.services.applications import ApplicationNotFoundError, JobNotFoundError
from app.services.auth import (
    DuplicateEmailError,
    InactiveUserError,
    InvalidCredentialsError,
)
from app.services.candidates import DuplicateCandidateError
from app.services.job_verification import JobVerificationCooldownError
from app.services.resume_upload import (
    CandidateNotFoundError,
    EmptyResumeError,
    EncryptedResumeError,
    ImageOnlyResumeError,
    InvalidResumeError,
    OversizedResumeError,
    UnsupportedResumeFormatError,
)

logger = logging.getLogger(__name__)

INTERNAL_ERROR_MESSAGE = "Internal server error"

_RUN_FAILURE_STATUS: dict[str, int] = {
    "CoverLetterInputError": 400,
    "DiscoveryInputError": 400,
    "InvalidRunInputError": 400,
    "JobAnalysisInputError": 400,
    "JobMatchingInputError": 400,
    "CandidateNotFoundError": 404,
    "JobNotFoundError": 404,
    "MatchNotFoundError": 404,
    "MasterResumeNotFoundError": 404,
    "ResumeDocumentNotFoundError": 404,
    "DuplicateSuccessError": 409,
    "InsufficientCandidateProfileError": 409,
    "InsufficientJobContentError": 409,
    "InvalidMatchError": 409,
    "InvalidMasterResumeError": 409,
    "InvalidResumeDocumentError": 409,
    "InvalidRunStateError": 409,
    "InvalidTransitionError": 409,
    "JobAnalysisNotFoundError": 409,
    "FirecrawlError": 503,
    "FirecrawlSearchError": 503,
    "GeminiError": 503,
    "SearXNGError": 503,
}


@dataclass(frozen=True)
class RecordedFailure:
    """A client-safe rendering of a failure recorded on an AgentRun."""

    status_code: int
    message: str
    error_name: str | None


def _recognized_name(error: str) -> str | None:
    """Return the known exception class appearing last in ``error``.

    The last occurrence wins so a chained traceback reports the exception
    that was actually raised, not the one it was raised from.
    """
    best: str | None = None
    best_index = -1
    for name in _RUN_FAILURE_STATUS:
        index = error.rfind(name)
        if index > best_index:
            best = name
            best_index = index
    return best


def _first_message_line(error: str, name: str) -> str:
    index = error.rfind(name)
    if index < 0:
        return ""
    rest = error[index + len(name) :].lstrip()
    if rest.startswith(":"):
        rest = rest[1:]
    for line in rest.splitlines():
        if line.strip():
            return line.strip()
    return ""


def recorded_failure(error: str | None) -> RecordedFailure:
    """Map a recorded traceback onto a status code and a safe message."""
    if not error:
        return RecordedFailure(500, INTERNAL_ERROR_MESSAGE, None)
    name = _recognized_name(error)
    if name is None:
        return RecordedFailure(500, INTERNAL_ERROR_MESSAGE, None)
    status_code = _RUN_FAILURE_STATUS[name]
    if status_code == 500:
        return RecordedFailure(status_code, INTERNAL_ERROR_MESSAGE, name)
    line = _first_message_line(error, name)
    message = f"{name}: {line}" if line else name
    return RecordedFailure(status_code, message, name)


def _message_from_exception(exc: Exception) -> str:
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    first = next((line for line in text.splitlines() if line.strip()), "")
    return first.strip() or type(exc).__name__


def _domain_handler(status_code: int):
    def handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status_code, content={"detail": _message_from_exception(exc)}
        )

    return handler


def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled application error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": INTERNAL_ERROR_MESSAGE})


def register_exception_handlers(app: FastAPI) -> None:
    """Install the domain and catch-all exception handlers on ``app``.

    Registered by exception class, so a subclass (an oversized resume is
    also an invalid resume) resolves to its own status through the
    exception's MRO. The catch-all makes sure a server-side failure is a
    small JSON body instead of a debug traceback, whatever ``debug`` is.
    """
    app.add_exception_handler(UnsupportedResumeFormatError, _domain_handler(415))
    app.add_exception_handler(EncryptedResumeError, _domain_handler(422))
    app.add_exception_handler(OversizedResumeError, _domain_handler(413))
    app.add_exception_handler(ImageOnlyResumeError, _domain_handler(400))
    app.add_exception_handler(EmptyResumeError, _domain_handler(400))
    app.add_exception_handler(InvalidResumeError, _domain_handler(400))
    app.add_exception_handler(DuplicateCandidateError, _domain_handler(409))
    app.add_exception_handler(DuplicateEmailError, _domain_handler(409))
    app.add_exception_handler(InvalidCredentialsError, _domain_handler(401))
    app.add_exception_handler(InactiveUserError, _domain_handler(403))
    app.add_exception_handler(CandidateNotFoundError, _domain_handler(404))
    app.add_exception_handler(ApplicationNotFoundError, _domain_handler(404))
    app.add_exception_handler(JobNotFoundError, _domain_handler(404))
    app.add_exception_handler(InvalidRunInputError, _domain_handler(400))
    app.add_exception_handler(InvalidRunStateError, _domain_handler(409))
    app.add_exception_handler(InvalidTransitionError, _domain_handler(409))
    app.add_exception_handler(DuplicateSuccessError, _domain_handler(409))
    app.add_exception_handler(JobVerificationCooldownError, _domain_handler(429))
    app.add_exception_handler(Exception, _unhandled_handler)


__all__ = [
    "INTERNAL_ERROR_MESSAGE",
    "RecordedFailure",
    "recorded_failure",
    "register_exception_handlers",
]
