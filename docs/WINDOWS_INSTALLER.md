# Windows installer

## Local API protection and threat model

In desktop mode, every API and health route requires a random per-launch bearer
capability. The Windows Tauri process creates 256 bits with
`BCryptGenRandom`, sends the value once to the child backend over standard
input, and retains it in memory. It is not stored in a file or configuration,
passed in command-line arguments or environment variables, or written to logs.
The backend holds the capability only for its process lifetime and compares
bearer values in constant time. Missing or invalid values receive a generic
`401` response with `Cache-Control: no-store`.

The React bootstrap obtains the capability through the Tauri `api_capability`
command only when called from the `main` window at the expected trusted origin
and after the backend is ready. It supplies the value through the Axios bearer
header and keeps it in memory, not browser storage. A restart creates a new
value; closing the application clears the native copy and shuts down the
backend. The middleware protects `/api`, unknown `/api/*` paths, and
`/health/*`; desktop mode disables Swagger, ReDoc, and OpenAPI routes.

The API and PostgreSQL bind to loopback. Origin validation remains additional
browser protection: unexpected `Origin` values and cross-site requests without
an Origin are rejected, and CORS has no wildcard or credential allowance. The
packaged WebView's exact `http://tauri.localhost` origin is allowed. Tauri's IPC
capabilities remain narrow and do not grant shell access.

This is a bearer capability, not process attestation. It blocks a local caller
that lacks the current value, but it does not protect against malicious code in
the trusted WebView, a same-user attacker able to inspect or interfere with the
application process, a local administrator, or compromised application
binaries/dependencies. It does not add account login or registration. The
implementation and detailed limitations are recorded in
[the system architecture](ARCHITECTURE.md) and [the Stage 4B verification report](STAGE4B_VERIFICATION.md).

## Installer architecture

The Windows x64 setup is a Tauri 2 NSIS setup executable. NSIS provides the
installation wizard, Start Menu shortcut, optional desktop shortcut, Windows
uninstall registration, and uninstaller. It installs per-machine under
`%ProgramFiles%\AI Career Copilot` and requires administrator approval for
install, repair, upgrade, and uninstall. The app itself runs as the signed-in
user. PostgreSQL is a child process managed by the app; no Windows service is
created.

The setup contains the Tauri application, its embedded React production build,
the `resources/frontend/dist` static frontend used by FastAPI, the exact
single-user packaged Python backend, the PostgreSQL 16.16 minimal x64 bundle,
and third-party notices. It does not use the older
`windows-x64-samesite` authenticated backend build. It does not require users
to install Python, PostgreSQL, Docker, Node.js, Rust, or development tools.
When WebView2 is missing, Tauri's embedded Microsoft bootstrapper may need an
internet connection to install it.

## Visual C++ prerequisite

The EDB PostgreSQL executables import `msvcp140.dll` and `vcruntime140.dll`.
EDB does not publish the exact minimum runtime build. Setup embeds Microsoft's
official latest-supported x64 v14 package, currently version 14.52.37029.0,
from <https://aka.ms/vc14/vc_redist.x64.exe>. The Windows setup stub is PE32,
but its signed product metadata identifies it as the x64 Redistributable; the
package installs x64 runtime libraries.

Before building, `tools/prepare_vc_redist.ps1` checks the file version,
product name, original filename, architecture metadata, Microsoft
Authenticode signature, and the pinned SHA-256 in
`tools/installer-toolchain.json`. The manifest records the version, URL,
signer, file size, and hash. The NSIS post-install hook checks the 64-bit
registry entry at
`HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64`. It skips
installation when `Installed` is set and the recorded v14 version is equal to
or newer than 14.52.37029.0. Otherwise it runs the signed package with a
progress display and no automatic restart. The prerequisite runs after the
wizard has copied the application files. On failure, setup displays the exit
code and log location and closes without launching the app; rerun the same
installer after resolving the prerequisite error. User data is unchanged. A
requested restart is reported to the user. The shared Visual C++ runtime remains
installed after application uninstall because other programs may use it.

The official download link is a moving latest-supported endpoint. The pinned
SHA-256 makes a build fail if Microsoft changes the payload; update the version,
hash, manifest, and installer tests together when deliberately refreshing it.
The package's Microsoft signature and x64 product metadata were verified on
the build host. Runtime installation on a clean machine has not been verified.

## Build procedure

Build on 64-bit Windows with the exact tool versions in
`tools/installer-toolchain.json`: CPython 3.13.7 x64, Node.js 22.19.0, npm
11.6.4, Rust/cargo 1.100.0 with `x86_64-pc-windows-msvc`, Tauri CLI 2.12.4,
and NSIS 3.13 plus the pinned Tauri NSIS utilities. The installer script
checks versions and hashes the backend, PostgreSQL bundle, Redistributable,
frontend lockfile, and Rust lockfile before building. The build uses no
developer virtual environment for the packaged backend.

From the repository root, create the backend package and installer:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_backend_runtime.ps1 `
  -OutputDirectory backend/artifacts/packaged-runtime/capability-runtime-v2
Set-Location frontend
npm ci
npm run desktop:installer
```

The verified PostgreSQL bundle must already exist at
`resources/postgresql/16.16/windows-x64-minimal/`. The installer build creates
the frontend, verifies or downloads the Microsoft runtime package, prepares
the Tauri-pinned NSIS tools, generates third-party notices, and creates one
setup executable. By default the output is:

```text
backend/artifacts/installers/AI-Career-Copilot-0.1.0-x64/
  AI-Career-Copilot-Setup-0.1.0-x64.exe
  AI-Career-Copilot-Setup-0.1.0-x64.exe.sha256
  release-manifest.json
```

Use `tools/build_windows_installer.ps1 -OutputDirectory <new-path>` to choose a
different output directory. Existing output is never replaced. The release
manifest records the installer hash and size, toolchain versions, input
manifest hashes, runtime package signer/version, and unsigned status. The
installer is not Authenticode-signed; public distribution needs an approved
code-signing certificate and signature verification.

The exact backend package input is
`backend/artifacts/packaged-runtime/capability-runtime-v2/`. Its
manifest must list every file and the build checks all file sizes and SHA-256
hashes, rejects unlisted or missing files, and checks the locked Python and
PyInstaller versions. The PostgreSQL manifest receives the same file checks
and must match the pinned 16.16 x64 EDB archive identity. Do not build from the
older authenticated backend package.

## Data, upgrades, repair, and uninstall

Application binaries and shortcuts are under Program Files and the Windows
Start Menu. The optional desktop shortcut is selected on the installer finish
page. Installed product metadata and the uninstaller are registered by NSIS.
The persistent per-user directory is:

```text
%LOCALAPPDATA%\AI Career Copilot\
  config\        generated runtime credentials and settings
  postgres\data\ managed PostgreSQL cluster
  backups\       pre-migration database backups and user recovery copies
  logs\          PostgreSQL logs
```

Installation and upgrade never create, reset, or delete this directory. The
desktop runtime validates the existing cluster marker and PostgreSQL major
before start, and makes a database backup before applying pending migrations.
Upgrades replace application binaries while retaining the profile, jobs,
applications, resumes, generated documents, database credentials, and backups.
Downgrades are disabled. If a migration or app startup fails, retain the
directory and use its backup for recovery; the installer does not roll back a
database migration or restore binaries transactionally.

The NSIS pre-uninstall hook asks only the executable located in that exact
installation directory to close gracefully. It does not terminate other
PostgreSQL instances or force-kill the desktop process. Normal uninstall
removes the installed application, shortcuts, and registration but preserves
all of `%LOCALAPPDATA%\AI Career Copilot\` and the shared Visual C++ runtime.
There is no data-removal checkbox or silent data deletion. Reinstalling the app
can reuse the retained data directory. Rerunning the same installer invokes
the NSIS maintenance/reinstall flow if the app is already installed.

The path checks, pre-migration backup behavior, and process identity checks
are implemented in the desktop runtime and NSIS hook. The packaged Tauri
runtime was exercised directly with a separate synthetic data directory, and
the synthetic profile survived a clean app close and relaunch. The original
Stage 4B lifecycle blockers are recorded in [the Stage 4B report](STAGE4B_VERIFICATION.md).

A later silent-mode lifecycle run on the development host (Windows 11 Home, the
pinned Visual C++ runtime already present, no prior data directory) used a
Stage 4B installer built before the uninstall-hook fix:

- Silent install (`/S`, administrator approval) registered the app, created
  Start Menu and desktop shortcuts, and removed the Redistributable after setup.
- First launch initialized PostgreSQL and passed `tools/headless_tauri_smoke.mjs`
  in `fresh` mode. Closing the window stopped the app, backend and PostgreSQL
  cleanly; a relaunch restored the synthetic profile.
- Uninstall aborted with "did not close cleanly" on every attempt. NSIS is
  32-bit, so the pre-uninstall hook ran 32-bit PowerShell, whose default
  execution policy (`Restricted`) blocked `close_running_app.ps1`. The hook now
  passes `-ExecutionPolicy Bypass` for that one process. With a per-process
  bypass the old build uninstalled correctly: registration, program files and
  shortcuts were removed, and the data directory and Visual C++ runtime were kept.
- Uninstalling a copy installed from an earlier, differently laid-out build left
  that build's files under Program Files; the uninstaller removes only files it
  installed.

Still unverified: an installer rebuilt with the hook fix, the interactive
wizard, upgrade, repair and reinstall over existing data, and any clean Windows
machine.

## Licenses and distribution review

The installer places generated `resources/notices/THIRD_PARTY_NOTICES.txt`
with the application. It carries the PostgreSQL server license and EDB
command-line third-party notices, Python runtime package licenses, frontend
runtime notices, Rust dependency notices, NSIS's complete `COPYING` text, and
Microsoft runtime attribution. Check those notices against the exact final
bundle before distribution.

No legal clearance is claimed. Human review remains required for EDB terms and
attribution, PostgreSQL bundled libraries and extensions, Python dependency
redistribution and any source-offer obligations, the Microsoft Redistributable
license and distributor eligibility, the Tauri/NSIS third-party notices,
product name/trademark usage, and code-signing identity. No signing key or
certificate is configured.

## Verification limits and troubleshooting

The build host is not a clean Windows machine. Do not treat successful builds,
manifest checks, or backend runtime tests as proof of clean-machine
compatibility. Native installer wizard tests and a clean Windows VM test remain
release checks. Tests must use synthetic profile/database contents and a new
temporary data directory, never a developer or real-user data directory.

If setup reports a Visual C++ prerequisite failure, see the temporary
`AI-Career-Copilot-vcredist-install.log` and rerun setup with administrator
approval after resolving the Microsoft runtime installer error. If the app
fails during startup, leave the per-user data directory untouched, inspect
`%LOCALAPPDATA%\AI Career Copilot\logs\postgres.log`, and retry through the
desktop recovery screen. Reinstalling the same application version repairs
program files without reinitializing the PostgreSQL cluster.

The current installer and runtime test results, exact checksums, commands,
headless WebView2 evidence, and unverified release checks are maintained in
[the Stage 4B verification report](STAGE4B_VERIFICATION.md). A successful build
or direct runtime test does not establish clean-machine compatibility or
complete the installer lifecycle checks.
