from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


HEADER = "AI Career Copilot — third-party notices"
LICENSE_NAME = re.compile(r"^(license|licence|copying|notice|copyright)s?(\.|$)", re.I)


def _license_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and LICENSE_NAME.match(path.name)
    )


def _append_package(
    sections: list[str],
    heading: str,
    name: str,
    version: str,
    license_expression: Any,
    license_files: list[Path],
) -> None:
    expression = (
        license_expression
        if isinstance(license_expression, str) and license_expression
        else json.dumps(license_expression, ensure_ascii=False)
        if license_expression
        else "Not declared in package metadata"
    )
    sections.append(f"\n{'=' * 88}\n{heading}: {name} {version}\nLicense: {expression}\n")
    if not license_files:
        sections.append("No separate license text was found in the installed package.\n")
        return
    for license_file in license_files:
        try:
            text = license_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            sections.append(f"Could not read {license_file.name}: {type(exc).__name__}\n")
            continue
        if text:
            sections.append(f"\n--- {license_file.name} ---\n{text}\n")


def _python_notices(runtime_root: Path, sections: list[str]) -> None:
    internal = runtime_root / "_internal"
    if not internal.is_dir():
        raise RuntimeError(f"packaged Python runtime is missing: {internal}")
    dist_infos = sorted(internal.glob("*.dist-info"))
    if not dist_infos:
        raise RuntimeError("packaged Python runtime has no distribution metadata")
    for dist_info in dist_infos:
        name_version = dist_info.name.removesuffix(".dist-info")
        name, _, version = name_version.rpartition("-")
        metadata: dict[str, str] = {}
        metadata_path = dist_info / "METADATA"
        if metadata_path.is_file():
            for line in metadata_path.read_text(encoding="utf-8", errors="replace").splitlines():
                key, separator, value = line.partition(":")
                if separator and key in {"Name", "Version", "License"}:
                    metadata.setdefault(key, value.strip())
        files = _license_files(dist_info)
        _append_package(
            sections,
            "Python runtime package",
            metadata.get("Name", name),
            metadata.get("Version", version),
            metadata.get("License"),
            files,
        )


def _npm_notices(frontend_root: Path, sections: list[str]) -> None:
    lock_path = frontend_root / "package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for relative, entry in sorted(lock.get("packages", {}).items()):
        if not relative.startswith("node_modules/") or entry.get("dev") is True:
            continue
        package_dir = frontend_root / "node_modules" / relative.removeprefix("node_modules/")
        manifest_path = package_dir / "package.json"
        if not manifest_path.is_file():
            raise RuntimeError(f"locked frontend package is missing: {relative}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _append_package(
            sections,
            "Frontend runtime package",
            str(manifest.get("name", entry.get("name", relative))),
            str(manifest.get("version", entry.get("version", "unknown"))),
            manifest.get("license"),
            _license_files(package_dir),
        )


def _cargo_notices(manifest_path: Path, cargo: str, sections: list[str]) -> None:
    command = [
        cargo,
        "metadata",
        "--format-version",
        "1",
        "--locked",
        "--filter-platform",
        "x86_64-pc-windows-msvc",
        "--manifest-path",
        str(manifest_path),
    ]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    metadata = json.loads(result.stdout)
    used_ids = {
        node["id"] for node in (metadata.get("resolve") or {}).get("nodes", [])
    }
    for package in sorted(
        metadata.get("packages", []),
        key=lambda item: (item["name"].casefold(), item["version"], item["id"]),
    ):
        if package["id"] not in used_ids:
            continue
        package_root = Path(package["manifest_path"]).parent
        files: set[Path] = set(_license_files(package_root))
        license_file = package.get("license_file")
        if license_file:
            candidate = Path(license_file)
            if not candidate.is_absolute():
                candidate = package_root / candidate
            if candidate.is_file():
                files.add(candidate)
        _append_package(
            sections,
            "Rust runtime/build package",
            package["name"],
            package["version"],
            package.get("license"),
            sorted(files),
        )


def _vendor_notices(postgres_root: Path, sections: list[str]) -> None:
    licenses = postgres_root / "licenses"
    if not licenses.is_dir():
        raise RuntimeError("PostgreSQL runtime is missing its vendor license notices")
    for license_file in sorted(licenses.iterdir()):
        if not license_file.is_file():
            continue
        sections.append(
            f"\n{'=' * 88}\nPostgreSQL bundle notice: {license_file.name}\n"
        )
        sections.append(license_file.read_text(encoding="utf-8", errors="replace").strip())
        sections.append("\n")
    sections.append(
        "WebView2 is installed or serviced by Microsoft. The installer embeds "
        "the Microsoft bootstrapper; when WebView2 is absent, installation may "
        "require an internet connection.\n"
    )


def _vc_redist_notices(redist_exe: Path, manifest_path: Path, sections: list[str]) -> None:
    if not redist_exe.is_file() or not manifest_path.is_file():
        raise RuntimeError("the Microsoft x64 VC Redistributable installer or its manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("architecture") != "windows-x64"
        or not manifest.get("version")
        or manifest.get("signatureStatus") != "Valid"
        or manifest.get("sha256") != hashlib.sha256(redist_exe.read_bytes()).hexdigest()
    ):
        raise RuntimeError("the Microsoft x64 VC Redistributable manifest is invalid")
    sections.append(
        f"\n{'=' * 88}\nMicrosoft Visual C++ v14 Redistributable {manifest['version']} (x64)\n"
        f"Installer: {manifest['packageFile']} ({manifest['packageBytes']} bytes)\n"
        f"SHA-256: {manifest['sha256']}\n"
        f"Authenticode signer: {manifest['signer']}\n"
        f"Official source: {manifest['sourceUrl']}\n"
        "Redistribution is subject to the applicable Visual Studio license terms "
        "and the distributor's eligibility. The x64 package's signed Windows "
        "setup stub is 32-bit; its Microsoft product metadata identifies the "
        "installed runtime as x64. See "
        "https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files. "
        "This notice does not establish legal clearance.\n"
    )


def _installer_tool_notices(nsis_root: Path, sections: list[str]) -> None:
    copying = nsis_root / "COPYING"
    if not copying.is_file():
        raise RuntimeError("the verified NSIS toolchain is missing its COPYING notice")
    sections.append(
        f"\n{'=' * 88}\nNSIS installer engine {nsis_root.name}\n"
        "License notices: NSIS COPYING\n"
    )
    sections.append(copying.read_text(encoding="utf-8", errors="replace").strip())
    sections.append("\n")
    sections.append(
        "Tauri NSIS utilities plugin 0.5.3 (bundled into the setup executable) "
        "is dual-licensed Apache-2.0 OR MIT. The complete license texts are "
        "included in the Tauri package license sections below. Source and license "
        "files: https://github.com/tauri-apps/nsis-tauri-utils.\n"
    )


def generate(
    runtime_root: Path,
    postgres_root: Path,
    vc_redist_exe: Path,
    vc_redist_manifest: Path,
    nsis_root: Path,
    frontend_root: Path,
    cargo_manifest: Path,
    cargo: str,
    output: Path,
    replace_generated: bool,
) -> None:
    sections = [
        f"{HEADER}\n",
        "This file is generated from the locked package inputs used for this build. "
        "License identifiers are package metadata, not legal advice or a finding "
        "that all redistribution obligations have been met.\n",
    ]
    _vendor_notices(postgres_root, sections)
    _vc_redist_notices(vc_redist_exe, vc_redist_manifest, sections)
    _installer_tool_notices(nsis_root, sections)
    _python_notices(runtime_root, sections)
    _npm_notices(frontend_root, sections)
    _cargo_notices(cargo_manifest, cargo, sections)
    content = "\n".join(sections).replace("\r\n", "\n")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        existing = output.read_text(encoding="utf-8", errors="replace")
        if not replace_generated or not existing.startswith(f"{HEADER}\n"):
            raise RuntimeError(f"refusing to overwrite a non-generated file: {output}")
    output.write_text(content, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"Generated third-party notices: {output} ({digest})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--postgres-root", type=Path, required=True)
    parser.add_argument("--vc-redist-exe", type=Path, required=True)
    parser.add_argument("--vc-redist-manifest", type=Path, required=True)
    parser.add_argument("--nsis-root", type=Path, required=True)
    parser.add_argument("--frontend-root", type=Path, required=True)
    parser.add_argument("--cargo-manifest", type=Path, required=True)
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replace-generated", action="store_true")
    arguments = parser.parse_args()
    try:
        generate(
            runtime_root=arguments.runtime_root.resolve(),
            postgres_root=arguments.postgres_root.resolve(),
            vc_redist_exe=arguments.vc_redist_exe.resolve(),
            vc_redist_manifest=arguments.vc_redist_manifest.resolve(),
            nsis_root=arguments.nsis_root.resolve(),
            frontend_root=arguments.frontend_root.resolve(),
            cargo_manifest=arguments.cargo_manifest.resolve(),
            cargo=arguments.cargo,
            output=arguments.output.resolve(),
            replace_generated=arguments.replace_generated,
        )
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"third-party notice generation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
