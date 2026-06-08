$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Prefix = if ($env:AGENTOPS_PREFIX) { $env:AGENTOPS_PREFIX } else { Join-Path $env:USERPROFILE ".agentops-swarm" }
$AppDir = Join-Path $Prefix "share\agentops-swarm"
$BinDir = Join-Path $Prefix "bin"
New-Item -ItemType Directory -Force -Path $AppDir, $BinDir | Out-Null
Write-Host "→ Installing AgentOps Swarm v3 to $AppDir"
robocopy $Root $AppDir /MIR /XD .git .agentops .agent-worktrees | Out-Null
$Launcher = Join-Path $BinDir "agentops.cmd"
@"
@echo off
set AGENTOPS_ROOT=$AppDir
set PYTHONPATH=%AGENTOPS_ROOT%;%PYTHONPATH%
python -m agentops_swarm.cli %*
"@ | Set-Content -Path $Launcher -Encoding ASCII
Write-Host "✓ agentops installed at $Launcher"

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
  Write-Warning "Antigravity CLI 'agy' not found. Install manually or set AGENTOPS_ANTIGRAVITY_INSTALL_URL and rerun."
}
Write-Host ""
Write-Host "Add to PATH if needed: $BinDir"
Write-Host "Then run: agentops doctor"
