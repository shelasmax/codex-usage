from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from codex_usage.db import (
    connect_readonly,
    get_spawn_edges,
    get_threads,
)
from codex_usage.pricing import calculate_cost
from codex_usage.rollouts import iter_usage_events


CONFIG_PATH = Path("config.yaml")


@dataclass
class Bucket:
    calls: int = 0
    total: int = 0
    input: int = 0
    cached: int = 0
    output: int = 0
    reasoning: int = 0
    cost_usd: float = 0.0
    cost_rub: float = 0.0
    unpriced: int = 0

    @property
    def uncached(self) -> int:
        return max(
            self.input - self.cached,
            0,
        )

    @property
    def cache_hit(self) -> float:
        if not self.input:
            return 0.0

        return self.cached / self.input


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(
            "config.yaml not found"
        )

    return yaml.safe_load(
        CONFIG_PATH.read_text(
            encoding="utf-8"
        )
    )

def classify_threads(
    threads: list[dict],
    edges: list[dict],
) -> dict[str, str]:

    child_ids = {
        edge["child_thread_id"]
        for edge in edges
    }

    roles = {}

    for thread in threads:
        thread_id = thread["id"]

        if thread_id in child_ids:
            roles[thread_id] = "SUBAGENT"
        else:
            roles[thread_id] = "ROOT"

    return roles


def project_name(
    cwd: str,
    aliases: dict[str, str],
) -> str:

    expanded = str(
        Path(cwd).expanduser()
    )

    if expanded in aliases:
        return aliases[expanded]

    name = Path(expanded).name

    return name or expanded


def add_event(
    bucket: Bucket,
    *,
    event,
    model: str,
    usd_rub: float,
) -> None:

    bucket.calls += 1
    bucket.total += event.total_tokens
    bucket.input += event.input_tokens
    bucket.cached += event.cached_input_tokens
    bucket.output += event.output_tokens
    bucket.reasoning += event.reasoning_output_tokens

    cost = calculate_cost(
        model=model,
        input_tokens=event.input_tokens,
        cached_tokens=event.cached_input_tokens,
        output_tokens=event.output_tokens,
        usd_rub=usd_rub,
    )

    if cost["priced"]:
        bucket.cost_usd += cost["cost_usd"]
        bucket.cost_rub += cost["cost_rub"]
    else:
        bucket.unpriced += event.total_tokens


def fmt_tokens(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"{value / 1_000:.1f}K"

    return str(value)


def print_bucket(
    label: str,
    bucket: Bucket,
) -> None:

    print(
        f"{label:32} "
        f"calls={bucket.calls:6,}  "
        f"tokens={fmt_tokens(bucket.total):>9}  "
        f"uncached={fmt_tokens(bucket.uncached):>8}  "
        f"output={fmt_tokens(bucket.output):>8}  "
        f"reason={fmt_tokens(bucket.reasoning):>8}  "
        f"cache={bucket.cache_hit:6.1%}  "
        f"rub={bucket.cost_rub:10,.0f}"
    )

def calculate_active_time(
    timestamps: list[datetime],
    gap: timedelta,
) -> tuple[
    timedelta,
    int,
    timedelta,
]:

    if not timestamps:
        return (
            timedelta(0),
            0,
            timedelta(0),
        )

    ordered = sorted(timestamps)

    sessions = 1
    active = timedelta(0)

    session_start = ordered[0]
    previous = ordered[0]

    for current in ordered[1:]:
        if current - previous > gap:
            active += (
                previous
                - session_start
            )

            sessions += 1
            session_start = current

        previous = current

    active += (
        previous
        - session_start
    )

    wall_time = (
        ordered[-1]
        - ordered[0]
    )

    return (
        active,
        sessions,
        wall_time,
    )

def parse_local_datetime(
    value: str,
    timezone: ZoneInfo,
) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid datetime: {value}. "
            "Use YYYY-MM-DDTHH:MM[:SS]."
        ) from exc

    if parsed.tzinfo is None:
        return parsed.replace(
            tzinfo=timezone
        )

    return parsed.astimezone(timezone)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two Codex usage periods, including "
            "tokens, cost, activity intensity, agent "
            "structure, task concentration, and fan-out."
        )
    )

    parser.add_argument(
        "--a-start",
        required=True,
        help=(
            "Start of period A. "
            "Example: 2026-01-01T09:00:00"
        ),
    )

    parser.add_argument(
        "--a-end",
        required=True,
        help=(
            "End of period A. "
            "Example: 2026-01-01T09:00:00"
        ),
    )

    parser.add_argument(
        "--b-start",
        required=True,
        help=(
            "Start of period B. "
            "Example: 2026-01-01T09:00:00"
        ),
    )

    parser.add_argument(
        "--b-end",
        required=True,
        help=(
            "End of period B. "
            "Example: 2026-01-01T09:00:00"
        ),
    )

    parser.add_argument(
        "--a-label",
        default="Period A",
        help="Human-readable label for period A.",
    )

    parser.add_argument(
        "--b-label",
        default="Period B",
        help="Human-readable label for period B.",
    )

    parser.add_argument(
        "--active-gap",
        type=int,
        default=30,
        help=(
            "Minutes without token events that start "
            "a new active session. Default: 30."
        ),
    )

    return parser.parse_args()

def main() -> None:
    args = parse_args()

    config = load_config()

    timezone_name = config.get(
        "timezone",
        "UTC",
    )

    tz = ZoneInfo(timezone_name)

    usd_rub = float(
        config["pricing"]["usd_rub"]
    )

    database = Path(
        config["codex"]["database"]
    ).expanduser()

    aliases = {
        str(Path(path).expanduser()): name
        for path, name in (
            config.get("project_aliases")
            or {}
        ).items()
    }

    # -------------------------------------------------
    # PERIODS
    # -------------------------------------------------
    #
    # Period A:
    # Previous exhausted allowance cycle.
    #
    # Period B:
    # Next exhausted weekly allowance cycle.
    #
    # Boundary is 00:00 UTC / 03:00 Europe/Moscow.

    period_a = f"A — {args.a_label}"
    period_b = f"B — {args.b_label}"

    a_start = parse_local_datetime(
        args.a_start,
        tz,
    )

    a_end = parse_local_datetime(
        args.a_end,
        tz,
    )

    b_start = parse_local_datetime(
        args.b_start,
        tz,
    )

    b_end = parse_local_datetime(
        args.b_end,
        tz,
    )

    if a_end <= a_start:
        raise ValueError(
            "Period A end must be after start."
        )

    if b_end <= b_start:
        raise ValueError(
            "Period B end must be after start."
        )

    periods = {
        period_a: (
            a_start,
            a_end,
        ),
        period_b: (
            b_start,
            b_end,
        ),
    }

    connection = connect_readonly(
        database
    )

    try:
        threads = get_threads(
            connection
        )

        edges = get_spawn_edges(
            connection
        )

    finally:
        connection.close()

    roles = classify_threads(
        threads,
        edges,
    )

    totals = {
        period: Bucket()
        for period in periods
    }

    by_role = defaultdict(Bucket)
    by_model = defaultdict(Bucket)
    by_project = defaultdict(Bucket)

    matched_events = {
        period: 0
        for period in periods
    }
    # Event timestamps are used to estimate active working time.
    #
    # A gap greater than 30 minutes starts a new activity session.
    period_timestamps = {
        period: []
        for period in periods
    }

    if args.active_gap <= 0:
        raise ValueError(
            "--active-gap must be greater than 0."
        )

    ACTIVE_GAP = timedelta(
        minutes=args.active_gap
    )

    # Period-scoped stats for every thread.
    period_thread_stats = defaultdict(Bucket)

    for thread in threads:
        rollout_path = thread[
            "rollout_path"
        ]

        if not rollout_path:
            continue

        thread_id = thread["id"]

        role = roles.get(
            thread_id,
            "ROOT",
        )

        model = (
            thread["model"]
            or "unknown"
        )

        effort = (
            thread["reasoning_effort"]
            or "unknown"
        )

        cwd = (
            thread["cwd"]
            or "unknown"
        )

        project = project_name(
            cwd,
            aliases,
        )

        for event in iter_usage_events(
            rollout_path
        ):
            timestamp = getattr(
                event,
                "timestamp",
                None,
            )

            if not timestamp:
                continue

            event_time = timestamp.astimezone(tz)

            for period, (
                start,
                end,
            ) in periods.items():

                if not (
                    start
                    <= event_time
                    < end
                ):
                    continue

                matched_events[
                    period
                ] += 1

                period_timestamps[
                    period
                ].append(
                    event_time
                )

                add_event(
                    totals[period],
                    event=event,
                    model=model,
                    usd_rub=usd_rub,
                )

                add_event(
                    period_thread_stats[
                        (
                            period,
                            thread_id,
                        )
                    ],
                    event=event,
                    model=model,
                    usd_rub=usd_rub,
                )

                add_event(
                    by_role[
                        (
                            period,
                            role,
                        )
                    ],
                    event=event,
                    model=model,
                    usd_rub=usd_rub,
                )

                add_event(
                    by_model[
                        (
                            period,
                            role,
                            model,
                            effort,
                        )
                    ],
                    event=event,
                    model=model,
                    usd_rub=usd_rub,
                )

                add_event(
                    by_project[
                        (
                            period,
                            project,
                        )
                    ],
                    event=event,
                    model=model,
                    usd_rub=usd_rub,
                )

                break

    # -------------------------------------------------
    # PERIOD-SCOPED TASK TREES
    # -------------------------------------------------

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
        children_by_parent[parent].append(
            child
        )

    def descendants(
        root_id: str,
    ) -> set[str]:

        result = set()

        stack = list(
            children_by_parent.get(
                root_id,
                [],
            )
        )

        while stack:
            child_id = stack.pop()

            if child_id in result:
                continue

            result.add(child_id)

            stack.extend(
                children_by_parent.get(
                    child_id,
                    [],
                )
            )

        return result

    period_task_rows = defaultdict(list)

    for period in periods:
        for root in threads:
            root_id = root["id"]

            # Only top-level threads are root tasks.
            if root_id in parent_by_child:
                continue

            direct = period_thread_stats.get(
                (
                    period,
                    root_id,
                )
            )

            child_ids = descendants(
                root_id
            )

            subagent = Bucket()
            review = Bucket()

            active_children = 0

            for child_id in child_ids:
                child_stats = (
                    period_thread_stats.get(
                        (
                            period,
                            child_id,
                        )
                    )
                )

                if (
                    child_stats is None
                    or child_stats.calls == 0
                ):
                    continue

                active_children += 1

                child = thread_by_id.get(
                    child_id
                )

                target = subagent

                if (
                    child is not None
                    and child["model"]
                    == "codex-auto-review"
                ):
                    target = review

                target.calls += (
                    child_stats.calls
                )

                target.total += (
                    child_stats.total
                )

                target.input += (
                    child_stats.input
                )

                target.cached += (
                    child_stats.cached
                )

                target.output += (
                    child_stats.output
                )

                target.reasoning += (
                    child_stats.reasoning
                )

                target.cost_usd += (
                    child_stats.cost_usd
                )

                target.cost_rub += (
                    child_stats.cost_rub
                )

                target.unpriced += (
                    child_stats.unpriced
                )

            direct = (
                direct
                if direct is not None
                else Bucket()
            )

            full_tokens = (
                direct.total
                + subagent.total
                + review.total
            )

            full_calls = (
                direct.calls
                + subagent.calls
                + review.calls
            )

            if not full_calls:
                continue

            cwd = (
                root["cwd"]
                or "unknown"
            )

            project = project_name(
                cwd,
                aliases,
            )

            title = (
                root["title"]
                or "(untitled)"
            )

            full_cost_rub = (
                direct.cost_rub
                + subagent.cost_rub
                + review.cost_rub
            )

            period_task_rows[
                period
            ].append(
                {
                    "root_id": root_id,
                    "project": project,
                    "title": title,
                    "model": (
                        root["model"]
                        or "unknown"
                    ),
                    "effort": (
                        root[
                            "reasoning_effort"
                        ]
                        or "unknown"
                    ),
                    "children": (
                        active_children
                    ),
                    "direct_tokens": (
                        direct.total
                    ),
                    "subagent_tokens": (
                        subagent.total
                    ),
                    "review_tokens": (
                        review.total
                    ),
                    "full_tokens": (
                        full_tokens
                    ),
                    "full_calls": (
                        full_calls
                    ),
                    "cost_rub": (
                        full_cost_rub
                    ),
                }
            )

        period_task_rows[
            period
        ].sort(
            key=lambda row:
                row["full_tokens"],
            reverse=True,
        )

    print()
    print("CODEX ALLOWANCE PERIOD COMPARISON")
    print("=" * 125)

    for period, (
        start,
        end,
    ) in periods.items():

        print()
        print(period)
        print(
            f"{start.isoformat()}  →  "
            f"{end.isoformat()}"
        )

        print(
            f"Matched events: "
            f"{matched_events[period]:,}"
        )

        print()
        print_bucket(
            "TOTAL",
            totals[period],
        )
        (
            active_time,
            active_sessions,
            wall_time,
        ) = calculate_active_time(
            period_timestamps[
                period
            ],
            ACTIVE_GAP,
        )

        active_hours = (
            active_time.total_seconds()
            / 3600
        )

        wall_hours = (
            wall_time.total_seconds()
            / 3600
        )

        tokens_per_active_hour = (
            totals[period].total
            / active_hours
            if active_hours
            else 0
        )

        calls_per_active_hour = (
            totals[period].calls
            / active_hours
            if active_hours
            else 0
        )

        uncached_per_active_hour = (
            totals[period].uncached
            / active_hours
            if active_hours
            else 0
        )

        output_per_active_hour = (
            totals[period].output
            / active_hours
            if active_hours
            else 0
        )

        print()
        print("ACTIVITY INTENSITY")
        print("-" * 125)

        print(
            f"Active sessions:       "
            f"{active_sessions:,}"
        )

        print(
            f"Active time:           "
            f"{active_hours:,.2f} h"
        )

        print(
            f"Wall time:             "
            f"{wall_hours:,.2f} h"
        )

        print(
            f"Tokens / active hour:  "
            f"{fmt_tokens(int(tokens_per_active_hour))}"
        )

        print(
            f"Calls / active hour:   "
            f"{calls_per_active_hour:,.1f}"
        )

        print(
            f"Uncached / active hr:  "
            f"{fmt_tokens(int(uncached_per_active_hour))}"
        )

        print(
            f"Output / active hour:  "
            f"{fmt_tokens(int(output_per_active_hour))}"
        )

        print()
        print("BY ROLE")
        print("-" * 125)

        for role in [
            "ROOT",
            "SUBAGENT",
            "REVIEW",
        ]:
            bucket = by_role.get(
                (
                    period,
                    role,
                )
            )

            if bucket:
                print_bucket(
                    role,
                    bucket,
                )

        print()
        print("MODEL × EFFORT × ROLE")
        print("-" * 125)

        model_rows = [
            (
                key,
                bucket,
            )
            for key, bucket
            in by_model.items()
            if key[0] == period
        ]

        model_rows.sort(
            key=lambda item:
                item[1].total,
            reverse=True,
        )

        for (
            _,
            role,
            model,
            effort,
        ), bucket in model_rows:

            print_bucket(
                f"{role} | {model} | {effort}",
                bucket,
            )

        print()
        print("TOP PROJECTS")
        print("-" * 125)

        project_rows = [
            (
                key,
                bucket,
            )
            for key, bucket
            in by_project.items()
            if key[0] == period
        ]

        project_rows.sort(
            key=lambda item:
                item[1].total,
            reverse=True,
        )

        for (
            _,
            project,
        ), bucket in project_rows[:15]:

            print_bucket(
                project[:32],
                bucket,
            )

    print()
    print(
        f"{args.b_label.upper()} / "
        f"{args.a_label.upper()}"
    )
    print("=" * 125)

    a = totals[period_a]
    b = totals[period_b]

    metrics = {
        "Calls": (
            a.calls,
            b.calls,
        ),
        "Total tokens": (
            a.total,
            b.total,
        ),
        "Uncached input": (
            a.uncached,
            b.uncached,
        ),
        "Output": (
            a.output,
            b.output,
        ),
        "Reasoning": (
            a.reasoning,
            b.reasoning,
        ),
        "API equivalent RUB": (
            a.cost_rub,
            b.cost_rub,
        ),
    }

    for name, (
        old,
        new,
    ) in metrics.items():

        ratio = (
            new / old
            if old
            else 0
        )

        print(
            f"{name:24} "
            f"A={old:15,.0f}  "
            f"B={new:15,.0f}  "
            f"B/A={ratio:7.2f}x"
        )

    # -------------------------------------------------
    # EXECUTIVE COMPARISON
    # -------------------------------------------------

    print()
    print("EFFICIENCY / INTENSITY")
    print("=" * 125)

    activity = {}

    for period_name in (
        period_a,
        period_b,
    ):
        (
            active_time,
            active_sessions,
            wall_time,
        ) = calculate_active_time(
            period_timestamps[
                period_name
            ],
            ACTIVE_GAP,
        )

        active_hours = (
            active_time.total_seconds()
            / 3600
        )

        activity[period_name] = {
            "active_hours": active_hours,
            "sessions": active_sessions,
            "wall_hours": (
                wall_time.total_seconds()
                / 3600
            ),
        }

    a_hours = activity[
        period_a
    ]["active_hours"]

    b_hours = activity[
        period_b
    ]["active_hours"]

    efficiency_metrics = {
        "Active hours": (
            a_hours,
            b_hours,
        ),
        "Tokens / active hour": (
            a.total / a_hours
            if a_hours else 0,
            b.total / b_hours
            if b_hours else 0,
        ),
        "Calls / active hour": (
            a.calls / a_hours
            if a_hours else 0,
            b.calls / b_hours
            if b_hours else 0,
        ),
        "Uncached / active hour": (
            a.uncached / a_hours
            if a_hours else 0,
            b.uncached / b_hours
            if b_hours else 0,
        ),
        "Output / active hour": (
            a.output / a_hours
            if a_hours else 0,
            b.output / b_hours
            if b_hours else 0,
        ),
        "Reasoning / active hour": (
            a.reasoning / a_hours
            if a_hours else 0,
            b.reasoning / b_hours
            if b_hours else 0,
        ),
    }

    for name, (
        old,
        new,
    ) in efficiency_metrics.items():

        ratio = (
            new / old
            if old
            else 0
        )

        print(
            f"{name:28} "
            f"A={old:15,.2f}  "
            f"B={new:15,.2f}  "
            f"B/A={ratio:7.2f}x"
        )

    print()
    print("AGENT STRUCTURE")
    print("=" * 125)

    for role in (
        "ROOT",
        "SUBAGENT",
    ):
        a_role = by_role.get(
            (
                period_a,
                role,
            ),
            Bucket(),
        )

        b_role = by_role.get(
            (
                period_b,
                role,
            ),
            Bucket(),
        )

        a_share = (
            a_role.total / a.total
            if a.total
            else 0
        )

        b_share = (
            b_role.total / b.total
            if b.total
            else 0
        )

        print(
            f"{role:12} "
            f"A={fmt_tokens(a_role.total):>9} "
            f"({a_share:5.1%})   "
            f"B={fmt_tokens(b_role.total):>9} "
            f"({b_share:5.1%})"
        )

    print()
    print("TASK CONCENTRATION")
    print("=" * 125)

    for label, period_name in (
        (
            f"A — {args.a_label}",
            period_a,
        ),
        (
            f"B — {args.b_label}",
            period_b,
        ),
    ):
        tasks = period_task_rows[
            period_name
        ]

        delegated = [
            task
            for task in tasks
            if task["children"] > 0
        ]

        total_tokens = sum(
            task["full_tokens"]
            for task in tasks
        )

        total_children = sum(
            task["children"]
            for task in tasks
        )

        max_children = max(
            (
                task["children"]
                for task in tasks
            ),
            default=0,
        )

        top1_share = (
            tasks[0]["full_tokens"]
            / total_tokens
            if tasks
            and total_tokens
            else 0
        )

        top3_share = (
            sum(
                task["full_tokens"]
                for task in tasks[:3]
            )
            / total_tokens
            if total_tokens
            else 0
        )

        print()
        print(label)

        print(
            f"  Root tasks:           "
            f"{len(tasks):,}"
        )

        print(
            f"  Delegated tasks:      "
            f"{len(delegated):,}"
        )

        print(
            f"  Active child agents:  "
            f"{total_children:,}"
        )

        print(
            f"  Max children/task:    "
            f"{max_children:,}"
        )

        print(
            f"  Top task share:       "
            f"{top1_share:.1%}"
        )

        print(
            f"  Top 3 share:          "
            f"{top3_share:.1%}"
        )

        print()
        print(
            f"TOP TASKS — {label}"
        )
        print("-" * 125)

        for index, task in enumerate(
            tasks[:10],
            start=1,
        ):
            title = (
                task["title"]
                .replace("\n", " ")
                .replace("\r", " ")
            )

            if len(title) > 55:
                title = (
                    title[:52]
                    + "..."
                )

            print(
                f"{index:2}. "
                f"{task['project'][:14]:14} "
                f"{task['model'][:16]:16} "
                f"{task['effort'][:7]:7} "
                f"child={task['children']:3d} "
                f"direct={fmt_tokens(task['direct_tokens']):>8} "
                f"sub={fmt_tokens(task['subagent_tokens']):>8} "
                f"full={fmt_tokens(task['full_tokens']):>8} "
                f"rub={task['cost_rub']:9,.0f}  "
                f"{title}"
            )

if __name__ == "__main__":
    main()
