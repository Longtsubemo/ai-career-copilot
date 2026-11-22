from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.engine import URL


class RuntimeStateError(RuntimeError):
    """Managed runtime metadata is invalid or cannot be safely persisted."""


STATE_VERSION = 1
POSTGRES_MAJOR_VERSION = 16
DATABASE_NAME = "career_copilot"
DATABASE_USER = "career_copilot_app"
OBSOLETE_AUTH_SETTING_KEYS = frozenset(
    {
        "session_cookie_name",
        "session_ttl_seconds",
        "session_cookie_secure",
        "session_cookie_samesite",
        "csrf_cookie_name",
    }
)


@dataclass
class RuntimeState:
    postgres_major: int
    admin_password: str
    database_password: str
    postgres_port: int | None = None
    cluster_state: str = "initializing"
    database_provisioned: bool = False
    desktop_configuration_initialized: bool = False
    desktop_settings: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls) -> "RuntimeState":
        return cls(
            postgres_major=POSTGRES_MAJOR_VERSION,
            admin_password=secrets.token_urlsafe(48),
            database_password=secrets.token_urlsafe(48),
            desktop_settings={
                "runtime_mode": "desktop",
                "environment": "desktop",
                "enable_resume_ai": False,
                "cors_origins": [],
            },
        )

    def database_url(self, port: int) -> str:
        return URL.create(
            "postgresql+psycopg",
            username=DATABASE_USER,
            password=self.database_password,
            host="127.0.0.1",
            port=port,
            database=DATABASE_NAME,
        ).render_as_string(hide_password=False)

    def settings_payload(self, port: int) -> dict[str, Any]:
        return {
            **self.desktop_settings,
            "runtime_mode": "desktop",
            "environment": "desktop",
            "database_url": self.database_url(port),
        }


class RuntimeStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> RuntimeState | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise RuntimeStateError("managed runtime metadata is unreadable") from None
        if (
            not isinstance(payload, dict)
            or type(payload.get("format_version")) is not int
            or payload.get("format_version") != STATE_VERSION
        ):
            raise RuntimeStateError("managed runtime metadata has an unsupported format")
        try:
            state = RuntimeState(
                postgres_major=payload["postgres_major"],
                admin_password=payload["admin_password"],
                database_password=payload["database_password"],
                postgres_port=payload.get("postgres_port"),
                cluster_state=payload["cluster_state"],
                database_provisioned=payload["database_provisioned"],
                desktop_configuration_initialized=payload.get(
                    "desktop_configuration_initialized", False
                ),
                desktop_settings=payload["desktop_settings"],
            )
        except (KeyError, TypeError):
            raise RuntimeStateError("managed runtime metadata is incomplete") from None
        state.desktop_settings = {
            key: value
            for key, value in state.desktop_settings.items()
            if key not in OBSOLETE_AUTH_SETTING_KEYS
        }
        if (
            type(state.postgres_major) is not int
            or state.postgres_major != POSTGRES_MAJOR_VERSION
            or not isinstance(state.admin_password, str)
            or not state.admin_password
            or not isinstance(state.database_password, str)
            or not state.database_password
            or not isinstance(state.cluster_state, str)
            or state.cluster_state not in {"initializing", "ready"}
            or not isinstance(state.database_provisioned, bool)
            or not isinstance(state.desktop_configuration_initialized, bool)
            or not isinstance(state.desktop_settings, dict)
            or (
                state.postgres_port is not None
                and (
                    type(state.postgres_port) is not int
                    or not 1 <= state.postgres_port <= 65_535
                )
            )
        ):
            raise RuntimeStateError("managed runtime metadata failed validation")
        return state

    def save(self, state: RuntimeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="runtime-", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(
                    {
                        "format_version": STATE_VERSION,
                        "postgres_major": state.postgres_major,
                        "admin_password": state.admin_password,
                        "database_password": state.database_password,
                        "postgres_port": state.postgres_port,
                        "cluster_state": state.cluster_state,
                        "database_provisioned": state.database_provisioned,
                        "desktop_configuration_initialized": state.desktop_configuration_initialized,
                        "desktop_settings": state.desktop_settings,
                    },
                    stream,
                    separators=(",", ":"),
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            finally:
                raise


__all__ = [
    "DATABASE_NAME",
    "DATABASE_USER",
    "OBSOLETE_AUTH_SETTING_KEYS",
    "POSTGRES_MAJOR_VERSION",
    "RuntimeState",
    "RuntimeStateError",
    "RuntimeStateStore",
]
