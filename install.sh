#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_BASE="${AGENTOPS_INSTALL_BASE:-$HOME/.local/share/agentops-swarm}"
BIN_DIR="${AGENTOPS_BIN_DIR:-$HOME/.local/bin}"
INSTALL_AGENTS="${AGENTOPS_INSTALL_AGENTS:-true}"

info(){ printf '\033[0;34m→\033[0m %s\n' "$*"; }
ok(){ printf '\033[0;32m✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m⚠\033[0m %s\n' "$*"; }
fail(){ printf '\033[0;31m✗\033[0m %s\n' "$*"; exit 1; }
have(){ command -v "$1" >/dev/null 2>&1; }

info "Installing AgentOps Swarm to $INSTALL_BASE"
mkdir -p "$INSTALL_BASE" "$BIN_DIR"
rsync -a --delete \
  --exclude='.git' \
  "$ROOT/" "$INSTALL_BASE/"
ln -sf "$INSTALL_BASE/bin/agentops" "$BIN_DIR/agentops"
chmod +x "$INSTALL_BASE/bin/agentops"
ok "agentops installed at $BIN_DIR/agentops"

if ! have python3; then warn "python3 not found. Install Python 3.10+ before using AgentOps."; fi
if ! have git; then warn "git not found. Install Git before using worktrees."; fi

install_node_linux(){
  if have node && have npm; then return 0; fi
  warn "Node/npm not found. Attempting OS package install."
  if have apt-get; then
    sudo apt-get update
    sudo apt-get install -y nodejs npm
  elif have dnf; then
    sudo dnf install -y nodejs npm
  elif have yum; then
    sudo yum install -y nodejs npm
  elif have pacman; then
    sudo pacman -Sy --noconfirm nodejs npm
  else
    warn "Unsupported package manager. Install Node.js 18+ manually."
  fi
}

install_claude(){
  if have claude; then ok "Claude Code already installed: $(claude --version 2>/dev/null | head -1 || echo claude)"; return 0; fi
  info "Installing Claude Code"
  if [[ "${AGENTOPS_NATIVE_CLAUDE_INSTALL:-true}" == "true" ]]; then
    curl -fsSL https://claude.ai/install.sh | bash || warn "Native Claude installer failed; trying npm fallback"
  fi
  if ! have claude; then
    install_node_linux
    npm install -g @anthropic-ai/claude-code
  fi
  have claude && ok "Claude Code installed" || warn "Claude Code not found after install"
}

install_codex(){
  if have codex; then ok "Codex already installed: $(codex --version 2>/dev/null | head -1 || echo codex)"; return 0; fi
  info "Installing OpenAI Codex CLI"
  install_node_linux
  npm install -g @openai/codex
  have codex && ok "Codex installed" || warn "Codex not found after install"
}

install_antigravity(){
  if have agy || have antigravity; then ok "Antigravity CLI already installed"; return 0; fi
  info "Installing Google Antigravity CLI"
  curl -fsSL https://antigravity.google/cli/install.sh | bash || warn "Antigravity CLI installer failed"
  export PATH="$HOME/.local/bin:$PATH"
  have agy || have antigravity && ok "Antigravity CLI installed" || warn "Antigravity CLI not found after install. Ensure ~/.local/bin is on PATH."
}

if [[ "$INSTALL_AGENTS" == "true" ]]; then
  install_claude
  install_codex
  install_antigravity
fi

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  warn "$BIN_DIR is not in PATH. Add this to your shell profile:"
  echo "export PATH=\"$BIN_DIR:\$PATH\""
fi

cat <<EOF

AgentOps installed.

Next:
  agentops doctor
  cd /path/to/project
  agentops init --name my-project
  agentops tui

Authentication still requires interactive login:
  claude doctor
  codex
  agy
EOF
