param(
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$toolchain = Get-Content -LiteralPath (Join-Path $PSScriptRoot "installer-toolchain.json") -Raw | ConvertFrom-Json
if ($OutputDirectory) {
    if ([IO.Path]::IsPathRooted($OutputDirectory)) {
        $outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
    }
    else {
        $outputRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))
    }
}
else {
    $outputRoot = Join-Path $repoRoot "resources\vc-redist"
}
$installerPath = Join-Path $outputRoot "vc_redist.x64.exe"
$manifestPath = Join-Path $outputRoot "bundle.json"

function Get-Sha256([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-Redistributable([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "The Microsoft x64 Visual C++ Redistributable is missing: $Path"
    }
    $file = Get-Item -LiteralPath $Path
    if ($file.VersionInfo.FileVersion -ne $toolchain.vcRedist.version -or
        $file.VersionInfo.ProductVersion -ne $toolchain.vcRedist.version -or
        $file.VersionInfo.ProductName -ne $toolchain.vcRedist.productName -or
        $file.VersionInfo.OriginalFilename -ne $toolchain.vcRedist.originalFilename -or
        $file.VersionInfo.CompanyName -ne "Microsoft Corporation") {
        throw "The downloaded Visual C++ Redistributable metadata does not match the locked Microsoft x64 package."
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -ne $toolchain.vcRedist.signer) {
        throw "The Visual C++ Redistributable does not have a valid Microsoft Authenticode signature."
    }
    $hash = Get-Sha256 $Path
    if ($hash -ne $toolchain.vcRedist.sha256) {
        throw "The Microsoft Visual C++ Redistributable SHA-256 does not match the locked release input."
    }
    return [ordered]@{
        redistributable = "Microsoft Visual C++ v14 Redistributable"
        version = $toolchain.vcRedist.version
        architecture = $toolchain.vcRedist.architecture
        sourceUrl = $toolchain.vcRedist.url
        packageFile = $file.Name
        packageBytes = $file.Length
        sha256 = $hash
        signer = $signature.SignerCertificate.Subject
        signerThumbprint = $signature.SignerCertificate.Thumbprint
        signatureStatus = $signature.Status.ToString()
        originalFilename = $file.VersionInfo.OriginalFilename
        productName = $file.VersionInfo.ProductName
        bootstrapperArchitecture = "x86 setup stub; Microsoft package identity and product metadata specify x64 runtime"
    }
}

if (Test-Path -LiteralPath $outputRoot) {
    $manifest = Assert-Redistributable $installerPath
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "The existing Microsoft runtime directory has no generated manifest; it was left unchanged: $outputRoot"
    }
    $existingManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($existingManifest.sha256 -ne $manifest.sha256 -or
        $existingManifest.version -ne $manifest.version -or
        $existingManifest.architecture -ne $manifest.architecture -or
        $existingManifest.signatureStatus -ne "Valid") {
        throw "The existing Microsoft runtime manifest does not match the locked, verified package; it was left unchanged."
    }
    $actualFiles = @(Get-ChildItem -LiteralPath $outputRoot -File | Select-Object -ExpandProperty Name | Sort-Object)
    if (($actualFiles -join "|") -ne "bundle.json|vc_redist.x64.exe") {
        throw "The Microsoft runtime directory contains unmanifested files; existing files were left unchanged."
    }
    Write-Output "Verified Microsoft Visual C++ Redistributable $($manifest.version) x64: $installerPath"
    exit 0
}

$tempRoot = Join-Path $env:TEMP ("aicc-vcredist-" + [guid]::NewGuid().ToString("N"))
$null = New-Item -ItemType Directory -Path $tempRoot
try {
    $downloadPath = Join-Path $tempRoot "vc_redist.x64.exe"
    Invoke-WebRequest -Uri $toolchain.vcRedist.url -OutFile $downloadPath
    $manifest = Assert-Redistributable $downloadPath
    $null = New-Item -ItemType Directory -Path $outputRoot
    Copy-Item -LiteralPath $downloadPath -Destination $installerPath
    $manifestJson = $manifest | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText($manifestPath, $manifestJson, [Text.UTF8Encoding]::new($false))
    $null = Assert-Redistributable $installerPath
    Write-Output "Downloaded and verified Microsoft's x64 Visual C++ Redistributable $($manifest.version)."
    Write-Output "Package: $installerPath"
    Write-Output "Bytes: $((Get-Item -LiteralPath $installerPath).Length)"
    Write-Output "SHA-256: $($manifest.sha256)"
}
finally {
    $resolvedTemp = [IO.Path]::GetFullPath($tempRoot)
    $tempPrefix = [IO.Path]::GetFullPath($env:TEMP).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($resolvedTemp.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTemp) -match '^aicc-vcredist-[a-f0-9]{32}$') {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
    }
}
