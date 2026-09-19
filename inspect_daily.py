from collections import defaultdict
from pathlib import Path
import unicodedata

import yaml

from codex_usage.analytics import UsageBucket, aggregate_usage
from codex_usage.db import (
    connect_readonly,
    get_spawn_edges,
    get_threads,
)
from codex_usage.rollouts import iter_usage_events


CONFIG_PATH = Path("config.yaml")


def normalize_path(value: str) -> str:
    return unicodedata.normalize(
        "NFC",
        str(Path(value).expanduser()),
    )


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(
            "config.yaml not found. "
            "Copy config.example.yaml to config.yaml first."
        )

    return yaml.safe_load(
        CONFIG_PATH.read_text(encoding="utf-8")
    )


def fmt(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"{value / 1_000:.1f}K"

    return str(value)


def main():
    config = load_config()

    codex_db = Path(
        config["codex"]["database"]
    ).expanduser()

    timezone_name = config.get(
        "timezone",
        "UTC",
    )

    project_aliases = {
        normalize_path(path): name
        for path, name in (
            config.get("project_aliases") or {}
        ).items()
    }

    connection = connect_readonly(codex_db)

    try:
        threads = get_threads(connection)
        edges = get_spawn_edges(connection)
    finally:
        connection.close()

    records = []

    for thread in threads:
        path = thread["rollout_path"]

        if not path:
            continue

        for event in iter_usage_events(path):
            records.append((thread, event))

    buckets = aggregate_usage(
        records,
        edges,
        timezone_name=timezone_name,
    )

    # Friendly project names.
    project_rows = []

    for key, bucket in buckets.items():
        day, cwd, role, model, effort = key

        normalized_cwd = normalize_path(cwd)

        project = project_aliases.get(
            normalized_cwd,
            Path(normalized_cwd).name,
        )

        project_rows.append(
            (
                day,
                project,
                role,
                model,
                effort,
                bucket,
            )
        )

    print()
    print("DAILY USAGE")
    print("=" * 105)

    for (
        day,
        project,
        role,
        model,
        effort,
        bucket,
    ) in sorted(project_rows):

        print(
            f"{day} "
            f"{project:10} "
            f"{role:9} "
            f"{model:18} "
            f"{effort:7} "
            f"calls={bucket.calls:5,} "
            f"total={fmt(bucket.total_tokens):>8} "
            f"uncached={fmt(bucket.uncached_input_tokens):>8} "
            f"cache={bucket.cache_hit:6.1%}"
        )

    # Separate daily KUDO summary.
    daily = defaultdict(UsageBucket)

    for (
        day,
        project,
        role,
        model,
        effort,
        bucket,
    ) in project_rows:

        if project != "KUDO":
            continue

        target = daily[day]

        target.calls += bucket.calls
        target.total_tokens += bucket.total_tokens
        target.input_tokens += bucket.input_tokens
        target.cached_input_tokens += bucket.cached_input_tokens
        target.uncached_input_tokens += bucket.uncached_input_tokens
        target.output_tokens += bucket.output_tokens
        target.reasoning_tokens += bucket.reasoning_tokens

    print()
    print("KUDO — BY DAY")
    print("=" * 80)

    for day, bucket in sorted(daily.items()):
        print(
            f"{day}  "
            f"calls={bucket.calls:5,}  "
            f"total={fmt(bucket.total_tokens):>8}  "
            f"uncached={fmt(bucket.uncached_input_tokens):>8}  "
            f"output={fmt(bucket.output_tokens):>7}  "
            f"cache={bucket.cache_hit:6.1%}"
        )


if __name__ == "__main__":
    main()