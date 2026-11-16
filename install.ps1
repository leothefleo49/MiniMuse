<#
.SYNOPSIS
  nanoMuse One-Command Installer for Windows (PowerShell)
  Usage:
    irm https://raw.githubusercontent.com/nanoMuse/nanoMuse/main/install.ps1 | iex
#>
[CmdletBinding()]
param(
  [switch]$CloudOnly,
  [switch]$Dev,
  [string]$InstallDir = "$env:USERPROFILE\nanoMuse"
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "                nanoMuse One-Click Setup                  " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "An open-source personal AI agent for every device you own." -ForegroundColor Gray
Write-Host ""

# 1. Zero-environment Cloud Check
if ($CloudOnly) {
  Write-Host "[*] Zero-Environment Cloud Mode selected!" -ForegroundColor Green
  Write-Host "Opening nanoMuse Cloud PWA in your default browser..." -ForegroundColor Yellow
  Start-Process "https://desktop-f71c82t.tailc1c35a.ts.net/"
  Write-Host "[+] All set! Sign in or use your account across all your devices." -ForegroundColor Green
  return
}

# 2. Check Python / uv / Git
Write-Host "[1/4] Checking prerequisites..." -ForegroundColor Yellow

$pythonCmd = $null
if (Get-Command "python" -ErrorAction SilentlyContinue) {
  $pythonCmd = "python"
} elseif (Get-Command "py" -ErrorAction SilentlyContinue) {
  $pythonCmd = "py"
}

if (-not $pythonCmd) {
  Write-Host "[!] Python not found. Installing via winget..." -ForegroundColor Yellow
  try {
    winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    $pythonCmd = "python"
  } catch {
    Write-Host "[!] Please install Python 3.11+ from https://python.org" -ForegroundColor Red
    return
  }
}

# 3. Setup Directory & Repository
Write-Host "[2/4] Setting up nanoMuse in $InstallDir..." -ForegroundColor Yellow
if (-not (Test-Path "$InstallDir")) {
  New-Item -ItemType Directory -Path "$InstallDir" -Force | Out-Null
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($scriptDir -and (Test-Path "$scriptDir\pyproject.toml")) {
  $InstallDir = $scriptDir
}

Set-Location "$InstallDir"

if (-not (Test-Path "$InstallDir\pyproject.toml") -and -not (Test-Path "$InstallDir\.git")) {
  if (Get-Command "git" -ErrorAction SilentlyContinue) {
    Write-Host "Cloning repository..." -ForegroundColor Gray
    git clone https://github.com/nanoMuse/nanoMuse.git .
  }
}

# 4. Virtual Environment & Dependencies
Write-Host "[3/4] Creating virtual environment and installing packages..." -ForegroundColor Yellow
if (-not (Test-Path "$InstallDir\.venv")) {
  & $pythonCmd -m venv .venv
}

$venvPython = "$InstallDir\.venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip --quiet
if (Test-Path "$InstallDir\pyproject.toml") {
  & $venvPython -m pip install -e . --quiet
} else {
  & $venvPython -m pip install nanomuse --quiet
}

# 5. Launch
Write-Host "[4/4] Starting nanoMuse server..." -ForegroundColor Green
Write-Host ""
Write-Host "nanoMuse is ready! You can open it in any browser or install the PWA." -ForegroundColor Cyan
Write-Host "Local URL: http://127.0.0.1:8787" -ForegroundColor Cyan
Write-Host ""

Start-Process "http://127.0.0.1:8787"
& $venvPython -m nanomuse.server
