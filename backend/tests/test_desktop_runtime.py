from __future__ import annotations

import json
import hashlib
import os
import socket
import subprocess
import threading
import time
from io import StringIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.deps import get_db
from app.api.routes.health import readiness_check, router as health_router
from app.core.config import Settings, get_settings
from app.desktop_runtime.paths import ApplicationPaths, DesktopPathError
from app.desktop_runtime.bundle import (
    BUNDLE_RELATIVE_PATH,
    REQUIRED_BUNDLE_DIRECTORIES,
    REQUIRED_BUNDLE_FILES,
    SOURCE_ARCHIVE_SHA256,
    PostgresBundleError,
    resolve_bundled_postgres_binary_dir,
    validate_postgres_bundle,
)
from app.desktop_runtime.postgres import ManagedPostgres, PostgresRuntimeError
from app.desktop_runtime.state import RuntimeState, RuntimeStateError, RuntimeStateStore
from app.desktop_runtime.supervisor import DesktopRuntime, DesktopRuntimeError, RuntimeLock
from app.desktop_runtime.__main__ import _parser, _watch_shutdown_commands


def paths_at(tmp_path: Path) -> ApplicationPaths:
    return ApplicationPaths(
        install_dir=tmp_path / "install",
        data_dir=tmp_path / "local-app-data" / "AI Career Copilot",
    )


@pytest.fixture(autouse=True)
def clear_cached_settings_between_runtime_tests():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_paths_keep_data_out_of_install_directory(tmp_path: Path) -> None:
    install = tmp_path / "install"
    paths = ApplicationPaths(install_dir=install, data_dir=install / "data")

    with pytest.raises(DesktopPathError, match="outside"):
        paths.prepare()

    assert not (install / "data").exists()


def test_paths_create_and_secure_separate_persistent_subdirectories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    secured: list[Path] = []
    monkeypatch.setattr(
        ApplicationPaths,
        "_secure_directory",
        staticmethod(lambda path: secured.append(path)),
    )

    paths.prepare()

    assert paths.data_dir.is_dir()
    assert paths.config_dir.is_dir()
    assert paths.postgres_data_dir.is_dir()
    assert paths.backup_dir.is_dir()
    assert paths.log_dir.is_dir()
    assert set(secured) == {
        paths.data_dir,
        paths.config_dir,
        paths.postgres_data_dir.parent,
        paths.postgres_data_dir,
        paths.backup_dir,
        paths.log_dir,
    }


def test_windows_data_directory_permission_hardening(tmp_path: Path) -> None:
    protected = tmp_path / "protected-user-data"
    protected.mkdir()

    ApplicationPaths._secure_directory(protected)

    assert protected.is_dir()


def test_managed_postgres_requires_absolute_binary_directory(tmp_path: Path) -> None:
    with pytest.raises(PostgresRuntimeError, match="absolute path"):
        ManagedPostgres(paths_at(tmp_path), Path("relative-postgres-bin"))


def test_default_data_directory_uses_local_app_data() -> None:
    from app.desktop_runtime.paths import default_data_directory

    assert default_data_directory({"LOCALAPPDATA": r"C:\Users\test\AppData\Local"}) == Path(
        r"C:\Users\test\AppData\Local\AI Career Copilot"
    )
    with pytest.raises(DesktopPathError):
        default_data_directory({})
    with pytest.raises(DesktopPathError):
        default_data_directory({"LOCALAPPDATA": "relative-app-data"})


def test_paths_refuse_to_secure_a_parent_of_the_installation(tmp_path: Path) -> None:
    data_dir = tmp_path / "AI Career Copilot"
    paths = ApplicationPaths(
        install_dir=data_dir / "Programs" / "AI Career Copilot",
        data_dir=data_dir,
    )

    with pytest.raises(DesktopPathError):
        paths.prepare()

    assert not paths.data_dir.exists()


def test_runtime_state_store_round_trips_secrets_and_never_truncates_malformed_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config" / "runtime.json"
    store = RuntimeStateStore(path)
    state = RuntimeState.create()
    state.postgres_port = 55432
    state.desktop_settings.update(
        {
            "session_cookie_name": "obsolete-session",
            "csrf_cookie_name": "obsolete-csrf",
        }
    )
    store.save(state)

    loaded = store.load()
    assert loaded is not None
    assert loaded.admin_password == state.admin_password
    assert loaded.database_password == state.database_password
    assert loaded.desktop_configuration_initialized is False
    assert not any("session" in key or "csrf" in key for key in loaded.desktop_settings)
    assert loaded.database_url(55432).startswith("postgresql+psycopg://career_copilot_app:")
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600

    original = b'{"format_version":broken'
    path.write_bytes(original)
    with pytest.raises(RuntimeStateError):
        store.load()
    assert path.read_bytes() == original


def test_runtime_state_store_rejects_malformed_state_types_without_rewriting(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config" / "runtime.json"
    path.parent.mkdir(parents=True)
    original = json.dumps(
        {
            "format_version": 1,
            "postgres_major": 16,
            "admin_password": "generated",
            "database_password": "generated",
            "postgres_port": None,
            "cluster_state": [],
            "database_provisioned": False,
            "desktop_settings": {},
        }
    ).encode()
    path.write_bytes(original)

    with pytest.raises(RuntimeStateError):
        RuntimeStateStore(path).load()

    assert path.read_bytes() == original


def test_desktop_settings_ignore_developer_env_and_load_explicit_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "desktop-settings.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime_mode": "desktop",
                "environment": "desktop",
                "database_url": "postgresql+psycopg://career_copilot_app:random@127.0.0.1:54321/career_copilot",
                "enable_resume_ai": False,
                "cors_origins": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "desktop")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", str(config_path))
    monkeypatch.setenv(
        "CAREER_COPILOT_DATABASE_URL",
        "postgresql+psycopg://unrelated:ignored@127.0.0.1:59999/ignored",
    )
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.runtime_mode == "desktop"
        assert settings.database_url.endswith("127.0.0.1:54321/career_copilot")
        assert settings.enable_resume_ai is False
    finally:
        get_settings.cache_clear()


def test_desktop_settings_reject_external_database_and_cors_origins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "desktop-settings.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime_mode": "desktop",
                "environment": "desktop",
                "database_url": "postgresql+psycopg://career_copilot_app:secret@example.org:54321/career_copilot",
                "cors_origins": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "desktop")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", str(config_path))
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="invalid"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_desktop_settings_reject_unapproved_cors_origins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "desktop-settings.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime_mode": "desktop",
                "environment": "desktop",
                "database_url": "postgresql+psycopg://career_copilot_app:random@127.0.0.1:54321/career_copilot",
                "cors_origins": ["https://example.org"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "desktop")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", str(config_path))
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="invalid"):
            get_settings()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5173"],
)
def test_desktop_settings_accepts_only_the_tauri_development_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    config_path = tmp_path / "desktop-settings.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime_mode": "desktop",
                "environment": "desktop",
                "database_url": "postgresql+psycopg://career_copilot_app:random@127.0.0.1:54321/career_copilot",
                "cors_origins": [origin],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "desktop")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", str(config_path))
    get_settings.cache_clear()
    try:
        assert get_settings().cors_origins == [origin]
    finally:
        get_settings.cache_clear()


class FakeCursor:
    def __init__(self, connector: "FakeConnector", database: str) -> None:
        self.connector = connector
        self.database = database
        self.query = ""

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: object, params: object = None) -> None:
        self.query = str(query)
        if "CREATE ROLE" in self.query:
            self.connector.role_exists = True
        if "CREATE DATABASE" in self.query:
            self.connector.database_owner = "career_copilot_app"

    def fetchone(self):
        if "current_setting('data_directory')" in self.query:
            return (
                str(self.connector.data_dir),
                str(self.connector.port),
                "127.0.0.1",
            )
        if "SELECT rolname" in self.query:
            return ("career_copilot_app",) if self.connector.role_exists else None
        if "pg_get_userbyid" in self.query:
            return (self.connector.database_owner,) if self.connector.database_owner else None
        if "SELECT 1" in self.query:
            return (1,)
        return None


class FakeConnection:
    def __init__(self, connector: "FakeConnector", database: str) -> None:
        self.connector = connector
        self.database = database

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.connector, self.database)


class FakeConnector:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.port = 0
        self.role_exists = False
        self.database_owner: str | None = None
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> FakeConnection:
        self.calls.append(kwargs)
        self.port = int(kwargs["port"])
        if kwargs["user"] == "career_copilot_app" and not self.role_exists:
            raise RuntimeError("application role not created")
        return FakeConnection(self, str(kwargs["dbname"]))


class FakePgTools:
    def __init__(self, data_dir: Path, version: str = "16.16") -> None:
        self.data_dir = data_dir
        self.version = version
        self.running = False
        self.commands: list[list[str]] = []
        self.start_failures = 0

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.commands.append(command)
        name = Path(command[0]).stem
        if name == "postgres":
            return subprocess.CompletedProcess(
                command, 0, stdout=f"postgres (PostgreSQL) {self.version}\n"
            )
        if name == "initdb":
            data_dir = Path(command[command.index("--pgdata") + 1])
            (data_dir / "global").mkdir(parents=True)
            (data_dir / "PG_VERSION").write_text("16\n", encoding="ascii")
            (data_dir / "global" / "pg_control").write_bytes(b"fake")
            return subprocess.CompletedProcess(command, 0, stdout="")
        mode = next((item for item in command if item in {"status", "start", "stop"}), None)
        if name == "pg_ctl" and mode == "status":
            return subprocess.CompletedProcess(command, 0 if self.running else 3, stdout="")
        if name == "pg_ctl" and mode == "start":
            assert "capture_output" not in kwargs
            assert kwargs["stdout"] == subprocess.DEVNULL
            assert kwargs["stderr"] == subprocess.DEVNULL
            options = command[command.index("-o") + 1]
            assert '-h "127.0.0.1"' in options
            assert "listen_addresses=127.0.0.1" in options
            if self.start_failures:
                self.start_failures -= 1
                return subprocess.CompletedProcess(command, 1, stderr="port is busy")
            self.running = True
            return subprocess.CompletedProcess(command, 0, stdout="")
        if name == "pg_ctl" and mode == "stop":
            self.running = False
            return subprocess.CompletedProcess(command, 0, stdout="")
        if name == "pg_dump":
            target = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--file=")))
            target.write_bytes(b"dump")
            return subprocess.CompletedProcess(command, 0, stdout="")
        return subprocess.CompletedProcess(command, 0, stdout="")


def create_fake_binaries(directory: Path) -> Path:
    directory.mkdir(parents=True)
    for name in ("initdb", "pg_ctl", "postgres", "pg_dump", "pg_restore"):
        (directory / (name + (".exe" if os.name == "nt" else ""))).write_bytes(b"fake")
    bundle_root = directory.parent
    for relative in (
        "lib/plpgsql.dll",
        "share/postgres.bki",
        "share/postgresql.conf.sample",
        "share/pg_hba.conf.sample",
        "share/pg_ident.conf.sample",
        "share/extension/plpgsql.control",
        "share/extension/plpgsql--1.0.sql",
    ):
        resource = bundle_root / relative
        resource.parent.mkdir(parents=True, exist_ok=True)
        resource.write_bytes(b"fake")
    for relative in ("share/locale", "share/timezone", "share/timezonesets", "share/tsearch_data"):
        directory_path = bundle_root / relative
        directory_path.mkdir(parents=True, exist_ok=True)
        (directory_path / "marker").write_bytes(b"fake")
    return directory


def create_test_bundle(bundle_root: Path, *, version: str = "16.16") -> Path:
    for relative in REQUIRED_BUNDLE_FILES:
        path = bundle_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test bundle payload")
    for relative in REQUIRED_BUNDLE_DIRECTORIES:
        path = bundle_root / relative
        path.mkdir(parents=True, exist_ok=True)
        (path / "marker").write_bytes(b"test resource")
    files = []
    for path in sorted(item for item in bundle_root.rglob("*") if item.is_file()):
        relative = path.relative_to(bundle_root).as_posix()
        data = path.read_bytes()
        files.append(
            {
                "path": relative,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    (bundle_root / "bundle.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "postgres_version": version,
                "architecture": "windows-x64",
                "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    return bundle_root / "bin"


def test_bundled_postgres_path_resolves_under_install_resources(tmp_path: Path) -> None:
    install_root = tmp_path / "installation"
    expected = create_test_bundle(install_root / BUNDLE_RELATIVE_PATH)

    assert resolve_bundled_postgres_binary_dir(install_root) == expected
    assert _parser().parse_args([]).postgres_bin is None


def test_postgres_bundle_rejects_missing_manifest_file(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    create_test_bundle(bundle_root)
    (bundle_root / "share" / "timezone" / "marker").unlink()

    with pytest.raises(PostgresBundleError, match="file is missing"):
        validate_postgres_bundle(bundle_root)


def test_postgres_bundle_rejects_incompatible_version(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    create_test_bundle(bundle_root, version="16.15")

    with pytest.raises(PostgresBundleError, match="incompatible"):
        validate_postgres_bundle(bundle_root)


def test_postgres_bundle_rejects_changed_payload(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    create_test_bundle(bundle_root)
    (bundle_root / "bin" / "postgres.exe").write_bytes(b"changed")

    with pytest.raises(PostgresBundleError, match="integrity verification"):
        validate_postgres_bundle(bundle_root)


def test_explicit_postgres_bin_override_stays_outside_persistent_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary_dir = create_fake_binaries(tmp_path / "bundle" / "bin")
    monkeypatch.setenv("PATH", r"C:\Windows\System32")
    manager = ManagedPostgres(paths_at(tmp_path), binary_dir)

    environment = manager._tool_environment()

    assert Path(binary_dir / "postgres.exe").is_absolute()
    assert environment["PATH"].split(os.pathsep)[0] == str(binary_dir.resolve())
    assert "PostgreSQL\\16.16\\bin" not in environment["PATH"]
    assert paths_at(tmp_path).postgres_data_dir != binary_dir.parent


def test_incompatible_bundled_minor_version_is_rejected_before_cluster_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    binary_dir = create_test_bundle(tmp_path / "bundle", version="16.16")
    tools = FakePgTools(paths.postgres_data_dir, version="16.15")
    manager = ManagedPostgres(
        paths,
        binary_dir,
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )

    with pytest.raises(PostgresRuntimeError, match="does not match its bundle"):
        manager.start()

    assert not paths.postgres_data_dir.exists() or not any(paths.postgres_data_dir.iterdir())


def test_missing_postgres_restore_tool_fails_before_cluster_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    binary_dir = create_fake_binaries(tmp_path / "bundle" / "bin")
    (binary_dir / "pg_restore.exe").unlink()
    manager = ManagedPostgres(
        paths,
        binary_dir,
        run=FakePgTools(paths.postgres_data_dir),
        connect=FakeConnector(paths.postgres_data_dir),
    )

    with pytest.raises(PostgresRuntimeError, match="incomplete"):
        manager.start()

    assert not paths.postgres_data_dir.exists() or not any(paths.postgres_data_dir.iterdir())


def test_new_managed_cluster_starts_on_loopback_and_stops_without_deleting_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    tools = FakePgTools(paths.postgres_data_dir)
    connector = FakeConnector(paths.postgres_data_dir)
    manager = ManagedPostgres(
        paths,
        create_fake_binaries(tmp_path / "postgres-bin"),
        run=tools,
        connect=connector,
    )

    state = manager.start()
    assert state.cluster_state == "ready"
    assert state.database_provisioned is True
    assert state.postgres_port is not None
    assert tools.running
    assert any(Path(command[0]).stem == "initdb" for command in tools.commands)
    assert any(Path(command[0]).stem == "pg_ctl" and "start" in command for command in tools.commands)
    assert connector.role_exists
    assert connector.database_owner == "career_copilot_app"
    pg_ctl_stop_count = len([cmd for cmd in tools.commands if Path(cmd[0]).stem == "pg_ctl" and "stop" in cmd])

    manager.stop()

    assert not tools.running
    assert len([cmd for cmd in tools.commands if Path(cmd[0]).stem == "pg_ctl" and "stop" in cmd]) == pg_ctl_stop_count + 1
    assert (paths.postgres_data_dir / "global" / "pg_control").exists()


@pytest.mark.parametrize("failure_mode", ["timeout", "nonzero"])
def test_backup_runner_failure_removes_partial_dump_and_reports_safe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_mode: str
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))

    class TimedDumpTools(FakePgTools):
        def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            if Path(command[0]).stem == "pg_dump":
                target = Path(
                    next(arg.split("=", 1)[1] for arg in command if arg.startswith("--file="))
                )
                target.write_bytes(b"partial dump")
                if failure_mode == "timeout":
                    raise subprocess.TimeoutExpired(command, 5)
                return subprocess.CompletedProcess(command, 1, stderr="disk full")
            return super().__call__(command, **kwargs)

    tools = TimedDumpTools(paths.postgres_data_dir)
    manager = ManagedPostgres(
        paths,
        create_fake_binaries(tmp_path / "postgres-bin"),
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )
    manager.start()

    with pytest.raises(PostgresRuntimeError, match="database backup failed; migrations were not run") as exc:
        manager.create_backup()

    assert "TimeoutExpired" not in str(exc.value)
    assert list(paths.backup_dir.iterdir()) == []
    manager.stop()


def test_backup_publication_never_overwrites_an_existing_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.desktop_runtime import postgres as postgres_module

    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    tools = FakePgTools(paths.postgres_data_dir)
    manager = ManagedPostgres(
        paths,
        create_fake_binaries(tmp_path / "postgres-bin"),
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )
    manager.start()
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    first_suffix = b"\x01" * 16
    second_suffix = b"\x02" * 16
    existing_backup = paths.backup_dir / (
        f"career-copilot-{timestamp}-{first_suffix.hex()}.dump"
    )
    existing_backup.write_bytes(b"existing backup")
    suffixes = iter((first_suffix, second_suffix))
    monkeypatch.setattr(postgres_module.os, "urandom", lambda _size: next(suffixes))

    created_backup = manager.create_backup()

    assert created_backup != existing_backup
    assert created_backup.read_bytes() == b"dump"
    assert existing_backup.read_bytes() == b"existing backup"
    manager.stop()


def test_interrupted_cluster_initialization_is_never_promoted_to_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))

    class InterruptedInitTools(FakePgTools):
        def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            if Path(command[0]).stem == "initdb":
                data_dir = Path(command[command.index("--pgdata") + 1])
                (data_dir / "global").mkdir(parents=True, exist_ok=True)
                (data_dir / "PG_VERSION").write_text("16\n", encoding="ascii")
                (data_dir / "global" / "pg_control").write_bytes(b"partial control file")
                return subprocess.CompletedProcess(command, 1, stderr="interrupted")
            return super().__call__(command, **kwargs)

    tools = InterruptedInitTools(paths.postgres_data_dir)
    binaries = create_fake_binaries(tmp_path / "postgres-bin")
    first = ManagedPostgres(
        paths,
        binaries,
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )
    with pytest.raises(PostgresRuntimeError, match="initialization failed"):
        first.start()

    second = ManagedPostgres(
        paths,
        binaries,
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )
    with pytest.raises(PostgresRuntimeError, match="partial PostgreSQL initialization"):
        second.start()

    assert (paths.postgres_data_dir / "global" / "pg_control").read_bytes() == b"partial control file"
    assert not any(
        Path(command[0]).stem == "pg_ctl" and "start" in command
        for command in tools.commands
    )


def test_incompatible_cluster_version_is_preserved_and_not_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    tools = FakePgTools(paths.postgres_data_dir)
    binaries = create_fake_binaries(tmp_path / "postgres-bin")
    first = ManagedPostgres(
        paths,
        binaries,
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )
    first.start()
    first.stop()
    start_count = sum(
        Path(command[0]).stem == "pg_ctl" and "start" in command
        for command in tools.commands
    )
    version_path = paths.postgres_data_dir / "PG_VERSION"
    version_path.write_text("15\n", encoding="ascii")

    second = ManagedPostgres(
        paths,
        binaries,
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )
    with pytest.raises(PostgresRuntimeError, match="cluster version differs"):
        second.start()

    assert version_path.read_text(encoding="ascii") == "15\n"
    assert sum(
        Path(command[0]).stem == "pg_ctl" and "start" in command
        for command in tools.commands
    ) == start_count


def test_corrupted_cluster_files_are_preserved_and_not_reinitialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    tools = FakePgTools(paths.postgres_data_dir)
    binaries = create_fake_binaries(tmp_path / "postgres-bin")
    first = ManagedPostgres(
        paths,
        binaries,
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )
    first.start()
    first.stop()
    init_count = sum(Path(command[0]).stem == "initdb" for command in tools.commands)
    start_count = sum(
        Path(command[0]).stem == "pg_ctl" and "start" in command
        for command in tools.commands
    )
    control_path = paths.postgres_data_dir / "global" / "pg_control"
    control_path.unlink()

    second = ManagedPostgres(
        paths,
        binaries,
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )
    with pytest.raises(PostgresRuntimeError, match="cluster files are incomplete"):
        second.start()

    assert not control_path.exists()
    assert sum(Path(command[0]).stem == "initdb" for command in tools.commands) == init_count
    assert sum(
        Path(command[0]).stem == "pg_ctl" and "start" in command
        for command in tools.commands
    ) == start_count


def test_unrecognized_existing_cluster_is_not_initialized_or_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    paths.postgres_data_dir.mkdir(parents=True)
    sentinel = paths.postgres_data_dir / "do-not-touch"
    sentinel.write_text("existing", encoding="utf-8")
    tools = FakePgTools(paths.postgres_data_dir)
    manager = ManagedPostgres(
        paths,
        create_fake_binaries(tmp_path / "postgres-bin"),
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )

    with pytest.raises(PostgresRuntimeError, match="unrecognized database cluster"):
        manager.start()

    assert sentinel.read_text(encoding="utf-8") == "existing"
    assert not any(Path(command[0]).stem == "initdb" for command in tools.commands)


def test_managed_cluster_initialization_failure_preserves_partial_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))

    class PartialInitTools(FakePgTools):
        def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            if Path(command[0]).stem == "initdb":
                data_dir = Path(command[command.index("--pgdata") + 1])
                data_dir.mkdir(parents=True, exist_ok=True)
                (data_dir / "partial").write_text("keep", encoding="utf-8")
                return subprocess.CompletedProcess(command, 1, stderr="initdb failed")
            return super().__call__(command, **kwargs)

    tools = PartialInitTools(paths.postgres_data_dir)
    manager = ManagedPostgres(
        paths,
        create_fake_binaries(tmp_path / "postgres-bin"),
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )

    with pytest.raises(PostgresRuntimeError, match="initialization failed"):
        manager.start()

    assert (paths.postgres_data_dir / "partial").read_text(encoding="utf-8") == "keep"
    assert paths.runtime_config_path.exists()


def test_port_collision_retries_with_another_loopback_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    tools = FakePgTools(paths.postgres_data_dir)
    tools.start_failures = 1
    connector = FakeConnector(paths.postgres_data_dir)
    manager = ManagedPostgres(
        paths,
        create_fake_binaries(tmp_path / "postgres-bin"),
        run=tools,
        connect=connector,
    )

    state = manager.start()
    start_commands = [cmd for cmd in tools.commands if Path(cmd[0]).stem == "pg_ctl" and "start" in cmd]
    selected_ports = [int(cmd[cmd.index("-o") + 1].split('-p "', 1)[1].split('"', 1)[0]) for cmd in start_commands]
    assert len(selected_ports) == 2
    assert selected_ports[0] != selected_ports[1]
    assert state.postgres_port == selected_ports[-1]
    manager.stop()


def test_port_selection_skips_a_busy_loopback_port(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    manager = ManagedPostgres(paths, tmp_path / "postgres-bin")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        busy_port = int(occupied.getsockname()[1])
        selected = manager._select_port(busy_port)

    assert selected != busy_port


def test_existing_marked_cluster_is_adopted_without_reinitialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    tools = FakePgTools(paths.postgres_data_dir)
    connector = FakeConnector(paths.postgres_data_dir)
    binaries = create_fake_binaries(tmp_path / "postgres-bin")
    first = ManagedPostgres(paths, binaries, run=tools, connect=connector)
    first.start()
    first.stop()
    init_count = sum(Path(command[0]).stem == "initdb" for command in tools.commands)
    start_count = sum(
        Path(command[0]).stem == "pg_ctl" and "start" in command for command in tools.commands
    )
    tools.running = True

    second = ManagedPostgres(paths, binaries, run=tools, connect=connector)
    second.start()

    assert sum(Path(command[0]).stem == "initdb" for command in tools.commands) == init_count
    assert sum(
        Path(command[0]).stem == "pg_ctl" and "start" in command for command in tools.commands
    ) == start_count
    assert second.is_running()
    second.stop()


def test_failed_postgres_start_preserves_initialized_cluster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    tools = FakePgTools(paths.postgres_data_dir)
    tools.start_failures = 5
    manager = ManagedPostgres(
        paths,
        create_fake_binaries(tmp_path / "postgres-bin"),
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )

    with pytest.raises(PostgresRuntimeError, match="could not start"):
        manager.start()

    assert (paths.postgres_data_dir / "global" / "pg_control").is_file()
    assert paths.runtime_config_path.is_file()
    assert not tools.running
    assert not any(
        "stop" in command for command in tools.commands if Path(command[0]).stem == "pg_ctl"
    )


def test_postgres_start_timeout_adopts_only_a_verified_managed_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))

    class TimedStartTools(FakePgTools):
        def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            if Path(command[0]).stem == "pg_ctl" and "start" in command:
                self.commands.append(command)
                self.running = True
                raise subprocess.TimeoutExpired(command, 5)
            return super().__call__(command, **kwargs)

    tools = TimedStartTools(paths.postgres_data_dir)
    manager = ManagedPostgres(
        paths,
        create_fake_binaries(tmp_path / "postgres-bin"),
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )

    with pytest.raises(subprocess.TimeoutExpired):
        manager.start()

    assert manager.is_running() is True
    manager.stop()
    assert tools.running is False


def test_unexpected_postgres_exit_is_detected_without_stopping_unrelated_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    tools = FakePgTools(paths.postgres_data_dir)
    manager = ManagedPostgres(
        paths,
        create_fake_binaries(tmp_path / "postgres-bin"),
        run=tools,
        connect=FakeConnector(paths.postgres_data_dir),
    )
    manager.start()
    tools.running = False

    assert manager.is_running() is False
    assert not any(
        "stop" in command for command in tools.commands if Path(command[0]).stem == "pg_ctl"
    )
    assert (paths.postgres_data_dir / "global" / "pg_control").is_file()


def test_existing_managed_cluster_with_missing_config_is_not_silently_reconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "development")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", "")
    paths = paths_at(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    postgres = FakePostgres(events)
    postgres.state.desktop_configuration_initialized = True
    runtime = DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        postgres=postgres,
        backend_root=tmp_path / "install" / "backend",
        server_factory=lambda _port: pytest.fail("API started without desktop settings"),
        readiness_check=lambda _url: True,
        migrate=lambda: None,
    )
    paths.postgres_data_dir.mkdir(parents=True)
    sentinel = paths.postgres_data_dir / "user-data-marker"
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(DesktopRuntimeError, match="configuration is missing"):
        runtime.start()

    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not paths.desktop_settings_path.exists()
    assert events[-1] == "postgres-stop"


class FakePostgres:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.state = RuntimeState.create()
        self.state.postgres_port = 54321
        self.state.database_provisioned = True
        self._running = False

    def start(self) -> RuntimeState:
        self.events.append("postgres-start")
        self._running = True
        return self.state

    def create_backup(self) -> Path:
        self.events.append("backup")
        return Path("backup.dump")

    def is_running(self) -> bool:
        return self._running

    def stop(self) -> None:
        self.events.append("postgres-stop")
        self._running = False


def test_backup_failure_prevents_migrations_and_api_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "development")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", "")
    paths = paths_at(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))

    class FailedBackupPostgres(FakePostgres):
        def create_backup(self) -> Path:
            self.events.append("backup-failed")
            raise PostgresRuntimeError("database backup failed; migrations were not run")

    runtime = DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        postgres=FailedBackupPostgres(events),
        backend_root=tmp_path / "install" / "backend",
        server_factory=lambda _port: pytest.fail("API started after backup failure"),
        readiness_check=lambda _url: True,
        migrate=lambda: events.append("migrate"),
    )

    with pytest.raises(DesktopRuntimeError, match="database backup failed"):
        runtime.start()

    assert events == ["postgres-start", "backup-failed", "postgres-stop"]


class FakeServer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.started = True
        self.should_exit = False
        self.force_exit = False

    def run(self) -> None:
        self.events.append("api-start")
        while not self.should_exit:
            time.sleep(0.001)
        self.events.append("api-stop")


def test_runtime_migrates_after_backup_and_reports_dynamic_loopback_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "development")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", "")
    paths = paths_at(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    runtime = DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        postgres=FakePostgres(events),
        backend_root=tmp_path / "install" / "backend",
        server_factory=lambda _port: FakeServer(events),
        readiness_check=lambda _url: True,
        migrate=lambda: events.append("migrate"),
    )

    api_url = runtime.start()
    assert api_url.startswith("http://127.0.0.1:")
    assert events.index("postgres-start") < events.index("backup") < events.index("migrate") < events.index("api-start")
    persisted = json.loads(paths.desktop_settings_path.read_text(encoding="utf-8"))
    assert persisted["runtime_mode"] == "desktop"
    assert persisted["environment"] == "desktop"
    assert persisted["enable_resume_ai"] is False
    assert persisted["database_url"].startswith("postgresql+psycopg://")
    assert runtime.postgres.state.desktop_configuration_initialized is True
    assert persisted["frontend_dist_dir"] == str(paths.install_dir / "frontend" / "dist")

    runtime.stop()
    lock = RuntimeLock(paths.data_dir / "runtime.lock")
    lock.acquire()
    lock.release()
    assert events[-2:] == ["api-stop", "postgres-stop"]


def test_desktop_runtime_uses_installed_resource_frontend_before_portable_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "development")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", "")
    paths = paths_at(tmp_path)
    packaged_frontend = paths.install_dir / "resources" / "frontend" / "dist"
    packaged_frontend.mkdir(parents=True)
    (packaged_frontend / "index.html").write_text("<main>packaged</main>")
    portable_frontend = paths.install_dir / "frontend" / "dist"
    portable_frontend.mkdir(parents=True)
    (portable_frontend / "index.html").write_text("<main>portable</main>")
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    runtime = DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        postgres=FakePostgres([]),
        backend_root=tmp_path / "install" / "backend",
    )

    settings = runtime._load_desktop_settings({"database_url": "postgresql://local"})

    assert settings["frontend_dist_dir"] == str(packaged_frontend)


def test_desktop_settings_remove_obsolete_authentication_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    paths = paths_at(tmp_path)
    paths.desktop_settings_path.parent.mkdir(parents=True, exist_ok=True)
    paths.desktop_settings_path.write_text(
        json.dumps(
            {
                "session_cookie_name": "old-session",
                "session_ttl_seconds": 600,
                "session_cookie_secure": False,
                "session_cookie_samesite": "lax",
                "csrf_cookie_name": "old-csrf",
            }
        ),
        encoding="utf-8",
    )
    runtime = DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        postgres=FakePostgres([]),
    )

    settings = runtime._load_desktop_settings({"database_url": "postgresql://local"})

    assert not any("session" in key or "csrf" in key for key in settings)


@pytest.mark.parametrize(
    ("mode", "api_prefix", "cors"),
    [
        ("tauri-dev", "http://localhost:", ["http://localhost:5173"]),
        ("tauri-local", "http://localhost:", ["http://tauri.localhost"]),
    ],
)
def test_runtime_advertises_same_site_tauri_origin_and_exact_cors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    api_prefix: str,
    cors: list[str],
) -> None:
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "development")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", "")
    paths = paths_at(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    runtime = DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        postgres=FakePostgres(events),
        backend_root=tmp_path / "install" / "backend",
        server_factory=lambda _port: FakeServer(events),
        readiness_check=lambda _url: True,
        migrate=lambda: None,
        webview_mode=mode,
    )

    try:
        api_url = runtime.start()
        assert api_url.startswith(api_prefix)
        persisted = json.loads(paths.desktop_settings_path.read_text(encoding="utf-8"))
        assert persisted["cors_origins"] == cors
    finally:
        runtime.stop()


def test_migration_failure_does_not_start_api_or_remove_user_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "development")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", "")
    paths = paths_at(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))

    def fail_migration() -> None:
        events.append("migration-failed")
        raise RuntimeError("failure")

    runtime = DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        postgres=FakePostgres(events),
        backend_root=tmp_path / "install" / "backend",
        server_factory=lambda _port: pytest.fail("API started after migration failure"),
        readiness_check=lambda _url: True,
        migrate=fail_migration,
    )
    paths.postgres_data_dir.mkdir(parents=True)
    sentinel = paths.postgres_data_dir / "keep-user-data"
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(DesktopRuntimeError):
        runtime.start()

    assert events.index("backup") < events.index("migration-failed")
    assert events[-1] == "postgres-stop"
    assert paths.desktop_settings_path.exists()
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_backend_readiness_timeout_shuts_down_without_touching_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.desktop_runtime import supervisor

    paths = paths_at(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    monkeypatch.setattr(supervisor, "STARTUP_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "development")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", "")
    runtime = DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        postgres=FakePostgres(events),
        backend_root=tmp_path / "install" / "backend",
        server_factory=lambda _port: FakeServer(events),
        readiness_check=lambda _url: False,
        migrate=lambda: None,
        sleep=lambda _seconds: None,
    )
    paths.postgres_data_dir.mkdir(parents=True)
    sentinel = paths.postgres_data_dir / "keep-user-data"
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(DesktopRuntimeError, match="readiness failed"):
        runtime.start()

    assert events.count("api-start") == 5
    assert events.count("api-stop") == 5
    assert events[-1] == "postgres-stop"
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_readiness_endpoint_performs_a_database_connection_check() -> None:
    class WorkingSession:
        def execute(self, _statement: object) -> None:
            return None

    class FailedSession:
        def execute(self, _statement: object) -> None:
            raise OperationalError("SELECT 1", {}, RuntimeError("offline"))

    assert readiness_check(WorkingSession()) == {"status": "ready"}
    with pytest.raises(Exception) as failure:
        readiness_check(FailedSession())
    assert getattr(failure.value, "status_code", None) == 503


def test_readiness_http_route_reports_database_failure_without_details() -> None:
    app = FastAPI()
    app.include_router(health_router)

    class FailedSession:
        def execute(self, _statement: object) -> None:
            raise OperationalError("SELECT 1", {}, RuntimeError("private connection detail"))

    def failed_session():
        yield FailedSession()

    app.dependency_overrides[get_db] = failed_session
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "service not ready"}
    assert "private connection detail" not in response.text


def test_desktop_supervisor_detects_unexpected_database_exit_and_stops_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "development")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", "")
    paths = paths_at(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    postgres = FakePostgres(events)
    runtime = DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        postgres=postgres,
        backend_root=tmp_path / "install" / "backend",
        server_factory=lambda _port: FakeServer(events),
        readiness_check=lambda _url: True,
        migrate=lambda: None,
    )
    runtime.start()
    postgres._running = False

    with pytest.raises(DesktopRuntimeError, match="database stopped unexpectedly"):
        runtime.run_until_stopped(poll_interval=0)

    assert events[-2:] == ["api-stop", "postgres-stop"]


def test_default_backend_server_binds_only_to_loopback() -> None:
    server = DesktopRuntime._default_server(54321)
    assert server.config.host == "127.0.0.1"
    assert server.config.port == 54321


def test_runtime_control_protocol_accepts_only_shutdown_command() -> None:
    shutdown = threading.Event()
    _watch_shutdown_commands(
        StringIO('{"command":"status"}\nnot json\n{"command":"shutdown"}\n'),
        shutdown,
    )
    assert shutdown.is_set()

    owner_closed = threading.Event()
    _watch_shutdown_commands(StringIO(""), owner_closed)
    assert owner_closed.is_set()


def test_startup_interruption_stops_managed_services_and_releases_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_at(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "development")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", "")

    class InterruptedPostgres(FakePostgres):
        def start(self) -> RuntimeState:
            self.events.append("postgres-start")
            self._running = True
            raise KeyboardInterrupt

    runtime = DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        postgres=InterruptedPostgres(events),
        backend_root=tmp_path / "install" / "backend",
    )

    with pytest.raises(KeyboardInterrupt):
        runtime.start()

    assert events[-1] == "postgres-stop"
    lock = RuntimeLock(paths.data_dir / "runtime.lock")
    lock.acquire()
    lock.release()


def test_production_supervisor_refuses_a_preloaded_development_database_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    paths = paths_at(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "development")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", "")
    runtime = DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        postgres=FakePostgres(events),
        backend_root=tmp_path / "install" / "backend",
    )

    get_settings.cache_clear()
    with pytest.raises(DesktopRuntimeError, match="fresh backend process"):
        runtime.start()
    get_settings.cache_clear()

    assert "backup" not in events
    assert "migrate" not in events
    assert events[-1] == "postgres-stop"


def test_desktop_app_serves_the_installed_frontend_from_the_configured_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import main

    frontend = tmp_path / "frontend" / "dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("desktop-shell", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        runtime_mode="desktop",
        environment="desktop",
        frontend_dist_dir=str(frontend),
        cors_origins=[],
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    with TestClient(main.create_app(), base_url="http://localhost") as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.text == "desktop-shell"


def test_database_stays_running_and_lock_is_held_until_api_thread_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.desktop_runtime import supervisor

    paths = paths_at(tmp_path)
    events: list[str] = []
    allow_exit = threading.Event()
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    monkeypatch.setattr(supervisor, "SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setenv("CAREER_COPILOT_RUNTIME_MODE", "development")
    monkeypatch.setenv("CAREER_COPILOT_DESKTOP_CONFIG", "")

    class SlowServer(FakeServer):
        def run(self) -> None:
            self.events.append("api-start")
            allow_exit.wait(timeout=2)
            self.events.append("api-stop")

    runtime = DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        postgres=FakePostgres(events),
        backend_root=tmp_path / "install" / "backend",
        server_factory=lambda _port: SlowServer(events),
        readiness_check=lambda _url: True,
        migrate=lambda: None,
    )
    runtime.start()

    with pytest.raises(DesktopRuntimeError, match="database remains running"):
        runtime.stop()
    assert "postgres-stop" not in events
    with pytest.raises(DesktopRuntimeError, match="already active"):
        RuntimeLock(paths.data_dir / "runtime.lock").acquire()

    allow_exit.set()
    runtime.stop()
    assert events[-2:] == ["api-stop", "postgres-stop"]
    check_lock = RuntimeLock(paths.data_dir / "runtime.lock")
    check_lock.acquire()
    check_lock.release()
