<#
.SYNOPSIS
Builds the all-in-one offline Windows installer (installer_output\FellowSetup.exe)
bundling both Fellow.exe and OllamaSetup.exe.

.PARAMETER SkipExeBuild
Reuse an existing dist\Fellow\Fellow.exe instead of rebuilding with PyInstaller.

.PARAMETER ForceDownloadOllama
Re-download the latest OllamaSetup.exe even if installer_assets\OllamaSetup.exe already exists.
#>
param(
    [switch]$SkipExeBuild,
    [switch]$ForceDownloadOllama
)

$ErrorActionPreference = "Stop"

Write-Host "=== Building Fellow All-in-One Offline Installer (FellowSetup.exe) ===" -ForegroundColor Cyan

# 1. Build standalone Fellow app folder (dist\Fellow\Fellow.exe) unless skipped and already built
if ($SkipExeBuild -and (Test-Path "dist\Fellow\Fellow.exe")) {
    Write-Host "Skipping PyInstaller build; reusing existing dist\Fellow\Fellow.exe." -ForegroundColor Yellow
} else {
    Write-Host "Building standalone Fellow application..." -ForegroundColor Yellow
    & ".\build_exe.ps1"
    if (-not (Test-Path "dist\Fellow\Fellow.exe")) {
        Write-Error "Build failed: dist\Fellow\Fellow.exe was not created."
        exit 1
    }
}

# 2. Ensure installer_assets\OllamaSetup.exe is present and valid (> 50 MB)
$assetsDir = "installer_assets"
$ollamaInstallerPath = Join-Path $assetsDir "OllamaSetup.exe"
$ollamaDownloadUrl = "https://ollama.com/download/OllamaSetup.exe"
$minExpectedBytes = 50MB

if (-not (Test-Path $assetsDir)) {
    New-Item -ItemType Directory -Path $assetsDir | Out-Null
}

$needDownload = $ForceDownloadOllama -or (-not (Test-Path $ollamaInstallerPath))
if (-not $needDownload) {
    $existingSize = (Get-Item $ollamaInstallerPath).Length
    if ($existingSize -lt $minExpectedBytes) {
        Write-Host "Existing OllamaSetup.exe appears incomplete ($existingSize bytes); re-downloading..." -ForegroundColor Yellow
        $needDownload = $true
    } else {
        $existingMb = [math]::Round($existingSize / 1MB, 1)
        Write-Host "Using cached $ollamaInstallerPath ($existingMb MB)." -ForegroundColor Green
    }
}

if ($needDownload) {
    Write-Host "Downloading official OllamaSetup.exe from $ollamaDownloadUrl ..." -ForegroundColor Yellow
    $tmpDownload = "$ollamaInstallerPath.downloading"
    if (Test-Path $tmpDownload) { Remove-Item -Force $tmpDownload }

    $downloaded = $false
    $curlCmd = Get-Command "curl.exe" -ErrorAction SilentlyContinue
    if ($curlCmd) {
        & $curlCmd.Source -L --fail --progress-bar -o $tmpDownload $ollamaDownloadUrl
        if ($LASTEXITCODE -eq 0 -and (Test-Path $tmpDownload)) {
            $downloaded = $true
        }
    }

    if (-not $downloaded) {
        Write-Host "Falling back to Invoke-WebRequest..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $ollamaDownloadUrl -OutFile $tmpDownload -UseBasicParsing
    }

    if (-not (Test-Path $tmpDownload)) {
        Write-Error "Failed to download OllamaSetup.exe from $ollamaDownloadUrl."
        exit 1
    }

    $dlSize = (Get-Item $tmpDownload).Length
    if ($dlSize -lt $minExpectedBytes) {
        Remove-Item -Force $tmpDownload -ErrorAction SilentlyContinue
        Write-Error "Downloaded file is unexpectedly small ($dlSize bytes). Expected official OllamaSetup.exe (> 50 MB)."
        exit 1
    }

    Move-Item -Force $tmpDownload $ollamaInstallerPath
    $dlMb = [math]::Round($dlSize / 1MB, 1)
    Write-Host "Downloaded OllamaSetup.exe ($dlMb MB)." -ForegroundColor Green
}

# 3. Locate Inno Setup 6 (ISCC.exe)
$isccCandidates = @()
$isccInPath = Get-Command "iscc" -ErrorAction SilentlyContinue
if ($isccInPath) {
    $isccCandidates += $isccInPath.Source
}
$isccCandidates += @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
)

$isccPath = $isccCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $isccPath) {
    Write-Host ""
    Write-Host "Inno Setup 6 (ISCC.exe) was not found on this machine." -ForegroundColor Red
    Write-Host "Install it easily with:" -ForegroundColor Yellow
    Write-Host "    winget install JRSoftware.InnoSetup" -ForegroundColor Cyan
    Write-Host "Or download from: https://jrsoftware.org/isdl.php" -ForegroundColor Cyan
    exit 1
}

# 4. Compile FellowSetup.exe
Write-Host "Compiling offline installer with $isccPath ..." -ForegroundColor Yellow
& $isccPath "installer.iss"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Inno Setup compilation failed with exit code $LASTEXITCODE."
    exit $LASTEXITCODE
}

$setupOut = "installer_output\FellowSetup.exe"
if (-not (Test-Path $setupOut)) {
    Write-Error "Expected output $setupOut was not produced."
    exit 1
}

$outBytes = (Get-Item $setupOut).Length
if ($outBytes -ge 1GB) {
    $sizeStr = "$([math]::Round($outBytes / 1GB, 2)) GB"
} else {
    $sizeStr = "$([math]::Round($outBytes / 1MB, 1)) MB"
}

Write-Host ""
Write-Host "All-in-one offline installer built successfully!" -ForegroundColor Green
Write-Host "Output: $setupOut ($sizeStr)" -ForegroundColor Green
