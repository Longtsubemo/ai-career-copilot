from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


POSTGRES_VERSION = "16.16"
POSTGRES_ARCHITECTURE = "windows-x64"
BUNDLE_DIRECTORY_NAME = "windows-x64-minimal"
BUNDLE_RELATIVE_PATH = Path("resources") / "postgresql" / POSTGRES_VERSION / BUNDLE_DIRECTORY_NAME
SOURCE_ARCHIVE_SHA256 = "5c0d06f3d26a929ecb36dd931409f1e51a4181355794a259b90aae8bffc57a9f"

REQUIRED_DLLS = (
    "bin/icudt67.dll",
    "bin/icuin67.dll",
    "bin/icuuc67.dll",
    "bin/libcrypto-3-x64.dll",
    "bin/libiconv-2.dll",
    "bin/libintl-9.dll",
    "bin/liblz4.dll",
    "bin/libpq.dll",
    "bin/libssl-3-x64.dll",
    "bin/libxml2.dll",
    "bin/libzstd.dll",
    "bin/zlib1.dll",
)
REQUIRED_CONVERSION_MODULES = (
    "cyrillic_and_mic.dll",
    "euc_cn_and_mic.dll",
    "euc_jp_and_sjis.dll",
    "euc_kr_and_mic.dll",
    "euc_tw_and_big5.dll",
    "latin_and_mic.dll",
    "latin2_and_win1250.dll",
    "utf8_and_big5.dll",
    "utf8_and_cyrillic.dll",
    "utf8_and_euc_cn.dll",
    "utf8_and_euc_jp.dll",
    "utf8_and_euc_kr.dll",
    "utf8_and_euc_tw.dll",
    "utf8_and_euc2004.dll",
    "utf8_and_gb18030.dll",
    "utf8_and_gbk.dll",
    "utf8_and_iso8859_1.dll",
    "utf8_and_iso8859.dll",
    "utf8_and_johab.dll",
    "utf8_and_sjis.dll",
    "utf8_and_sjis2004.dll",
    "utf8_and_uhc.dll",
    "utf8_and_win.dll",
)
REQUIRED_BOOTSTRAP_MODULES = (
    "lib/dict_snowball.dll",
    "lib/euc2004_sjis2004.dll",
)

REQUIRED_BUNDLE_FILES = (
    "bin/initdb.exe",
    "bin/pg_ctl.exe",
    "bin/postgres.exe",
    "bin/pg_dump.exe",
    "bin/pg_restore.exe",
    *REQUIRED_DLLS,
    "lib/plpgsql.dll",
    *(f"lib/{name}" for name in REQUIRED_CONVERSION_MODULES),
    *REQUIRED_BOOTSTRAP_MODULES,
    "share/postgres.bki",
    "share/postgresql.conf.sample",
    "share/pg_hba.conf.sample",
    "share/pg_ident.conf.sample",
    "share/extension/plpgsql.control",
    "share/extension/plpgsql--1.0.sql",
    "licenses/server_license.txt",
    "licenses/commandlinetools_3rd_party_licenses.txt",
)
REQUIRED_BUNDLE_DIRECTORIES = (
    "share/locale",
    "share/timezone",
    "share/timezonesets",
    "share/tsearch_data",
)


class PostgresBundleError(RuntimeError):
    """A packaged PostgreSQL runtime is missing or incompatible."""


def resolve_bundled_postgres_binary_dir(install_root: Path | None = None) -> Path:
    """Find and validate the versioned PostgreSQL bundle beside the app."""
    if install_root is None:
        # backend/app/desktop_runtime/bundle.py -> application installation root
        install_root = Path(__file__).resolve().parents[3]
    root = (install_root / BUNDLE_RELATIVE_PATH).resolve()
    if not root.is_dir():
        raise PostgresBundleError("the PostgreSQL 16.16 Windows x64 bundle is missing")
    validate_postgres_bundle(root)
    return root / "bin"


def validate_postgres_bundle(bundle_root: Path) -> dict[str, Any]:
    """Validate bundle identity and the hashes recorded by its build manifest."""
    manifest_path = bundle_root / "bundle.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise PostgresBundleError("the PostgreSQL bundle manifest is missing or invalid") from None
    if (
        not isinstance(payload, dict)
        or payload.get("format_version") != 1
        or payload.get("postgres_version") != POSTGRES_VERSION
        or payload.get("architecture") != POSTGRES_ARCHITECTURE
        or payload.get("source_archive_sha256") != SOURCE_ARCHIVE_SHA256
        or not isinstance(payload.get("files"), list)
    ):
        raise PostgresBundleError("the PostgreSQL bundle is incompatible")

    root = bundle_root.resolve()
    if not isinstance(payload.get("files"), list):
        raise PostgresBundleError("the PostgreSQL bundle manifest is invalid")
    listed: set[str] = set()
    for item in payload["files"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("sha256"), str)
            or not isinstance(item.get("size"), int)
        ):
            raise PostgresBundleError("the PostgreSQL bundle manifest is invalid")
        relative = PurePosixPath(item["path"])
        if relative.is_absolute() or ".." in relative.parts or "\\" in item["path"]:
            raise PostgresBundleError("the PostgreSQL bundle manifest contains an unsafe path")
        normalized = relative.as_posix()
        if normalized in listed:
            raise PostgresBundleError("the PostgreSQL bundle manifest contains duplicate files")
        listed.add(normalized)
        candidate = (root / Path(*relative.parts)).resolve()
        try:
            candidate.relative_to(root)
            data = candidate.read_bytes()
        except (OSError, ValueError):
            raise PostgresBundleError("a required PostgreSQL bundle file is missing") from None
        if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise PostgresBundleError("a PostgreSQL bundle file failed integrity verification")

    if not set(REQUIRED_BUNDLE_FILES).issubset(listed):
        raise PostgresBundleError("the PostgreSQL bundle is incomplete")
    if any(not (root / relative).is_dir() for relative in REQUIRED_BUNDLE_DIRECTORIES):
        raise PostgresBundleError("required PostgreSQL resource directories are missing")
    return payload


__all__ = [
    "BUNDLE_RELATIVE_PATH",
    "POSTGRES_ARCHITECTURE",
    "POSTGRES_VERSION",
    "REQUIRED_CONVERSION_MODULES",
    "REQUIRED_DLLS",
    "PostgresBundleError",
    "resolve_bundled_postgres_binary_dir",
    "validate_postgres_bundle",
]
