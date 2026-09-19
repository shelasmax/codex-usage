from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from codex_usage.rollouts import UsageEvent


@dataclass
class UsageBucket:
    calls: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    uncached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def add(self, event: UsageEvent) -> None:
        self.calls += 1
        self.total_tokens += event.total_tokens
        self.input_tokens += event.input_tokens
        self.cached_input_tokens += event.cached_input_tokens
        self.uncached_input_tokens += event.uncached_input_tokens
        self.output_tokens += event.output_tokens
        self.reasoning_tokens += event.reasoning_output_tokens

    @property
    def cache_hit(self) -> float:
        if not self.input_tokens:
            return 0.0

        return self.cached_input_tokens / self.input_tokens


def classify_role(
    thread_id: str,
    model: str | None,
    child_ids: set[str],
) -> str:
    # REVIEW has priority because not every review thread
    # participates in thread_spawn_edges.
    if model == "codex-auto-review":
        return "REVIEW"

    if thread_id in child_ids:
        return "SUBAGENT"

    return "ROOT"


def aggregate_usage(
    threads,
    edges,
    timezone_name: str = "Europe/Moscow",
):
    timezone = ZoneInfo(timezone_name)

    child_ids = {
        edge["child_thread_id"]
        for edge in edges
    }

    buckets = defaultdict(UsageBucket)

    for thread, event in threads:
        local_date: date = event.timestamp.astimezone(
            timezone
        ).date()

        role = classify_role(
            thread_id=thread["id"],
            model=thread["model"],
            child_ids=child_ids,
        )

        key = (
            local_date.isoformat(),
            thread["cwd"],
            role,
            thread["model"] or "unknown",
            thread["reasoning_effort"] or "unknown",
        )

        buckets[key].add(event)

    return buckets