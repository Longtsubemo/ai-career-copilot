# Stage 4B verification report

**Verification date:** 2026-11-15  
**Status:** Partially verified. Local API protection and the direct packaged
Tauri runtime passed automated and headless native-WebView checks. Installer
fresh install, upgrade, uninstall, and reinstall were not run. Stage 4B
acceptance criteria are not complete, and the artifact is not release-ready.

## Installer artifact and integrity

| Field | Verified value |
| --- | --- |
| Installer | `backend/artifacts/installers/Stage4B-local-api-capability-v2/AI-Career-Copilot-Setup-0.1.0-x64.exe` |
| Format | Tauri NSIS setup executable |
| Size | 70,167,142 bytes (66.92 MiB) |
| SHA-256 | `d51558e048e935db495d28590982877508f4da0d9accf096ae63dc55b226ee2b` |
| NSIS | 3.13 |
| Tauri CLI | 2.12.4 |
| Authenticode | Not signed |

The computed installer hash matched the release manifest and `.sha256`
sidecar. The release manifest's backend and PostgreSQL manifest hashes matched
the current package manifests. The Microsoft Visual C++ Redistributable input
signature was valid and its pinned metadata/hash matched the release manifest.
The installer itself has no Authenticode signature.

The package notice file is
`resources/notices/THIRD_PARTY_NOTICES.txt`; the release manifest records its
hash. It contains PostgreSQL server and command-line notices, Python runtime
package notices, Microsoft runtime attribution, NSIS licensing, and Tauri
utility license sections. Their presence is not legal clearance. EDB
redistribution terms, the complete dependency obligations, Microsoft
redistribution eligibility, product naming, and signing identity still need
human review.

## Native Tauri WebView2 test

The release-mode Tauri executable built for the installer was copied to a new
temporary application directory. Its backend, PostgreSQL, and frontend
resources pointed to the manifest-checked package inputs. No installer was
run, and no installed application or real-user data directory was used. The
synthetic persistent data directory was a separate temporary path ending in
`AI Career Copilot`, as required by the runtime's path validation.

WebView2's documented `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` override enabled
Chromium DevTools Protocol with a dynamically selected port and
`--remote-debugging-address=127.0.0.1`. `WEBVIEW2_USER_DATA_FOLDER` pointed to
a separate temporary WebView profile. The listening socket was observed on
`127.0.0.1` only. Automation connected to the actual Tauri WebView page using
Node.js 22's built-in WebSocket and CDP; there was no screen, mouse, or keyboard
automation. No production code or remote-debugging configuration was added.
See [Microsoft's WebView2 debugging guidance](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/debug-visual-studio-code)
and [WebView2 environment options](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/webview2-idl?view=webview2-1.0.4258.31).

The test used only a synthetic local profile named `Headless Local Profile`
with headline `UI verification profile`.

| Check | Result |
| --- | --- |
| Native window and WebView ready | Passed; title `AI Career Copilot`, origin `http://tauri.localhost` |
| Backend/PostgreSQL startup | Passed; direct packaged runtime initialized its isolated PostgreSQL 16.16 cluster and reached API readiness |
| Dashboard and dashboard data | Passed; empty-state job and application data loaded |
| Login or registration screen | None appeared |
| Jobs, Applications, Resume, Profile navigation | Passed |
| Profile edit | Passed; name and headline saved via the trusted WebView API client |
| API capability in the trusted WebView | Passed; page API requests succeeded with 200 responses; no 401/403 responses occurred |
| Unauthorized API request | Passed; request without a bearer value returned 401, `Cache-Control: no-store`, and `WWW-Authenticate: Bearer` |
| Browser storage | Passed; no local/session storage values and no persisted capability |
| Unexpected frontend errors | None observed (zero console/runtime errors) |
| Resume page | Loaded; `/api/resumes/master` returned 404 because this synthetic profile has no uploaded master resume; history returned 200 |
| Profile after app close and relaunch | Passed; both synthetic fields reappeared from the same PostgreSQL data directory |
| Runtime configuration and cluster marker | Passed; their hashes and PostgreSQL major stayed unchanged across relaunch |
| Shutdown/process cleanup | Passed; app and backend exited, PostgreSQL logged `database system is shut down`, and the API/PostgreSQL listening ports were closed |
| Listener scope | Passed; the API and PostgreSQL listeners were each observed on `127.0.0.1` only |

The database contained one migration backup before and after the relaunch.
This direct-runtime check does not test an installer upgrade or uninstall.
Jobs and Applications were navigated with empty synthetic collections; no
synthetic job, application, resume, or generated document was created.

Evidence files are under
`backend/artifacts/verification/stage4b-b07b62446a4247638ab16f93f70dcf18/`,
`backend/artifacts/verification/stage4b-relaunch-b07b62446a4247638ab16f93f70dcf18/`,
and `backend/artifacts/verification/stage4b-loopback-b07b62446a4247638ab16f93f70dcf18/`.
They include the dashboard screenshot, JSON test results, runtime/cluster
fingerprint comparison, PostgreSQL log, process snapshots, shutdown results,
and release integrity checks. The dashboard screenshot is
`backend/artifacts/verification/stage4b-b07b62446a4247638ab16f93f70dcf18/native-dashboard.png`.
The repeatable CDP checks are in `tools/headless_tauri_smoke.mjs`.

## Installer lifecycle

**Fresh installation, upgrade, uninstall, and reinstall: not tested.** No
installer or uninstaller was launched.

The setup is built as NSIS 3.13, and the generated script was inspected. Its
Tauri configuration sets `installMode` to `perMachine`; the generated script
contains `RequestExecutionLevel admin`, selects Program Files for `$INSTDIR`,
and restores the previously registered installation location. This host
already has AI Career Copilot registered at
`C:\Program Files\AI Career Copilot`, and the current process is not elevated.
The host's Visual C++ x64 runtime is 14.51.36247.00, below the installer input
version 14.52.37029.0. Running setup would therefore risk changing the
existing machine-wide installation registration and/or updating a shared
Visual C++ runtime. No Windows Sandbox, Hyper-V VM command, or other available
disposable VM was found. The existing app and its data were left untouched.

NSIS documents `/S` for silent setup/uninstall, `/D=<absolute path>` for the
default install directory, and `_?=<install directory>` for silent uninstaller
execution. These switches were identified from the [NSIS command-line
documentation](https://nsis.sourceforge.io/Docs/Chapter3.html), but were not
run. In this generated per-machine installer, `/D` is not a safe isolation
mechanism: the generated initialization selects Program Files and restores
the registered product location. Fresh installation, shortcuts, registration,
upgrade/migration backup behavior, uninstall data retention, and reinstall
recovery remain unverified.

## Commands and automated results

| Command/check | Result |
| --- | --- |
| `tools/build_windows_installer.ps1 -OutputDirectory backend/artifacts/installers/Stage4B-local-api-capability-v2 -BuildCacheDirectory %TEMP%\aicc-installer-stage4-b81bf36034b243a5b6215229ed8f13d6` | Exit 0; NSIS installer built, 70,167,142 bytes |
| `tools/verify_packaged_backend.ps1 -ApplicationRoot %TEMP%\aicc-stage4b-capability-v2-5ca4ec43ac414cdb848db9b5b152e09d -RunBackendTests` | Exit 0; packaged smoke passed; backend suite **1,210 passed, 1 warning** |
| `\.venv\Scripts\python.exe -m pytest tests\test_local_api_capability.py tests\test_desktop_runtime.py -ra -p no:cacheprovider` (from `backend/`) | Exit 0; **68 passed, 1 deprecation warning** |
| `npm test` (from `frontend/`) | Exit 0; **31 passed, 0 failed** |
| `npm run build` (run by the installer build) | Exit 0; TypeScript and Vite production build succeeded |
| `cargo test --manifest-path Cargo.toml` (Tauri crate; isolated target directory) | Exit 0; **5 passed, 0 failed** |
| `node --check tools/headless_tauri_smoke.mjs` | Exit 0 |
| Headless native WebView CDP, fresh profile, port 51230 | Exit 0; dashboard, routes, profile edit, auth checks passed |
| Headless native WebView CDP, relaunch, port 53952 | Exit 0; profile restored and auth checks passed |
| Headless native WebView CDP, loopback observation, port 61083 | Exit 0; relaunch passed; API `127.0.0.1:51838`, PostgreSQL `127.0.0.1:63074` |
| `Get-FileHash <installer> -Algorithm SHA256` plus comparisons to `.sha256` and `release-manifest.json` | Exit 0; all matched |
| Installer SHA-256, sidecar, release manifest, runtime manifest, PostgreSQL manifest | All matched |
| `git diff --check` | Exit 0; existing line-ending conversion warnings only |

Backend pytest ran through the packaged-backend verifier. The verifier started
its own temporary PostgreSQL cluster and pointed both `TEST_DATABASE_URL` and
the application database setting at that disposable cluster; the development
database was not used.

## Remaining release blockers

- Fresh install, genuine upgrade, uninstall, and reinstall have not been tested.
- No clean Windows VM/image was available; clean-machine compatibility is not
  established.
- The installer is unsigned. Do not distribute it as a signed/publisher-trusted
  release.
- Legal review remains for EDB/PostgreSQL redistribution, Python and dependency
  licenses, Tauri/frontend/NSIS notices, Microsoft redistribution eligibility,
  and product naming. The bundled notices do not constitute clearance.
- This capability does not protect against malicious code in the trusted
  WebView, a same-user attacker able to inspect/interfere with the process, a
  local administrator, or compromised application binaries/dependencies.

The local API security and native UI behavior are verified on this host, but
Stage 4B acceptance criteria and release readiness are not complete.
