"""Model tier routing, provider abstraction, and budget-aware fallback.

Tiers:
  t0-local   – Ollama qwen3 (1.7b/4b). Free. File ops, grep, status, trivial extraction.
  t1-fast    – local Ollama qwen3 (8b/14b) with optional legacy Gemini. Scout, summarize, course gen.
  t2-mid     – Claude Sonnet / Codex. Execution, repair, complex patches.
  t3-heavy   – Claude Opus. Planning, architecture, complex reasoning ONLY.

Design rule: heavy models do cognitive planning. Cheap models do everything else.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

@dataclass
class ModelProvider:
    """A concrete model endpoint."""
    name: str               # human label
    engine: str             # ollama | antigravity | claude | codex | gemini
    model: str              # e.g. "qwen3:4b", "sonnet", "opus", "gemini-2.5-flash"
    max_context: int        # tokens
    cost_per_1k_input: float  # USD per 1k input tokens (0 for local)
    cost_per_1k_output: float
    available: bool = True  # set at runtime after probe

    @property
    def is_local(self) -> bool:
        return self.engine == "ollama"

    @property
    def is_free(self) -> bool:
        return self.cost_per_1k_input == 0.0

    def estimated_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens / 1000) * self.cost_per_1k_input + (output_tokens / 1000) * self.cost_per_1k_output


@dataclass
class ModelTier:
    """A logical tier grouping providers by cost/capability."""
    name: str
    providers: list[ModelProvider]
    use_for: list[str]         # task categories this tier handles
    priority: int              # 0 = cheapest, 3 = most expensive


# Canonical provider definitions.
# Users can override via config; these are sensible defaults.
PROVIDERS: dict[str, ModelProvider] = {
    # Tier 0 – local/free
    "ollama-qwen3-1.7b": ModelProvider("Qwen3 1.7B", "ollama", "qwen3:1.7b", 4096, 0.0, 0.0),
    "ollama-qwen3-4b":   ModelProvider("Qwen3 4B",   "ollama", "qwen3:4b",   8192, 0.0, 0.0),
    # Tier 1 – cheap/fast
    "ollama-qwen3-8b":   ModelProvider("Qwen3 8B",   "ollama", "qwen3:8b",  32768, 0.0, 0.0),
    "ollama-qwen3-14b":  ModelProvider("Qwen3 14B",  "ollama", "qwen3:14b", 32768, 0.0, 0.0),
    "ollama-qwen3-32b":  ModelProvider("Qwen3 32B",  "ollama", "qwen3:32b", 32768, 0.0, 0.0),
    # Optional legacy API provider. Kept for compatibility; not selected by default.
    "gemini-flash":      ModelProvider("Gemini 2.5 Flash", "gemini", "gemini-2.5-flash", 1_000_000, 0.00015, 0.0006),
    # Tier 2 – agentic coding CLIs
    "antigravity":       ModelProvider("Google Antigravity", "antigravity", "", 128_000, 0.0, 0.0),
    "claude-sonnet":     ModelProvider("Claude Sonnet", "claude", "sonnet", 200_000, 0.003, 0.015),
    "claude-haiku":      ModelProvider("Claude Haiku",  "claude", "haiku",  200_000, 0.0008, 0.004),
    "codex":             ModelProvider("OpenAI Codex",  "codex",  "",       128_000, 0.003, 0.012),
    # Tier 3 – heavy (planning ONLY)
    "claude-opus":       ModelProvider("Claude Opus",   "claude", "opus",   200_000, 0.015, 0.075),
}

TIERS: dict[str, ModelTier] = {
    "t0-local": ModelTier(
        "t0-local",
        [PROVIDERS["ollama-qwen3-1.7b"], PROVIDERS["ollama-qwen3-4b"]],
        ["file_listing", "grep", "status_check", "simple_extraction", "diff_summary"],
        0,
    ),
    "t1-fast": ModelTier(
        "t1-fast",
        [PROVIDERS["ollama-qwen3-8b"], PROVIDERS["ollama-qwen3-14b"], PROVIDERS["ollama-qwen3-32b"], PROVIDERS["gemini-flash"]],
        ["scout", "summarize", "simple_patch", "course_generation", "report", "context_compress"],
        1,
    ),
    "t2-mid": ModelTier(
        "t2-mid",
        [PROVIDERS["claude-sonnet"], PROVIDERS["claude-haiku"], PROVIDERS["antigravity"], PROVIDERS["codex"]],
        ["execution", "repair", "complex_patch", "test_writing", "refactor"],
        2,
    ),
    "t3-heavy": ModelTier(
        "t3-heavy",
        [PROVIDERS["claude-opus"]],
        ["planning", "architecture", "complex_reasoning", "dag_generation"],
        3,
    ),
}

# ---------------------------------------------------------------------------
# Task → tier routing
# ---------------------------------------------------------------------------

# Map agentops roles to tiers
ROLE_TIER_MAP: dict[str, str] = {
    "planner":    "t3-heavy",
    "scout":      "t1-fast",
    "executor":   "t2-mid",
    "repair":     "t2-mid",
    "verifier":   "t1-fast",
    "course":     "t1-fast",
    "summarizer": "t0-local",
    "grep":       "t0-local",
}


def tier_for_role(role: str) -> str:
    """Return the tier name for a given role."""
    return ROLE_TIER_MAP.get(role, "t2-mid")


def select_provider(tier_name: str, budget_remaining: float | None = None, prefer_local: bool = False) -> ModelProvider:
    """Pick the best available provider from a tier.
    
    Strategy:
    - If prefer_local or budget is tight, try local providers first.
    - Otherwise pick the first available provider (ordered by quality).
    - If nothing in the tier works, cascade DOWN to cheaper tiers.
    """
    tier = TIERS.get(tier_name)
    if not tier:
        tier = TIERS["t2-mid"]  # safe fallback

    candidates = [p for p in tier.providers if p.available]

    if prefer_local:
        local = [p for p in candidates if p.is_local]
        if local:
            return local[0]

    if budget_remaining is not None and budget_remaining < 0.50:
        # Budget is tight – prefer free providers
        free = [p for p in candidates if p.is_free]
        if free:
            return free[0]

    if candidates:
        return candidates[0]

    # Cascade DOWN: try cheaper tiers
    for fallback_tier_name in sorted(TIERS.keys(), key=lambda t: TIERS[t].priority):
        if TIERS[fallback_tier_name].priority < tier.priority:
            fb_candidates = [p for p in TIERS[fallback_tier_name].providers if p.available]
            if fb_candidates:
                return fb_candidates[0]

    # Last resort: return the first provider defined, even if marked unavailable
    return list(PROVIDERS.values())[0]


# ---------------------------------------------------------------------------
# Provider availability probes
# ---------------------------------------------------------------------------

_probe_cache: dict[str, bool] = {}


def probe_ollama() -> bool:
    """Check if ollama is running and responsive."""
    if "ollama" in _probe_cache:
        return _probe_cache["ollama"]
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
        result = r.returncode == 0
    except Exception:
        result = False
    _probe_cache["ollama"] = result
    return result


def probe_ollama_model(model: str) -> bool:
    """Check if a specific ollama model is pulled."""
    cache_key = f"ollama:{model}"
    if cache_key in _probe_cache:
        return _probe_cache[cache_key]
    if not probe_ollama():
        _probe_cache[cache_key] = False
        return False
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
        # Model names in ollama list output: "qwen3:4b    ..."
        base = model.split(":")[0] if ":" in model else model
        result = base in r.stdout
    except Exception:
        result = False
    _probe_cache[cache_key] = result
    return result


def probe_claude() -> bool:
    if "claude" in _probe_cache:
        return _probe_cache["claude"]
    result = shutil.which("claude") is not None
    _probe_cache["claude"] = result
    return result


def probe_codex() -> bool:
    if "codex" in _probe_cache:
        return _probe_cache["codex"]
    result = shutil.which("codex") is not None
    _probe_cache["codex"] = result
    return result


def probe_antigravity() -> bool:
    """Check if a Google Antigravity-compatible CLI is installed.

    The harness supports either `agy` or `antigravity` on PATH. Headless
    execution can be wired with AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE.
    """
    if "antigravity" in _probe_cache:
        return _probe_cache["antigravity"]
    result = shutil.which("agy") is not None or shutil.which("antigravity") is not None
    _probe_cache["antigravity"] = result
    return result


def probe_gemini() -> bool:
    """Legacy Gemini API availability; no longer a default route."""
    if "gemini" in _probe_cache:
        return _probe_cache["gemini"]
    result = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    _probe_cache["gemini"] = result
    return result


def probe_all() -> dict[str, bool]:
    """Probe all providers and update their availability flags."""
    results: dict[str, bool] = {}

    ollama_up = probe_ollama()
    for key, prov in PROVIDERS.items():
        if prov.engine == "ollama":
            if ollama_up:
                prov.available = probe_ollama_model(prov.model)
            else:
                prov.available = False
            results[key] = prov.available
        elif prov.engine == "claude":
            prov.available = probe_claude()
            results[key] = prov.available
        elif prov.engine == "codex":
            prov.available = probe_codex()
            results[key] = prov.available
        elif prov.engine == "antigravity":
            prov.available = probe_antigravity()
            results[key] = prov.available
        elif prov.engine == "gemini":
            prov.available = probe_gemini()
            results[key] = prov.available
        else:
            prov.available = False
            results[key] = False

    return results


# ---------------------------------------------------------------------------
# Direct model invocation (for scout, course gen, summarization)
# ---------------------------------------------------------------------------

def invoke_ollama(model: str, prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    """Call ollama generate endpoint directly."""
    import urllib.request
    base = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if system:
        payload["system"] = system
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"{base}/api/generate", data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
            return result.get("response", "")
    except Exception as exc:
        return f"[ollama error: {exc}]"


def invoke_gemini(prompt: str, system: str = "", max_tokens: int = 8192) -> str:
    """Call Gemini API directly. Requires GEMINI_API_KEY or GOOGLE_API_KEY."""
    import urllib.request
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return "[gemini error: no API key set]"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": f"[System instruction]\n{system}"}]})
        contents.append({"role": "model", "parts": [{"text": "Understood. I'll follow those instructions."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    payload = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode())
            candidates = result.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                return "".join(p.get("text", "") for p in parts)
            return "[gemini: no candidates returned]"
    except Exception as exc:
        return f"[gemini error: {exc}]"


def invoke_model(provider: ModelProvider, prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    """Universal model invocation for non-CLI models (ollama, gemini).
    
    For claude/codex, use the CLI-based worker_command path instead.
    """
    if provider.engine == "ollama":
        return invoke_ollama(provider.model, prompt, system, max_tokens)
    elif provider.engine == "gemini":
        return invoke_gemini(prompt, system, max_tokens)
    else:
        # Claude/codex are invoked via CLI, not this function
        return f"[engine {provider.engine} should use CLI invocation]"


# ---------------------------------------------------------------------------
# Ollama model management
# ---------------------------------------------------------------------------

def pull_ollama_model(model: str, quiet: bool = False) -> bool:
    """Pull an ollama model. Returns True on success."""
    if not probe_ollama():
        return False
    try:
        cmd = ["ollama", "pull", model]
        if quiet:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            return r.returncode == 0
        else:
            r = subprocess.run(cmd, timeout=600)
            return r.returncode == 0
    except Exception:
        return False


def ensure_ollama_models(models: list[str] | None = None, interactive: bool = True) -> dict[str, bool]:
    """Ensure required ollama models are pulled. Optionally prompt user."""
    if models is None:
        models = ["qwen3:4b", "qwen3:8b"]  # sensible defaults

    if not probe_ollama():
        return {m: False for m in models}

    results = {}
    for model in models:
        if probe_ollama_model(model):
            results[model] = True
            continue
        if interactive and sys.stdin.isatty():
            ans = input(f"Ollama model '{model}' not found. Pull it now? [Y/n] ").strip().lower()
            if ans in ("", "y", "yes", "s", "sim"):
                results[model] = pull_ollama_model(model)
            else:
                results[model] = False
        else:
            results[model] = False

    return results


# ---------------------------------------------------------------------------
# Smart fallback cascade
# ---------------------------------------------------------------------------

@dataclass
class FallbackResult:
    """Result of a fallback cascade attempt."""
    provider_used: ModelProvider
    output: str
    success: bool
    attempts: list[tuple[str, str]]  # (provider_name, error_or_empty)


def invoke_with_fallback(
    role: str,
    prompt: str,
    system: str = "",
    max_tokens: int = 4096,
    budget_remaining: float | None = None,
    prefer_local: bool = False,
) -> FallbackResult:
    """Try the best provider for a role, cascading down on failure.
    
    This is for direct invocation tasks (scout, summarize, course gen).
    NOT for CLI-based execution (that uses worker_command).
    """
    tier_name = tier_for_role(role)
    attempts: list[tuple[str, str]] = []

    # Build ordered candidate list: preferred tier first, then cheaper tiers
    ordered_tiers = sorted(TIERS.values(), key=lambda t: abs(t.priority - TIERS[tier_name].priority))
    candidates = []
    for tier in ordered_tiers:
        for prov in tier.providers:
            if prov.available and prov not in candidates:
                candidates.append(prov)

    if prefer_local:
        candidates.sort(key=lambda p: (0 if p.is_local else 1, 0 if p.is_free else 1))

    if budget_remaining is not None and budget_remaining < 0.50:
        candidates.sort(key=lambda p: (0 if p.is_free else 1, p.cost_per_1k_input))

    for provider in candidates:
        if provider.engine in ("claude", "codex"):
            # These need CLI invocation, skip for direct invoke
            continue
        try:
            output = invoke_model(provider, prompt, system, max_tokens)
            if output and not output.startswith("[") and len(output) > 20:
                return FallbackResult(provider, output, True, attempts)
            attempts.append((provider.name, f"empty or error output: {output[:100]}"))
        except Exception as exc:
            attempts.append((provider.name, str(exc)))

    return FallbackResult(
        candidates[0] if candidates else list(PROVIDERS.values())[0],
        "",
        False,
        attempts,
    )


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token count. ~4 chars per token for English, ~3 for code."""
    if not text:
        return 0
    # Simple heuristic: count words and multiply
    words = len(text.split())
    chars = len(text)
    # Use the more conservative estimate
    return max(words * 1.3, chars / 3.5).__ceil__()


def estimate_cost(provider: ModelProvider, input_text: str, expected_output_tokens: int = 2000) -> float:
    """Estimate cost of a model call."""
    input_tokens = estimate_tokens(input_text)
    return provider.estimated_cost(input_tokens, expected_output_tokens)


# ---------------------------------------------------------------------------
# Profile mapping (backward compat with v3 profiles)
# ---------------------------------------------------------------------------

V3_PROFILE_TO_PROVIDER: dict[str, str] = {
    "opus-planner":        "claude-opus",
    "haiku-scout":         "claude-haiku",
    "sonnet-executor":     "claude-sonnet",
    "sonnet-repair":       "claude-sonnet",
    "codex-verifier":      "codex",
    "gpt-codex":           "codex",
    "antigravity-executor": "antigravity",
    # New v4 profiles
    "local-scout":         "ollama-qwen3-4b",
    "flash-scout":         "ollama-qwen3-8b",
    "flash-course":        "ollama-qwen3-8b",
    "local-grep":          "ollama-qwen3-1.7b",
    "local-summarizer":    "ollama-qwen3-4b",
}

DEFAULT_PROFILES_V4: dict[str, dict[str, str]] = {
    "opus-planner":        {"engine": "claude", "model": "opus",   "role": "planner",    "tier": "t3-heavy"},
    "sonnet-executor":     {"engine": "claude", "model": "sonnet", "role": "executor",   "tier": "t2-mid"},
    "sonnet-repair":       {"engine": "claude", "model": "sonnet", "role": "repair",     "tier": "t2-mid"},
    "haiku-scout":         {"engine": "claude", "model": "haiku",  "role": "scout",      "tier": "t1-fast"},
    "antigravity-executor": {"engine": "antigravity", "model": "", "role": "executor", "tier": "t2-mid"},
    "flash-scout":         {"engine": "ollama", "model": "qwen3:8b", "role": "scout", "tier": "t1-fast"},
    "local-scout":         {"engine": "ollama", "model": "qwen3:8b",  "role": "scout",   "tier": "t1-fast"},
    "flash-course":        {"engine": "ollama", "model": "qwen3:8b", "role": "course", "tier": "t1-fast"},
    "local-summarizer":    {"engine": "ollama", "model": "qwen3:4b",  "role": "summarizer", "tier": "t0-local"},
    "local-grep":          {"engine": "ollama", "model": "qwen3:1.7b","role": "grep",     "tier": "t0-local"},
    "codex-verifier":      {"engine": "codex",  "model": "",       "role": "verifier",   "tier": "t2-mid"},
    "gpt-codex":           {"engine": "codex",  "model": "",       "role": "executor",   "tier": "t2-mid"},
}


def resolve_profile(name: str, profiles: dict[str, dict[str, str]] | None = None) -> dict[str, str]:
    """Resolve a profile name to its config dict."""
    all_profiles = {**DEFAULT_PROFILES_V4}
    if profiles:
        all_profiles.update(profiles)
    return all_profiles.get(name, DEFAULT_PROFILES_V4.get("sonnet-executor", {}))
