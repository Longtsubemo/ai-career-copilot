param(
    [string]$Python = "python",
    [string]$OutputDirectory = "",
    [string]$BuildCacheDirectory = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$frontendRoot = Join-Path $repoRoot "frontend"
$tauriRoot = Join-Path $frontendRoot "src-tauri"
$backendRuntime = Join-Path $repoRoot "backend\artifacts\packaged-runtime\capability-runtime-v2"
$postgresBundle = Join-Path $repoRoot "resources\postgresql\16.16\windows-x64-minimal"
$vcRedistRoot = Join-Path $repoRoot "resources\vc-redist"
$toolchainPath = Join-Path $PSScriptRoot "installer-toolchain.json"
$toolchain = Get-Content -LiteralPath $toolchainPath -Raw | ConvertFrom-Json

if ([Environment]::Is64BitOperatingSystem -ne $true) {
    throw "The installer release build requires 64-bit Windows."
}

$pythonCommand = Get-Command $Python -ErrorAction Stop
$pythonVersion = & $pythonCommand.Source -c "import platform, sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} {platform.architecture()[0]}')"
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "$($toolchain.python) 64bit") {
    throw "The locked backend runtime was built with Python $($toolchain.python) x64; found '$pythonVersion'."
}

$nodeVersion = (& node --version).Trim().TrimStart("v")
$npmCommand = Get-Command npm.cmd -ErrorAction Stop
$npmVersion = (& $npmCommand.Source --version).Trim()
if ($LASTEXITCODE -ne 0 -or $nodeVersion -ne $toolchain.node -or $npmVersion -ne $toolchain.npm) {
    throw "Installer build requires Node $($toolchain.node) and npm $($toolchain.npm); found Node $nodeVersion and npm $npmVersion."
}

$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path -LiteralPath (Join-Path $cargoBin "cargo.exe")) {
    $env:Path = "$cargoBin;$env:Path"
}
$cargoCommand = Get-Command cargo -ErrorAction Stop
$rustcCommand = Get-Command rustc -ErrorAction Stop
$cargoVersion = (& $cargoCommand.Source --version).Trim() -replace '^cargo\s+([^\s]+).*$','$1'
$rustcVersionText = (& $rustcCommand.Source --version).Trim()
$rustcVersion = [regex]::Match($rustcVersionText, '^rustc\s+([^\s]+)').Groups[1].Value
if ($cargoVersion -ne $toolchain.cargo -or $rustcVersion -ne $toolchain.rustc) {
    throw "Installer build requires Rust/cargo $($toolchain.rustc); found rustc $rustcVersion and cargo $cargoVersion."
}

$appPackage = Get-Content -LiteralPath (Join-Path $frontendRoot "package.json") -Raw | ConvertFrom-Json
$tauriConfig = Get-Content -LiteralPath (Join-Path $tauriRoot "tauri.conf.json") -Raw | ConvertFrom-Json
$packageLockVersion = (& node -e "process.stdout.write(JSON.parse(require('fs').readFileSync(process.argv[1], 'utf8')).packages[''].version)" (Join-Path $frontendRoot "package-lock.json")).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Could not read the locked frontend package version."
}
$cargoToml = Get-Content -LiteralPath (Join-Path $tauriRoot "Cargo.toml") -Raw
$cargoLock = Get-Content -LiteralPath (Join-Path $tauriRoot "Cargo.lock") -Raw
$cargoAppVersion = [regex]::Match($cargoToml, '(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
$cargoLockVersionMatch = [regex]::Match(
    $cargoLock,
    '(?ms)^\[\[package\]\]\s*name\s*=\s*"ai-career-copilot-desktop"\s*version\s*=\s*"([^"]+)"'
)
$cargoLockVersion = if ($cargoLockVersionMatch.Success) { $cargoLockVersionMatch.Groups[1].Value } else { "" }
$version = [string]$tauriConfig.version
if (-not $version -or $appPackage.version -ne $version -or $packageLockVersion -ne $version -or $cargoAppVersion -ne $version -or $cargoLockVersion -ne $version) {
    throw "Tauri, frontend package/lock, and Rust package/lock versions must match before release."
}
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Installer version must use a stable major.minor.patch version: '$version'."
}
if (-not $tauriConfig.bundle.active -or @($tauriConfig.bundle.targets) -notcontains "nsis") {
    throw "Tauri NSIS bundling is not enabled in tauri.conf.json."
}
if ($tauriConfig.bundle.windows.nsis.installMode -ne "perMachine") {
    throw "The release installer must install per-machine into the protected Program Files location."
}
$configuredResourceTargets = @(
    $tauriConfig.bundle.resources.PSObject.Properties |
        ForEach-Object { ([string]$_.Value).Replace('/', '\').TrimEnd('\') }
)
$requiredResourceTargets = @(
    "resources\backend",
    "resources\postgresql\16.16\windows-x64-minimal",
    "resources\vc-redist",
    "resources\notices",
    "resources\frontend\dist",
    "resources\installer\close_running_app.ps1"
)
foreach ($target in $requiredResourceTargets) {
    if ($configuredResourceTargets -notcontains $target) {
        throw "The Tauri installer resource target is missing or does not match the runtime layout: $target"
    }
}

$vcRedistStageScript = Join-Path $PSScriptRoot "prepare_vc_redist.ps1"
$powershellCommand = Get-Command powershell.exe -ErrorAction Stop
& $powershellCommand.Source -NoProfile -File $vcRedistStageScript
if ($LASTEXITCODE -ne 0) { throw "Could not download and verify the locked Microsoft Visual C++ x64 Redistributable." }

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $algorithm.ComputeHash($stream)
        return [BitConverter]::ToString($bytes).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

foreach ($required in @(
    (Join-Path $backendRuntime "AI-Career-Copilot-Backend.exe"),
    (Join-Path $backendRuntime "build-manifest.json"),
    (Join-Path $postgresBundle "bundle.json"),
    (Join-Path $vcRedistRoot "bundle.json"),
    (Join-Path $vcRedistRoot "vc_redist.x64.exe"),
    (Join-Path $frontendRoot "package-lock.json"),
    (Join-Path $tauriRoot "Cargo.lock"),
    (Join-Path $tauriRoot "icons\icon.ico"),
    (Join-Path $tauriRoot "windows\installer_hooks.nsh"),
    (Join-Path $tauriRoot "windows\close_running_app.ps1")
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "A release input is missing: $required"
    }
}

function Assert-ManifestFiles([string]$Root, [object[]]$Files, [string]$Label) {
    $resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
    $expectedPaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $Files) {
        $relative = ([string]$entry.path).Replace('/', '\')
        if ([IO.Path]::IsPathRooted($relative) -or $relative.Split('\') -contains '..') {
            throw "$Label manifest contains an unsafe path."
        }
        if (-not $expectedPaths.Add($relative)) {
            throw "$Label manifest contains a duplicate path."
        }
        $filePath = Join-Path $resolvedRoot $relative
        if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
            throw "$Label package is missing a file listed in its manifest: $relative"
        }
        $item = Get-Item -LiteralPath $filePath
        if ([long]$item.Length -ne [long]$entry.size) {
            throw "$Label package file size does not match its manifest: $relative"
        }
        $hash = Get-Sha256 $filePath
        if ($hash -ne [string]$entry.sha256) {
            throw "$Label package file hash does not match its manifest: $relative"
        }
    }
    $actual = Get-ChildItem -LiteralPath $resolvedRoot -File -Recurse | Where-Object { $_.Name -ne "build-manifest.json" -and $_.Name -ne "bundle.json" }
    if ($actual.Count -ne $expectedPaths.Count) {
        throw "$Label package has files not covered by its manifest or is missing manifest entries."
    }
}

$runtimeManifestPath = Join-Path $backendRuntime "build-manifest.json"
$runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw | ConvertFrom-Json
if ($runtimeManifest.architecture -ne "windows-x64" -or $runtimeManifest.python -ne "$($toolchain.python) 64bit" -or $runtimeManifest.pyinstaller -ne $toolchain.pyinstaller -or $runtimeManifest.dependencyLock -ne "requirements-runtime-win-x64-py313.lock" -or $runtimeManifest.packagingLock -ne "requirements-packaging-win-x64-py313.lock") {
    throw "The packaged single-user Python backend does not match the locked release inputs."
}
Assert-ManifestFiles -Root $backendRuntime -Files $runtimeManifest.files -Label "Python backend"

$postgresManifestPath = Join-Path $postgresBundle "bundle.json"
$postgresManifest = Get-Content -LiteralPath $postgresManifestPath -Raw | ConvertFrom-Json
if ($postgresManifest.architecture -ne "windows-x64" -or $postgresManifest.postgres_version -ne $toolchain.postgresql) {
    throw "The PostgreSQL bundle does not match the locked release inputs."
}
Assert-ManifestFiles -Root $postgresBundle -Files $postgresManifest.files -Label "PostgreSQL runtime"

$vcRedistManifestPath = Join-Path $vcRedistRoot "bundle.json"
$vcRedistPath = Join-Path $vcRedistRoot "vc_redist.x64.exe"
$vcRedistManifest = Get-Content -LiteralPath $vcRedistManifestPath -Raw | ConvertFrom-Json
$vcRedistFile = Get-Item -LiteralPath $vcRedistPath
$vcRedistSignature = Get-AuthenticodeSignature -LiteralPath $vcRedistPath
if ($vcRedistManifest.architecture -ne $toolchain.vcRedist.architecture -or $vcRedistManifest.version -ne $toolchain.vcRedist.version -or $vcRedistManifest.sha256 -ne $toolchain.vcRedist.sha256 -or $vcRedistFile.VersionInfo.FileVersion -ne $toolchain.vcRedist.version -or $vcRedistFile.VersionInfo.ProductName -ne $toolchain.vcRedist.productName -or $vcRedistFile.VersionInfo.OriginalFilename -ne $toolchain.vcRedist.originalFilename -or $vcRedistSignature.Status -ne "Valid" -or $vcRedistSignature.SignerCertificate.Subject -ne $toolchain.vcRedist.signer) {
    throw "The Microsoft x64 Visual C++ Redistributable does not match the signed, hash-pinned release input."
}
if ((Get-Sha256 $vcRedistPath) -ne $toolchain.vcRedist.sha256 -or [long]$vcRedistFile.Length -ne [long]$vcRedistManifest.packageBytes) {
    throw "The Microsoft x64 Visual C++ Redistributable file failed its locked size or SHA-256 check."
}

$tauriPackagePath = Join-Path $frontendRoot "node_modules\@tauri-apps\cli\package.json"
if (-not (Test-Path -LiteralPath $tauriPackagePath -PathType Leaf)) {
    throw "Tauri CLI is missing. Run npm ci in the frontend directory first."
}
$tauriCliVersion = (& node -e "process.stdout.write(JSON.parse(require('fs').readFileSync(process.argv[1], 'utf8')).version)" $tauriPackagePath).Trim()
if ($LASTEXITCODE -ne 0 -or $tauriCliVersion -ne $toolchain.tauriCli) {
    throw "Installer build requires Tauri CLI $($toolchain.tauriCli); found '$tauriCliVersion'."
}

if ($OutputDirectory) {
    if ([IO.Path]::IsPathRooted($OutputDirectory)) {
        $outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
    }
    else {
        $outputRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))
    }
}
else {
    $outputRoot = Join-Path $repoRoot "backend\artifacts\installers\AI-Career-Copilot-$version-x64"
}
if (Test-Path -LiteralPath $outputRoot) {
    throw "Installer output already exists; choose a new output location to avoid overwriting it: $outputRoot"
}

$tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if ($BuildCacheDirectory) {
    $buildTarget = [IO.Path]::GetFullPath($BuildCacheDirectory)
    $buildTargetName = Split-Path -Leaf $buildTarget
    if (-not $buildTarget.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or $buildTargetName -notmatch '^aicc-installer-stage4-[a-f0-9]{32}$') {
        throw "A reusable Stage 4 cache must be a named aicc-installer-stage4-* directory directly under TEMP."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $buildTarget "x86_64-pc-windows-msvc\release\ai-career-copilot-desktop.exe") -PathType Leaf)) {
        throw "The specified Stage 4 cache does not contain the expected completed Windows executable: $buildTarget"
    }
}
else {
    $buildTargetId = [guid]::NewGuid().ToString("N")
    $buildTarget = Join-Path $env:TEMP "aicc-installer-stage4-$buildTargetId"
    if (Test-Path -LiteralPath $buildTarget) {
        throw "Stage 4 build cache already exists; choose a new cache path: $buildTarget"
    }
    $null = New-Item -ItemType Directory -Path $buildTarget
}
$env:CARGO_TARGET_DIR = $buildTarget
$previousLocation = Get-Location
try {
    Set-Location $frontendRoot
    & $npmCommand.Source run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed." }

    $nsisPrepareScript = Join-Path $PSScriptRoot "prepare_tauri_nsis.ps1"
    & $powershellCommand.Source -NoProfile -File $nsisPrepareScript
    if ($LASTEXITCODE -ne 0) { throw "Could not prepare the Tauri-pinned NSIS toolchain." }
    $nsisRoot = Join-Path $env:LOCALAPPDATA "tauri\NSIS"

    $noticesPath = Join-Path $repoRoot "resources\notices\THIRD_PARTY_NOTICES.txt"
    & $pythonCommand.Source (Join-Path $PSScriptRoot "generate_third_party_notices.py") `
        --runtime-root $backendRuntime `
        --postgres-root $postgresBundle `
        --vc-redist-exe $vcRedistPath `
        --vc-redist-manifest $vcRedistManifestPath `
        --nsis-root $nsisRoot `
        --frontend-root $frontendRoot `
        --cargo-manifest (Join-Path $tauriRoot "Cargo.toml") `
        --cargo $cargoCommand.Source `
        --output $noticesPath `
        --replace-generated
    if ($LASTEXITCODE -ne 0) { throw "Third-party notice generation failed." }

    $tauriCli = Join-Path $frontendRoot "node_modules\.bin\tauri.cmd"
    & $tauriCli build --target x86_64-pc-windows-msvc --bundles nsis --no-sign --ci
    if ($LASTEXITCODE -ne 0) { throw "Tauri NSIS installer build failed." }

    $installers = @(Get-ChildItem -LiteralPath $buildTarget -File -Recurse -Filter "*-setup.exe")
    if ($installers.Count -ne 1) {
        throw "Expected exactly one Tauri NSIS setup executable, found $($installers.Count)."
    }

    $null = New-Item -ItemType Directory -Path $outputRoot -Force
    $installerPath = Join-Path $outputRoot "AI-Career-Copilot-Setup-$version-x64.exe"
    Copy-Item -LiteralPath $installers[0].FullName -Destination $installerPath
    $installerHash = Get-Sha256 $installerPath
    $hashFile = Join-Path $outputRoot "AI-Career-Copilot-Setup-$version-x64.exe.sha256"
    Set-Content -LiteralPath $hashFile -Value "$installerHash  $(Split-Path -Leaf $installerPath)" -Encoding ascii

    $releaseManifest = [ordered]@{
        application = "AI Career Copilot"
        version = $version
        architecture = "windows-x64"
        installerFormat = "Tauri NSIS setup executable"
        installMode = "per-machine; Program Files; administrator approval required for install and upgrade"
        persistentDataDirectory = "%LOCALAPPDATA%\AI Career Copilot"
        python = $runtimeManifest.python
        pyinstaller = $runtimeManifest.pyinstaller
        postgresql = $postgresManifest.postgres_version
        node = $nodeVersion
        npm = $npmVersion
        rustc = $rustcVersion
        cargo = $cargoVersion
        tauriCli = $toolchain.tauriCli
        nsis = $toolchain.nsis.version
        nsisArchiveSha1 = $toolchain.nsis.archiveSha1.ToLowerInvariant()
        nsisUtilitiesVersion = $toolchain.nsis.tauriUtilitiesVersion
        nsisUtilitiesSha1 = $toolchain.nsis.tauriUtilitiesSha1.ToLowerInvariant()
        installerFile = Split-Path -Leaf $installerPath
        installerBytes = (Get-Item -LiteralPath $installerPath).Length
        installerSha256 = $installerHash
        runtimeManifestSha256 = Get-Sha256 $runtimeManifestPath
        postgresManifestSha256 = Get-Sha256 $postgresManifestPath
        vcRedistVersion = $vcRedistManifest.version
        vcRedistArchitecture = $vcRedistManifest.architecture
        vcRedistUrl = $vcRedistManifest.sourceUrl
        vcRedistBytes = $vcRedistFile.Length
        vcRedistSha256 = Get-Sha256 $vcRedistPath
        vcRedistSigner = $vcRedistManifest.signer
        vcRedistSignatureStatus = $vcRedistManifest.signatureStatus
        vcRedistManifestSha256 = Get-Sha256 $vcRedistManifestPath
        frontendPackageLockSha256 = Get-Sha256 (Join-Path $frontendRoot "package-lock.json")
        cargoLockSha256 = Get-Sha256 (Join-Path $tauriRoot "Cargo.lock")
        noticesSha256 = Get-Sha256 $noticesPath
        codeSigned = $false
        webview2 = "Microsoft bootstrapper embedded; runtime downloads only if missing"
        visualCRuntime = "Microsoft Visual C++ v14 x64 Redistributable $($vcRedistManifest.version); NSIS checks the installed x64 v14 runtime and installs this signed, hash-pinned package only when it is missing or older"
    }
    $releaseManifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $outputRoot "release-manifest.json") -Encoding utf8

    Write-Output "Installer: $installerPath"
    Write-Output "Bytes: $((Get-Item -LiteralPath $installerPath).Length)"
    Write-Output "SHA-256: $installerHash"
    Write-Output "Release manifest: $(Join-Path $outputRoot 'release-manifest.json')"
    Write-Output "Tauri build cache: $buildTarget"
}
finally {
    Set-Location $previousLocation
}
