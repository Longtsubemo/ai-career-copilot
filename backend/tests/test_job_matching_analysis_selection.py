"""Offline SQL-shape and precedence tests for selecting job analyses."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.agents.job_analysis.prompt import PROMPT_VERSION as JOB_ANALYSIS_PROMPT_VERSION
from app.agents.job_matching.agent import _select_succeeded_analysis


class _Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _Session:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        compiled = statement.compile()
        requested_version = next(
            (
                value
                for value in compiled.params.values()
                if isinstance(value, str) and value.startswith("job-analysis-")
            ),
            None,
        )
        eligible = [
            row
            for row in self.rows
            if row.status == "succeeded"
            and row.analysis is not None
            and (requested_version is None or row.prompt_version == requested_version)
        ]
        eligible.sort(key=lambda row: (row.created_at, row.id.hex), reverse=True)
        return _Result(eligible[0] if eligible else None)


def _analysis(
    prompt_version: str,
    *,
    summary: str = "usable",
    created_at: int = 0,
    status: str = "succeeded",
    usable: bool = True,
):
    return SimpleNamespace(
        id=uuid4(),
        prompt_version=prompt_version,
        analysis={"summary": summary} if usable else None,
        status=status,
        created_at=created_at,
    )


def _compiled(session: _Session, index: int) -> tuple[str, dict]:
    statement = session.statements[index]
    compiled = statement.compile()
    return str(compiled), compiled.params


def test_newest_usable_v2_is_preferred_over_newer_legacy_analysis() -> None:
    newest_v2 = _analysis(JOB_ANALYSIS_PROMPT_VERSION, summary="v2", created_at=1)
    newer_v1 = _analysis("job-analysis-v1", summary="newer v1", created_at=2)
    session = _Session(newest_v2, newer_v1)

    selected = _select_succeeded_analysis(session, uuid4())

    assert selected is newest_v2
    assert len(session.statements) == 1
    sql, params = _compiled(session, 0)
    assert "job_analyses.prompt_version =" in sql
    assert JOB_ANALYSIS_PROMPT_VERSION in params.values()


def test_v2_query_uses_created_at_and_id_ordering_for_newest_selection() -> None:
    older_v2 = _analysis(JOB_ANALYSIS_PROMPT_VERSION, summary="older v2", created_at=1)
    newest_v2 = _analysis(JOB_ANALYSIS_PROMPT_VERSION, summary="newest v2", created_at=2)
    session = _Session(older_v2, newest_v2)

    assert _select_succeeded_analysis(session, uuid4()) is newest_v2
    sql, _ = _compiled(session, 0)
    assert "ORDER BY job_analyses.created_at DESC, job_analyses.id DESC" in sql
    assert older_v2 is not newest_v2


def test_latest_usable_analysis_is_fallback_when_no_v2_exists() -> None:
    older_legacy = _analysis("job-analysis-v1", summary="older legacy", created_at=1)
    legacy_latest = _analysis("job-analysis-v1", summary="latest legacy", created_at=2)
    session = _Session(older_legacy, legacy_latest)

    selected = _select_succeeded_analysis(session, uuid4())

    assert selected is legacy_latest
    assert len(session.statements) == 2
    versioned_sql, versioned_params = _compiled(session, 0)
    fallback_sql, fallback_params = _compiled(session, 1)
    assert JOB_ANALYSIS_PROMPT_VERSION in versioned_params.values()
    assert "job_analyses.prompt_version =" in versioned_sql
    assert "job_analyses.prompt_version =" not in fallback_sql
    assert JOB_ANALYSIS_PROMPT_VERSION not in fallback_params.values()


def test_both_selection_queries_exclude_failed_and_null_payload_rows() -> None:
    legacy = _analysis("job-analysis-v1")
    session = _Session(
        _analysis("job-analysis-v2", status="failed"),
        _analysis("job-analysis-v2", usable=False),
        legacy,
    )

    assert _select_succeeded_analysis(session, uuid4()) is legacy

    for index in (0, 1):
        sql, _ = _compiled(session, index)
        assert "job_analyses.status =" in sql
        assert "job_analyses.analysis IS NOT NULL" in sql
        assert "ORDER BY job_analyses.created_at DESC, job_analyses.id DESC" in sql
