"""Application-managed Windows runtime for PostgreSQL and FastAPI."""

from app.desktop_runtime.bundle import (
    PostgresBundleError,
    resolve_bundled_postgres_binary_dir,
)
from app.desktop_runtime.paths import ApplicationPaths, default_data_directory
from app.desktop_runtime.postgres import ManagedPostgres, PostgresRuntimeError
from app.desktop_runtime.supervisor import DesktopRuntime, DesktopRuntimeError

__all__ = [
    "ApplicationPaths",
    "DesktopRuntime",
    "DesktopRuntimeError",
    "ManagedPostgres",
    "PostgresRuntimeError",
    "PostgresBundleError",
    "default_data_directory",
    "resolve_bundled_postgres_binary_dir",
]
