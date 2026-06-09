$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Prefix = if ($env:AGENTOPS_PREFIX) { $env:AGENTOPS_PREFIX } else { Join-Path $env:USERPROFILE ".agentops-swarm" }
$AppDir = Join-Path $Prefix "share\agentops-swarm"
$BinDir = Join-Path $Prefix "bin"
$SetupLocal = $env:AGENTOPS_SETUP_LOCAL -eq "true"

New-Item -ItemType Directory -Force -Path $AppDir, $BinDir | Out-Null

Write-Host "→ Installing AgentOps Swarm v4 to $AppDir"
robocopy $Root $AppDir /MIR /XD .git .agentops .agent-worktrees __pycache__ /XF *.pyc | Out-Null

# ── Launcher ──────────────────────────────────────────────────
$Launcher = Join-Path $BinDir "agentops.cmd"
@"
@echo off
set AGENTOPS_ROOT=$AppDir
set PYTHONPATH=%AGENTOPS_ROOT%;%PYTHONPATH%
python -m agentops_swarm.cli %*
"@ | Set-Content -Path $Launcher -Encoding ASCII
Write-Host "✓ agentops installed at $Launcher"

# ── npm tool helper ──────────────────────────────────────────
function Install-NpmTool($Exe, $Pkg, $Label) {
  $cmd = Get-Command $Exe -ErrorAction SilentlyContinue
  if ($cmd) { Write-Host "✓ $Label already installed"; return }
  if ($env:AGENTOPS_INSTALL_TOOLS -eq "false") { Write-Warning "$Label missing; tool install disabled"; return }
  $npm = Get-Command npm -ErrorAction SilentlyContinue
  if (-not $npm) { Write-Warning "npm missing; cannot install $Label"; return }
  Write-Host "→ Installing $Label via npm package $Pkg"
  npm install -g $Pkg
}

Install-NpmTool "claude" "@anthropic-ai/claude-code" "Claude Code"
Install-NpmTool "codex" "@openai/codex" "OpenAI Codex CLI"
if (-not (Get-Command agy -ErrorAction SilentlyContinue)) {
  Write-Warning "Antigravity CLI 'agy' not found. Install manually."
}

# ── Python check ──────────────────────────────────────────────
try {
  $pyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
  if ([version]$pyVer -ge [version]"3.10") {
    Write-Host "✓ Python $pyVer detected"
  } else {
    Write-Warning "Python 3.10+ required, found $pyVer"
  }
} catch {
  Write-Warning "Python not found. Install Python 3.10+ and ensure it's on PATH."
}

# ── Ollama ────────────────────────────────────────────────────
$hasOllama = Get-Command ollama -ErrorAction SilentlyContinue
if ($hasOllama) {
  Write-Host "✓ Ollama already installed"
} elseif ($SetupLocal) {
  Write-Host "→ Download Ollama from https://ollama.ai/download/windows"
  Write-Host "  After installing, run: agentops setup-models"
} else {
  Write-Host "→ Ollama not found. Set AGENTOPS_SETUP_LOCAL=true or install from https://ollama.ai"
}

# Pull models if requested
if ($hasOllama -and $SetupLocal) {
  Write-Host "→ Pulling lightweight Ollama models..."
  foreach ($model in @("qwen3:1.7b", "qwen3:4b")) {
    Write-Host "→ Pulling $model..."
    ollama pull $model 2>$null
  }
  Write-Host "✓ Local models ready"
}

# ── Antigravity / legacy provider check ───────────────────────
if (Get-Command agy -ErrorAction SilentlyContinue) {
  Write-Host "✓ Antigravity CLI found"
  if (-not $env:AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE) { Write-Host "→ Set AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE for headless Antigravity runs." }
} elseif (Get-Command antigravity -ErrorAction SilentlyContinue) {
  Write-Host "✓ Antigravity CLI found"
  if (-not $env:AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE) { Write-Host "→ Set AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE for headless Antigravity runs." }
} else {
  Write-Host "→ Antigravity CLI not found. Install agy/antigravity to enable that fallback route."
}
if ($env:GEMINI_API_KEY -or $env:GOOGLE_API_KEY) { Write-Host "→ Legacy Gemini key found; Gemini is not used by default." }

# ── Summary ───────────────────────────────────────────────────
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "  AgentOps Swarm v4 installed."
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host ""
Write-Host "Add to PATH if needed: $BinDir"
Write-Host "Then run: agentops doctor"
Write-Host ""
Write-Host "Optional: agentops setup-models"
Write-Host "Optional: set AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE=... for headless Antigravity"
