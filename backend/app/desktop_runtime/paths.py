from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class DesktopPathError(RuntimeError):
    """A managed runtime path is unsafe or unavailable."""


def default_data_directory(environ: dict[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    local_app_data = values.get("LOCALAPPDATA")
    if not local_app_data:
        raise DesktopPathError("Windows per-user application data is unavailable")
    root = Path(local_app_data)
    if not root.is_absolute():
        raise DesktopPathError("Windows per-user application data is unavailable")
    return root / "AI Career Copilot"


@dataclass(frozen=True)
class ApplicationPaths:
    install_dir: Path
    data_dir: Path

    @property
    def config_dir(self) -> Path:
        return self.data_dir / "config"

    @property
    def postgres_data_dir(self) -> Path:
        return self.data_dir / "postgres" / "data"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def runtime_config_path(self) -> Path:
        return self.config_dir / "runtime.json"

    @property
    def desktop_settings_path(self) -> Path:
        return self.config_dir / "desktop-settings.json"

    @property
    def ownership_marker_path(self) -> Path:
        return self.config_dir / "postgres-cluster.json"

    def prepare(self) -> None:
        install = self.install_dir.resolve()
        data = self.data_dir.resolve()
        if (
            not self.install_dir.is_absolute()
            or not self.data_dir.is_absolute()
            or data == install
            or install in data.parents
            or data in install.parents
            or data.name.casefold() != "ai career copilot"
            or data == data.parent
        ):
            raise DesktopPathError(
                "persistent application data must use a dedicated per-user directory outside the installation"
            )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._secure_directory(self.data_dir)
        for directory in (
            self.config_dir,
            self.postgres_data_dir.parent,
            self.postgres_data_dir,
            self.backup_dir,
            self.log_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
            self._secure_directory(directory)

    @staticmethod
    def _secure_directory(path: Path) -> None:
        if os.name == "nt":
            user = subprocess.run(
                ["whoami"], capture_output=True, text=True, check=False, timeout=10
            )
            if user.returncode != 0 or not user.stdout.strip():
                raise DesktopPathError("could not determine the current Windows user")
            entries = [
                f"{user.stdout.strip()}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
            ]
            secured = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", *entries],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            if secured.returncode != 0:
                raise DesktopPathError("could not secure the application data directory")
        else:
            path.chmod(0o700)


__all__ = ["ApplicationPaths", "DesktopPathError", "default_data_directory"]
