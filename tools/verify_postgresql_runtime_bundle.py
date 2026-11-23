from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

import httpx
import psycopg
from psycopg import sql
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, insert, inspect, text
from sqlalchemy.dialects.postgresql import JSONB

import app.models  # noqa: F401
from app.db.base import Base
from app.desktop_runtime.bundle import resolve_bundled_postgres_binary_dir
from app.desktop_runtime.paths import ApplicationPaths
from app.desktop_runtime.supervisor import DesktopRuntime
from app.models.company import Company


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _isolated_environment(binary_dir: Path) -> dict[str, str]:
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        raise RuntimeError("Windows SystemRoot is unavailable")
    return {
        "PATH": os.pathsep.join((str(binary_dir), str(Path(system_root) / "System32"))),
        "SYSTEMROOT": system_root,
        "WINDIR": system_root,
        "TEMP": os.environ.get("TEMP", str(REPO_ROOT)),
        "TMP": os.environ.get("TMP", os.environ.get("TEMP", str(REPO_ROOT))),
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
    }


def _run_tool(
    binary_dir: Path,
    env: dict[str, str],
    name: str,
    *arguments: str,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    output_options = (
        {"capture_output": True, "text": True}
        if capture_output
        else {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    )
    return subprocess.run(
        [str(binary_dir / f"{name}.exe"), *arguments],
        cwd=REPO_ROOT,
        env=env,
        **output_options,
        check=False,
        timeout=60,
    )


def _revision_and_schema(connection: psycopg.Connection[Any]) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM alembic_version")
        revisions = sorted(str(row[0]) for row in cursor.fetchall())
        cursor.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        tables = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            "SELECT table_name || '.' || column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND data_type='jsonb' ORDER BY 1"
        )
        jsonb_columns = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n "
            "ON n.oid=c.connamespace WHERE n.nspname='public'"
        )
        constraints = int(cursor.fetchone()[0])
        cursor.execute(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='public'"
        )
        indexes = int(cursor.fetchone()[0])
    return {
        "revisions": revisions,
        "tables": tables,
        "jsonb_columns": jsonb_columns,
        "constraints": constraints,
        "indexes": indexes,
    }


def _create_restore_cluster(
    binary_dir: Path, env: dict[str, str], root: Path
) -> tuple[Path, int, str]:
    data_dir = root / "restore-cluster" / "data"
    data_dir.parent.mkdir(parents=True, exist_ok=False)
    admin_password = secrets.token_urlsafe(36)
    pwfile = root / "restore-initdb-password.tmp"
    pwfile.write_text(admin_password + "\n", encoding="utf-8")
    try:
        result = _run_tool(
            binary_dir,
            env,
            "initdb",
            "--pgdata",
            str(data_dir),
            "--username=postgres",
            "--auth-local=scram-sha-256",
            "--auth-host=scram-sha-256",
            f"--pwfile={pwfile}",
        )
        if result.returncode != 0:
            raise RuntimeError("reduced-bundle initdb failed for the restore cluster")
    finally:
        pwfile.unlink(missing_ok=True)

    port = _free_loopback_port()
    log_path = root / "restore-cluster.log"
    options = f'-h "127.0.0.1" -p "{port}" -c "listen_addresses=127.0.0.1"'
    result = _run_tool(
        binary_dir,
        env,
        "pg_ctl",
        "-D",
        str(data_dir),
        "-l",
        str(log_path),
        "-w",
        "-t",
        "30",
        "-o",
        options,
        "start",
        capture_output=False,
    )
    if result.returncode != 0:
        raise RuntimeError("reduced-bundle pg_ctl failed to start the restore cluster")
    return data_dir, port, admin_password


def _create_restore_database(
    port: int, admin_password: str, app_password: str, database_name: str
) -> None:
    with psycopg.connect(
        host="127.0.0.1",
        port=port,
        dbname="postgres",
        user="postgres",
        password=admin_password,
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier("career_copilot_app"), sql.Literal(app_password)
                )
            )
            cursor.execute(
                "CREATE DATABASE {} OWNER career_copilot_app".format(
                    psycopg.sql.Identifier(database_name).as_string(connection)
                )
            )


def verify(data_root: Path | None = None) -> dict[str, Any]:
    binary_dir = resolve_bundled_postgres_binary_dir(REPO_ROOT)
    env = _isolated_environment(binary_dir)
    os.environ["PATH"] = env["PATH"]
    if data_root is None:
        data_root = Path(tempfile.mkdtemp(prefix="AI-Career-Copilot-min-pg16.16-"))
    else:
        data_root = data_root.resolve()
        data_root.mkdir(parents=True, exist_ok=False)
    data_dir = data_root / "AI Career Copilot"
    paths = ApplicationPaths(install_dir=REPO_ROOT, data_dir=data_dir)
    runtime = DesktopRuntime(paths, binary_dir, backend_root=BACKEND_ROOT)
    restore_data: Path | None = None
    restore_port: int | None = None
    restore_admin_password: str | None = None
    result: dict[str, Any] = {"temporary_root": str(data_root)}

    try:
        api_url = runtime.start()
        print("verification: managed runtime started", flush=True)
        state = runtime.postgres.state
        if state is None or state.postgres_port is None:
            raise RuntimeError("managed runtime did not record its PostgreSQL port")
        with psycopg.connect(
            host="127.0.0.1",
            port=state.postgres_port,
            dbname="career_copilot",
            user="career_copilot_app",
            password=state.database_password,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT inet_server_addr()::text, current_setting('listen_addresses')")
                bound_address, listen_addresses = cursor.fetchone()
            address = ipaddress.ip_interface(str(bound_address)).ip
            if address != ipaddress.IPv4Address("127.0.0.1") or listen_addresses.strip() != "127.0.0.1":
                raise RuntimeError(
                    "managed PostgreSQL bound address/listen_addresses were "
                    f"{str(bound_address)!r}/{listen_addresses!r}"
                )

        versions: dict[str, str] = {}
        for executable in ("initdb", "pg_ctl", "postgres", "pg_dump", "pg_restore"):
            version_result = _run_tool(binary_dir, env, executable, "--version")
            if version_result.returncode != 0 or "16.16" not in version_result.stdout:
                raise RuntimeError(f"{executable} did not report PostgreSQL 16.16")
            versions[executable] = version_result.stdout.strip()

        alembic_config = runtime._alembic_config()
        revision_count = len(list(ScriptDirectory.from_config(alembic_config).walk_revisions()))
        if revision_count != 8:
            raise RuntimeError(f"expected eight Alembic revisions; found {revision_count}")
        command.check(alembic_config)
        print("verification: migrations and alembic check passed", flush=True)

        engine = create_engine(state.database_url(state.postgres_port))
        try:
            with engine.begin() as connection:
                json_value = connection.scalar(
                    text(
                        "SELECT jsonb_build_object('bundle', "
                        "jsonb_build_object('version', CAST(:value AS text)))::jsonb "
                        "#>> '{bundle,version}'"
                    ),
                    {"value": "jsonb-ok"},
                )
                connection.execute(
                    text("SELECT 1")
                ).scalar_one()
                connection.execute(
                    insert(Company).values(
                        name="PostgreSQL bundle verification",
                        normalized_name=f"postgresql-bundle-verification-{uuid4().hex}",
                    )
                )
            if json_value != "jsonb-ok":
                raise RuntimeError("PostgreSQL JSONB operation returned an unexpected value")
            source_tables = inspect(engine).get_table_names(schema="public")
            expected_tables = set(Base.metadata.tables)
            if not expected_tables.issubset(set(source_tables)):
                raise RuntimeError("one or more expected application tables are missing")
            jsonb_expected = {
                f"{table.name}.{column.name}"
                for table in Base.metadata.tables.values()
                for column in table.columns
                if isinstance(column.type, JSONB)
            }
            schema = None
            with psycopg.connect(
                host="127.0.0.1",
                port=state.postgres_port,
                dbname="career_copilot",
                user="career_copilot_app",
                password=state.database_password,
            ) as connection:
                schema = _revision_and_schema(connection)
            if not jsonb_expected.issubset(set(schema["jsonb_columns"])):
                raise RuntimeError("one or more expected JSONB columns are missing")
            if not schema["constraints"] or not schema["indexes"]:
                raise RuntimeError("application constraints or indexes are missing")
            if schema["revisions"] != ["20261016_0008"]:
                raise RuntimeError("the source cluster did not reach the expected Alembic head")
        finally:
            engine.dispose()
        print("verification: schema and database operations passed", flush=True)

        for route in ("/health", "/health/ready"):
            response = httpx.get(api_url + route, timeout=10)
            if response.status_code != 200:
                raise RuntimeError(f"FastAPI {route} did not return HTTP 200")
        print("verification: health endpoints passed", flush=True)

        backup_path = runtime.postgres.create_backup()
        if not backup_path.is_file() or backup_path.stat().st_size == 0:
            raise RuntimeError("custom-format database backup was not created")
        toc = _run_tool(binary_dir, env, "pg_restore", "--list", str(backup_path))
        if toc.returncode != 0 or "TABLE DATA" not in toc.stdout:
            raise RuntimeError("pg_restore could not inspect the custom-format backup")
        print("verification: custom backup created and inspected", flush=True)

        restore_data, restore_port, restore_admin_password = _create_restore_cluster(
            binary_dir, env, data_root
        )
        print("verification: second isolated PostgreSQL cluster started", flush=True)
        restore_app_password = secrets.token_urlsafe(36)
        _create_restore_database(
            restore_port,
            restore_admin_password,
            restore_app_password,
            "career_copilot",
        )
        print("verification: restore database and role provisioned", flush=True)
        restore_env = {**env, "PGPASSWORD": restore_app_password}
        restored = subprocess.run(
            [
                str(binary_dir / "pg_restore.exe"),
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "--host=127.0.0.1",
                f"--port={restore_port}",
                "--username=career_copilot_app",
                "--dbname=career_copilot",
                str(backup_path),
            ],
            cwd=REPO_ROOT,
            env=restore_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        print("verification: pg_restore process returned", flush=True)
        if restored.returncode != 0:
            raise RuntimeError("custom-format backup restore failed")
        with psycopg.connect(
            host="127.0.0.1",
            port=restore_port,
            dbname="career_copilot",
            user="career_copilot_app",
            password=restore_app_password,
        ) as connection:
            restored_schema = _revision_and_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM companies WHERE normalized_name LIKE "
                    "'postgresql-bundle-verification-%'"
                )
                restored_company_count = int(cursor.fetchone()[0])
        if restored_schema != schema or restored_company_count != 1:
            raise RuntimeError("restored schema, revision, or synthetic data did not match")
        print("verification: restore into second isolated cluster passed", flush=True)

        invalid_backup = data_root / "invalid-restore.dump"
        invalid_backup.write_bytes(b"not a PostgreSQL archive")
        with psycopg.connect(
            host="127.0.0.1",
            port=restore_port,
            dbname="postgres",
            user="postgres",
            password=restore_admin_password,
            autocommit=True,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE DATABASE career_copilot_restore_probe "
                    "OWNER career_copilot_app"
                )
        invalid = subprocess.run(
            [
                str(binary_dir / "pg_restore.exe"),
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "--host=127.0.0.1",
                f"--port={restore_port}",
                "--username=career_copilot_app",
                "--dbname=career_copilot_restore_probe",
                str(invalid_backup),
            ],
            cwd=REPO_ROOT,
            env={**env, "PGPASSWORD": restore_app_password},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if invalid.returncode == 0:
            raise RuntimeError("invalid restore unexpectedly succeeded")
        with psycopg.connect(
            host="127.0.0.1",
            port=restore_port,
            dbname="career_copilot_restore_probe",
            user="career_copilot_app",
            password=restore_app_password,
        ) as connection:
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                ).fetchall()
            }
            probe = _revision_and_schema(connection) if "alembic_version" in table_names else None
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
                probe_table_count = int(cursor.fetchone()[0])
        if probe is not None or probe_table_count != 0:
            raise RuntimeError("invalid restore left application tables or a revision marker")
        print("verification: invalid archive rejected without application tables", flush=True)

        runtime.stop()
        print("verification: managed runtime stopped", flush=True)
        status = runtime.postgres._run_tool(
            "pg_ctl", ["-D", str(paths.postgres_data_dir), "status"], timeout=10, raise_on_error=False
        )
        if status.returncode != 3:
            raise RuntimeError("managed PostgreSQL did not report a clean shutdown")
        restarted_api = runtime.start()
        print("verification: managed runtime restarted", flush=True)
        with httpx.Client(timeout=10) as client:
            if client.get(restarted_api + "/health/ready").status_code != 200:
                raise RuntimeError("managed PostgreSQL/API recovery did not become ready")
        with psycopg.connect(
            host="127.0.0.1",
            port=runtime.postgres.state.postgres_port,
            dbname="career_copilot",
            user="career_copilot_app",
            password=runtime.postgres.state.database_password,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM companies WHERE normalized_name LIKE "
                    "'postgresql-bundle-verification-%'"
                )
                persisted_company_count = int(cursor.fetchone()[0])
        if persisted_company_count != 1:
            raise RuntimeError("synthetic data was not preserved across runtime restart")
        print("verification: persisted data survived restart", flush=True)

        result.update(
            {
                "bundle_directory": str(binary_dir.parent),
                "postgres_versions": versions,
                "api_health": "passed",
                "loopback_only": True,
                "alembic_revisions": revision_count,
                "schema": schema,
                "jsonb_operation": "passed",
                "restored_schema_and_data_match": True,
                "invalid_restore_rejected_without_application_tables": True,
                "restart_recovery": "passed",
                "backup_bytes": backup_path.stat().st_size,
                "backup_toc_entries": toc.stdout.count(";"),
                "clean_path_isolated_from_development_postgres": True,
            }
        )
        return result
    finally:
        if runtime._started:
            runtime.stop()
        if restore_data is not None and restore_port is not None:
            stop = _run_tool(
                binary_dir,
                env,
                "pg_ctl",
                "-D",
                str(restore_data),
                "-m",
                "fast",
                "-w",
                "-t",
                "30",
                "stop",
            )
            if stop.returncode != 0:
                print("WARNING: restore cluster did not stop cleanly", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the reduced PostgreSQL bundle using isolated temporary clusters."
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        help="new temporary root to create; it must not already exist",
    )
    arguments = parser.parse_args()
    try:
        results = verify(arguments.data_root)
    except Exception as exc:
        detail = str(exc)
        detail = re.sub(
            r"(?i)postgres(?:ql)?(?:\+\w+)?://\S+",
            "[redacted database URL]",
            detail,
        )
        detail = re.sub(
            r"(?i)(password\s*[=:]\s*)[^\s,;]+",
            r"\1[redacted]",
            detail,
        )
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "detail": detail,
                    "message": "reduced PostgreSQL bundle verification failed; temporary data was preserved",
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"status": "passed", **results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
