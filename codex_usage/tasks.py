from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from codex_usage.rollouts import iter_usage_events
from codex_usage.pricing import calculate_cost


@dataclass
class TokenStats:
    calls: int = 0
    total: int = 0
    input: int = 0
    cached: int = 0
    uncached: int = 0
    output: int = 0
    reasoning: int = 0
    cost_usd: float = 0.0
    cost_rub: float = 0.0
    unpriced_tokens: int = 0

    def add_event(self, event) -> None:
        self.calls += 1
        self.total += event.total_tokens
        self.input += event.input_tokens
        self.cached += event.cached_input_tokens
        self.uncached += event.uncached_input_tokens
        self.output += event.output_tokens
        self.reasoning += event.reasoning_output_tokens

    def add_stats(self, other: "TokenStats") -> None:
        self.calls += other.calls
        self.total += other.total
        self.input += other.input
        self.cached += other.cached
        self.uncached += other.uncached
        self.output += other.output
        self.reasoning += other.reasoning
        self.cost_usd += other.cost_usd
        self.cost_rub += other.cost_rub
        self.unpriced_tokens += other.unpriced_tokens

    @property
    def cache_hit(self) -> float:
        if not self.input:
            return 0.0
        return self.cached / self.input


def build_task_rows(
    threads,
    edges,
    project_map: dict[str, str],
    normalize_path,
    usd_rub: float,
    timezone_name: str = "Europe/Moscow",
) -> list[dict]:

    timezone = ZoneInfo(timezone_name)

    thread_by_id = {
        thread["id"]: thread
        for thread in threads
    }

    parent_by_child = {}
    children_by_parent = defaultdict(list)

    for edge in edges:
        parent = edge["parent_thread_id"]
        child = edge["child_thread_id"]

        parent_by_child[child] = parent
        children_by_parent[parent].append(child)

    # -----------------------------
    # THREAD TOKEN STATS
    # -----------------------------

    thread_stats: dict[str, TokenStats] = {}
    thread_first_event = {}
    thread_last_event = {}

    for thread in threads:
        path = thread["rollout_path"]

        stats = TokenStats()

        if path and Path(path).expanduser().is_file():
            for event in iter_usage_events(path):
                stats.add_event(event)

                local_time = event.timestamp.astimezone(timezone)

                if thread["id"] not in thread_first_event:
                    thread_first_event[thread["id"]] = local_time

                thread_last_event[thread["id"]] = local_time
        cost = calculate_cost(
            model=thread["model"] or "unknown",
            input_tokens=stats.input,
            cached_tokens=stats.cached,
            output_tokens=stats.output,
            usd_rub=usd_rub,
        )

        stats.cost_usd = cost["cost_usd"]
        stats.cost_rub = cost["cost_rub"]

        if not cost["priced"]:
            stats.unpriced_tokens = stats.total

        thread_stats[thread["id"]] = stats

    # -----------------------------
    # DESCENDANTS
    # -----------------------------

    def descendants(root_id: str) -> set[str]:
        result = set()
        stack = list(children_by_parent.get(root_id, []))

        while stack:
            child_id = stack.pop()

            if child_id in result:
                continue

            result.add(child_id)

            stack.extend(
                children_by_parent.get(child_id, [])
            )

        return result

    # -----------------------------
    # ROOT TASKS
    # -----------------------------

    rows = []

    for root in threads:
        root_id = root["id"]

        # A ROOT task cannot itself be a child.
        if root_id in parent_by_child:
            continue

        normalized_cwd = normalize_path(root["cwd"])

        project = project_map.get(normalized_cwd)

        if not project:
            continue

        root_stats = thread_stats[root_id]
        child_ids = descendants(root_id)

        subagent_stats = TokenStats()
        review_stats = TokenStats()

        all_activity = []

        if root_id in thread_first_event:
            all_activity.append(
                thread_first_event[root_id]
            )

        if root_id in thread_last_event:
            all_activity.append(
                thread_last_event[root_id]
            )

        for child_id in child_ids:
            child = thread_by_id.get(child_id)

            if child is None:
                continue

            stats = thread_stats.get(
                child_id,
                TokenStats(),
            )

            if child["model"] == "codex-auto-review":
                review_stats.add_stats(stats)
            else:
                subagent_stats.add_stats(stats)

            if child_id in thread_first_event:
                all_activity.append(
                    thread_first_event[child_id]
                )

            if child_id in thread_last_event:
                all_activity.append(
                    thread_last_event[child_id]
                )

        # Reviews are not always represented in
        # thread_spawn_edges, so only reviews actually linked
        # into this root tree are included here.
        #
        # Unlinked reviews remain visible in Daily / Models.

        full_stats = TokenStats()
        full_stats.add_stats(root_stats)
        full_stats.add_stats(subagent_stats)
        full_stats.add_stats(review_stats)

        if all_activity:
            first_activity = min(all_activity)
            last_activity = max(all_activity)

            duration_hours = (
                last_activity - first_activity
            ).total_seconds() / 3600

            active_days = (
                last_activity.date()
                - first_activity.date()
            ).days + 1
        else:
            created = datetime.fromtimestamp(
                root["created_at"],
                tz=timezone,
            )

            updated = datetime.fromtimestamp(
                root["updated_at"],
                tz=timezone,
            )

            first_activity = created
            last_activity = updated

            duration_hours = (
                updated - created
            ).total_seconds() / 3600

            active_days = (
                updated.date() - created.date()
            ).days + 1

        title = (
            root["title"]
            or root["id"]
        ).replace("\n", " ").strip()

        rows.append({
            "root_thread_id": root_id,
            "project": project,
            "title": title,

            "root_model": root["model"] or "unknown",
            "root_effort": root["reasoning_effort"] or "unknown",

            "first_activity": first_activity,
            "last_activity": last_activity,
            "duration_hours": max(duration_hours, 0),
            "active_days": max(active_days, 1),

            "children": len(child_ids),

            "direct_calls": root_stats.calls,
            "subagent_calls": subagent_stats.calls,
            "review_calls": review_stats.calls,
            "full_calls": full_stats.calls,

            "direct_tokens": root_stats.total,
            "subagent_tokens": subagent_stats.total,
            "review_tokens": review_stats.total,
            "full_tokens": full_stats.total,

            "input_tokens": full_stats.input,
            "cached_tokens": full_stats.cached,
            "uncached_tokens": full_stats.uncached,
            "output_tokens": full_stats.output,
            "reasoning_tokens": full_stats.reasoning,

            "cache_hit": full_stats.cache_hit,
            "direct_cost_usd": root_stats.cost_usd,
            "subagent_cost_usd": subagent_stats.cost_usd,
            "review_cost_usd": review_stats.cost_usd,
            "full_cost_usd": full_stats.cost_usd,
            "direct_cost_rub": root_stats.cost_rub,
            "subagent_cost_rub": subagent_stats.cost_rub,
            "review_cost_rub": review_stats.cost_rub,
            "full_cost_rub": full_stats.cost_rub,
            "unpriced_tokens": full_stats.unpriced_tokens,
        })

    rows.sort(
        key=lambda row: row["full_tokens"],
        reverse=True,
    )

    return rows