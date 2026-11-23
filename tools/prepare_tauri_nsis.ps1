$ErrorActionPreference = "Stop"
$toolchain = Get-Content -LiteralPath (Join-Path $PSScriptRoot "installer-toolchain.json") -Raw | ConvertFrom-Json
$toolsRoot = Join-Path $env:LOCALAPPDATA "tauri"
$nsisRoot = Join-Path $toolsRoot "NSIS"
$pluginRelative = "Plugins\x86-unicode\additional\nsis_tauri_utils.dll"
$requiredFiles = @(
    "makensis.exe",
    "Bin\makensis.exe",
    "Stubs\lzma-x86-unicode",
    "Stubs\lzma_solid-x86-unicode",
    $pluginRelative,
    "Include\MUI2.nsh",
    "Include\FileFunc.nsh",
    "Include\x64.nsh",
    "Include\nsDialogs.nsh",
    "Include\WinMessages.nsh",
    "Include\Win\COM.nsh",
    "Include\Win\Propkey.nsh",
    "Include\Win\RestartManager.nsh"
)

function Get-Sha1([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA1]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace("-", "").ToUpperInvariant()
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Assert-ValidNsis([string]$Root) {
    foreach ($relative in $requiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $relative) -PathType Leaf)) {
            throw "Existing Tauri NSIS cache is incomplete; missing $relative. It was left unchanged: $Root"
        }
    }
    $exeHash = Get-Sha1 (Join-Path $Root "NSIS.exe")
    if ($exeHash -ne $toolchain.nsis.executableSha1) {
        throw "Existing NSIS.exe does not match the Tauri-pinned hash; it was left unchanged: $Root"
    }
    $pluginHash = Get-Sha1 (Join-Path $Root $pluginRelative)
    if ($pluginHash -ne $toolchain.nsis.tauriUtilitiesSha1) {
        throw "Existing NSIS utility plugin does not match the Tauri-pinned hash; it was left unchanged: $Root"
    }
}

if (Test-Path -LiteralPath $nsisRoot) {
    Assert-ValidNsis $nsisRoot
    Write-Output "Verified Tauri NSIS $($toolchain.nsis.version) cache: $nsisRoot"
    exit 0
}

$stagingRoot = Join-Path $env:TEMP ("aicc-nsis-" + [guid]::NewGuid().ToString("N"))
if (Test-Path -LiteralPath $stagingRoot) {
    throw "NSIS staging directory already exists; refusing to reuse it: $stagingRoot"
}
$null = New-Item -ItemType Directory -Path $stagingRoot
$archivePath = Join-Path $stagingRoot "nsis.zip"
$pluginPath = Join-Path $stagingRoot "nsis_tauri_utils.dll"
& curl.exe --location --fail --silent --show-error --max-time 180 --output $archivePath $toolchain.nsis.archiveUrl
if ($LASTEXITCODE -ne 0) {
    throw "Could not download the Tauri-pinned NSIS archive (curl exit $LASTEXITCODE)."
}
if ((Get-Sha1 $archivePath) -ne $toolchain.nsis.archiveSha1) {
    throw "Downloaded NSIS archive did not match Tauri's pinned SHA-1."
}

& curl.exe --location --fail --silent --show-error --max-time 120 --output $pluginPath $toolchain.nsis.tauriUtilitiesUrl
if ($LASTEXITCODE -ne 0) {
    throw "Could not download the Tauri-pinned NSIS utility plugin (curl exit $LASTEXITCODE)."
}
if ((Get-Sha1 $pluginPath) -ne $toolchain.nsis.tauriUtilitiesSha1) {
    throw "Downloaded NSIS utility plugin did not match Tauri's pinned SHA-1."
}

$extractRoot = Join-Path $stagingRoot "extracted"
$null = New-Item -ItemType Directory -Path $extractRoot
Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot
$preparedRoot = Join-Path $extractRoot ("nsis-" + $toolchain.nsis.version)
if (-not (Test-Path -LiteralPath $preparedRoot -PathType Container)) {
    throw "The verified archive did not contain the expected NSIS $($toolchain.nsis.version) directory."
}
if ((Get-Sha1 (Join-Path $preparedRoot "NSIS.exe")) -ne $toolchain.nsis.executableSha1) {
    throw "Extracted NSIS.exe did not match Tauri's pinned SHA-1."
}

$pluginTarget = Join-Path $preparedRoot $pluginRelative
if (Test-Path -LiteralPath $pluginTarget) {
    if ((Get-Sha1 $pluginTarget) -ne $toolchain.nsis.tauriUtilitiesSha1) {
        throw "The NSIS archive contains a conflicting utility plugin."
    }
}
else {
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $pluginTarget) -Force
    Copy-Item -LiteralPath $pluginPath -Destination $pluginTarget
}
Assert-ValidNsis $preparedRoot

if (-not (Test-Path -LiteralPath $toolsRoot)) {
    $null = New-Item -ItemType Directory -Path $toolsRoot
}
if (Test-Path -LiteralPath $nsisRoot) {
    throw "Tauri NSIS cache appeared while preparing it; refusing to overwrite: $nsisRoot"
}
Move-Item -LiteralPath $preparedRoot -Destination $nsisRoot
Assert-ValidNsis $nsisRoot
Write-Output "Installed and verified Tauri NSIS $($toolchain.nsis.version): $nsisRoot"
Write-Output "Download staging directory retained for inspection: $stagingRoot"
