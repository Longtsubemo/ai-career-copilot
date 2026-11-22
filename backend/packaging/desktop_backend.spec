from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


backend_root = Path(SPECPATH).parent
datas = [
    (str(backend_root / "alembic.ini"), "."),
    (str(backend_root / "alembic"), "alembic"),
]
binaries = []
hiddenimports = [
    "app.main",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.lifespan.on",
]

# These packages use native extensions, plugin discovery, or runtime resource
# lookups that need explicit collection in a frozen Windows application.
for package in ("psycopg", "docx", "pypdf"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# google-genai ships test helpers in its import package. Collect the runtime
# modules and data while excluding test-only submodules and fixtures.
datas += collect_data_files("google.genai", excludes=["google.genai.tests*"])
binaries += collect_dynamic_libs("google.genai")
hiddenimports += collect_submodules(
    "google.genai",
    filter=lambda name: ".tests" not in name and name != "google.genai._test_api_client",
)

# Preserve each locked runtime distribution's metadata and license notices;
# build-only packages are absent from this lock and therefore not copied.
runtime_lock = backend_root / "requirements-runtime-win-x64-py313.lock"
for line in runtime_lock.read_text(encoding="utf-8").splitlines():
    requirement = line.strip()
    if requirement and not requirement.startswith("#") and "==" in requirement:
        distribution = requirement.split("==", 1)[0]
        datas += copy_metadata(distribution)

a = Analysis(
    [str(backend_root / "packaging" / "desktop_backend_entry.py")],
    pathex=[str(backend_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "IPython",
        "notebook",
        "jupyter",
        "watchfiles",
        "setuptools",
        "pkg_resources",
        "google.genai.tests",
        "google.genai._test_api_client",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI-Career-Copilot-Backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    codesign_identity=None,
    entitlements_file=None,
)
collect = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AI-Career-Copilot-Backend",
)
