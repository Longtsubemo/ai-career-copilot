param(
    [Parameter(Mandatory = $true)]
    [string]$ExecutablePath
)

$ErrorActionPreference = "Stop"
$targetPath = [System.IO.Path]::GetFullPath($ExecutablePath)
if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
    exit 0
}

$targetName = [System.IO.Path]::GetFileName($targetPath)
$processes = Get-CimInstance -ClassName Win32_Process -Filter "Name='$targetName'"
foreach ($processInfo in $processes) {
    if (-not $processInfo.ExecutablePath) {
        continue
    }
    $processPath = [System.IO.Path]::GetFullPath($processInfo.ExecutablePath)
    if (-not [string]::Equals(
            $processPath,
            $targetPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        continue
    }

    $process = Get-Process -Id $processInfo.ProcessId -ErrorAction Stop
    if ($process.MainWindowHandle -eq [IntPtr]::Zero) {
        exit 2
    }
    if (-not $process.CloseMainWindow()) {
        exit 3
    }
    if (-not $process.WaitForExit(120000)) {
        exit 4
    }
}

exit 0
