#!/usr/bin/env bash
# Envoy — Mac/Linux startup script
# Run from the repo root: ./start.sh
# Starts all three services (agent, tools, portal) and opens the UI.
# Press Ctrl+C to stop everything.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS=()

# ── Colours ───────────────────────────────────────────────────────────────────
step()  { echo -e "  \033[36m$*\033[0m"; }
ok()    { echo -e "  \033[32m✓ $*\033[0m"; }
warn()  { echo -e "  \033[33m⚠ $*\033[0m"; }
fail()  { echo -e "  \033[31m✗ $*\033[0m"; exit 1; }

echo ""
echo -e "  \033[1mEnvoy\033[0m"
echo -e "  \033[90m─────────────────────────────────────────\033[0m"
echo ""

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
step "Checking prerequisites..."

# Python 3.12+
if ! command -v python3 &>/dev/null; then
  fail "Python 3 not found. Install from https://python.org"
fi
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYMAJOR=$(echo "$PYVER" | cut -d. -f1)
PYMINOR=$(echo "$PYVER" | cut -d. -f2)
if [[ $PYMAJOR -lt 3 || ($PYMAJOR -eq 3 && $PYMINOR -lt 12) ]]; then
  fail "Python 3.12+ required (found: $PYVER)"
fi
ok "Python: $PYVER"

# Node 20+
if ! command -v node &>/dev/null; then
  fail "Node not found. Install from https://nodejs.org"
fi
NODEVER=$(node --version | sed 's/v//')
NODEMAJOR=$(echo "$NODEVER" | cut -d. -f1)
if [[ $NODEMAJOR -lt 20 ]]; then
  fail "Node 20+ required (found: v$NODEVER)"
fi
ok "Node: v$NODEVER"

# Chrome
CHROME=""
for candidate in \
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  "/Applications/Chromium.app/Contents/MacOS/Chromium" \
  "/usr/bin/google-chrome" \
  "/usr/bin/google-chrome-stable" \
  "/usr/bin/chromium-browser" \
  "/usr/bin/chromium"
do
  if [[ -x "$candidate" ]]; then CHROME="$candidate"; break; fi
done
if [[ -z "$CHROME" ]]; then
  fail "Google Chrome not found. Install from https://google.com/chrome"
fi
ok "Chrome: $CHROME"

echo ""

# ── 2. First-run setup ────────────────────────────────────────────────────────
step "Checking first-run setup..."

# Generate random secret
new_secret() { python3 -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(24)).decode())"; }

AGENT_ENV="$ROOT/agent/.env"
TOOLS_ENV="$ROOT/tools/.env"

if [[ ! -f "$AGENT_ENV" ]]; then
  warn "agent/.env not found — creating from template..."
  SECRET=$(new_secret)
  read -rp "  OpenAI-compatible model name (e.g. gpt-4o): " MODEL
  read -rp "  API base URL (e.g. https://api.openai.com/v1): " BASE_URL
  read -rsp "  API key: " API_KEY; echo ""
  cat > "$AGENT_ENV" <<EOF
INTERNAL_AUTH_SECRET=$SECRET
OPENAI_COMPAT_BASE_URL=$BASE_URL
OPENAI_COMPAT_API_KEY=$API_KEY
OPENAI_COMPAT_MODEL=$MODEL
PROFILE_PATH=../profile/my_profile.json
EOF
  echo "INTERNAL_AUTH_SECRET=$SECRET" > "$TOOLS_ENV"
  ok "Created agent/.env and tools/.env"
else
  ok "agent/.env exists"
  if [[ ! -f "$TOOLS_ENV" ]]; then
    SECRET=$(grep INTERNAL_AUTH_SECRET "$AGENT_ENV" | cut -d= -f2-)
    echo "INTERNAL_AUTH_SECRET=$SECRET" > "$TOOLS_ENV"
    ok "Created tools/.env"
  else
    ok "tools/.env exists"
  fi
fi

# Profile JSON
PROFILE_DIR="$ROOT/profile"
mkdir -p "$PROFILE_DIR"
PROFILE_DEST="$PROFILE_DIR/my_profile.json"
PROFILE_EXAMPLE="$PROFILE_DIR/example_profile.json"
if [[ ! -f "$PROFILE_DEST" ]]; then
  if [[ -f "$PROFILE_EXAMPLE" ]]; then
    cp "$PROFILE_EXAMPLE" "$PROFILE_DEST"
    warn "Profile template created at profile/my_profile.json — edit it before running searches."
  else
    warn "No profile found. Create profile/my_profile.json (see README)."
  fi
else
  ok "profile/my_profile.json exists"
fi

echo ""

# ── 3. Install dependencies ───────────────────────────────────────────────────
step "Installing dependencies (if needed)..."

# Python venv
VENV="$ROOT/agent/.venv"
if [[ ! -d "$VENV" ]]; then
  step "Creating Python venv..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -e "$ROOT/agent" --quiet
  ok "Agent venv ready"
else
  ok "Agent venv exists"
fi

# Tools
if [[ ! -d "$ROOT/tools/node_modules" ]]; then
  step "Installing tools dependencies..."
  (cd "$ROOT/tools" && npm install --silent)
  ok "Tools node_modules ready"
else
  ok "Tools node_modules exist"
fi

# Portal
if [[ ! -d "$ROOT/portal/node_modules" ]]; then
  step "Installing portal dependencies..."
  (cd "$ROOT/portal" && npm install --silent)
  ok "Portal node_modules ready"
else
  ok "Portal node_modules exist"
fi

echo ""

# ── 4. Create logs dir ────────────────────────────────────────────────────────
mkdir -p "$ROOT/logs"

# ── 5. Cleanup on exit ────────────────────────────────────────────────────────
cleanup() {
  echo ""
  echo -e "  \033[33mStopping services...\033[0m"
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo -e "  \033[32mDone.\033[0m"
}
trap cleanup SIGINT SIGTERM EXIT

# ── 6. Start services ─────────────────────────────────────────────────────────
step "Starting services..."

# Agent (port 8000)
(cd "$ROOT/agent" && "$VENV/bin/python" -m uvicorn app.main:app --port 8000 >> "$ROOT/logs/agent.log" 2>&1) &
PIDS+=($!)
ok "Agent started (PID ${PIDS[-1]}) — http://localhost:8000"

sleep 1

# Tools (port 4320)
(cd "$ROOT/tools" && npm run dev >> "$ROOT/logs/tools.log" 2>&1) &
PIDS+=($!)
ok "Tools started (PID ${PIDS[-1]}) — http://localhost:4320"

sleep 0.5

# Portal (port 5200)
(cd "$ROOT/portal" && npm run dev >> "$ROOT/logs/portal.log" 2>&1) &
PIDS+=($!)
ok "Portal started (PID ${PIDS[-1]}) — http://localhost:5200"

echo ""
echo -e "  \033[1mAll services running.\033[0m"
echo -e "  \033[90mOpening http://localhost:5200 ...\033[0m"
echo ""
echo -e "  \033[33mFirst time? Go to Setup to log in to SEEK.\033[0m"
echo -e "  \033[90mLogs: $ROOT/logs/\033[0m"
echo -e "  \033[90mPress Ctrl+C to stop.\033[0m"
echo ""

sleep 2

# Open browser
if command -v xdg-open &>/dev/null; then
  xdg-open "http://localhost:5200/setup" &>/dev/null &
elif command -v open &>/dev/null; then
  open "http://localhost:5200/setup"
fi

# Wait forever (cleanup fires on Ctrl+C via trap)
wait
