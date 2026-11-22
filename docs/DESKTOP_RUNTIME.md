# Windows desktop application and runtime

The repository contains a Tauri 2 Windows desktop application connected to the
managed PostgreSQL 16.16 and FastAPI runtime. Development mode starts Python
from `backend/.venv`; the portable staging build and NSIS installer start the
packaged backend. The portable directory is for packaging and local verification,
not a claim of clean-machine compatibility. The installer targets per-machine
installation under Program Files. No Windows service is registered, and the
app-managed database does not change system or user `PATH`. The NSIS installer
registers the application for uninstall and installs the shared Microsoft
Visual C++ runtime only when the supported x64 v14 runtime is absent or older.

## Package the Windows x64 backend and desktop directory

The backend uses **PyInstaller one-folder mode**. The output is
`AI-Career-Copilot-Backend.exe` plus an `_internal/` directory that contains
CPython, Python packages, native extensions, and DLLs. Tauri starts that
executable directly from the application's `resources/backend/` directory.
One-folder output is easier to inspect and troubleshoot than a self-extracting
single file and avoids extraction during each launch. PyInstaller bundles the
interpreter and dependencies, so the packaged program does not need Python on
the machine ([PyInstaller operating modes](https://pyinstaller.org/en/stable/operating-mode.html)).

Nuitka standalone was evaluated as an alternative. It compiles modules and can
also produce a standalone directory, but it adds a native compilation toolchain
and package-specific configuration. For this backend, PyInstaller provides the
smaller build surface while keeping a normal CPython runtime and straightforward
native-extension collection. The generated output remains a Python runtime,
not compiled application logic.

The source dependency set is in `backend/requirements-runtime.in`; pytest and
other test tools are omitted, and the optional Uvicorn development extras are
not needed by the HTTP API. The Windows x64 / CPython 3.13 runtime lock and the
separate PyInstaller build lock are hash-pinned in
`backend/requirements-runtime-win-x64-py313.lock` and
`backend/requirements-packaging-win-x64-py313.lock`. The packaging lock selects
PyInstaller 6.22.3. The packaging script creates a temporary virtual environment
and installs only these locks; it does not read or install from `backend/.venv`.
To refresh a lock on Windows, use pip-tools 7.5.2 with CPython 3.13 and run
`pip-compile --allow-unsafe --generate-hashes --resolver=backtracking` against
the corresponding `.in` file and its `*-win-x64-py313.lock` output.

Build prerequisites are 64-bit CPython 3.13, PowerShell, Node.js/npm, the Rust
MSVC target and Microsoft C++ Build Tools, WebView2 Runtime, and the already
verified PostgreSQL 16.16 bundle. From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_backend_runtime.ps1
Set-Location frontend
npm run desktop:build
Set-Location ..
powershell -NoProfile -ExecutionPolicy Bypass -File tools/package_desktop_portable.ps1
```

The combined `npm run desktop:package` command from `frontend/` runs the same
three steps. The backend-only output is
`backend/artifacts/packaged-runtime/capability-runtime-v2/`. The staged desktop
directory is `backend/artifacts/desktop/windows-x64/` and contains:

```text
ai-career-copilot-desktop.exe
resources/backend/AI-Career-Copilot-Backend.exe
resources/backend/_internal/...
resources/postgresql/16.16/windows-x64-minimal/...
frontend/dist/...
```

The portable build scripts refuse to replace an existing output directory.
`npm run desktop:build` uses `--no-bundle` for portable staging; a separate
Stage 4 command builds the NSIS setup executable described below. User data,
generated database credentials, backups, and logs remain under
`%LOCALAPPDATA%\AI Career Copilot`, outside the staged application directory.
The packaged process receives no provider credentials or database password in
its command line. Rust clears inherited environment values and gives the
packaged executable a process-local `PATH` containing the PostgreSQL bundle,
the installed application's directory, and Windows system directories, with
no Python or virtual-environment path.

Run the isolated backend check against a newly staged directory with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/verify_packaged_backend.ps1 `
  -ApplicationRoot backend/artifacts/desktop/windows-x64 -RunBackendTests
```

It launches the backend with that sanitized `PATH`, creates a unique temporary
application data root and database, waits for health and readiness, exercises
Alembic startup, automatic local profile initialization, profile save,
cross-origin rejection, persistence after restart, and graceful shutdown. The
script removes its temporary data only after the backend has exited cleanly.
`-RunBackendTests` also runs `backend/.venv`'s pytest suite against that same
throwaway PostgreSQL cluster using a separate disposable database; it never
uses the developer database URL. The script caps this optional suite at 15
minutes so a long-running evaluation cannot leave managed services behind.
This validates the packaged backend on the build host; it does not replace
launching the Tauri desktop directory or testing on a clean Windows machine.

For a reviewable DLL inventory and measured output size, inspect the generated
`build-manifest.json` and the `_internal/` directory. PyInstaller includes the
native extensions discovered from the locked Windows wheels, including the
PostgreSQL driver. Windows system libraries are not copied
from the developer host into the portable package. The NSIS setup embeds the
locked Microsoft x64 v14 Redistributable installer. Setup checks the 64-bit
registry key and installs version 14.52.37029.0 only if the installed runtime
is missing or older. The PE import inventory shows PostgreSQL requires
`msvcp140.dll` and `vcruntime140.dll`; EDB does not state its exact minimum
build-toolset version. The package comes from Microsoft's official
`aka.ms/vc14/vc_redist.x64.exe` endpoint. Its Microsoft signature, x64 product
metadata, file version, and SHA-256 are checked when preparing inputs and
before bundling. The signed x64 setup stub is 32-bit; its product metadata
identifies the installed runtime as x64. Runtime redistribution eligibility
still requires human review.

## Development mode

## Run the local shell

Prerequisites are Node.js and npm, the backend virtual environment with
`backend/requirements.txt` installed, Rust's MSVC target, Microsoft C++ Build
Tools, and WebView2 Runtime. From `frontend/`:

```powershell
npm ci
npm run desktop:dev
```

The Tauri development command builds the frontend, starts Vite at
`http://localhost:5173`, then starts the Python runtime supervisor. The shell
does not read `backend/.env` or inherit `CAREER_COPILOT_*` developer settings.
Development mode starts the fixed `backend/.venv/Scripts/python.exe` path;
packaged mode starts the bundled backend executable. Both use a sanitized
environment and the bundled PostgreSQL `bin` directory on its process-local
`PATH`. In a local development build, the frontend assets are embedded in the
executable, but the runtime still expects the repository virtual environment
and PostgreSQL resources beside the checkout.

For an isolated development check, set `AI_CAREER_COPILOT_TEST_DATA_DIR` to a
new absolute temporary directory whose final component is `AI Career Copilot`
before running `npm run desktop:dev`. The runtime refuses a data directory
inside the repository or an unrecognized existing cluster. The default
interactive runtime data remains `%LOCALAPPDATA%\AI Career Copilot`. The same
test-only variable can point a staged release executable at a disposable data
directory when verifying its native window; it is forwarded as a validated
`--data-dir` value and is not copied to the backend environment.

`npm run desktop:build` builds the local Tauri executable with the `--no-bundle`
option. It does not stage resources. `npm run desktop:package` then packages
the separately built backend with the PostgreSQL bundle and frontend output.
Browser development still uses the developer-managed PostgreSQL instructions
in the root README.

## PostgreSQL distribution decision

PostgreSQL remains the database. The current schema and persistence layer use
PostgreSQL-specific behavior, including JSONB, PostgreSQL upserts, advisory
locks, and `SKIP LOCKED`; replacing it with SQLite would change application
semantics.

The planned Windows package should carry a pinned PostgreSQL 16 binary
distribution beside the application, rather than installing a machine-wide
service or asking users to install PostgreSQL or Docker. PostgreSQL's Windows
download page points users to the EDB distribution, and EDB publishes a binary
archive of installed files that is suitable for review as an application-bundle
input ([PostgreSQL Windows downloads](https://www.postgresql.org/download/windows/),
[EDB binary archive](https://www.enterprisedb.com/download-postgresql-binaries)).
PostgreSQL is permissively licensed ([license](https://www.postgresql.org/about/licence/)),
but the eventual package must inventory every bundled component, its license
and notices, and any Visual C++ runtime dependency. The reduced PostgreSQL
runtime bundle is now present under `resources/postgresql/16.16/`; its original
EDB ZIP remains outside the repository as a build input.

PostgreSQL 16 is pinned in the runtime foundation to match the project's current
database and CI target. A major-version change must use a reviewed data upgrade
plan; the application refuses to open a cluster whose recorded major differs
from the bundled runtime. PostgreSQL's upgrade documentation describes major
upgrades as an explicit `pg_upgrade` or dump/restore operation
([upgrade guidance](https://www.postgresql.org/docs/current/upgrading.html)).

## PostgreSQL 16.16 distribution and licensing audit

Audit date: 2026-11-13. The inspected vendor archive is
`postgresql-16.16-1-windows-x64-binaries.zip`, from EDB's official [binary
archive page](https://www.enterprisedb.com/download-postgresql-binaries). Its
size was 378,078,504 bytes. The EDB-published and locally calculated SHA-256
were both
`5c0d06f3d26a929ecb36dd931409f1e51a4181355794a259b90aae8bffc57a9f`. The
archive is the input reviewed here; it remains in the separate developer-tools
directory and is not part of this repository.

The ZIP's internal root is `pgsql\`; the inspected extraction is
`%LOCALAPPDATA%\AI-Career-Copilot\dev-tools\PostgreSQL\16.16\`.
The six reviewed executables (`initdb.exe`, `pg_ctl.exe`, `postgres.exe`,
`psql.exe`, `pg_dump.exe`, and `pg_restore.exe`) report PostgreSQL 16.16 and
are x64/AMD64. The current runtime's `_validate_binaries` requires
`initdb.exe`, `pg_ctl.exe`, `postgres.exe`, `pg_dump.exe`, and `pg_restore.exe`.
It invokes `pg_dump` for a pre-migration backup; it does not currently invoke
`psql` or `pg_restore`. `psql` is useful as a packaged support tool, while
`pg_restore` is a required binary check and the natural utility for restoring
the runtime's custom-format dumps. No Alembic migration or application model
currently creates a PostgreSQL extension.

### Reduced PostgreSQL 16.16 Windows x64 bundle

The verified output is
`resources/postgresql/16.16/windows-x64-minimal/`. It contains 1,152 payload
files (82,190,349 bytes, about 78.4 MiB) plus `bundle.json` (201,291 bytes).
The bundle is generated from the exact EDB archive above, and the builder
refuses to overwrite an existing destination. `bundle.json` pins the source
archive SHA-256 and records every included payload's path, size, and SHA-256.
The runtime validates those hashes, the bundle identity, required resources,
and the exact PostgreSQL 16.16 version before using a manifest-bearing bundle.

Included executables (each was run with `--version` from the reduced tree):

```text
bin/initdb.exe, bin/pg_ctl.exe, bin/postgres.exe,
bin/pg_dump.exe, bin/pg_restore.exe
```

`psql.exe` is not included: the runtime and migration/backup code do not invoke
it. It remains useful as a separate diagnostic tool when available.

The bundled `bin/` DLL closure is:

```text
icudt67.dll, icuin67.dll, icuuc67.dll, libcrypto-3-x64.dll,
libiconv-2.dll, libintl-9.dll, liblz4.dll, libpq.dll,
libssl-3-x64.dll, libxml2.dll, libzstd.dll, zlib1.dll
```

The bundled `lib/` modules are `plpgsql.dll`, `dict_snowball.dll`, the
`euc2004_sjis2004.dll` conversion module, and all 23 other encoding conversion
modules matching the EDB 16.16 `share/postgres.bki` references. These were
retained after real `initdb` runs showed that the conversion modules and
`dict_snowball.dll` are dynamically loaded during cluster initialization.
Keeping only the five executable import closure would have produced an
incomplete bundle.

The bundle retains the complete `share/` tree except `share/contrib/` and
unused extension definitions under `share/extension/`; it keeps the two
`plpgsql` extension files. That tree includes `postgres.bki`, sample
configuration and authentication files, `locale/`, `timezone/`,
`timezonesets/`, and `tsearch_data/`. It also includes unchanged
`licenses/server_license.txt` and
`licenses/commandlinetools_3rd_party_licenses.txt` notices.

| Included content | Runtime reason |
| --- | --- |
| Five executables above and their 12 bundled `bin/` DLLs | Cluster initialization, server lifecycle, database server, application migration backup, and custom-format restore |
| 26 `lib/` DLLs | PL/pgSQL, modules referenced during bootstrap, and the encoding conversions needed by PostgreSQL |
| Core PostgreSQL `share/` resources and the two PL/pgSQL extension files | Bootstrap SQL/catalog, localization, time zones, text search, and server initialization |
| Two complete license/third-party notice files | Preserve PostgreSQL and bundled-library notices and obligations |
| `bundle.json` | Version, architecture, archive provenance, file sizes, and per-file integrity verification |

The builder excludes `psql.exe`, unrelated `bin/` utilities, pgAdmin, StackBuilder,
headers, import libraries, documentation, optional extension DLLs, `share/contrib/`,
and unused extension definitions. No database cluster or user data is stored
in the bundle. It is not installed as a Windows service.

### DLL and Visual C++ runtime findings

The builder walks PE imports recursively for the five executables, the
encoding modules, `dict_snowball.dll`, and `plpgsql.dll`. All non-Windows
dependencies in that static closure are bundled; imports otherwise resolve
from Windows system/API-set libraries or the Visual C++ runtime. The system
imports recorded in `bundle.json` include `vcruntime140.dll` and
`msvcp140.dll`; these are not present in the EDB archive and are not copied
from this computer. The inspected development host supplied them from
`%WINDIR%\System32` at version `14.51.36247.0`.

The EDB bundle imports `msvcp140.dll` and `vcruntime140.dll`. The installer
uses Microsoft's central-deployment x64 v14 Redistributable 14.52.37029.0 and
checks the registry's `Installed` and `Version` values before running it with
`/install /passive /norestart`. It skips the prerequisite when an equal or
newer v14 version is present. The package is downloaded from Microsoft's
official latest-supported x64 endpoint, pinned by version and SHA-256, and
verified for Authenticode signer, architecture metadata, and file version.
Microsoft recommends central deployment so it can service shared runtime
files independently. See the [compatibility guidance](https://learn.microsoft.com/en-us/lifecycle/faq/visual-c-faq),
[redistribution terms](https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files),
and [latest supported downloads](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist/).
The EDB archive does not publish the exact minimum runtime build. Redistribution
eligibility and license terms still require human review.

### Reproducing the reduced-bundle checks

From the repository root, with the verified EDB archive available outside the
repository, regenerate only into a new empty output directory:

```powershell
.\backend\.venv\Scripts\python.exe tools\build_postgresql_runtime_bundle.py `
  --archive "C:\path\to\postgresql-16.16-1-windows-x64-binaries.zip" `
  --output "C:\path\to\new\windows-x64-minimal"
```

The builder rejects an archive whose SHA-256 differs from the pinned EDB
archive. To run the isolated real-database verification from the repository
root, supply a new temporary data root. The harness uses only the reduced
bundle, a sanitized process-local `PATH` containing the bundle and Windows
`System32`, dynamic loopback ports, and isolated temporary clusters:

```powershell
$env:PATH = "$(Resolve-Path .\resources\postgresql\16.16\windows-x64-minimal\bin);$env:SystemRoot\System32"
.\backend\.venv\Scripts\python.exe tools\verify_postgresql_runtime_bundle.py `
  --data-root "$env:TEMP\AI-Career-Copilot-pg16.16-<new-unique-id>"
```

The successful run verified the five reported executable versions, loopback
binding, all eight Alembic revisions through `20261016_0008`,
`alembic check`, expected tables/JSONB columns/constraints/indexes, a
SQLAlchemy insert and JSONB operation, both FastAPI health routes, a custom
backup and its TOC, a matching restore in a second cluster, rejection of an
invalid archive without application tables, and data recovery after a
managed-server restart. The data root must not already exist; verification
data is preserved for inspection rather than deleted automatically.

`pytest tests/test_desktop_runtime.py -q` separately covers mocked safety
behavior, including bundle discovery, missing/incompatible/modified files,
PATH isolation, and the rule that backup failure prevents migrations and API
startup. Those mocked cases are not real PostgreSQL results.

The test used no development PostgreSQL path or `.env` database URL, and did
not call `psql`, pgAdmin, StackBuilder, or any external provider. A clean
Windows machine still needs the VC++ prerequisite above.

### Excluded archive components

The following content was excluded based on runtime call sites and the real
reduced-bundle acceptance run:

| Archive content | Reason |
| --- | --- |
| `pgAdmin 4\` and `StackBuilder\` | Separate GUI and add-on download manager; neither is used by the application-managed database |
| `include\` | Development headers; the desktop application does not compile PostgreSQL extensions |
| `doc\` | Offline manuals are not runtime inputs; provide online product documentation instead |
| Other executables in `bin\` including `psql.exe` | Not called by the current app-managed runtime; `psql` can be supplied separately for developer diagnostics |
| `include\`, `doc\`, and import libraries | Development/documentation inputs, not runtime files |
| pgAdmin, StackBuilder, wxWidgets GUI libraries | Separate GUI and add-on download manager, not used by the app |
| `share\contrib\` and unused `share\extension\` files | No shipped migration or tested app workflow uses these extensions; add future modules only with their control/SQL files, dependencies, notices, and verification |
| Optional server DLLs such as `pgxml.dll`, `libxslt.dll`, `libcurl.dll` | Not loaded by the current executables or application schema |

Do not prune `share\timezone`, `share\timezonesets`, locale data, text-search
data, or the bootstrap modules/files confirmed by the real test. Future
features that use an extension must include and verify its DLL, control/SQL
files, transitive dependencies, and required notices.

### Third-party license inventory

The archive contains `server_license.txt` and a 49 KB
`commandlinetools_3rd_party_licenses.txt`. The latter contains notices for the
third-party components in this bundle. Carry the complete files
unchanged in a visible “Open source notices” area in the installed product and
include a concise third-party inventory. Do not replace these notices with a
statement that the entire bundle uses the PostgreSQL License.

| Component in base bundle | Inspected version | License indicated by archive/upstream | Notice and redistribution conditions |
| --- | --- | --- | --- |
| PostgreSQL tools, server, `libpq.dll`, `plpgsql.dll` | 16.16 | PostgreSQL License | Keep the complete copyright, permission, and disclaimer text in `server_license.txt`; the license permits commercial and noncommercial redistribution subject to preserving the notice and terms ([PostgreSQL license](https://www.postgresql.org/about/licence/)). |
| OpenSSL (`libcrypto-3-x64.dll`, `libssl-3-x64.dll`) | 3.5.9 | Apache License 2.0 | Ship the Apache license and preserve any applicable copyright/NOTICE material. The archive's command-line license file includes the license text; OpenSSL 3.x uses Apache 2.0 ([OpenSSL documentation](https://docs.openssl.org/master/man7/ossl-guide-introduction/)). |
| zlib (`zlib1.dll`) | 1.4 | zlib license | Preserve its copyright/license text. Commercial use is permitted; product acknowledgment is appreciated but not required ([zlib license](https://zlib.net/zlib_license.html)). |
| libxml2 (`libxml2.dll`) | 2.15.4 | MIT | Include the copyright and permission notice ([libxml2 license information](https://gnome.pages.gitlab.gnome.org/libxml2/)). |
| ICU (`icudt67.dll`, `icuin67.dll`, `icuuc67.dll`) | 67.1 | ICU License plus Unicode data terms and enumerated third-party notices | Preserve the entire ICU section of the archive notice file, including the embedded Unicode/data notices; do not reduce it to a single “MIT” label ([ICU project](https://icu.unicode.org/)). |
| LZ4 (`liblz4.dll`) | 1.11.1 | BSD 2-Clause for the library | Include the copyright, license conditions, and disclaimer ([LZ4 license](https://github.com/lz4/lz4/blob/dev/LICENSE)). |
| Zstandard (`libzstd.dll`) | 1.5.7 | BSD 3-Clause | Include the copyright, conditions, and disclaimer from the archive notice file. |
| GNU libiconv (`libiconv-2.dll`) | 1.15 | LGPL v3 | Include the LGPL v3 text and make the corresponding source and build materials available; preserve the shared-library boundary and license rights ([GNU libiconv](https://www.gnu.org/software/libiconv/)). |
| GNU gettext (`libintl-9.dll`) | 0.19.8 | LGPL v3 and BSD 3-Clause dual license | Preserve both license notices in the archive file and make the corresponding source/build materials available. EDB's installer history identifies the dual licensing and the unmodified upstream components ([EDB installer changes](https://github.com/EnterpriseDB/edb-installers/blob/REL-19/changes.md)). |
| libxslt (`libxslt.dll`; optional XML/XSLT feature) | 1.1.45 in EDB 16.x release history; DLL has no readable file-version metadata | MIT-like | Not needed by the base tool closure. If included, keep its archive copyright/license text and pin the exact binary/source ([EDB REL-16 changes](https://github.com/EnterpriseDB/edb-installers/blob/REL-16/changes.md)). |
| libcurl (`libcurl.dll`; optional) | 8.23.0 | curl license | Not needed by the base tool closure. Keep the archive's copyright and permission notice if retained. |
| wxWidgets (pgAdmin/StackBuilder support DLLs) | 3.2.12 | wxWindows Licence | Exclude with pgAdmin and StackBuilder. If either GUI tool is shipped later, preserve the wxWidgets licence and its associated notices. |

Other PostgreSQL `lib\` DLLs such as `libecpg*.dll`, and ICU DLLs outside the
verified dependency closure, are not needed by this bundle. They remain
optional for future features and must be added together with their matching
notices if that changes.

### EDB provenance and redistribution conclusion

The [official PostgreSQL Windows page](https://www.postgresql.org/download/windows/)
identifies EDB as the provider of the Windows distribution and says the
standalone binary ZIP is intended for users who want to include PostgreSQL in
another application's installer. EDB describes its binary archives as files
installed by its PostgreSQL installers and as a convenience for expert users
([EDB archive page](https://www.enterprisedb.com/download-postgresql-binaries)).
This supports using the archive as packaging input. The inspected ZIP has no
separate EDB redistribution license or EULA that overrides the component
notices; no blanket EDB license for every included third-party file should be
inferred from that fact.

Preliminary conclusion: redistribution in a commercial or free desktop
installer appears supportable under the component licenses if their notices
and license-specific conditions are met, including LGPLv3 source/relinking
obligations for gettext/libiconv and a properly licensed Microsoft runtime.
This is not legal clearance. Before public distribution, counsel should review
the exact reduced bundle, EDB's terms applicable to the selected download,
LGPL compliance/source delivery, Microsoft Visual Studio redistribution
eligibility, trademark/attribution wording, and the final notices. The package
pipeline should pin the archive URL, package revision, vendor and calculated
SHA-256, list every included file and hash, preserve the original notices, and
check Authenticode signatures and provenance for the exact binaries before
release.

## Installer layout, upgrades, and uninstall policy

The Stage 4 release build uses Tauri 2's NSIS setup executable in per-machine
mode. NSIS was selected over MSI because it is directly supported by Tauri and
supports graceful process-close hooks before upgrade or uninstall. Setup creates
a Start Menu shortcut and offers a desktop shortcut. Install and upgrade require
administrator approval; the application itself runs as the signed-in user
without elevation. See the [Tauri Windows installer guide](https://v2.tauri.app/distribute/windows-installer/).

The installed resources follow this layout. The uninstaller and any Tauri
application runtime files sit at the installation root:

```text
%ProgramFiles%\AI Career Copilot\
  ai-career-copilot-desktop.exe
  resources\backend\           packaged FastAPI/Python runtime
  resources\frontend\dist\    built React frontend
  resources\notices\           third-party notices
  resources\installer\         graceful-close helper
  resources\postgresql\16.16\windows-x64-minimal\
    bin\                       PostgreSQL runtime executables and DLLs
    lib\                       required PostgreSQL modules
    share\                     PostgreSQL initialization/server resources
    licenses\                  PostgreSQL and third-party notices
    bundle.json                pinned hashes and archive provenance

%LOCALAPPDATA%\AI Career Copilot\
  config\runtime.json           generated local credentials and selected port
  config\desktop-settings.json desktop settings
  config\postgres-cluster.json cluster ownership/version marker
  postgres\data\               persistent app-owned cluster
  backups\                      migration backups and user recovery copies
  logs\postgres.log             server log
```

The current `ApplicationPaths` implementation already keeps `data_dir` outside
`install_dir`, and secures the per-user data/config directories for the current
user, Local System, and local Administrators. The installer retains the existing
`%LOCALAPPDATA%\AI Career Copilot\` data tree; it never puts `postgres\data`
below Program Files. Installed binaries are readable but not writable by a
standard user.

| Event | Required policy |
| --- | --- |
| First install / first cluster | Install the exact versioned binaries and notices. Initialize only an empty app-owned data directory with no prior state or ownership marker. Record a marker with the absolute data path, PostgreSQL major, and runtime owner. Never adopt an unknown nonempty directory. |
| Existing cluster on launch | Read the cluster marker and `PG_VERSION`; confirm the recorded owner/path and matching major before any server start. If metadata is absent, unreadable, inconsistent, partial, or incompatible, stop and preserve all data for recovery. The current runtime checks the PostgreSQL major but not the exact minor/package hash; the installer/update manifest should pin and verify the signed archive, file hashes, architecture, and selected 16.x release before the runtime starts. |
| Application update | NSIS invokes the previous uninstaller in update mode, then installs the new program files. The pre-uninstall hook asks the exact installed app process to close gracefully and aborts if it cannot; it does not force-kill processes. Persistent data is outside the install tree and remains in place. Runtime migration first makes a database backup and uses the existing migration checks. NSIS does not provide transactional rollback of replaced binaries: if setup is interrupted, rerun the same installer to repair the program files. A failed migration preserves the database and backup but may require recovery before the app can start. |
| PostgreSQL 16 minor update | Stop the managed server cleanly, stage and verify the new 16.x binaries and notices, then switch the versioned binary pointer and restart the same cluster. PostgreSQL minor releases keep the on-disk format compatible, so do not run `initdb`, `pg_upgrade`, or dump/restore solely for a minor update ([PostgreSQL 16 upgrade guidance](https://www.postgresql.org/docs/16/upgrading.html)). Keep the previous binaries and a pre-update backup until health checks pass. |
| PostgreSQL major update | Install the new major version side-by-side. Never point it at the old data directory as a binary swap. Require a reviewed and tested `pg_upgrade` or logical dump/restore procedure, a complete backup, an explicit compatibility check, and a rollback plan. Do not delete the old cluster until the new one is validated and the user accepts the result ([`pg_upgrade` guidance](https://www.postgresql.org/docs/16/pgupgrade.html)). |
| Uninstall | Remove application binaries and shortcuts only. The pre-uninstall hook requests graceful application shutdown and aborts on failure. Keep `%LOCALAPPDATA%\AI Career Copilot\` intact, including data, credentials, backups, and logs. Tauri's optional WebView data checkbox concerns WebView storage, not this application data directory; it is unchecked by default. No database/data deletion option is offered. |

### Reproducing and verifying the installer

On 64-bit Windows, use the pinned tool versions in
`tools/installer-toolchain.json`. On a fresh checkout, build the backend to the
installer's configured input directory (the script refuses to overwrite it):

```powershell
powershell -NoProfile -File tools/build_backend_runtime.ps1 `
  -OutputDirectory backend/artifacts/packaged-runtime/capability-runtime-v2
Set-Location frontend
npm ci
npm run desktop:installer
```

The verified PostgreSQL bundle must already be present at
`resources/postgresql/16.16/windows-x64-minimal/`. The installer command builds
the frontend, downloads and verifies the pinned Microsoft x64 Redistributable
from its official source, prepares the Tauri-pinned NSIS 3.13 toolchain with SHA-1 checks, and generates
`resources/notices/THIRD_PARTY_NOTICES.txt` from runtime metadata, locked
frontend/Rust dependencies, PostgreSQL notices, NSIS's complete `COPYING` file,
and Microsoft runtime attribution. It then creates the setup executable. It
refuses existing installer output and creates a unique short-path cache under
`%TEMP%`; a completed cache
can be explicitly reused after a later packaging failure. The output contains
the versioned setup executable, its SHA-256
file, and `release-manifest.json` with tool/input hashes and signing status.
Inputs and tool versions are pinned, but a byte-identical rebuild across
machines has not been demonstrated. Keep secrets out of the build environment.

The NSIS installer embeds the Microsoft WebView2 bootstrapper. Windows 10
version 1803 or newer and Windows 11 include WebView2; if the runtime is absent,
setup may need an internet connection to install it. Python and PostgreSQL do
not need to be separately installed. The installer and application were not
tested on a clean Windows VM. The development host already has the Visual C++
runtime, so clean-machine startup and WebView prerequisite behavior remain
unverified. The setup is unsigned; Windows SmartScreen may warn.

Security requirements for a release package: ship only signed and hash-pinned
vendor binaries; keep the exact binary directory in a versioned install path;
run PostgreSQL as the interactive user, not as a Windows service; bind only to
`127.0.0.1`; use random credentials stored in the secured config directory;
avoid printing secrets; and do not set global PATH or registry values. ACLs
are access control, not encryption, and administrators/SYSTEM or code running
as the same user can still access local data. Keep the host OS and VC++ runtime
serviced and supported.

## Audit follow-up and limits

This report reviews the archive's file inventory, version resources, license
texts, PE imports, runtime code, migrations, and a successful end-to-end run
of the actual reduced bundle. The real verification created two isolated
temporary clusters and confirmed they shut down; no PostgreSQL server process
remained after the run. Stage 4 adds the NSIS installer build, but clean Windows
execution remains unverified because a clean VM/image was unavailable. The
desktop foundation still has no backup/restore UI, major-version migration, or
data-removal workflow.

The successful real verification left its temporary data root under `%TEMP%`
for inspection. Earlier verification roots also remain there. Windows policy
blocked the earlier exact-path recursive cleanup, so no alternative deletion
method was used. See the task completion report for the exact paths created
or left in place.

## Installation and persistent data

The installer places executables, Python runtime/dependencies, built frontend
assets, and PostgreSQL binaries under Program Files. Those files are
replaceable during upgrades. Persistent state is kept separately at:

```text
%LOCALAPPDATA%\AI Career Copilot\
  config\runtime.json
  config\desktop-settings.json
  config\postgres-cluster.json
  postgres\data\
  backups\
  logs\postgres.log
```

`runtime.json` holds randomly generated local PostgreSQL credentials and the
selected port. It and the desktop configuration inherit a per-user Windows
ACL granting access to the current user, Local System, and local
Administrators. This is access control, not encryption at rest; a local
administrator or SYSTEM can read these files. The data root and PostgreSQL
cluster are not deleted by startup or shutdown. An unreadable state file,
mismatched cluster marker, unsupported major version, partial initialization,
or unknown non-empty cluster stops startup and preserves the files for
recovery. If initialization was interrupted after PostgreSQL wrote some
cluster files, the runtime refuses to infer completeness from `PG_VERSION` and
`pg_control` alone; manual inspection/recovery is required.

The runtime uses PostgreSQL's `pg_ctl` scoped to the app's exact data directory,
records an ownership marker for that directory, and verifies PostgreSQL's
reported data directory, port, and listen address over an authenticated local
connection. PostgreSQL binds only to `127.0.0.1`; it does not terminate a
machine-wide service or an unverified process. Its selected port is written to
protected local configuration. A separate file lock prevents two app
supervisors from managing the same user data at once.

## Startup, readiness, and shutdown

The standalone foundation is invoked from `backend/`. By default it finds
the versioned bundle at
`<installation-root>\resources\postgresql\16.16\windows-x64-minimal\bin`,
relative to the runtime code, validates `bundle.json`, and verifies all
payload hashes before starting PostgreSQL:

```powershell
.\.venv\Scripts\python.exe -m app.desktop_runtime
```

`--postgres-bin` remains available as an explicit development/test override.
The default does not consult a developer download path, system `PATH`, or
Windows registry. PostgreSQL child processes receive the bundle's `bin`
directory at the front of their process-local `PATH`; the runtime changes no
machine or user `PATH`. The Tauri shell launches the supervisor from the
repository virtual environment using fixed arguments and reads its JSON
`ready` line. The Rust process manager accepts only the expected loopback
hostname, HTTP scheme, and a dynamically selected port before passing the URL
to the frontend.
The supervisor must be a fresh backend process. It fails closed if an already
loaded SQLAlchemy engine points at another database, rather than risking an
Alembic operation against a developer database.

The line-oriented process contract is:

```json
{"event":"ready","api_url":"http://127.0.0.1:<selected-port>"}
```

The packaged Tauri page stays at `http://tauri.localhost` while the backend
starts and after it becomes ready. The Rust shell reports the validated
`http://localhost:<dynamic-port>` API URL over Tauri IPC; the frontend does not
navigate away from the trusted native origin. The API binds to `127.0.0.1`.
The packaged CORS allowlist contains only `http://tauri.localhost`, and the
origin middleware rejects unexpected browser origins. Tauri development keeps
the `http://localhost:5173` origin and its exact CORS allowance. Desktop mode
serves static assets with a restrictive Content Security Policy and disables
the interactive OpenAPI, Swagger, and ReDoc pages.

Before API pages mount, the React bootstrap obtains the per-launch capability
from the native `api_capability` command and installs it on the Axios client in
memory. The command is restricted to the `main` Tauri window at the expected
native origin and a ready backend. The Rust shell transfers the random
capability to the backend once over the child's standard input; readiness
responses contain only the API URL. The backend requires the bearer value on
all API and health routes and uses constant-time comparison. An absent or
invalid value receives `401` with no-store headers. An unexpected browser
origin remains separately rejected, and the API and PostgreSQL listeners stay
on loopback. No account login or registration is involved.

To shut down cleanly, the parent writes `{"command":"shutdown"}` followed by
a newline to the supervisor's standard input. It should keep that pipe open
while the service is running. The supervisor stops FastAPI first, then the
verified managed PostgreSQL instance. Ctrl+C remains supported for an
interactive developer console.

The PostgreSQL server is started with `pg_ctl` and can outlive the Python
supervisor if that parent is forcibly terminated or crashes before its
shutdown path runs. The OS releases the runtime lock, but this does not itself
stop PostgreSQL. On the next launch, the runtime checks the marked data
directory and verifies the listener using the stored credentials before
adopting it. The Tauri shell sends the shutdown command and waits up to two
minutes for the supervisor to exit before allowing the window to close. If
the owning shell process exits unexpectedly, closing the standard-input pipe
also asks the Python supervisor to stop. A forced termination of both parent
and supervisor is not a graceful database shutdown guarantee.

The supervisor sequence is:

1. Secure and prepare the per-user data directories and acquire the runtime
   lock.
2. Load and validate the runtime state and ownership marker. Initialize a
   cluster only when the app data directory is empty and no prior state or
   ownership marker exists.
3. Start or safely adopt the marked PostgreSQL instance on an available
   loopback port; verify its cluster identity and perform authenticated
   application-database readiness checks.
4. Create or verify the dedicated `career_copilot_app` role and
   `career_copilot` database. Existing mismatched ownership is an error; the
   runtime does not reset it.
5. Read the current Alembic revision. If an upgrade is pending, make a unique
   custom-format `pg_dump` backup first, publish it atomically without
   replacing an existing backup, run `alembic upgrade head`, and verify the
   resulting revision. Backup failures prevent the migration from starting;
   temporary-file cleanup is best effort if the filesystem itself refuses
   deletion.
6. Start FastAPI on a dynamically selected `127.0.0.1` port and wait for
   `/health/ready`, which performs an actual database query. The existing
   `/health` endpoint remains a process-health endpoint.
7. On normal shutdown, stop FastAPI before asking `pg_ctl` to stop the verified
   managed database. If either service exits unexpectedly, the supervisor
   detects it, shuts down the other service when safe, and leaves database
   files intact.

The database credentials remain in the backend's protected configuration and
are never sent to the frontend bundle or printed by the supervisor. Desktop
configuration is loaded from an explicit absolute path; the backend ignores
developer environment and `.env` settings in desktop mode. Developer mode
continues to use the existing `CAREER_COPILOT_*` environment settings and
`backend/.env` workflow.

The frontend uses `VITE_API_BASE_URL` only in ordinary browser development.
For Tauri, the Axios base URL is set from the validated Rust backend status
before the application mounts. The API capability is also held only in memory
in the trusted frontend and the native/backend processes. Database credentials
stay in protected backend runtime configuration and are not sent in command
line arguments or frontend assets. The app initializes one stable local profile
without sign-in. The bearer capability blocks local callers that lack the
current value, but it does not protect against code in the trusted WebView, a
same-user process able to inspect or interfere with the app, an administrator,
or compromised binaries/dependencies. See the [Stage 4B verification report](STAGE4B_VERIFICATION.md)
for native headless UI evidence and installer lifecycle blockers. No
clean-machine compatibility, legal clearance, or production signing claim is
made.

## Backups and recovery

Before any pending Alembic upgrade, the runtime writes a timestamped unique
custom-format database dump under `backups/`. Completed files are published
without replacing an earlier dump. These dumps contain user data and must be
protected and copied to secure storage by the user; they are not cloud-synced
by the app. If a migration fails, it may have applied some changes before
failing: the runtime keeps the pre-migration dump and does not start the API,
but it does not automatically roll the database back. Do not assume the live
database is unchanged; preserve it and recover from the backup into a separate
compatible cluster before selecting recovered data.

There is no backup/restore UI or tested one-click recovery command yet. Before
manual recovery, close the app and preserve a separate copy of the current
`postgres` directory and all configuration. A dump should be restored only
into a separate compatible PostgreSQL 16 database/cluster, validated there,
and then selected as the recovered data set. Do not run a clean/overwrite
restore against the only copy of an existing database. The installer does not
add a one-click recovery workflow; any failed database migration still needs
manual recovery using these safeguards.

## Development remains separate

Browser development remains unchanged: use the existing Docker PostgreSQL
container or another developer-managed PostgreSQL 16 instance, put
`CAREER_COPILOT_DATABASE_URL` and optional provider settings in
`backend/.env`, run Alembic manually, then start FastAPI and Vite separately.
The desktop runtime is not used by Docker/Render or ordinary tests. It does not
start SearXNG, Firecrawl, or Gemini; provider services and credentials remain
independently configured.

Run the isolated mocked runtime tests from `backend/`:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_desktop_runtime.py -q
```

Those tests mock process execution and database connections. The real reduced
bundle acceptance command and its results are documented above; it should be
rerun when the archive, resource manifest, runtime, or migrations change.
Before public distribution, the project still needs clean-Windows verification,
legal review of the exact bundle (including EDB, LGPL and Microsoft runtime
terms), and a code-signing certificate and signing process. The Stage 4 build
uses pinned build inputs and produces a checksum manifest, but it is unsigned.
