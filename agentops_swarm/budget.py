"""Token budget tracking and cost management.

Tracks estimated token usage and costs across all model tiers.
Automatically triggers tier downgrades when budget is running low.
Provides clear reporting of where tokens went.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UsageRecord:
    """Single model invocation record."""
    timestamp: str
    task_id: str
    provider: str          # e.g. "ollama-qwen3-4b", "claude-sonnet"
    tier: str              # e.g. "t0-local", "t2-mid"
    role: str              # e.g. "scout", "executor", "course"
    input_tokens: int
    output_tokens: int
    estimated_cost: float  # USD
    duration_seconds: float
    success: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "task_id": self.task_id,
            "provider": self.provider,
            "tier": self.tier,
            "role": self.role,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost": self.estimated_cost,
            "duration_seconds": self.duration_seconds,
            "success": self.success,
        }


@dataclass
class BudgetState:
    """Current budget state with limits and usage history."""
    daily_limit_usd: float = 5.0        # conservative default
    session_limit_usd: float = 2.0       # per-session limit
    warn_threshold_pct: float = 0.7      # warn at 70% of limit
    downgrade_threshold_pct: float = 0.85 # auto-downgrade at 85%
    total_spent_usd: float = 0.0
    session_spent_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    records: list[UsageRecord] = field(default_factory=list)
    session_start: str = ""

    def remaining_session(self) -> float:
        return max(0.0, self.session_limit_usd - self.session_spent_usd)

    def remaining_daily(self) -> float:
        return max(0.0, self.daily_limit_usd - self.total_spent_usd)

    def remaining(self) -> float:
        return min(self.remaining_session(), self.remaining_daily())

    def should_warn(self) -> bool:
        return self.session_spent_usd >= self.session_limit_usd * self.warn_threshold_pct

    def should_downgrade(self) -> bool:
        return self.session_spent_usd >= self.session_limit_usd * self.downgrade_threshold_pct

    def is_exhausted(self) -> bool:
        return self.session_spent_usd >= self.session_limit_usd or self.total_spent_usd >= self.daily_limit_usd

    def record(self, usage: UsageRecord) -> None:
        self.records.append(usage)
        self.total_spent_usd += usage.estimated_cost
        self.session_spent_usd += usage.estimated_cost
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens

    def cost_by_tier(self) -> dict[str, float]:
        costs: dict[str, float] = {}
        for r in self.records:
            costs[r.tier] = costs.get(r.tier, 0.0) + r.estimated_cost
        return costs

    def cost_by_role(self) -> dict[str, float]:
        costs: dict[str, float] = {}
        for r in self.records:
            costs[r.role] = costs.get(r.role, 0.0) + r.estimated_cost
        return costs

    def cost_by_task(self) -> dict[str, float]:
        costs: dict[str, float] = {}
        for r in self.records:
            costs[r.task_id] = costs.get(r.task_id, 0.0) + r.estimated_cost
        return costs

    def tokens_by_tier(self) -> dict[str, int]:
        tokens: dict[str, int] = {}
        for r in self.records:
            tokens[r.tier] = tokens.get(r.tier, 0) + r.input_tokens + r.output_tokens
        return tokens

    def summary(self) -> str:
        """Human-readable budget summary."""
        lines = [
            f"Budget: ${self.session_spent_usd:.4f} / ${self.session_limit_usd:.2f} session"
            f" | ${self.total_spent_usd:.4f} / ${self.daily_limit_usd:.2f} daily",
            f"Tokens: {self.total_input_tokens:,} in + {self.total_output_tokens:,} out",
        ]

        by_tier = self.cost_by_tier()
        if by_tier:
            tier_parts = [f"  {k}: ${v:.4f}" for k, v in sorted(by_tier.items())]
            lines.append("By tier:\n" + "\n".join(tier_parts))

        by_role = self.cost_by_role()
        if by_role:
            role_parts = [f"  {k}: ${v:.4f}" for k, v in sorted(by_role.items())]
            lines.append("By role:\n" + "\n".join(role_parts))

        if self.should_warn():
            lines.append("⚠ Budget warning: approaching session limit")
        if self.should_downgrade():
            lines.append("⚠ Auto-downgrade active: using cheaper models")
        if self.is_exhausted():
            lines.append("✗ Budget exhausted: only local models available")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_budget(path: Path) -> BudgetState:
    """Load budget state from disk."""
    if not path.exists():
        state = BudgetState()
        state.session_start = _now_iso()
        return state

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        state = BudgetState(
            daily_limit_usd=data.get("daily_limit_usd", 5.0),
            session_limit_usd=data.get("session_limit_usd", 2.0),
            warn_threshold_pct=data.get("warn_threshold_pct", 0.7),
            downgrade_threshold_pct=data.get("downgrade_threshold_pct", 0.85),
            total_spent_usd=data.get("total_spent_usd", 0.0),
            session_spent_usd=data.get("session_spent_usd", 0.0),
            total_input_tokens=data.get("total_input_tokens", 0),
            total_output_tokens=data.get("total_output_tokens", 0),
            session_start=data.get("session_start", _now_iso()),
        )

        # Reset session if stale (>6 hours)
        if state.session_start:
            try:
                session_ts = time.mktime(time.strptime(state.session_start, "%Y-%m-%dT%H:%M:%SZ"))
                if time.time() - session_ts > 6 * 3600:
                    state.session_spent_usd = 0.0
                    state.session_start = _now_iso()
            except Exception:
                pass

        # Reset daily if date changed
        today = time.strftime("%Y-%m-%d")
        last_date = data.get("last_date", today)
        if last_date != today:
            state.total_spent_usd = 0.0

        for rec in data.get("records", []):
            try:
                state.records.append(UsageRecord(**rec))
            except Exception:
                pass

        return state
    except Exception:
        state = BudgetState()
        state.session_start = _now_iso()
        return state


def save_budget(state: BudgetState, path: Path) -> None:
    """Save budget state to disk."""
    # Only keep last 500 records to prevent file bloat
    recent_records = state.records[-500:]

    data = {
        "daily_limit_usd": state.daily_limit_usd,
        "session_limit_usd": state.session_limit_usd,
        "warn_threshold_pct": state.warn_threshold_pct,
        "downgrade_threshold_pct": state.downgrade_threshold_pct,
        "total_spent_usd": state.total_spent_usd,
        "session_spent_usd": state.session_spent_usd,
        "total_input_tokens": state.total_input_tokens,
        "total_output_tokens": state.total_output_tokens,
        "session_start": state.session_start,
        "last_date": time.strftime("%Y-%m-%d"),
        "records": [r.to_dict() for r in recent_records],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Usage tracking helpers
# ---------------------------------------------------------------------------

def track_usage(
    budget: BudgetState,
    budget_path: Path,
    task_id: str,
    provider_name: str,
    tier: str,
    role: str,
    input_text: str,
    output_text: str,
    duration: float,
    success: bool,
) -> UsageRecord:
    """Record a model invocation and save to disk."""
    from .models import estimate_tokens, PROVIDERS

    input_tokens = estimate_tokens(input_text)
    output_tokens = estimate_tokens(output_text)

    prov = PROVIDERS.get(provider_name)
    if prov:
        cost = prov.estimated_cost(input_tokens, output_tokens)
    else:
        cost = 0.0

    record = UsageRecord(
        timestamp=_now_iso(),
        task_id=task_id,
        provider=provider_name,
        tier=tier,
        role=role,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost=cost,
        duration_seconds=duration,
        success=success,
    )

    budget.record(record)
    save_budget(budget, budget_path)
    return record


def format_budget_report(state: BudgetState) -> str:
    """Generate a detailed budget report."""
    lines = [
        "# AgentOps Budget Report",
        "",
        f"Session started: {state.session_start}",
        f"Session spent: ${state.session_spent_usd:.4f} / ${state.session_limit_usd:.2f}",
        f"Daily spent: ${state.total_spent_usd:.4f} / ${state.daily_limit_usd:.2f}",
        f"Total tokens: {state.total_input_tokens:,} in + {state.total_output_tokens:,} out",
        "",
    ]

    # By tier
    by_tier = state.cost_by_tier()
    if by_tier:
        lines.append("## Cost by tier")
        for tier, cost in sorted(by_tier.items()):
            tokens = state.tokens_by_tier().get(tier, 0)
            lines.append(f"  {tier}: ${cost:.4f} ({tokens:,} tokens)")
        lines.append("")

    # By role
    by_role = state.cost_by_role()
    if by_role:
        lines.append("## Cost by role")
        for role, cost in sorted(by_role.items()):
            lines.append(f"  {role}: ${cost:.4f}")
        lines.append("")

    # By task (top 10)
    by_task = state.cost_by_task()
    if by_task:
        lines.append("## Cost by task (top 10)")
        sorted_tasks = sorted(by_task.items(), key=lambda x: x[1], reverse=True)[:10]
        for task_id, cost in sorted_tasks:
            lines.append(f"  {task_id}: ${cost:.4f}")
        lines.append("")

    # Efficiency metrics
    if state.total_output_tokens > 0 and state.total_spent_usd > 0:
        efficiency = state.total_output_tokens / state.total_spent_usd
        lines.append(f"## Efficiency: {efficiency:.0f} output tokens per dollar")

    # Warnings
    if state.should_warn():
        lines.append("")
        lines.append("⚠ Approaching session budget limit.")
    if state.is_exhausted():
        lines.append("✗ Budget exhausted. Only local/free models available.")

    return "\n".join(lines)
