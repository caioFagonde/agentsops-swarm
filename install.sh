#!/usr/bin/env bash
set -Eeuo pipefail

PREFIX="${AGENTOPS_PREFIX:-$HOME/.local}"
APP_DIR="$PREFIX/share/agentops-swarm"
BIN_DIR="$PREFIX/bin"
INSTALL_TOOLS="${AGENTOPS_INSTALL_TOOLS:-true}"
SETUP_LOCAL="${AGENTOPS_SETUP_LOCAL:-false}"

info(){ printf '\033[0;34m→\033[0m %s\n' "$*"; }
ok(){ printf '\033[0;32m✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m⚠\033[0m %s\n' "$*"; }
fail(){ printf '\033[0;31m✗\033[0m %s\n' "$*"; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$APP_DIR" "$BIN_DIR"

info "Installing AgentOps Swarm v4 to $APP_DIR"
rsync -a --delete \
  --exclude='.git' \
  --exclude='.agentops' \
  --exclude='.agent-worktrees' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  "$ROOT/" "$APP_DIR/"

# ── Launcher ──────────────────────────────────────────────────
cat > "$BIN_DIR/agentops" <<'LAUNCHER'
#!/usr/bin/env bash
set -Eeuo pipefail
CANDIDATES=(
  "${AGENTOPS_HOME:-}"
  "$HOME/.local/share/agentops-swarm"
  "$HOME/.local/share/agentops-swarm/agentops-swarm"
  "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/share/agentops-swarm"
)
AGENTOPS_ROOT=""
for candidate in "${CANDIDATES[@]}"; do
  if [[ -n "$candidate" && -f "$candidate/agentops_swarm/cli.py" ]]; then
    AGENTOPS_ROOT="$candidate"
    break
  fi
done
if [[ -z "$AGENTOPS_ROOT" ]]; then
  echo "agentops: could not find agentops_swarm/cli.py" >&2
  echo "Reinstall with: cd /path/to/agentops-swarm && ./install.sh" >&2
  exit 1
fi
export PYTHONPATH="$AGENTOPS_ROOT:${PYTHONPATH:-}"
exec "${AGENTOPS_PYTHON:-python3}" -m agentops_swarm.cli "$@"
LAUNCHER
chmod +x "$BIN_DIR/agentops"
ok "agentops launcher installed at $BIN_DIR/agentops"

# ── Helper: npm package installer ────────────────────────────
install_npm_pkg(){
  local exe="$1" pkg="$2" label="$3"
  if command -v "$exe" >/dev/null 2>&1; then
    ok "$label already installed: $($exe --version 2>/dev/null | head -1 || true)"
    return 0
  fi
  if [[ "$INSTALL_TOOLS" != "true" ]]; then
    warn "$label missing; tool installation disabled"
    return 0
  fi
  if ! command -v npm >/dev/null 2>&1; then
    warn "npm missing; cannot auto-install $label"
    return 0
  fi
  info "Installing $label via npm package $pkg"
  npm install -g "$pkg" || warn "$label install failed; install manually"
}

# ── Core CLI tools ────────────────────────────────────────────
install_npm_pkg claude "@anthropic-ai/claude-code" "Claude Code"
install_npm_pkg codex "@openai/codex" "OpenAI Codex CLI"

if command -v agy >/dev/null 2>&1; then
  ok "Antigravity CLI already installed"
elif [[ "$INSTALL_TOOLS" == "true" ]]; then
  warn "Antigravity CLI not found. Install manually and ensure 'agy' is on PATH."
fi

# ── Python version check ─────────────────────────────────────
PYTHON="${AGENTOPS_PYTHON:-python3}"
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
if [[ "$(echo "$PY_VER >= 3.10" | bc -l 2>/dev/null || echo 0)" == "1" ]]; then
  ok "Python $PY_VER detected"
else
  warn "Python 3.10+ required, found $PY_VER"
fi

# ── Ollama (local models) ────────────────────────────────────
if command -v ollama >/dev/null 2>&1; then
  ok "Ollama already installed: $(ollama --version 2>/dev/null | head -1 || echo 'unknown')"
  HAVE_OLLAMA=true
else
  HAVE_OLLAMA=false
  if [[ "$SETUP_LOCAL" == "true" ]]; then
    info "Installing Ollama..."
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL https://ollama.ai/install.sh | sh || {
        warn "Ollama auto-install failed. Install manually: https://ollama.ai"
      }
      command -v ollama >/dev/null 2>&1 && HAVE_OLLAMA=true
    else
      warn "curl missing; cannot auto-install Ollama"
    fi
  else
    info "Ollama not found. Run with AGENTOPS_SETUP_LOCAL=true to auto-install,"
    info "or install manually: https://ollama.ai"
  fi
fi

# Pull lightweight models if ollama available and setup requested
if [[ "$HAVE_OLLAMA" == "true" && "$SETUP_LOCAL" == "true" ]]; then
  info "Pulling lightweight Ollama models for local scouts/summarizers..."
  for model in qwen3:1.7b qwen3:4b; do
    if ollama list 2>/dev/null | grep -q "${model%%:*}.*${model##*:}"; then
      ok "$model already available"
    else
      info "Pulling $model (this may take a few minutes)..."
      ollama pull "$model" || warn "Failed to pull $model"
    fi
  done
  ok "Local models ready"
fi

# ── Antigravity / legacy provider check ───────────────────────
if command -v agy >/dev/null 2>&1 || command -v antigravity >/dev/null 2>&1; then
  ok "Antigravity CLI found"
  if [[ -z "${AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE:-}" ]]; then
    info "Set AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE for headless Antigravity runs."
  fi
else
  info "Antigravity CLI not found. Install agy/antigravity to enable that fallback route."
fi
if [[ -n "${GEMINI_API_KEY:-}" || -n "${GOOGLE_API_KEY:-}" ]]; then
  info "Legacy Gemini key found; Gemini is not used by default."
fi

# ── Summary ───────────────────────────────────────────────────
cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  AgentOps Swarm v4 installed.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Next steps:
  export PATH="$BIN_DIR:\$PATH"   # add to shell rc
  agentops doctor                 # verify everything
  cd /path/to/project
  agentops init --name my-project
  agentops tui

Optional setup:
  agentops setup-models           # interactive local model setup
  export AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE=...  # enable headless Antigravity

Model tiers:
  t0-local : Ollama qwen (free, scouts/summaries)
  t1-fast  : Ollama qwen3 8b+ (scouts/courses)
  t2-mid   : Sonnet/Antigravity/Codex (execution)
  t3-heavy : Opus (planning only)

Provider login is still interactive:
  claude doctor
  codex
EOF
