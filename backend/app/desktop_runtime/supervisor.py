from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from app.desktop_runtime.paths import ApplicationPaths
from app.api.capability import is_valid_api_capability
from app.desktop_runtime.postgres import ManagedPostgres, PostgresRuntimeError
from app.desktop_runtime.state import (
    OBSOLETE_AUTH_SETTING_KEYS,
    RuntimeStateError,
    RuntimeStateStore,
)
from app.desktop_runtime.webview import WEBVIEW_MODES


class DesktopRuntimeError(RuntimeError):
    """The application-managed local services could not start safely."""


STARTUP_TIMEOUT_SECONDS = 45
SHUTDOWN_TIMEOUT_SECONDS = 15
PORT_ATTEMPTS = 5


class RuntimeLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a+b")
        if self._stream.seek(0, os.SEEK_END) == 0:
            self._stream.write(b"0")
            self._stream.flush()
        self._stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._stream.close()
            self._stream = None
            raise DesktopRuntimeError("another application runtime is already active") from None

    def release(self) -> None:
        if self._stream is None:
            return
        self._stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        self._stream.close()
        self._stream = None

    def __enter__(self) -> "RuntimeLock":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class DesktopRuntime:
    def __init__(
        self,
        paths: ApplicationPaths,
        postgres_binary_dir: Path,
        *,
        postgres: ManagedPostgres | None = None,
        backend_root: Path | None = None,
        server_factory: Callable[[int], Any] | None = None,
        readiness_check: Callable[[str], bool] | None = None,
        webview_mode: str = "standalone",
        api_capability: str | None = None,
        migrate: Callable[[], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.paths = paths
        self.postgres = postgres or ManagedPostgres(paths, postgres_binary_dir)
        self.backend_root = (backend_root or Path(__file__).resolve().parents[2]).resolve()
        self.api_capability = api_capability or secrets.token_hex(32)
        if not is_valid_api_capability(self.api_capability):
            raise ValueError("desktop API capability is invalid")
        self._server_factory = server_factory or (
            lambda port: self._default_server(port, self.api_capability)
        )
        self._readiness_check = readiness_check or (
            lambda url: _http_readiness_check(url, self.api_capability)
        )
        self._migrate_override = migrate
        if webview_mode not in WEBVIEW_MODES:
            raise ValueError("unsupported desktop WebView mode")
        self.webview_mode = webview_mode
        self.api_url_host, self.cors_origins = WEBVIEW_MODES[webview_mode]
        self._sleep = sleep
        self._clock = clock
        self._lock = RuntimeLock(paths.data_dir / "runtime.lock")
        self._server = None
        self._server_thread: threading.Thread | None = None
        self._started = False
        self.api_url: str | None = None

    def start(self) -> str:
        self.paths.prepare()
        self._lock.acquire()
        try:
            state = self.postgres.start()
            settings = self._load_desktop_settings(state.settings_payload(state.postgres_port))
            self._save_desktop_settings(settings)
            self._configure_process_environment(settings["database_url"])
            self._apply_migrations_if_needed()
            self.api_url = self._start_backend()
            self._started = True
            return self.api_url
        except BaseException as exc:
            try:
                self.stop()
            except Exception:
                pass
            if not isinstance(exc, Exception):
                raise
            if isinstance(exc, DesktopRuntimeError):
                raise
            if isinstance(exc, (PostgresRuntimeError, RuntimeStateError)):
                raise DesktopRuntimeError(str(exc)) from None
            raise DesktopRuntimeError(
                f"desktop startup failed ({type(exc).__name__}); persistent data was preserved"
            ) from None

    def run_until_stopped(
        self,
        *,
        poll_interval: float = 1.0,
        shutdown_event: threading.Event | None = None,
    ) -> None:
        if not self._started:
            self.start()
        try:
            while True:
                if shutdown_event is not None and shutdown_event.is_set():
                    return
                thread = self._server_thread
                if thread is None or not thread.is_alive():
                    raise DesktopRuntimeError("the local API stopped unexpectedly")
                if not self.postgres.is_running():
                    raise DesktopRuntimeError(
                        "the managed database stopped unexpectedly; data was preserved"
                    )
                self._sleep(poll_interval)
        except KeyboardInterrupt:
            return
        finally:
            self.stop()

    def stop(self) -> None:
        shutdown_error: Exception | None = None
        server = self._server
        thread = self._server_thread
        if server is not None and thread is not None and thread.is_alive():
            server.should_exit = True
            thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)
            if thread.is_alive():
                server.force_exit = True
                thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)
            if thread.is_alive():
                raise DesktopRuntimeError(
                    "the local API did not shut down cleanly; the database remains running"
                )
        try:
            try:
                self.postgres.stop()
            except Exception as exc:
                shutdown_error = shutdown_error or exc
            if shutdown_error is not None:
                raise DesktopRuntimeError(
                    f"managed services did not shut down cleanly ({type(shutdown_error).__name__})"
                ) from None
        finally:
            self._server = None
            self._server_thread = None
            self._started = False
            self._lock.release()

    def _load_desktop_settings(self, generated: dict[str, Any]) -> dict[str, Any]:
        bundled_frontend = (
            self.paths.install_dir / "resources" / "frontend" / "dist"
        )
        portable_frontend = self.paths.install_dir / "frontend" / "dist"
        frontend_dist = (
            bundled_frontend
            if (bundled_frontend / "index.html").is_file()
            else portable_frontend
        )
        generated["frontend_dist_dir"] = str(frontend_dist)
        path = self.paths.desktop_settings_path
        if not path.exists():
            if (
                self.postgres.state is not None
                and self.postgres.state.desktop_configuration_initialized
            ):
                raise DesktopRuntimeError(
                    "desktop configuration is missing; it was not recreated automatically"
                )
            settings = generated
        else:
            try:
                settings = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise DesktopRuntimeError("desktop configuration is unreadable") from None
            if not isinstance(settings, dict):
                raise DesktopRuntimeError("desktop configuration is invalid")
            settings.update(generated)
        settings["runtime_mode"] = "desktop"
        settings["environment"] = "desktop"
        for obsolete_key in OBSOLETE_AUTH_SETTING_KEYS:
            settings.pop(obsolete_key, None)
        enable_resume_ai = settings.get("enable_resume_ai", False)
        if not isinstance(enable_resume_ai, bool):
            raise DesktopRuntimeError("desktop configuration is invalid")
        settings["enable_resume_ai"] = enable_resume_ai
        settings["debug"] = False
        settings["cors_origins"] = list(self.cors_origins)
        settings["database_url"] = generated["database_url"]
        settings["frontend_dist_dir"] = generated["frontend_dist_dir"]
        return settings

    def _save_desktop_settings(self, settings: dict[str, Any]) -> None:
        path = self.paths.desktop_settings_path
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="desktop-settings-", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(settings, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                path.chmod(0o600)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise DesktopRuntimeError("desktop configuration could not be saved") from None
        if self.postgres.state is not None:
            self.postgres.state.desktop_configuration_initialized = True
            RuntimeStateStore(self.paths.runtime_config_path).save(self.postgres.state)

    def _configure_process_environment(self, expected_database_url: str) -> None:
        os.environ["CAREER_COPILOT_RUNTIME_MODE"] = "desktop"
        os.environ["CAREER_COPILOT_DESKTOP_CONFIG"] = str(
            self.paths.desktop_settings_path.resolve()
        )
        from app.core.config import get_settings

        get_settings.cache_clear()
        settings = get_settings()
        if settings.database_url != expected_database_url:
            raise DesktopRuntimeError(
                "desktop database configuration was not selected; migrations were not run"
            )
        session_module = sys.modules.get("app.db.session")
        if self._migrate_override is None and session_module is not None:
            if session_module.engine.url != make_url(expected_database_url):
                raise DesktopRuntimeError(
                    "desktop runtime must start in a fresh backend process; migrations were not run"
                )

    def _apply_migrations_if_needed(self) -> None:
        if self._migrate_override is not None:
            self.postgres.create_backup()
            self._migrate_override()
            return
        config = self._alembic_config()
        scripts = ScriptDirectory.from_config(config)
        expected = set(scripts.get_heads())
        applied = self._applied_revisions()
        if applied == expected:
            return
        self.postgres.create_backup()
        command.upgrade(config, "head")
        if self._applied_revisions() != expected:
            raise DesktopRuntimeError("database migration did not reach the current head")

    def _applied_revisions(self) -> set[str]:
        state = self.postgres.state
        if state is None or state.postgres_port is None:
            raise DesktopRuntimeError("database connection is unavailable for migration checks")
        try:
            with self.postgres._connect(
                host="127.0.0.1",
                port=state.postgres_port,
                dbname="career_copilot",
                user="career_copilot_app",
                password=state.database_password,
                connect_timeout=3,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT to_regclass('public.alembic_version')")
                    exists = cursor.fetchone()[0]
                    if exists is None:
                        return set()
                    cursor.execute("SELECT version_num FROM alembic_version")
                    return {str(row[0]) for row in cursor.fetchall()}
        except Exception:
            raise DesktopRuntimeError("database migration state could not be read") from None

    def _alembic_config(self) -> Config:
        ini_path = self.backend_root / "alembic.ini"
        migration_path = self.backend_root / "alembic"
        if not ini_path.is_file() or not migration_path.is_dir():
            raise DesktopRuntimeError("bundled database migrations are unavailable")
        config = Config(str(ini_path))
        config.set_main_option("script_location", str(migration_path))
        return config

    def _start_backend(self) -> str:
        last_error: Exception | None = None
        for _ in range(PORT_ATTEMPTS):
            port = _select_loopback_port()
            server = self._server_factory(port)
            thread = threading.Thread(
                target=server.run,
                name="career-copilot-api",
                daemon=True,
            )
            self._server = server
            self._server_thread = thread
            thread.start()
            deadline = self._clock() + STARTUP_TIMEOUT_SECONDS
            readiness_url = f"http://127.0.0.1:{port}"
            api_url = f"http://{self.api_url_host}:{port}"
            while self._clock() < deadline:
                if not thread.is_alive():
                    last_error = RuntimeError("the local API process exited during startup")
                    break
                if getattr(server, "started", False) and self._readiness_check(
                    readiness_url + "/health/ready"
                ):
                    return api_url
                self._sleep(0.1)
            if self._server_thread is not None and self._server_thread.is_alive():
                server.should_exit = True
                thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)
                if thread.is_alive():
                    self._server = server
                    self._server_thread = thread
                    raise DesktopRuntimeError("the local API failed to stop after startup")
            self._server = None
            self._server_thread = None
        raise DesktopRuntimeError(
            f"local API readiness failed ({type(last_error).__name__ if last_error else 'TimeoutError'})"
        ) from None

    @staticmethod
    def _default_server(port: int, api_capability: str | None = None):
        import uvicorn

        from app.main import create_app

        config = uvicorn.Config(
            create_app(api_capability=api_capability),
            host="127.0.0.1",
            port=port,
            log_config=None,
            access_log=False,
        )
        return uvicorn.Server(config)


def _select_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _http_readiness_check(url: str, api_capability: str) -> bool:
    try:
        with httpx.Client(timeout=1.0, trust_env=False) as client:
            response = client.get(
                url,
                headers={"Authorization": f"Bearer {api_capability}"},
            )
        return response.status_code == 200 and response.json() == {"status": "ready"}
    except (httpx.HTTPError, ValueError):
        return False


__all__ = ["DesktopRuntime", "DesktopRuntimeError", "RuntimeLock"]
