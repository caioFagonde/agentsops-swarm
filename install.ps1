# AgentOps Swarm Windows installer
# Run in PowerShell: Set-ExecutionPolicy -Scope Process Bypass -Force; .\install.ps1
param(
  [switch]$SkipAgents,
  [string]$InstallBase = "$env:LOCALAPPDATA\AgentOpsSwarm",
  [string]$BinDir = "$env:USERPROFILE\.local\bin"
)
$ErrorActionPreference = "Stop"
function Info($m){ Write-Host "→ $m" -ForegroundColor Blue }
function Ok($m){ Write-Host "✓ $m" -ForegroundColor Green }
function Warn($m){ Write-Host "⚠ $m" -ForegroundColor Yellow }
function Have($cmd){ return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Info "Installing AgentOps Swarm to $InstallBase"
New-Item -ItemType Directory -Force -Path $InstallBase | Out-Null
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
robocopy $Root $InstallBase /MIR /XD .git __pycache__ | Out-Null
$Shim = Join-Path $BinDir "agentops.cmd"
@"
@echo off
python -m agentops_swarm.cli %*
"@ | Set-Content -Encoding ASCII $Shim
Ok "agentops shim installed at $Shim"

$env:PYTHONPATH = "$InstallBase;$env:PYTHONPATH"

function Install-WingetPackage($Id){
  if (-not (Have winget)) { Warn "winget not found; install $Id manually."; return }
  winget install --id $Id --silent --accept-package-agreements --accept-source-agreements
}

if (-not (Have git)) { Info "Installing Git"; Install-WingetPackage "Git.Git" }
if (-not (Have python)) { Info "Installing Python"; Install-WingetPackage "Python.Python.3.12" }
if (-not (Have node)) { Info "Installing Node.js"; Install-WingetPackage "OpenJS.NodeJS.LTS" }

if (-not $SkipAgents) {
  if (-not (Have claude)) {
    Info "Installing Claude Code"
    try { irm https://claude.ai/install.ps1 | iex } catch { Warn "Native Claude installer failed; trying npm fallback" }
    if (-not (Have claude)) { npm install -g @anthropic-ai/claude-code }
  } else { Ok "Claude Code already installed" }

  if (-not (Have codex)) {
    Info "Installing OpenAI Codex CLI"
    npm install -g @openai/codex
  } else { Ok "Codex already installed" }

  if (-not (Have agy) -and -not (Have antigravity)) {
    Info "Installing Google Antigravity CLI"
    irm https://antigravity.google/cli/install.ps1 | iex
  } else { Ok "Antigravity CLI already installed" }
}

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$BinDir*") {
  [Environment]::SetEnvironmentVariable("Path", "$BinDir;$userPath", "User")
  Warn "Added $BinDir to user PATH. Restart PowerShell."
}

Write-Host ""
Ok "AgentOps installed."
Write-Host "Next commands:"
Write-Host "  agentops doctor"
Write-Host "  claude doctor"
Write-Host "  codex"
Write-Host "  agy"
