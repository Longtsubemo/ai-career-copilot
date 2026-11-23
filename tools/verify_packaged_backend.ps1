param(
    [string]$ApplicationRoot = "",
    [switch]$RunBackendTests
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $ApplicationRoot) {
    $ApplicationRoot = Join-Path $repoRoot "backend\artifacts\desktop\windows-x64"
}
$ApplicationRoot = (Resolve-Path -LiteralPath $ApplicationRoot).Path
$backendExe = Join-Path $ApplicationRoot "resources\backend\AI-Career-Copilot-Backend.exe"
$postgresBin = Join-Path $ApplicationRoot "resources\postgresql\16.16\windows-x64-minimal\bin"
if (-not (Test-Path -LiteralPath $backendExe) -or -not (Test-Path -LiteralPath (Join-Path $postgresBin "postgres.exe"))) {
    throw "The staged packaged backend or PostgreSQL runtime is incomplete."
}

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("aicc-packaged-backend-" + [guid]::NewGuid().ToString("N"))
$dataRoot = Join-Path $tempRoot "AI Career Copilot"
$process = $null
$httpClient = $null
$clientHandler = $null
$databaseWasStopped = $false

function Start-PackagedBackend {
    param([string]$DataDirectory)

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $backendExe
    $startInfo.WorkingDirectory = $ApplicationRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.Environment.Clear()
    $startInfo.Environment["PATH"] = "$postgresBin;$env:SystemRoot\System32;$env:SystemRoot"
    foreach ($key in @("SystemRoot", "WINDIR", "SYSTEMDRIVE", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "PROGRAMDATA")) {
        if (Get-Item -Path "Env:$key" -ErrorAction SilentlyContinue) {
            $startInfo.Environment[$key] = [Environment]::GetEnvironmentVariable($key)
        }
    }
    $startInfo.Environment["TEMP"] = $tempRoot
    $startInfo.Environment["TMP"] = $tempRoot
    $startInfo.Environment["CAREER_COPILOT_RUNTIME_MODE"] = "desktop"
    $arguments = @(
        "--webview-mode",
        "standalone",
        "--install-dir",
        $ApplicationRoot,
        "--data-dir",
        $DataDirectory
    )
    $startInfo.Arguments = ($arguments | ForEach-Object {
        '"' + ([string]$_).Replace('"', '\"') + '"'
    }) -join " "

    $random = [Security.Cryptography.RNGCryptoServiceProvider]::new()
    $capabilityBytes = New-Object byte[] 32
    $random.GetBytes($capabilityBytes)
    $random.Dispose()
    $apiCapability = [BitConverter]::ToString($capabilityBytes).Replace("-", "").ToLowerInvariant()
    $child = [Diagnostics.Process]::new()
    $child.StartInfo = $startInfo
    if (-not $child.Start()) { throw "The packaged backend process did not start." }
    $handshake = @{ command = "initialize"; api_capability = $apiCapability } | ConvertTo-Json -Compress
    $child.StandardInput.WriteLine($handshake)
    $child.StandardInput.Flush()
    $readyTask = $child.StandardOutput.ReadLineAsync()
    $stderrTask = $child.StandardError.ReadToEndAsync()
    $deadline = [DateTime]::UtcNow.AddMinutes(3)
    while (-not $readyTask.IsCompleted) {
        if ($child.HasExited) {
            $errorText = $stderrTask.GetAwaiter().GetResult()
            throw "The packaged backend exited during startup with code $($child.ExitCode). $errorText"
        }
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "The packaged backend did not become ready within three minutes."
        }
        Start-Sleep -Milliseconds 100
    }
    $readyLine = $readyTask.GetAwaiter().GetResult()
    try { $ready = $readyLine | ConvertFrom-Json -ErrorAction Stop } catch { throw "The packaged backend returned an invalid startup message." }
    if ($ready.event -ne "ready" -or $ready.api_url -notmatch '^http://127\.0\.0\.1:[0-9]+$') {
        throw "The packaged backend did not report a valid loopback API address."
    }
    return @{ Process = $child; Url = $ready.api_url; ApiCapability = $apiCapability }
}

function Stop-PackagedBackend {
    param([Diagnostics.Process]$Child)
    if ($null -eq $Child -or $Child.HasExited) { return $true }
    $Child.StandardInput.WriteLine('{"command":"shutdown"}')
    $Child.StandardInput.Flush()
    if ($Child.WaitForExit(45000)) { return $true }
    return $false
}

function Send-JsonRequest {
    param(
        [Net.Http.HttpClient]$Client,
        [string]$Method,
        [string]$Uri,
        [object]$Body = $null
    )
    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::new($Method), $Uri)
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 8 -Compress
        $request.Content = [Net.Http.StringContent]::new($json, [Text.Encoding]::UTF8, "application/json")
    }
    return $Client.SendAsync($request).GetAwaiter().GetResult()
}

function Invoke-BackendTests {
    $pythonExe = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        throw "The repository test environment is missing; the packaged backend smoke checks can still run without it."
    }
    $statePath = Join-Path $dataRoot "config\runtime.json"
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if (-not $state.admin_password -or -not $state.postgres_port) {
        throw "The isolated managed database credentials are unavailable for the test run."
    }
    $escapedPassword = [Uri]::EscapeDataString([string]$state.admin_password)
    $testDatabaseUrl = "postgresql+psycopg://postgres:$escapedPassword@127.0.0.1:$($state.postgres_port)/aicc_packaging"
    $backendDirectory = Join-Path $repoRoot "backend"
    $venvDirectory = Join-Path $backendDirectory ".venv"
    $pythonHome = $null
    $pyvenvConfig = Join-Path $venvDirectory "pyvenv.cfg"
    if (Test-Path -LiteralPath $pyvenvConfig) {
        $pythonHome = Get-Content -LiteralPath $pyvenvConfig | ForEach-Object {
            if ($_ -match '^\s*home\s*=\s*(.+)$') { $Matches[1].Trim() }
        } | Select-Object -First 1
    }
    $testPath = @((Join-Path $venvDirectory "Scripts"), $postgresBin, "$env:SystemRoot\System32", $env:SystemRoot)
    if ($pythonHome) { $testPath += @($pythonHome, (Join-Path $pythonHome "DLLs")) }

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $pythonExe
    $startInfo.Arguments = "-m pytest -ra -p no:cacheprovider"
    $startInfo.WorkingDirectory = $backendDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.Environment.Clear()
    $startInfo.Environment["PATH"] = $testPath -join ";"
    $startInfo.Environment["PYTHONPATH"] = $backendDirectory
    $startInfo.Environment["PYTHONNOUSERSITE"] = "1"
    $startInfo.Environment["VIRTUAL_ENV"] = $venvDirectory
    $startInfo.Environment["TEST_DATABASE_URL"] = $testDatabaseUrl
    $startInfo.Environment["CAREER_COPILOT_RUNTIME_MODE"] = "development"
    $startInfo.Environment["CAREER_COPILOT_DATABASE_URL"] = $testDatabaseUrl
    foreach ($key in @("SystemRoot", "WINDIR", "SYSTEMDRIVE", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "PROGRAMDATA", "TEMP", "TMP")) {
        if (Get-Item -Path "Env:$key" -ErrorAction SilentlyContinue) {
            $startInfo.Environment[$key] = [Environment]::GetEnvironmentVariable($key)
        }
    }

    $testProcess = [Diagnostics.Process]::new()
    $testProcess.StartInfo = $startInfo
    if (-not $testProcess.Start()) { throw "The backend test suite did not start." }
    $stdoutTask = $testProcess.StandardOutput.ReadToEndAsync()
    $stderrTask = $testProcess.StandardError.ReadToEndAsync()
    if (-not $testProcess.WaitForExit(900000)) {
        $testProcess.Kill()
        throw "The backend test suite exceeded its 15-minute limit."
    }
    $testExitCode = $testProcess.ExitCode
    $testOutput = $stdoutTask.GetAwaiter().GetResult() + $stderrTask.GetAwaiter().GetResult()
    $testProcess.Dispose()
    $summary = $testOutput -split "`r?`n" | Where-Object {
        $_ -match '\b\d+\s+(passed|failed|error|errors|skipped|deselected)\b'
    } | Select-Object -Last 1
    if (-not $summary) {
        throw "The backend test process exited without a pytest result summary (exit code $testExitCode)."
    }
    if ($testExitCode -ne 0) {
        throw "The backend test suite failed (exit code $testExitCode): $summary"
    }
    Write-Output "Backend tests: $summary"
}

try {
    $null = New-Item -ItemType Directory -Path $tempRoot
    $firstRun = Start-PackagedBackend -DataDirectory $dataRoot
    $process = $firstRun.Process
    $apiUrl = $firstRun.Url
    $apiCapability = $firstRun.ApiCapability

    $clientHandler = [Net.Http.HttpClientHandler]::new()
    $clientHandler.UseCookies = $false
    $httpClient = [Net.Http.HttpClient]::new($clientHandler)
    $httpClient.DefaultRequestHeaders.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $apiCapability)

    $unauthenticatedHandler = [Net.Http.HttpClientHandler]::new()
    $unauthenticatedHandler.UseCookies = $false
    $unauthenticatedClient = [Net.Http.HttpClient]::new($unauthenticatedHandler)
    foreach ($path in @("/health", "/health/ready", "/api/candidates/me", "/api/not-a-route")) {
        $unauthorized = $unauthenticatedClient.GetAsync($apiUrl + $path).GetAwaiter().GetResult()
        if ($unauthorized.StatusCode -ne [Net.HttpStatusCode]::Unauthorized) { throw "A request without a local capability reached $path." }
        $unauthorized.Dispose()
    }
    $unauthenticatedClient.Dispose(); $unauthenticatedHandler.Dispose()

    $invalidRequest = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, $apiUrl + "/health")
    $invalidRequest.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", ("c" * 64))
    $invalidResponse = $httpClient.SendAsync($invalidRequest).GetAwaiter().GetResult()
    if ($invalidResponse.StatusCode -ne [Net.HttpStatusCode]::Unauthorized) { throw "The packaged API accepted an invalid local capability." }
    $invalidResponse.Dispose(); $invalidRequest.Dispose()

    foreach ($path in @("/health", "/health/ready")) {
        $response = $httpClient.GetAsync($apiUrl + $path).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) { throw "Packaged backend health check failed at $path." }
    }

    $response = $httpClient.GetAsync($apiUrl + "/api/candidates/me").GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) { throw "The packaged backend did not initialize its local profile." }
    if ($response.Headers.Contains("Set-Cookie")) { throw "The packaged local profile unexpectedly set a cookie." }
    $profile = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
    $candidateId = [string]$profile.id
    $response.Dispose()

    $authPath = $httpClient.GetAsync($apiUrl + "/api/auth/me").GetAwaiter().GetResult()
    if ($authPath.StatusCode -ne [Net.HttpStatusCode]::NotFound) { throw "An obsolete authentication route is still available." }
    $authPath.Dispose()

    $profileBody = @{ name = "Packaging verification"; headline = "Local profile"; skills = @("Python", "PostgreSQL"); summary = "Temporary packaged runtime verification." }
    $response = Send-JsonRequest $httpClient "PATCH" ($apiUrl + "/api/candidates/me") $profileBody
    if (-not $response.IsSuccessStatusCode) { throw "The packaged profile update failed without authentication." }
    $response.Dispose()

    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::new("PATCH"), $apiUrl + "/api/candidates/me")
    $request.Content = [Net.Http.StringContent]::new('{"headline":"forged"}', [Text.Encoding]::UTF8, "application/json")
    $request.Headers.Add("Origin", "https://unrelated.example")
    $response = $httpClient.SendAsync($request).GetAwaiter().GetResult()
    if ($response.StatusCode -ne [Net.HttpStatusCode]::Forbidden) { throw "The packaged API did not reject an unrelated browser origin." }
    $response.Dispose()

    if ($RunBackendTests) { Invoke-BackendTests }

    $desktopSettings = Get-Content (Join-Path $dataRoot "config\desktop-settings.json") -Raw
    if ($desktopSettings.Contains($apiCapability) -or $process.StartInfo.Arguments.Contains($apiCapability)) {
        throw "The local capability appeared in persistent settings or backend command-line arguments."
    }

    $httpClient.Dispose(); $clientHandler.Dispose()
    $httpClient = $null; $clientHandler = $null
    if (-not (Stop-PackagedBackend $process)) { throw "The packaged backend did not shut down gracefully after its first run." }
    $databaseWasStopped = $true
    $process.Dispose(); $process = $null

    $secondRun = Start-PackagedBackend -DataDirectory $dataRoot
    $process = $secondRun.Process
    $apiUrl = $secondRun.Url
    $oldApiCapability = $apiCapability
    $apiCapability = $secondRun.ApiCapability
    $clientHandler = [Net.Http.HttpClientHandler]::new()
    $clientHandler.UseCookies = $false
    $httpClient = [Net.Http.HttpClient]::new($clientHandler)
    $httpClient.DefaultRequestHeaders.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $apiCapability)
    $oldCapabilityRequest = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, $apiUrl + "/health")
    $oldCapabilityRequest.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $oldApiCapability)
    $oldCapabilityResponse = $httpClient.SendAsync($oldCapabilityRequest).GetAwaiter().GetResult()
    if ($oldCapabilityResponse.StatusCode -ne [Net.HttpStatusCode]::Unauthorized) { throw "The previous backend capability remained valid after restart." }
    $oldCapabilityResponse.Dispose(); $oldCapabilityRequest.Dispose()
    $response = $httpClient.GetAsync($apiUrl + "/api/candidates/me").GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) { throw "The profile was not available after restarting the packaged backend." }
    $profile = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
    if ([string]$profile.id -ne $candidateId -or $profile.name -ne "Packaging verification" -or $profile.headline -ne "Local profile" -or "Python" -notin $profile.skills) {
        throw "The packaged profile data did not survive restart."
    }
    $response.Dispose()

    Write-Output "Packaged backend started with a PATH that contains no Python installation."
    Write-Output "Capability authentication, invalid/old capability rejection, health and readiness, migrations, profile persistence, origin protection, and graceful shutdown passed."
}
finally {
    if ($null -ne $httpClient) { $httpClient.Dispose() }
    if ($null -ne $clientHandler) { $clientHandler.Dispose() }
    if ($null -ne $process) {
        $databaseWasStopped = Stop-PackagedBackend $process
        if ($databaseWasStopped) { $process.Dispose() }
    }
    $dataRootIsAbsent = -not (Test-Path -LiteralPath $dataRoot)
    if (($databaseWasStopped -or ($null -eq $process -and $dataRootIsAbsent)) -and (Test-Path -LiteralPath $tempRoot)) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
    elseif (Test-Path -LiteralPath $tempRoot) {
        Write-Warning "The isolated test data remains at $tempRoot because graceful shutdown was not confirmed."
    }
}
