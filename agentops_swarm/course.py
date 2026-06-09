"""Course generation system.

After each task or tranche completes, a lightweight model generates a
reveal.js course module explaining:
  - What was done and why
  - How things could be improved
  - Possible failure modes and debugging tips
  - The files touched and their relationships

Uses the t1-fast tier (local Ollama qwen3 8b+ by default, optional legacy APIs) to keep costs near zero.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .models import (
    invoke_with_fallback,
    select_provider,
    tier_for_role,
    estimate_tokens,
    FallbackResult,
)


# ---------------------------------------------------------------------------
# Course directory structure
# ---------------------------------------------------------------------------

COURSE_DIR_NAME = "course"


def course_root(agentops_dir: Path) -> Path:
    return agentops_dir / COURSE_DIR_NAME


def ensure_course_dirs(agentops_dir: Path) -> Path:
    root = course_root(agentops_dir)
    for d in ["slides", "guides", "checklists", "assets", "css", "js"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Course generation prompts
# ---------------------------------------------------------------------------

SLIDE_SYSTEM = """You are a technical course author. You create concise, well-structured
reveal.js slide modules that explain what a software agent task accomplished.

Rules:
- Use reveal.js markdown syntax: --- for horizontal slides, -- for vertical slides.
- Start each module with a <span class="kicker"> tag for the module label.
- Use <div class="grid2"> or <div class="grid3"> with <div class="card"> for multi-column layouts.
- Use <span class="pill"> for technology tags.
- Include code blocks with proper language hints.
- Keep each slide focused on ONE concept.
- Be specific about file paths, functions, and changes.
- Include a "What could go wrong" slide with concrete failure modes.
- Include a "How to improve" slide with actionable next steps.
- Do NOT include generic platitudes. Be technical and precise.
- Write in English unless the task content is in another language.
"""

GUIDE_SYSTEM = """You are a technical writer creating a detailed reference guide for a
software change. The guide complements a slide deck and provides:

1. A summary of what changed and why
2. File-by-file breakdown of modifications
3. Test coverage analysis
4. Debugging playbook for likely failure modes
5. Improvement opportunities
6. Related areas that might need attention

Write in Markdown. Be specific about paths, functions, error messages.
Do NOT pad with generic advice. Every sentence should be actionable.
"""


def _build_slide_prompt(task_data: dict[str, Any], report: str, diff_summary: str, context: str = "") -> str:
    task_id = task_data.get("id", "unknown")
    title = task_data.get("title", task_id)
    framework = task_data.get("framework", "generic")
    acceptance = task_data.get("acceptance", [])

    return f"""Create a reveal.js slide module for this completed agent task.

## Task info
- ID: {task_id}
- Title: {title}
- Framework: {framework}
- Acceptance criteria: {json.dumps(acceptance)}

## Agent report
```markdown
{report[:6000]}
```

## Diff summary
```
{diff_summary[:4000]}
```

{f"## Relevant context{chr(10)}{context[:3000]}" if context else ""}

Generate the slide content. Start with a kicker span, then the title, then 4-8 slides covering:
1. What was done (files changed, approach taken)
2. How it works (architecture/data flow if applicable)
3. What could go wrong (concrete failure modes, NOT generic)
4. How to verify (commands to run, what to look for)
5. How to improve (specific next steps)
"""


def _build_guide_prompt(task_data: dict[str, Any], report: str, diff_summary: str, context: str = "") -> str:
    task_id = task_data.get("id", "unknown")
    title = task_data.get("title", task_id)

    return f"""Write a technical reference guide for this completed task.

## Task: {title} ({task_id})

## Report
```markdown
{report[:8000]}
```

## Diff
```
{diff_summary[:6000]}
```

{f"## Context{chr(10)}{context[:3000]}" if context else ""}

Write a guide covering:
1. Summary (2-3 sentences)
2. Files changed (table or list with path and what changed)
3. How to verify the change works
4. Debugging playbook (if X fails, check Y)
5. Improvement opportunities
6. Related areas

Use markdown. Be specific.
"""


# ---------------------------------------------------------------------------
# Generate course content for a single task
# ---------------------------------------------------------------------------

def generate_task_course(
    task_data: dict[str, Any],
    report: str,
    diff_summary: str,
    context: str = "",
    agentops_dir: Path | None = None,
    prefer_local: bool = False,
    budget_remaining: float | None = None,
) -> dict[str, str]:
    """Generate slide and guide content for a completed task.
    
    Returns dict with 'slide' and 'guide' content strings.
    """
    task_id = task_data.get("id", "unknown")
    results: dict[str, str] = {}

    # Generate slide
    slide_prompt = _build_slide_prompt(task_data, report, diff_summary, context)
    slide_result = invoke_with_fallback(
        role="course",
        prompt=slide_prompt,
        system=SLIDE_SYSTEM,
        max_tokens=4096,
        budget_remaining=budget_remaining,
        prefer_local=prefer_local,
    )
    if slide_result.success:
        results["slide"] = slide_result.output
    else:
        results["slide"] = _fallback_slide(task_data, report, diff_summary)

    # Generate guide
    guide_prompt = _build_guide_prompt(task_data, report, diff_summary, context)
    guide_result = invoke_with_fallback(
        role="course",
        prompt=guide_prompt,
        system=GUIDE_SYSTEM,
        max_tokens=6000,
        budget_remaining=budget_remaining,
        prefer_local=prefer_local,
    )
    if guide_result.success:
        results["guide"] = guide_result.output
    else:
        results["guide"] = _fallback_guide(task_data, report, diff_summary)

    # Write to disk if agentops_dir provided
    if agentops_dir:
        _write_course_files(agentops_dir, task_id, results)

    return results


def _fallback_slide(task_data: dict[str, Any], report: str, diff_summary: str) -> str:
    """Generate a minimal slide when the model is unavailable."""
    task_id = task_data.get("id", "unknown")
    title = task_data.get("title", task_id)
    # Extract file list from diff
    files = []
    for line in diff_summary.splitlines():
        line = line.strip()
        if line and not line.startswith(("---", "+++", "@@", "diff", "index", "#")):
            parts = line.split("\t")
            if len(parts) >= 2:
                files.append(parts[-1])
            elif line and not line.startswith(("+", "-", " ")):
                files.append(line)

    file_list = "\n".join(f"- `{f}`" for f in files[:15]) or "- See report for details"

    # Extract report summary (first paragraph)
    report_lines = [l for l in report.splitlines() if l.strip() and not l.startswith("#")]
    summary = " ".join(report_lines[:3])[:300] if report_lines else "Task completed."

    return f"""<span class="kicker">Task: {task_id}</span>

# {title}

{summary}

---

## Files changed

{file_list}

---

## Verification

Run the task's defined checks to verify this change.

```bash
# Check the task report
cat .agentops/reports/{task_id}/report.md
```

---

## What could go wrong

- Review the diff carefully for unintended side effects.
- Run the full test suite, not just task-specific checks.
- Check for hardcoded values or missing error handling.
"""


def _fallback_guide(task_data: dict[str, Any], report: str, diff_summary: str) -> str:
    """Generate a minimal guide when the model is unavailable."""
    task_id = task_data.get("id", "unknown")
    title = task_data.get("title", task_id)

    return f"""# {title}

## Summary

Task `{task_id}` completed. See the agent report below for details.

## Agent report

{report[:4000]}

## Diff summary

```
{diff_summary[:3000]}
```

## Verification

Run the task's acceptance criteria checks to verify correctness.

## Debugging

If issues arise after merging this task:

1. Check the rollback ref: `agentops rollback --list`
2. Review the full diff: `git diff agentops/rollback/{task_id}-*..HEAD`
3. Check the task's log files in `.agentops/runtime/logs/`
"""


def _write_course_files(agentops_dir: Path, task_id: str, content: dict[str, str]) -> None:
    """Write generated course content to the course directory."""
    root = ensure_course_dirs(agentops_dir)

    # Determine slide number based on existing slides
    existing_slides = sorted(root.glob("slides/*.md"))
    next_num = len(existing_slides)

    # Write slide
    slide_path = root / "slides" / f"{next_num:02d}-{task_id}.md"
    slide_path.write_text(content.get("slide", ""), encoding="utf-8")

    # Write guide
    guide_path = root / "guides" / f"{next_num:02d}-{task_id}.md"
    guide_path.write_text(content.get("guide", ""), encoding="utf-8")

    # Update manifest
    _update_manifest(root, task_id, slide_path.name, guide_path.name)

    # Update index.html
    _regenerate_index(root)


def _update_manifest(course_dir: Path, task_id: str, slide_file: str, guide_file: str) -> None:
    """Update the course manifest.json."""
    manifest_path = course_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    else:
        manifest = {}

    manifest.setdefault("title", "AgentOps Task Course")
    manifest.setdefault("format", "reveal.js multi-file course")
    manifest.setdefault("version", "1.0")
    manifest.setdefault("generated_by", "agentops-swarm v4")
    manifest.setdefault("modules", [])
    manifest.setdefault("guides", [])

    if slide_file not in manifest["modules"]:
        manifest["modules"].append(slide_file)
    if guide_file not in manifest["guides"]:
        manifest["guides"].append(guide_file)

    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _regenerate_index(course_dir: Path) -> None:
    """Regenerate the reveal.js index.html from available slides."""
    slides = sorted(course_dir.glob("slides/*.md"))
    if not slides:
        return

    sections = []
    for slide in slides:
        rel = f"slides/{slide.name}"
        sections.append(
            f'      <section data-markdown="{rel}" data-separator="^---$" data-separator-vertical="^--$"></section>'
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>AgentOps Task Course</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reset.css">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.css">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/theme/black.css" id="theme">
  <link rel="stylesheet" href="css/course.css">
</head>
<body>
  <div class="reveal">
    <div class="slides">
{chr(10).join(sections)}
    </div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/reveal.js@5/plugin/markdown/markdown.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/reveal.js@5/plugin/notes/notes.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/reveal.js@5/plugin/highlight/highlight.js"></script>
  <script>
    Reveal.initialize({{
      hash: true,
      plugins: [RevealMarkdown, RevealHighlight, RevealNotes]
    }});
  </script>
</body>
</html>
"""
    (course_dir / "index.html").write_text(html, encoding="utf-8")

    # Ensure CSS exists
    css_path = course_dir / "css" / "course.css"
    if not css_path.exists():
        css_path.write_text(DEFAULT_COURSE_CSS, encoding="utf-8")


# ---------------------------------------------------------------------------
# Generate tranche summary course
# ---------------------------------------------------------------------------

def generate_tranche_course(
    tranche: str | int,
    tasks: list[dict[str, Any]],
    reports_dir: Path,
    agentops_dir: Path,
    prefer_local: bool = False,
    budget_remaining: float | None = None,
) -> dict[str, str]:
    """Generate a summary course module for an entire tranche."""
    summaries = []
    for task in tasks:
        task_id = task.get("id", "")
        report_path = reports_dir / task_id / "report.md"
        report = ""
        if report_path.exists():
            report = report_path.read_text(encoding="utf-8", errors="ignore")[:3000]
        summaries.append(f"### {task_id}: {task.get('title', '')}\n{report[:500]}\n")

    combined = "\n".join(summaries)

    prompt = f"""Create a reveal.js summary slide deck for tranche {tranche}.

This tranche contained {len(tasks)} tasks:

{combined}

Generate 3-5 slides covering:
1. Title slide with tranche overview
2. What was accomplished (bullet list of completed tasks)
3. Integration notes (how tasks relate to each other)
4. Known risks or gaps
5. Next steps
"""

    result = invoke_with_fallback(
        role="course",
        prompt=prompt,
        system=SLIDE_SYSTEM,
        max_tokens=4096,
        budget_remaining=budget_remaining,
        prefer_local=prefer_local,
    )

    content = {
        "slide": result.output if result.success else _fallback_tranche_slide(tranche, tasks),
    }

    # Write
    root = ensure_course_dirs(agentops_dir)
    slide_path = root / "slides" / f"tranche-{tranche}-summary.md"
    slide_path.write_text(content["slide"], encoding="utf-8")
    _regenerate_index(root)

    return content


def _fallback_tranche_slide(tranche: str | int, tasks: list[dict[str, Any]]) -> str:
    task_list = "\n".join(f"- `{t.get('id')}`: {t.get('title', '')}" for t in tasks)
    return f"""<span class="kicker">Tranche {tranche} Summary</span>

# Tranche {tranche} Complete

{len(tasks)} tasks completed.

---

## Tasks

{task_list}

---

## Next steps

Review reports, run integration tests, proceed to next tranche.
"""


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

DEFAULT_COURSE_CSS = """\
/* AgentOps Course Theme */
:root {
  --r-background-color: #0a0a12;
  --r-main-color: #e0e0e0;
  --r-heading-color: #ffffff;
  --r-link-color: #6ec1e4;
}

.kicker {
  display: inline-block;
  font-size: 0.55em;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: #6ec1e4;
  border: 1px solid #6ec1e4;
  padding: 0.15em 0.6em;
  border-radius: 3px;
  margin-bottom: 0.5em;
}

.pill {
  display: inline-block;
  font-size: 0.45em;
  background: rgba(110, 193, 228, 0.15);
  color: #6ec1e4;
  padding: 0.15em 0.5em;
  border-radius: 12px;
  margin: 0.1em;
}

.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1em; text-align: left; }
.grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1em; text-align: left; }

.card {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 1em;
}
.card h3 {
  font-size: 0.7em;
  color: #6ec1e4;
  margin-top: 0;
}
.card p, .card li { font-size: 0.55em; }

.diagram { max-width: 90%; margin: 0 auto; }

.reveal pre code {
  font-size: 0.65em;
  max-height: 400px;
}
"""
