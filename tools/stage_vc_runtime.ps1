$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$toolchain = Get-Content -LiteralPath (Join-Path $PSScriptRoot "installer-toolchain.json") -Raw | ConvertFrom-Json
$outputRoot = Join-Path $repoRoot "resources\vc-runtime"
$manifestPath = Join-Path $outputRoot "bundle.json"
$requiredRuntimeFiles = @("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Assert-StagedRuntime {
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "The existing app-local VC runtime has no generated manifest; it was left unchanged: $outputRoot"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.runtime_version -ne $toolchain.vcRuntime -or $manifest.architecture -ne "windows-x64") {
        throw "The existing app-local VC runtime does not match the locked release input; it was left unchanged."
    }
    $expected = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $manifest.files) {
        $relative = [string]$entry.path
        if ([IO.Path]::IsPathRooted($relative) -or $relative.Contains("..") -or -not $expected.Add($relative)) {
            throw "The staged VC runtime manifest contains an unsafe or duplicate path."
        }
        $filePath = Join-Path $outputRoot $relative
        if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
            throw "The staged VC runtime is missing $relative; existing files were left unchanged."
        }
        $file = Get-Item -LiteralPath $filePath
        if ([long]$file.Length -ne [long]$entry.size -or (Get-Sha256 $filePath) -ne [string]$entry.sha256) {
            throw "The staged VC runtime file does not match its manifest: $relative"
        }
    }
    foreach ($required in $requiredRuntimeFiles) {
        if (-not $expected.Contains($required)) {
            throw "The staged VC runtime is missing required file $required."
        }
    }
    $actual = Get-ChildItem -LiteralPath $outputRoot -File | Where-Object { $_.Name -ne "bundle.json" }
    if ($actual.Count -ne $expected.Count) {
        throw "The staged VC runtime has unmanifested or missing files; existing files were left unchanged."
    }
}

if (Test-Path -LiteralPath $outputRoot) {
    Assert-StagedRuntime
    Write-Output "Verified staged Microsoft VC runtime $($toolchain.vcRuntime): $outputRoot"
    exit 0
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
    throw "Visual Studio Installer's vswhere.exe is required to locate the locked VC runtime files."
}
$vsInstall = (& $vswhere -latest -products '*' -property installationPath | Select-Object -First 1).Trim()
if ($LASTEXITCODE -ne 0 -or -not $vsInstall) {
    throw "Could not locate an installed Visual Studio instance for the locked VC runtime."
}
$sourceRoot = Join-Path $vsInstall "VC\Redist\MSVC\$($toolchain.vcRuntime)\x64\Microsoft.VC143.CRT"
if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
    throw "The locked Visual C++ $($toolchain.vcRuntime) x64 redistributable files are missing from the selected Visual Studio installation."
}
foreach ($required in $requiredRuntimeFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot $required) -PathType Leaf)) {
        throw "The Visual C++ runtime package is missing required file $required."
    }
}

$sourceFiles = @(Get-ChildItem -LiteralPath $sourceRoot -File | Sort-Object Name)
if ($sourceFiles.Count -eq 0) {
    throw "The locked Visual C++ runtime directory contains no files."
}
foreach ($file in $sourceFiles) {
    if ($file.Extension -eq ".dll" -and $file.VersionInfo.FileVersion -notlike "$($toolchain.vcRuntime).*" ) {
        throw "Visual C++ runtime file $($file.Name) has an unexpected version: $($file.VersionInfo.FileVersion)"
    }
    if ($file.Extension -eq ".dll" -and (Get-AuthenticodeSignature -LiteralPath $file.FullName).Status -ne "Valid") {
        throw "Visual C++ runtime file $($file.Name) does not have a valid Microsoft signature."
    }
}

$null = New-Item -ItemType Directory -Path $outputRoot
foreach ($file in $sourceFiles) {
    Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $outputRoot $file.Name)
}
$manifest = [ordered]@{
    runtime = "Microsoft Visual C++ 2015-2022 x64 app-local runtime"
    runtime_version = $toolchain.vcRuntime
    architecture = "windows-x64"
    files = @(
        Get-ChildItem -LiteralPath $outputRoot -File | Sort-Object Name | ForEach-Object {
            [ordered]@{
                path = $_.Name
                size = $_.Length
                sha256 = Get-Sha256 $_.FullName
                fileVersion = if ($_.Extension -eq ".dll") { $_.VersionInfo.FileVersion } else { $null }
            }
        }
    )
}
$manifestJson = $manifest | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText($manifestPath, $manifestJson, [Text.UTF8Encoding]::new($false))
Assert-StagedRuntime
Write-Output "Staged and verified Microsoft VC runtime $($toolchain.vcRuntime): $outputRoot"
Write-Output "Source files were read from the installed Visual Studio redistributable folder; no system DLLs were copied."
