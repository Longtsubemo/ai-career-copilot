from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath


VERSION = "16.16"
ARCHITECTURE = "windows-x64"
ARCHIVE_NAME = "postgresql-16.16-1-windows-x64-binaries.zip"
ARCHIVE_SHA256 = "5c0d06f3d26a929ecb36dd931409f1e51a4181355794a259b90aae8bffc57a9f"
BUNDLE_DIRECTORY_NAME = "windows-x64-minimal"
ARCHIVE_ROOT = "pgsql/"
EXECUTABLES = (
    "initdb.exe",
    "pg_ctl.exe",
    "postgres.exe",
    "pg_dump.exe",
    "pg_restore.exe",
)
LICENSE_FILES = (
    "server_license.txt",
    "commandlinetools_3rd_party_licenses.txt",
)
PLPGSQL_FILES = {
    "share/extension/plpgsql.control",
    "share/extension/plpgsql--1.0.sql",
}
EXPECTED_CONVERSION_MODULES = {
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
}
BOOTSTRAP_MODULES = ("lib/dict_snowball.dll", "lib/euc2004_sjis2004.dll")
API_SET_PREFIXES = ("api-ms-win-", "ext-ms-win-")


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def pe_details(data: bytes, label: str) -> tuple[int, list[str]]:
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise RuntimeError(f"not a Windows PE file: {label}")
    pe_offset = _u32(data, 0x3C)
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise RuntimeError(f"invalid PE header: {label}")
    machine = _u16(data, pe_offset + 4)
    section_count = _u16(data, pe_offset + 6)
    optional_size = _u16(data, pe_offset + 20)
    optional = pe_offset + 24
    magic = _u16(data, optional)
    if magic not in (0x10B, 0x20B):
        raise RuntimeError(f"unsupported PE optional header: {label}")
    directories = optional + (112 if magic == 0x20B else 96)
    import_rva = _u32(data, directories + 8)
    section_table = optional + optional_size
    sections = []
    for index in range(section_count):
        offset = section_table + index * 40
        virtual_size = _u32(data, offset + 8)
        virtual_address = _u32(data, offset + 12)
        raw_size = _u32(data, offset + 16)
        raw_pointer = _u32(data, offset + 20)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_pointer))

    def rva_to_offset(rva: int) -> int | None:
        for virtual_address, size, raw_pointer in sections:
            if virtual_address <= rva < virtual_address + size:
                offset = raw_pointer + rva - virtual_address
                return offset if offset < len(data) else None
        return None

    descriptor = rva_to_offset(import_rva)
    imports: list[str] = []
    if descriptor is None:
        return machine, imports
    while descriptor + 20 <= len(data) and any(data[descriptor : descriptor + 20]):
        name_offset = rva_to_offset(_u32(data, descriptor + 12))
        if name_offset is None:
            raise RuntimeError(f"invalid DLL import table: {label}")
        end = data.find(b"\0", name_offset)
        if end < 0:
            raise RuntimeError(f"unterminated DLL import name: {label}")
        imports.append(data[name_offset:end].decode("ascii").lower())
        descriptor += 20
    return machine, imports


def _safe_member_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise RuntimeError(f"unsafe archive member path: {name}")
    return path.as_posix()


def _system_dll_path(name: str) -> Path | None:
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not root:
        return None
    candidate = Path(root) / "System32" / name
    if candidate.is_file():
        return candidate
    # Windows file systems are case-insensitive; the host may also be exposed
    # through a case-sensitive layer in test environments.
    directory = candidate.parent
    if directory.is_dir():
        match = next((item for item in directory.iterdir() if item.name.lower() == name.lower()), None)
        if match is not None:
            return match
    return None


def build_bundle(archive_path: Path, output_root: Path) -> dict[str, object]:
    archive_path = archive_path.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        raise RuntimeError(f"refusing to overwrite existing bundle directory: {output_root}")
    if not archive_path.is_file():
        raise RuntimeError(f"source archive does not exist: {archive_path}")
    archive_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if archive_hash != ARCHIVE_SHA256:
        raise RuntimeError("source archive SHA-256 does not match the approved EDB archive")

    with zipfile.ZipFile(archive_path) as archive:
        members: dict[str, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            name = _safe_member_name(info.filename)
            if info.is_dir():
                continue
            if not name.startswith(ARCHIVE_ROOT):
                continue
            relative = name[len(ARCHIVE_ROOT) :]
            if not relative:
                continue
            mode = info.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise RuntimeError(f"symbolic link in source archive is not allowed: {name}")
            key = relative.lower()
            if key in members:
                raise RuntimeError(f"duplicate archive member: {name}")
            members[key] = info

        def member_for(relative: str) -> zipfile.ZipInfo:
            info = members.get(relative.lower())
            if info is None:
                raise RuntimeError(f"required file missing from archive: {relative}")
            return info

        included: dict[str, bytes] = {}
        system_dependencies: set[str] = set()
        queued: list[str] = [f"bin/{name}" for name in EXECUTABLES]
        for name in EXECUTABLES:
            info = member_for(f"bin/{name}")
            data = archive.read(info)
            machine, _ = pe_details(data, name)
            if machine != 0x8664:
                raise RuntimeError(f"{name} is not an x64 executable")
            included[f"bin/{name}"] = data

        plpgsql_info = member_for("lib/plpgsql.dll")
        plpgsql_data = archive.read(plpgsql_info)
        machine, _ = pe_details(plpgsql_data, "lib/plpgsql.dll")
        if machine != 0x8664:
            raise RuntimeError("lib/plpgsql.dll is not an x64 module")
        included["lib/plpgsql.dll"] = plpgsql_data
        queued.append("lib/plpgsql.dll")
        conversion_modules: set[str] = set()
        for key, info in members.items():
            if key.startswith("lib/") and re.fullmatch(r"[a-z0-9_]+_and_[a-z0-9_]+\.dll", key[4:]):
                included[info.filename[len(ARCHIVE_ROOT) :]] = archive.read(info)
                queued.append(info.filename[len(ARCHIVE_ROOT) :])
                conversion_modules.add(PurePosixPath(info.filename).name.lower())
        if conversion_modules != EXPECTED_CONVERSION_MODULES:
            missing = sorted(EXPECTED_CONVERSION_MODULES - conversion_modules)
            unexpected = sorted(conversion_modules - EXPECTED_CONVERSION_MODULES)
            raise RuntimeError(
                f"unexpected PostgreSQL encoding conversion module set; missing={missing}, unexpected={unexpected}"
            )
        for relative in BOOTSTRAP_MODULES:
            info = member_for(relative)
            data = archive.read(info)
            machine, _ = pe_details(data, relative)
            if machine != 0x8664:
                raise RuntimeError(f"{relative} is not an x64 module")
            included[relative] = data
            queued.append(relative)

        inspected: set[str] = set()
        while queued:
            relative = queued.pop()
            if relative.lower() in inspected:
                continue
            inspected.add(relative.lower())
            data = included.get(relative)
            if data is None:
                info = member_for(relative)
                data = archive.read(info)
                machine, _ = pe_details(data, relative)
                if machine != 0x8664:
                    raise RuntimeError(f"{relative} is not an x64 module")
                included[relative] = data
            _, imports = pe_details(data, relative)
            for dependency in imports:
                if dependency in {name.lower() for name in EXECUTABLES}:
                    continue
                candidates = (f"bin/{dependency}", f"lib/{dependency}")
                found = next((name for name in candidates if name.lower() in members), None)
                if found is not None:
                    if found not in included:
                        included[found] = archive.read(member_for(found))
                    queued.append(found)
                    continue
                if dependency.startswith(API_SET_PREFIXES):
                    system_dependencies.add(dependency)
                    continue
                if _system_dll_path(dependency) is None:
                    raise RuntimeError(f"unresolved runtime DLL dependency: {dependency}")
                system_dependencies.add(dependency)

        for relative, info in members.items():
            # Preserve the original casing from the archive's member name.
            archive_name = info.filename[len(ARCHIVE_ROOT) :]
            if archive_name.startswith("share/"):
                if archive_name.startswith(("share/contrib/", "share/extension/")):
                    if archive_name not in PLPGSQL_FILES:
                        continue
                if archive_name not in included:
                    included[archive_name] = archive.read(info)
            elif archive_name in LICENSE_FILES:
                included[f"licenses/{archive_name}"] = archive.read(info)

    absent = [name for name in (*LICENSE_FILES, *PLPGSQL_FILES) if name not in {p.removeprefix("licenses/") for p in included} and name not in included]
    if absent:
        raise RuntimeError(f"bundle is missing required notices/resources: {', '.join(absent)}")
    for relative in (
        "bin/initdb.exe",
        "bin/pg_ctl.exe",
        "bin/postgres.exe",
        "bin/pg_dump.exe",
        "bin/pg_restore.exe",
        "lib/plpgsql.dll",
        *BOOTSTRAP_MODULES,
        "share/postgres.bki",
        "share/postgresql.conf.sample",
        "share/pg_hba.conf.sample",
        "share/pg_ident.conf.sample",
        "share/extension/plpgsql.control",
        "share/extension/plpgsql--1.0.sql",
    ):
        if relative not in included:
            raise RuntimeError(f"bundle preflight is missing required file: {relative}")

    output_root.parent.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=False, exist_ok=False)
    files: list[dict[str, object]] = []
    for relative, data in sorted(included.items()):
        destination = output_root / Path(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        files.append(
            {
                "path": relative,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    binary_dir = output_root / "bin"
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        raise RuntimeError("Windows SystemRoot is unavailable for isolated verification")
    env = {
        "PATH": os.pathsep.join((str(binary_dir), str(Path(system_root) / "System32"))),
        "SYSTEMROOT": system_root,
        "WINDIR": system_root,
        "TEMP": os.environ.get("TEMP", str(output_root)),
        "TMP": os.environ.get("TMP", os.environ.get("TEMP", str(output_root))),
    }
    for executable in EXECUTABLES:
        result = subprocess.run(
            [str(binary_dir / executable), "--version"],
            cwd=output_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError(f"{executable} failed isolated --version verification")
        match = re.search(r"\b(\d+\.\d+)\b", result.stdout)
        if match is None or match.group(1) != VERSION:
            raise RuntimeError(f"{executable} did not report PostgreSQL {VERSION}")

    manifest: dict[str, object] = {
        "format_version": 1,
        "postgres_version": VERSION,
        "architecture": ARCHITECTURE,
        "source_archive": ARCHIVE_NAME,
        "source_archive_sha256": archive_hash,
        "required_executables": list(EXECUTABLES),
        "windows_imports": sorted(system_dependencies),
        "vc_runtime_prerequisite": "Microsoft Visual C++ 2015-2022 x64 Redistributable (v14); minimum EDB build-toolset version not published in the archive",
        "files": files,
        "total_bytes": sum(int(item["size"]) for item in files),
    }
    (output_root / "bundle.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    default_output = repo_root / "resources" / "postgresql" / VERSION / BUNDLE_DIRECTORY_NAME
    parser = argparse.ArgumentParser(description="Build the pinned PostgreSQL Windows runtime bundle.")
    parser.add_argument("--archive", required=True, type=Path, help="EDB archive with the pinned SHA-256")
    parser.add_argument("--output", type=Path, default=default_output)
    arguments = parser.parse_args()
    try:
        manifest = build_bundle(arguments.archive, arguments.output)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"bundle build failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "bundle_directory": str(arguments.output.resolve()),
                "file_count": len(manifest["files"]),
                "payload_bytes": manifest["total_bytes"],
                "archive_sha256": manifest["source_archive_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
