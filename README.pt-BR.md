# AgentOps Swarm v3 — Guia PT-BR

AgentOps Swarm é um orquestrador local para usar Claude Code, Codex/GPT e Antigravity com múltiplos agentes em paralelo, cada um em uma worktree isolada do Git.

## Instalação Linux

```bash
./install.sh
export PATH="$HOME/.local/bin:$PATH"
agentops doctor
```

## Instalação Windows PowerShell

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\install.ps1
agentops doctor
```

O instalador tenta instalar Claude Code e Codex via npm quando não estão presentes. O Antigravity é verificado pelo comando `agy`; se não existir, o `agentops doctor` avisa.

Você ainda precisa fazer login interativo nos provedores:

```bash
claude doctor
codex
agy
```

## Uso básico em qualquer projeto

```bash
cd /caminho/do/projeto
agentops init --name meu-projeto
agentops tui
```

## Gerar plano automaticamente com Opus

```bash
agentops plan --overview overview.md --tranches 4 --run --profile opus-planner --overwrite
agentops list
```

## Rodar scouts com Haiku

```bash
agentops scout --tranche 1 --profile haiku-scout
```

## Rodar tarefas com animações

```bash
agentops launch --tranche 1 --spawn --monitor --mode headless --permission workspace
```

## Fallback automático

Por padrão, fallback pede confirmação. Para permitir Codex automaticamente:

```bash
agentops launch --tranche 1 --spawn --fallback codex --fallback-on-any-failure --yes
```

## Merge com reparo automático

```bash
agentops collect
agentops merge --tranche 1 --auto-repair --repair-attempts 2
```

Antes de cada merge, o AgentOps cria uma referência de rollback:

```bash
agentops rollback --list
```

## Prompts

Colar manualmente:

```bash
agentops prompt minha-tarefa
# cole o prompt e finalize com EOF
```

Editar no editor:

```bash
agentops prompt minha-tarefa --edit
```

## Pasta de exemplos

Coloque imagens, HTML, markdown, sketches e dados fake em:

```txt
.agentops/examples/
```

Depois rode:

```bash
agentops examples-index
```

## Modelo mental

O fluxo ideal é:

```txt
planejar → scout → executar em worktrees → coletar relatórios → merge sequencial → reparar se necessário → rollback se necessário
```

Isso permite produtividade alta sem perder controle.
