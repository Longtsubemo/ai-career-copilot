param(
    [string]$BackendRuntimeDirectory = "",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendRoot = Join-Path $repoRoot "backend"
if ($BackendRuntimeDirectory) {
    if ([IO.Path]::IsPathRooted($BackendRuntimeDirectory)) {
        $backendBundle = [IO.Path]::GetFullPath($BackendRuntimeDirectory)
    }
    else {
        $backendBundle = [IO.Path]::GetFullPath((Join-Path $repoRoot $BackendRuntimeDirectory))
    }
}
else {
$backendBundle = Join-Path $backendRoot "artifacts\packaged-runtime\capability-runtime-v2"
}
$postgresBundle = Join-Path $repoRoot "resources\postgresql\16.16\windows-x64-minimal"
$tauriRelease = Join-Path $repoRoot "frontend\src-tauri\target\release"
$desktopExe = Join-Path $tauriRelease "ai-career-copilot-desktop.exe"
$frontendDist = Join-Path $repoRoot "frontend\dist"
if ($OutputDirectory) {
    if ([IO.Path]::IsPathRooted($OutputDirectory)) {
        $outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
    }
    else {
        $outputRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))
    }
}
else {
    $outputRoot = Join-Path $backendRoot "artifacts\desktop\windows-x64"
}
$outputParent = Split-Path -Parent $outputRoot

foreach ($requiredPath in @($backendBundle, $postgresBundle, $desktopExe, (Join-Path $frontendDist "index.html"))) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "A required build input is missing: $requiredPath"
    }
}
if (Test-Path -LiteralPath $outputRoot) {
    throw "The portable desktop output already exists; choose a new output location or move it yourself: $outputRoot"
}

$null = New-Item -ItemType Directory -Path $outputParent -Force
$stageRoot = Join-Path $outputParent ("windows-x64.stage-" + [guid]::NewGuid().ToString("N"))
try {
    $null = New-Item -ItemType Directory -Path $stageRoot
    Copy-Item -LiteralPath $desktopExe -Destination (Join-Path $stageRoot "ai-career-copilot-desktop.exe")
    Get-ChildItem -LiteralPath $tauriRelease -File -Filter "*.dll" | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $stageRoot
    }
    $resources = Join-Path $stageRoot "resources"
    $null = New-Item -ItemType Directory -Path (Join-Path $resources "backend") -Force
    $null = New-Item -ItemType Directory -Path (Join-Path $resources "postgresql\16.16") -Force
    Get-ChildItem -LiteralPath $backendBundle | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $resources "backend") -Recurse
    }
    Copy-Item -LiteralPath $postgresBundle -Destination (Join-Path $resources "postgresql\16.16") -Recurse
    $frontendDestination = Join-Path $stageRoot "frontend\dist"
    $null = New-Item -ItemType Directory -Path $frontendDestination -Force
    Get-ChildItem -LiteralPath $frontendDist | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $frontendDestination -Recurse
    }
    Move-Item -LiteralPath $stageRoot -Destination $outputRoot
    Write-Output "Portable desktop directory: $outputRoot"
}
finally {
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
}
