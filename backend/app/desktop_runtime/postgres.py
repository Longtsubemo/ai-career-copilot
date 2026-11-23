from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import psycopg
from psycopg import sql

from app.desktop_runtime.bundle import PostgresBundleError, validate_postgres_bundle
from app.desktop_runtime.paths import ApplicationPaths
from app.desktop_runtime.state import (
    DATABASE_NAME,
    DATABASE_USER,
    POSTGRES_MAJOR_VERSION,
    RuntimeState,
    RuntimeStateError,
    RuntimeStateStore,
)


class PostgresRuntimeError(RuntimeError):
    """A managed PostgreSQL cluster could not start or be verified safely."""


COMMAND_TIMEOUT_SECONDS = 45
STARTUP_TIMEOUT_SECONDS = 30
PORT_ATTEMPTS = 5
MIGRATION_BACKUPS_KEPT = 5
MIGRATION_BACKUP_RE = re.compile(r"career-copilot-\d{8}T\d{6}Z-[0-9a-f]{32}\.dump")


class ManagedPostgres:
    def __init__(
        self,
        paths: ApplicationPaths,
        binary_dir: Path,
        *,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        connect: Callable[..., Any] = psycopg.connect,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.paths = paths
        if not binary_dir.is_absolute():
            raise PostgresRuntimeError(
                "the PostgreSQL binary directory must be an explicit absolute path"
            )
        self.binary_dir = binary_dir.resolve()
        self._run = run
        self._connect = connect
        self._sleep = sleep
        self._clock = clock
        self.store = RuntimeStateStore(paths.runtime_config_path)
        self.state: RuntimeState | None = None
        self._verified_running = False

    def start(self) -> RuntimeState:
        self.paths.prepare()
        self._validate_binaries()
        self.state = self.store.load()
        if self.state is None:
            self._create_new_cluster_state()
        else:
            self._validate_existing_cluster_state()
        self._validate_cluster_version()
        if self._server_status() == 0:
            self._verify_admin_connection()
            self._verified_running = True
        else:
            self._start_server()
        self._ensure_database_and_user()
        self._verify_application_connection()
        return self._require_state()

    def stop(self, *, timeout_seconds: int = STARTUP_TIMEOUT_SECONDS) -> None:
        if not self._verified_running or self.state is None:
            return
        if self._server_status() != 0:
            self._verified_running = False
            return
        self._verify_admin_connection()
        result = self._run_tool(
            "pg_ctl",
            [
                "-D",
                str(self.paths.postgres_data_dir),
                "stop",
                "-m",
                "fast",
                "-w",
                "-t",
                str(timeout_seconds),
            ],
            timeout=timeout_seconds + 5,
        )
        if result.returncode != 0:
            raise PostgresRuntimeError("managed PostgreSQL did not shut down cleanly")
        self._verified_running = False

    def is_running(self) -> bool:
        if not self._verified_running:
            return False
        if self._server_status() != 0:
            self._verified_running = False
            return False
        try:
            self._verify_admin_connection()
        except PostgresRuntimeError:
            self._verified_running = False
            return False
        return True

    def ensure_database(self) -> None:
        self._ensure_database_and_user()
        self._verify_application_connection()

    def create_backup(self) -> Path:
        state = self._require_state()
        if not self._verified_running:
            raise PostgresRuntimeError("managed PostgreSQL is not running")
        self.paths.backup_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="migration-backup-", suffix=".tmp", dir=self.paths.backup_dir
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        if os.name != "nt":
            temporary.chmod(0o600)
        environment = self._tool_environment()
        environment["PGPASSWORD"] = state.database_password
        try:
            result = self._run(
                [
                    str(self.binary_dir / self._exe("pg_dump")),
                    "--no-password",
                    "--host=127.0.0.1",
                    f"--port={state.postgres_port}",
                    f"--username={DATABASE_USER}",
                    f"--dbname={DATABASE_NAME}",
                    "--format=custom",
                    f"--file={temporary}",
                ],
                cwd=str(self.paths.install_dir),
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=COMMAND_TIMEOUT_SECONDS,
                **self._creation_flags(),
            )
            if (
                result.returncode != 0
                or not temporary.is_file()
                or temporary.stat().st_size == 0
            ):
                raise PostgresRuntimeError(
                    "database backup failed; migrations were not run"
                )
            timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            for _ in range(10):
                backup = self.paths.backup_dir / (
                    f"career-copilot-{timestamp}-{os.urandom(16).hex()}.dump"
                )
                try:
                    # Hard-link publication is atomic and fails if the chosen
                    # name already exists; it never replaces an earlier dump.
                    os.link(temporary, backup)
                except FileExistsError:
                    continue
                temporary.unlink()
                self._prune_migration_backups()
                return backup
            raise PostgresRuntimeError(
                "database backup could not be published; migrations were not run"
            )
        except BaseException as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Keep the safe backup error below as the externally visible
                # failure. The incomplete file remains isolated in the backup
                # directory and migrations are still blocked.
                pass
            if isinstance(error, Exception):
                if isinstance(error, PostgresRuntimeError):
                    raise
                raise PostgresRuntimeError(
                    "database backup failed; migrations were not run"
                ) from None
            raise

    def _prune_migration_backups(self) -> None:
        """Keep the newest app-made dumps; never touch user recovery copies."""
        # Names start with a UTC timestamp, so name order is age order.
        dumps = sorted(
            path
            for path in self.paths.backup_dir.iterdir()
            if MIGRATION_BACKUP_RE.fullmatch(path.name)
        )
        for old in dumps[:-MIGRATION_BACKUPS_KEPT]:
            try:
                old.unlink()
            except OSError:
                pass  # A leftover dump only costs disk; it must not block migration.

    def _create_new_cluster_state(self) -> None:
        data_dir = self.paths.postgres_data_dir
        marker = self.paths.ownership_marker_path
        if marker.exists():
            raise PostgresRuntimeError(
                "PostgreSQL ownership metadata exists but runtime credentials are missing"
            )
        if data_dir.exists() and any(data_dir.iterdir()):
            raise PostgresRuntimeError(
                "an unrecognized database cluster already exists; it was not modified"
            )
        state = RuntimeState.create()
        self.store.save(state)
        marker.parent.mkdir(parents=True, exist_ok=True)
        self._write_marker(
            {
                "format_version": 1,
                "postgres_major": POSTGRES_MAJOR_VERSION,
                "data_directory": str(data_dir.resolve()),
                "owner": "ai-career-copilot-desktop-runtime",
            }
        )
        self.state = state
        self._initialize_cluster()

    def _validate_existing_cluster_state(self) -> None:
        state = self._require_state()
        try:
            marker = json.loads(self.paths.ownership_marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise PostgresRuntimeError(
                "PostgreSQL ownership metadata is missing or invalid; data was preserved"
            ) from None
        if (
            not isinstance(marker, dict)
            or marker.get("format_version") != 1
            or marker.get("owner") != "ai-career-copilot-desktop-runtime"
            or marker.get("postgres_major") != state.postgres_major
            or Path(str(marker.get("data_directory", ""))).resolve()
            != self.paths.postgres_data_dir.resolve()
        ):
            raise PostgresRuntimeError(
                "database cluster ownership could not be verified; it was not modified"
            )
        data_dir = self.paths.postgres_data_dir
        if not data_dir.exists():
            raise PostgresRuntimeError("managed database files are missing; they were not recreated")
        if state.cluster_state == "ready":
            self._verify_cluster_files()
        elif not any(data_dir.iterdir()):
            self._initialize_cluster()
        else:
            raise PostgresRuntimeError(
                "partial PostgreSQL initialization was detected; files were preserved"
            )

    def _initialize_cluster(self) -> None:
        state = self._require_state()
        data_dir = self.paths.postgres_data_dir
        if data_dir.exists() and any(data_dir.iterdir()):
            raise PostgresRuntimeError(
                "partial PostgreSQL initialization was detected; files were preserved"
            )
        data_dir.mkdir(parents=True, exist_ok=True)
        file_descriptor, password_file_name = tempfile.mkstemp(
            prefix="initdb-", suffix=".pw", dir=self.paths.config_dir
        )
        password_file = Path(password_file_name)
        try:
            if os.name != "nt":
                os.chmod(password_file, 0o600)
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(state.admin_password + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            result = self._run_tool(
                "initdb",
                [
                    "--pgdata",
                    str(data_dir),
                    "--username=postgres",
                    "--auth-local=scram-sha-256",
                    "--auth-host=scram-sha-256",
                    f"--pwfile={password_file}",
                ],
                timeout=COMMAND_TIMEOUT_SECONDS,
                raise_on_error=False,
            )
            if result.returncode != 0:
                raise PostgresRuntimeError("PostgreSQL cluster initialization failed")
            self._verify_cluster_files()
            state.cluster_state = "ready"
            self.store.save(state)
        finally:
            password_file.unlink(missing_ok=True)

    def _validate_binaries(self) -> None:
        required = ("initdb", "pg_ctl", "postgres", "pg_dump", "pg_restore")
        missing = [name for name in required if not (self.binary_dir / self._exe(name)).is_file()]
        if missing:
            raise PostgresRuntimeError("the bundled PostgreSQL runtime is incomplete")
        bundle_root = self.binary_dir.parent
        required_resources = (
            bundle_root / "lib" / "plpgsql.dll",
            bundle_root / "share" / "postgres.bki",
            bundle_root / "share" / "postgresql.conf.sample",
            bundle_root / "share" / "pg_hba.conf.sample",
            bundle_root / "share" / "pg_ident.conf.sample",
            bundle_root / "share" / "extension" / "plpgsql.control",
            bundle_root / "share" / "extension" / "plpgsql--1.0.sql",
        )
        required_resource_directories = (
            bundle_root / "share" / "locale",
            bundle_root / "share" / "timezone",
            bundle_root / "share" / "timezonesets",
            bundle_root / "share" / "tsearch_data",
        )
        if any(not path.is_file() for path in required_resources) or any(
            not path.is_dir() or not any(path.iterdir())
            for path in required_resource_directories
        ):
            raise PostgresRuntimeError(
                "the PostgreSQL runtime is missing required share or PL/pgSQL resources"
            )
        bundle_manifest = None
        if (bundle_root / "bundle.json").exists():
            try:
                bundle_manifest = validate_postgres_bundle(bundle_root)
            except PostgresBundleError as exc:
                raise PostgresRuntimeError(str(exc)) from None
        result = self._run_tool("postgres", ["--version"], timeout=10)
        if result.returncode != 0:
            raise PostgresRuntimeError("the bundled PostgreSQL runtime could not be inspected")
        version_match = re.search(r"\b(\d+\.\d+)\b", result.stdout or "")
        if (
            version_match is None
            or int(version_match.group(1).split(".", 1)[0]) != POSTGRES_MAJOR_VERSION
        ):
            raise PostgresRuntimeError("the bundled PostgreSQL version is unsupported")
        if (
            bundle_manifest is not None
            and version_match.group(1) != str(bundle_manifest["postgres_version"])
        ):
            raise PostgresRuntimeError("the PostgreSQL binary version does not match its bundle")

    def _validate_cluster_version(self) -> None:
        state = self._require_state()
        version_path = self.paths.postgres_data_dir / "PG_VERSION"
        try:
            actual = int(version_path.read_text(encoding="ascii").strip().split(".", 1)[0])
        except (OSError, ValueError):
            raise PostgresRuntimeError("the PostgreSQL cluster version is invalid") from None
        if actual != state.postgres_major:
            raise PostgresRuntimeError(
                "the PostgreSQL cluster version differs from the bundled runtime; data was preserved"
            )

    def _verify_cluster_files(self) -> None:
        if not (self.paths.postgres_data_dir / "PG_VERSION").is_file() or not (
            self.paths.postgres_data_dir / "global" / "pg_control"
        ).is_file():
            raise PostgresRuntimeError(
                "PostgreSQL cluster files are incomplete; data was preserved"
            )

    def _server_status(self) -> int:
        result = self._run_tool(
            "pg_ctl",
            ["-D", str(self.paths.postgres_data_dir), "status"],
            timeout=10,
            raise_on_error=False,
        )
        if result.returncode not in {0, 3}:
            raise PostgresRuntimeError(
                "PostgreSQL process ownership or status could not be verified"
            )
        return result.returncode

    def _start_server(self) -> None:
        state = self._require_state()
        last_error: Exception | None = None
        rejected_ports: set[int] = set()
        for _ in range(PORT_ATTEMPTS):
            port = self._select_port(state.postgres_port, excluded=rejected_ports)
            state.postgres_port = port
            self.store.save(state)
            options = f'-h "127.0.0.1" -p "{port}" -c "listen_addresses=127.0.0.1"'
            try:
                result = self._run_tool(
                    "pg_ctl",
                    [
                        "-D",
                        str(self.paths.postgres_data_dir),
                        "-l",
                        str(self.paths.log_dir / "postgres.log"),
                        "-w",
                        "-t",
                        str(STARTUP_TIMEOUT_SECONDS),
                        "-o",
                        options,
                        "start",
                    ],
                    timeout=STARTUP_TIMEOUT_SECONDS + 5,
                    raise_on_error=False,
                    capture_output=False,
                )
            except BaseException:
                try:
                    self._adopt_if_owned_after_start_attempt()
                except Exception:
                    pass
                raise
            if result.returncode == 0:
                self._verified_running = True
                try:
                    self._verify_admin_connection()
                except PostgresRuntimeError as exc:
                    self._verified_running = False
                    last_error = exc
                    break
                return
            if self._server_status() == 0:
                if self._adopt_if_owned_after_start_attempt():
                    return
                raise PostgresRuntimeError(
                    "PostgreSQL started unexpectedly but its managed identity could not be verified"
                )
            rejected_ports.add(port)
            last_error = PostgresRuntimeError("PostgreSQL did not accept a managed startup")
        raise PostgresRuntimeError(
            "PostgreSQL could not start on an available loopback port"
        ) from last_error

    def _adopt_if_owned_after_start_attempt(self) -> bool:
        if self._server_status() != 0:
            return False
        self._verify_admin_connection()
        self._verified_running = True
        return True

    def _select_port(self, preferred: int | None, *, excluded: set[int] | None = None) -> int:
        unavailable = excluded or set()
        candidates = [preferred] if preferred is not None and preferred not in unavailable else []
        for _ in range(PORT_ATTEMPTS + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
                try:
                    probe.bind(("127.0.0.1", 0))
                except OSError as exc:
                    raise PostgresRuntimeError("a loopback port could not be selected") from exc
                candidates.append(int(probe.getsockname()[1]))
        for candidate in candidates:
            if candidate is None or candidate in unavailable:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind(("127.0.0.1", candidate))
                except OSError:
                    continue
                return candidate
        raise PostgresRuntimeError("no available loopback database port was found")

    def _ensure_database_and_user(self) -> None:
        state = self._require_state()
        if not self._verified_running:
            raise PostgresRuntimeError("managed PostgreSQL is not running")
        try:
            with self._connect(
                host="127.0.0.1",
                port=state.postgres_port,
                dbname="postgres",
                user="postgres",
                password=state.admin_password,
                connect_timeout=3,
                autocommit=True,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT rolname FROM pg_roles WHERE rolname = %s", (DATABASE_USER,))
                    role = cursor.fetchone()
                    cursor.execute(
                        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s",
                        (DATABASE_NAME,),
                    )
                    database = cursor.fetchone()
                    if role is None and database is not None:
                        raise PostgresRuntimeError(
                            "the application database owner is missing; database was preserved"
                        )
                    if role is None:
                        cursor.execute(
                            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                                sql.Identifier(DATABASE_USER),
                                sql.Literal(state.database_password),
                            )
                        )
                    if database is None:
                        cursor.execute(
                            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                                sql.Identifier(DATABASE_NAME),
                                sql.Identifier(DATABASE_USER),
                            )
                        )
                    elif database[0] != DATABASE_USER:
                        raise PostgresRuntimeError(
                            "the application database owner could not be verified; database was preserved"
                        )
            state.database_provisioned = True
            self.store.save(state)
        except PostgresRuntimeError:
            raise
        except Exception:
            raise PostgresRuntimeError(
                "the managed application database could not be verified"
            ) from None

    def _verify_admin_connection(self) -> None:
        state = self._require_state()
        if state.postgres_port is None:
            raise PostgresRuntimeError("managed PostgreSQL port is unavailable")
        try:
            with self._connect(
                host="127.0.0.1",
                port=state.postgres_port,
                dbname="postgres",
                user="postgres",
                password=state.admin_password,
                connect_timeout=3,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT current_setting('data_directory'), current_setting('port'), current_setting('listen_addresses')"
                    )
                    directory, port, addresses = cursor.fetchone()
            if (
                Path(str(directory)).resolve() != self.paths.postgres_data_dir.resolve()
                or int(port) != state.postgres_port
                or str(addresses).strip() != "127.0.0.1"
            ):
                raise PostgresRuntimeError(
                    "a PostgreSQL listener did not match the managed cluster identity"
                )
        except PostgresRuntimeError:
            raise
        except Exception:
            raise PostgresRuntimeError(
                "managed PostgreSQL readiness could not be verified"
            ) from None

    def _verify_application_connection(self) -> None:
        state = self._require_state()
        try:
            with self._connect(
                host="127.0.0.1",
                port=state.postgres_port,
                dbname=DATABASE_NAME,
                user=DATABASE_USER,
                password=state.database_password,
                connect_timeout=3,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    if cursor.fetchone() != (1,):
                        raise PostgresRuntimeError("application database readiness failed")
        except PostgresRuntimeError:
            raise
        except Exception:
            raise PostgresRuntimeError(
                "the application database user could not authenticate"
            ) from None

    def _write_marker(self, payload: dict[str, object]) -> None:
        marker = self.paths.ownership_marker_path
        temporary = marker.with_suffix(".tmp")
        if temporary.exists():
            raise PostgresRuntimeError("stale PostgreSQL ownership metadata was found")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, marker)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise PostgresRuntimeError("PostgreSQL ownership metadata could not be saved") from None

    def _run_tool(
        self,
        name: str,
        arguments: list[str],
        *,
        timeout: int,
        raise_on_error: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess:
        output_options = (
            {"capture_output": True, "text": True}
            if capture_output
            else {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        )
        result = self._run(
            [str(self.binary_dir / self._exe(name)), *arguments],
            cwd=str(self.paths.install_dir),
            env=self._tool_environment(),
            check=False,
            timeout=timeout,
            **output_options,
            **self._creation_flags(),
        )
        if raise_on_error and result.returncode != 0:
            raise PostgresRuntimeError(f"PostgreSQL utility {name} failed")
        return result

    def _tool_environment(self) -> dict[str, str]:
        path = os.environ.get("PATH", "")
        environment = {"PATH": str(self.binary_dir) + os.pathsep + path}
        for name in (
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "LOCALAPPDATA",
            "HOMEDRIVE",
            "HOMEPATH",
        ):
            if name in os.environ:
                environment[name] = os.environ[name]
        return environment

    @staticmethod
    def _creation_flags() -> dict[str, int]:
        if os.name == "nt":
            return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        return {}

    @staticmethod
    def _exe(name: str) -> str:
        return name + ".exe" if os.name == "nt" else name

    def _require_state(self) -> RuntimeState:
        if self.state is None:
            raise PostgresRuntimeError("managed PostgreSQL state is not loaded")
        return self.state


__all__ = ["ManagedPostgres", "PostgresRuntimeError"]
