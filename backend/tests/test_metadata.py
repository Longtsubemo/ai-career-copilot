"""Metadata and model-registration guarantees.

Alembic derives its metadata from ``import app.models``, so a model that is not
exported there is invisible to autogenerate and will later be proposed as a
``DROP TABLE``. These tests guard that wiring.
"""

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlalchemy.inspection import inspect

import app.models
from app.db.base import Base

EXPECTED_TABLES = {
    "agent_runs",
    "applications",
    "candidates",
    "companies",
    "documents",
    "discovery_daily_usage",
    "job_analyses",
    "jobs",
    "matches",
    "sessions",
    "users",
}


def test_every_exported_model_is_registered_on_metadata() -> None:
    registered = set(Base.metadata.tables)
    exported = {model.__tablename__ for model in app.models.__all__ and
                [getattr(app.models, name) for name in app.models.__all__]}
    assert exported == registered


def test_metadata_contains_exactly_the_expected_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_metadata_does_not_contain_unexported_tables() -> None:
    exported_tables = {
        getattr(app.models, name).__tablename__ for name in app.models.__all__
    }
    assert set(Base.metadata.tables) == exported_tables


def test_job_has_no_salary_columns() -> None:
    columns = set(Base.metadata.tables["jobs"].columns.keys())
    assert "salary_min_ctc" not in columns
    assert "salary_max_ctc" not in columns
    assert "salary_text" not in columns


def test_job_has_no_salary_indexes() -> None:
    jobs_table = Base.metadata.tables["jobs"]
    indexed = {
        column.name
        for index in jobs_table.indexes
        for column in index.columns
    }
    assert not any("salary" in name for name in indexed)


def test_job_description_is_nullable() -> None:
    assert Base.metadata.tables["jobs"].columns["description"].nullable is True


def test_job_canonical_url_is_the_dedupe_key() -> None:
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in Base.metadata.tables["jobs"].constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("canonical_url",) in unique_columns
    assert ("job_url",) not in unique_columns


def test_company_normalized_name_is_unique() -> None:
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in Base.metadata.tables["companies"].constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("normalized_name",) in unique_columns


def test_application_job_id_is_not_unique() -> None:
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in Base.metadata.tables["applications"].constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("job_id",) not in unique_columns


def test_application_has_no_removed_columns() -> None:
    columns = set(Base.metadata.tables["applications"].columns.keys())
    assert "resume_version" not in columns
    assert "cover_letter" not in columns
    assert "last_updated" not in columns


def test_application_has_snapshot_columns() -> None:
    columns = Base.metadata.tables["applications"].columns
    assert "job_title_snapshot" in columns
    assert "company_name_snapshot" in columns


@pytest.mark.parametrize(
    "table_name", sorted(EXPECTED_TABLES)
)
def test_every_table_carries_timestamp_columns(table_name: str) -> None:
    columns = set(Base.metadata.tables[table_name].columns.keys())
    assert {"created_at", "updated_at"} <= columns


def test_no_table_cascades_into_applications() -> None:
    """Applications are historical facts and must never be cascade deleted."""
    from sqlalchemy import ForeignKeyConstraint

    def targets_applications(element) -> bool:
        return (
            isinstance(element, ForeignKeyConstraint)
            and "applications.id" in {fk.target_fullname for fk in element.elements}
        )

    offenders = {
        table.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if targets_applications(constraint)
        and any(fk.ondelete == "CASCADE" for fk in constraint.elements)
    }
    assert offenders == set()


def test_jsonb_columns_use_postgres_jsonb() -> None:
    jsonb_columns = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, postgresql.JSONB)
    }
    assert jsonb_columns == {
        ("candidates", "skills"),
        ("job_analyses", "analysis"),
        ("matches", "verdict"),
        ("agent_runs", "input"),
        ("agent_runs", "output"),
    }


def test_salary_columns_absent_from_live_database(engine: Engine) -> None:
    inspector = inspect(engine)
    for table_name in ("jobs", "applications"):
        assert "salary_min_ctc" not in inspector.get_columns(table_name)
        assert "salary_max_ctc" not in inspector.get_columns(table_name)
        assert "salary_text" not in inspector.get_columns(table_name)
