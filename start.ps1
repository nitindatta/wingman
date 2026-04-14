# Envoy — Windows startup script
# Run from the repo root: .\start.ps1
# Starts all three services (agent, tools, portal) and opens the UI.
# Press Ctrl+C to stop everything.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT = $PSScriptRoot

# ── Colours ───────────────────────────────────────────────────────────────────
function Write-Step  { param($msg) Write-Host "  $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "  $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "  $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "  Envoy" -ForegroundColor White
Write-Host "  ─────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
Write-Step "Checking prerequisites..."

# Python 3.12+
try {
    $pyver = python --version 2>&1
    if ($pyver -notmatch "Python 3\.(1[2-9]|[2-9]\d)") {
        Write-Fail "Python 3.12+ required (found: $pyver)"
        exit 1
    }
    Write-Ok "Python: $pyver"
} catch {
    Write-Fail "Python not found. Install from https://python.org"
    exit 1
}

# Node 20+
try {
    $nodever = node --version 2>&1
    $major = [int]($nodever -replace "v(\d+).*", '$1')
    if ($major -lt 20) {
        Write-Fail "Node 20+ required (found: $nodever)"
        exit 1
    }
    Write-Ok "Node: $nodever"
} catch {
    Write-Fail "Node not found. Install from https://nodejs.org"
    exit 1
}

# Chrome
$chromePaths = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)
$chromeFound = $false
foreach ($p in $chromePaths) {
    if (Test-Path $p) { $chromeFound = $true; Write-Ok "Chrome: $p"; break }
}
if (-not $chromeFound) {
    Write-Fail "Google Chrome not found. Install from https://google.com/chrome"
    exit 1
}

Write-Host ""

# ── 2. First-run setup ────────────────────────────────────────────────────────
Write-Step "Checking first-run setup..."

# Generate a random secret if needed
function New-Secret { return [System.Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(24)) }

# agent/.env
$agentEnv = Join-Path $ROOT "agent\.env"
if (-not (Test-Path $agentEnv)) {
    Write-Warn "agent/.env not found — creating from template..."
    $secret = New-Secret
    $model = Read-Host "  OpenAI-compatible model name (e.g. gpt-4o)"
    $baseUrl = Read-Host "  API base URL (e.g. https://api.openai.com/v1)"
    $apiKey = Read-Host "  API key"
    @"
INTERNAL_AUTH_SECRET=$secret
OPENAI_COMPAT_BASE_URL=$baseUrl
OPENAI_COMPAT_API_KEY=$apiKey
OPENAI_COMPAT_MODEL=$model
PROFILE_PATH=../profile/my_profile.json
"@ | Set-Content $agentEnv
    Write-Ok "Created agent/.env"
    $toolsEnv = Join-Path $ROOT "tools\.env"
    "INTERNAL_AUTH_SECRET=$secret" | Set-Content $toolsEnv
    Write-Ok "Created tools/.env"
} else {
    Write-Ok "agent/.env exists"
    # Sync secret to tools/.env if missing
    $toolsEnv = Join-Path $ROOT "tools\.env"
    if (-not (Test-Path $toolsEnv)) {
        $secret = (Get-Content $agentEnv | Select-String "INTERNAL_AUTH_SECRET=(.+)").Matches[0].Groups[1].Value
        "INTERNAL_AUTH_SECRET=$secret" | Set-Content $toolsEnv
        Write-Ok "Created tools/.env"
    } else {
        Write-Ok "tools/.env exists"
    }
}

# Profile JSON
$profileDir = Join-Path $ROOT "profile"
if (-not (Test-Path $profileDir)) { New-Item -ItemType Directory -Path $profileDir | Out-Null }
$profileDest = Join-Path $profileDir "my_profile.json"
$profileExample = Join-Path $profileDir "example_profile.json"
if (-not (Test-Path $profileDest)) {
    if (Test-Path $profileExample) {
        Copy-Item $profileExample $profileDest
        Write-Warn "Profile template created at profile/my_profile.json — edit it before running searches."
    } else {
        Write-Warn "No profile found. Create profile/my_profile.json (see README)."
    }
} else {
    Write-Ok "profile/my_profile.json exists"
}

Write-Host ""

# ── 3. Install dependencies ───────────────────────────────────────────────────
Write-Step "Installing dependencies (if needed)..."

# Agent Python venv
$venv = Join-Path $ROOT "agent\.venv"
if (-not (Test-Path $venv)) {
    Write-Step "Creating Python venv..."
    Push-Location (Join-Path $ROOT "agent")
    python -m venv .venv
    .\.venv\Scripts\pip install -e . --quiet
    Pop-Location
    Write-Ok "Agent venv ready"
} else {
    Write-Ok "Agent venv exists"
}

# Tools Node modules
$toolsModules = Join-Path $ROOT "tools\node_modules"
if (-not (Test-Path $toolsModules)) {
    Write-Step "Installing tools dependencies..."
    Push-Location (Join-Path $ROOT "tools")
    npm install --silent
    Pop-Location
    Write-Ok "Tools node_modules ready"
} else {
    Write-Ok "Tools node_modules exist"
}

# Portal Node modules
$portalModules = Join-Path $ROOT "portal\node_modules"
if (-not (Test-Path $portalModules)) {
    Write-Step "Installing portal dependencies..."
    Push-Location (Join-Path $ROOT "portal")
    npm install --silent
    Pop-Location
    Write-Ok "Portal node_modules ready"
} else {
    Write-Ok "Portal node_modules exist"
}

Write-Host ""

# ── 4. Create logs dir ────────────────────────────────────────────────────────
$logsDir = Join-Path $ROOT "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }

# ── 5. Start services ─────────────────────────────────────────────────────────
Write-Step "Starting services..."

$jobs = @()

# Agent (port 8000)
$agentLog = Join-Path $ROOT "logs\agent.log"
$agentProc = Start-Process -PassThru -NoNewWindow powershell -ArgumentList @(
    "-Command",
    "cd '$ROOT\agent'; .\.venv\Scripts\python -m uvicorn app.main:app --port 8000 2>&1 | Tee-Object -FilePath '$agentLog'"
)
$jobs += $agentProc
Write-Ok "Agent started (PID $($agentProc.Id)) — http://localhost:8000"

Start-Sleep -Milliseconds 1000

# Tools (port 4320)
$toolsLog = Join-Path $ROOT "logs\tools.log"
$toolsProc = Start-Process -PassThru -NoNewWindow powershell -ArgumentList @(
    "-Command",
    "cd '$ROOT\tools'; npm run dev 2>&1 | Tee-Object -FilePath '$toolsLog'"
)
$jobs += $toolsProc
Write-Ok "Tools started (PID $($toolsProc.Id)) — http://localhost:4320"

Start-Sleep -Milliseconds 500

# Portal (port 5200)
$portalLog = Join-Path $ROOT "logs\portal.log"
$portalProc = Start-Process -PassThru -NoNewWindow powershell -ArgumentList @(
    "-Command",
    "cd '$ROOT\portal'; npm run dev 2>&1 | Tee-Object -FilePath '$portalLog'"
)
$jobs += $portalProc
Write-Ok "Portal started (PID $($portalProc.Id)) — http://localhost:5200"

Write-Host ""
Write-Host "  All services running." -ForegroundColor White
Write-Host "  Opening http://localhost:5200 ..." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  First time? Go to Setup to log in to SEEK." -ForegroundColor Yellow
Write-Host "  Press Ctrl+C to stop all services." -ForegroundColor DarkGray
Write-Host ""

Start-Sleep -Seconds 2
Start-Process "http://localhost:5200/setup"

# ── 6. Wait and clean up ──────────────────────────────────────────────────────
try {
    while ($true) { Start-Sleep -Seconds 5 }
} finally {
    Write-Host ""
    Write-Host "  Stopping services..." -ForegroundColor Yellow
    foreach ($proc in $jobs) {
        if (-not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "  Done." -ForegroundColor Green
}
