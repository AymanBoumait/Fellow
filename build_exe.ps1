# PowerShell Build Script for Fellow Desktop Application (.exe)
$ErrorActionPreference = "Stop"

Write-Host "=== Building Fellow Standalone Windows Application ===" -ForegroundColor Cyan

# 1. Ensure application icons exist
if (-not (Test-Path "assets\icon.ico")) {
    Write-Host "Generating Fellow icons..." -ForegroundColor Yellow
    python generate_icon.py
}

# 2. Clean previous build artifacts
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }

# 3. Run PyInstaller using fellow.spec
Write-Host "Running PyInstaller..." -ForegroundColor Yellow
pyinstaller fellow.spec --clean --noconfirm

Write-Host ""
Write-Host "Build complete!" -ForegroundColor Green
Write-Host "Standalone folder distribution: dist\Fellow\Fellow.exe" -ForegroundColor Green
