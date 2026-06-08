# AgentOps Swarm — Guia de instalação e uso em PT-BR

O AgentOps Swarm é uma ferramenta local para coordenar vários agentes de programação com segurança e produtividade: Claude Code, Codex e Google Antigravity.

A ideia é simples: em vez de pedir para um agente mexer no projeto inteiro de uma vez, você cria uma maratona de trabalho dividida em tranches/sprints. Cada agente trabalha em uma branch/worktree isolada. Depois, você coleta relatórios, roda testes e faz merge controlado.

## O que ele faz

- Cria tarefas e tranches automaticamente a partir de um prompt geral.
- Usa Opus como planejador.
- Usa Haiku como scout/leitor rápido.
- Usa Sonnet como executor e reparador.
- Usa Codex como verificador ou executor focado.
- Usa Antigravity como executor adicional.
- Cria worktrees do git para evitar bagunçar sua branch principal.
- Roda testes depois de cada merge.
- Se um teste falhar, cria um nó de reparo automático.
- Mostra status via TUI com Rich.
- Registra eventos em JSONL.
- Mede tempo/budget por tarefa.
- Permite uma pasta de exemplos com imagens, HTML, Markdown, fluxos e sketches.

## Instalação no Linux/macOS

Abra um terminal na pasta do AgentOps:

```bash
./install.sh
```

O instalador vai:

1. Copiar o AgentOps para `~/.local/share/agentops-swarm`.
2. Criar o comando `agentops` em `~/.local/bin`.
3. Tentar instalar Claude Code, Codex e Antigravity CLI.
4. Avisar se faltar Python, Git, Node ou npm.

Se `~/.local/bin` não estiver no PATH:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

## Instalação no Windows

Abra PowerShell na pasta do AgentOps:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\install.ps1
```

Depois reinicie o PowerShell.

## Login nos agentes

O instalador pode instalar os programas, mas o login precisa ser feito de forma interativa:

```bash
claude doctor
codex
agy
```

No Windows, rode os mesmos comandos no PowerShell depois de reiniciar.

## Primeiro uso em um projeto

Entre na pasta do projeto:

```bash
cd /caminho/do/projeto
agentops init --name meu-projeto
agentops doctor
```

Crie um prompt geral:

```bash
cat > overview.md <<'EOF'
Quero melhorar este projeto com segurança. Corrigir testes, melhorar UX, organizar arquitetura, adicionar documentação e preservar limites de segurança. Divida o trabalho em tranches pequenas, com testes e critérios de aceite.
EOF
```

Peça para o planejador criar a DAG:

```bash
agentops plan --overview overview.md --tranches 4 --run --profile opus-planner
```

Veja as tarefas:

```bash
agentops list
agentops status
```

Rode um scout rápido com Haiku:

```bash
agentops scout --tranche 1 --profile haiku-scout
```

Rode os workers:

```bash
agentops launch --tranche 1 --spawn --monitor --mode headless --permission workspace
```

Se as janelas não abrirem, use tmux:

```bash
agentops launch --tranche 1 --spawn --terminal tmux --monitor --mode headless --permission workspace
tmux attach -t agentops
```

Depois que terminarem:

```bash
agentops collect
agentops merge --tranche 1 --auto-repair --repair-attempts 2
```

## TUI visual

Instale Rich:

```bash
pip install rich
```

Abra:

```bash
agentops tui
```

Comandos:

```text
r / Enter  atualizar
c          coletar relatórios
b          mostrar budget/tempo
q          sair
```

## Pasta de exemplos

Use esta pasta para colocar prints, layouts, HTML, Markdown e specs:

```text
.agentops/examples/images/
.agentops/examples/html/
.agentops/examples/markdown/
.agentops/examples/sketches/
.agentops/examples/flows/
.agentops/examples/data/
.agentops/examples/ui/
```

Gere índice:

```bash
agentops examples-index
```

Depois, inclua nos prompts: “Leia `.agentops/examples/GENERATED_INDEX.md` e use os exemplos relevantes como referência.”

## Usando no personal-os

Para continuar o Personal OS:

```bash
cd ~/Documentos/github/personal-os-scaffold/personal-os
agentops init --name personal-os --force
```

Coloque as imagens de referência em:

```text
.agentops/examples/images/
```

Gere índice:

```bash
agentops examples-index
```

Crie tarefas de UI:

```bash
agentops add-task nexus-big-picture-ui \
  --title "Refazer UI com experiência Big Picture" \
  --tranche 4 \
  --priority p0 \
  --executor claude \
  --profile sonnet-executor \
  --framework vue-quasar \
  --allowed-path apps/web/src \
  --allowed-path tests \
  --allowed-path docs \
  --acceptance "Command Center parece um cockpit visual" \
  --acceptance "Carrosséis e cards flutuantes funcionam" \
  --acceptance "Sem cards ilegíveis" \
  --acceptance "pnpm build passa" \
  --check "pnpm --dir apps/web build"
```

Rode:

```bash
agentops launch --tranche 4 --spawn --monitor --mode headless --permission workspace
agentops merge --tranche 4 --auto-repair --repair-attempts 2
```

## Publicando em um repositório privado no GitHub

```bash
git init
git add .
git commit -m "Initial AgentOps Swarm"
gh repo create agentops-swarm --private --source=. --push
```

Ou crie o repositório privado pelo site do GitHub e faça push manual.

## Regra de ouro

Os agentes fazem branches. Quem faz o push final é você.

Sempre rode:

```bash
git diff --stat
agentops collect
agentops merge --tranche N --auto-repair
```

E revise antes de enviar para o remoto.
