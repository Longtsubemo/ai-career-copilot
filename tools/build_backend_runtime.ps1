param(
    [string]$Python = "python",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendRoot = Join-Path $repoRoot "backend"
if ($OutputDirectory) {
    if ([IO.Path]::IsPathRooted($OutputDirectory)) {
        $outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
    }
    else {
        $outputRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))
    }
}
else {
    $outputRoot = Join-Path $backendRoot "artifacts\packaged-runtime\capability-runtime-v2"
}
$outputParent = Split-Path -Parent $outputRoot

if (Test-Path -LiteralPath $outputRoot) {
    throw "The backend output already exists; choose a new output location or move the existing output yourself: $outputRoot"
}

$pythonCommand = Get-Command $Python -ErrorAction Stop
$pythonCheck = & $pythonCommand.Source -c "import platform, sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} {platform.architecture()[0]}')"
if ($LASTEXITCODE -ne 0 -or $pythonCheck -notmatch '^3\.13\.[0-9]+ 64bit$') {
    throw "Backend packaging requires 64-bit CPython 3.13; found '$pythonCheck'."
}

$buildRoot = Join-Path $env:TEMP ("aicc-backend-build-" + [guid]::NewGuid().ToString("N"))
try {
    $null = New-Item -ItemType Directory -Path $buildRoot
    $venvPython = Join-Path $buildRoot "venv\Scripts\python.exe"
    & $pythonCommand.Source -m venv (Join-Path $buildRoot "venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create the isolated packaging environment." }

    & $venvPython -m pip install --require-hashes `
        -r (Join-Path $backendRoot "requirements-runtime-win-x64-py313.lock") `
        -r (Join-Path $backendRoot "requirements-packaging-win-x64-py313.lock")
    if ($LASTEXITCODE -ne 0) { throw "Could not install the pinned, hashed build dependencies." }

    $env:PYTHONHASHSEED = "0"
    & $venvPython -m PyInstaller --noconfirm --clean `
        --distpath (Join-Path $buildRoot "dist") `
        --workpath (Join-Path $buildRoot "work") `
        (Join-Path $backendRoot "packaging\desktop_backend.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed to build the backend runtime." }

    $builtRuntime = Join-Path $buildRoot "dist\AI-Career-Copilot-Backend"
    if (-not (Test-Path -LiteralPath (Join-Path $builtRuntime "AI-Career-Copilot-Backend.exe"))) {
        throw "The expected backend executable is missing from PyInstaller output."
    }
    $null = New-Item -ItemType Directory -Path $outputParent -Force
    Copy-Item -LiteralPath $builtRuntime -Destination $outputRoot -Recurse

    $outputPrefix = [IO.Path]::GetFullPath($outputRoot).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $files = Get-ChildItem -LiteralPath $outputRoot -File -Recurse | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($outputPrefix.Length).Replace("\", "/")
            size = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $runtimeBytes = [long](($files | ForEach-Object { [long]$_.size } | Measure-Object -Sum).Sum)
    $manifest = [ordered]@{
        application = "AI Career Copilot backend"
        architecture = "windows-x64"
        python = $pythonCheck
        pyinstaller = "6.22.3"
        dependencyLock = "requirements-runtime-win-x64-py313.lock"
        packagingLock = "requirements-packaging-win-x64-py313.lock"
        fileCount = @($files).Count
        payloadBytes = $runtimeBytes
        files = @($files)
    }
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $outputRoot "build-manifest.json") -Encoding utf8
    Write-Output "Packaged backend: $outputRoot"
    Write-Output "Payload bytes: $runtimeBytes"
}
finally {
    if (Test-Path -LiteralPath $buildRoot) {
        Remove-Item -LiteralPath $buildRoot -Recurse -Force
    }
}
