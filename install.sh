#!/usr/bin/env bash
set -Eeuo pipefail

PREFIX="${AGENTOPS_PREFIX:-$HOME/.local}"
APP_DIR="$PREFIX/share/agentops-swarm"
BIN_DIR="$PREFIX/bin"
INSTALL_TOOLS="${AGENTOPS_INSTALL_TOOLS:-true}"

info(){ printf '\033[0;34m→\033[0m %s\n' "$*"; }
ok(){ printf '\033[0;32m✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m⚠\033[0m %s\n' "$*"; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$APP_DIR" "$BIN_DIR"

info "Installing AgentOps Swarm v3 to $APP_DIR"
rsync -a --delete \
  --exclude='.git' \
  --exclude='.agentops' \
  --exclude='.agent-worktrees' \
  "$ROOT/" "$APP_DIR/"

cat > "$BIN_DIR/agentops" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
CANDIDATES=(
  "${AGENTOPS_HOME:-}"
  "$HOME/.local/share/agentops-swarm"
  "$HOME/.local/share/agentops-swarm/agentops-swarm"
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
EOF
chmod +x "$BIN_DIR/agentops"
ok "agentops installed at $BIN_DIR/agentops"

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

install_npm_pkg claude "@anthropic-ai/claude-code" "Claude Code"
install_npm_pkg codex "@openai/codex" "OpenAI Codex CLI"

if command -v agy >/dev/null 2>&1; then
  ok "Antigravity CLI already installed"
elif [[ "$INSTALL_TOOLS" == "true" ]]; then
  warn "Antigravity CLI not found. Attempting best-effort installer."
  URL="${AGENTOPS_ANTIGRAVITY_INSTALL_URL:-https://raw.githubusercontent.com/google-antigravity/antigravity-cli/main/install.sh}"
  if command -v curl >/dev/null 2>&1; then
    bash -lc "curl -fsSL '$URL' | bash" || warn "Antigravity auto-install failed; install manually and ensure 'agy' is on PATH"
  else
    warn "curl missing; cannot auto-install Antigravity"
  fi
fi

cat <<EOF

AgentOps installed.

Next:
  export PATH="$BIN_DIR:\$PATH"   # add to shell rc if needed
  agentops doctor
  cd /path/to/project
  agentops init --name my-project
  agentops tui

Authentication still requires provider login:
  claude doctor
  codex
  agy
EOF
