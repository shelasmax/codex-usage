from codex_usage.rollouts import iter_usage_events

from pathlib import Path

from codex_usage.db import (
    connect_readonly,
    get_spawn_edges,
    get_threads,
)


CODEX_DB = Path.home() / ".codex" / "state_5.sqlite"


def main() -> None:
    connection = connect_readonly(CODEX_DB)

    try:
        threads = get_threads(connection)
        edges = get_spawn_edges(connection)
    finally:
        connection.close()

    child_ids = {
        edge["child_thread_id"]
        for edge in edges
    }

    roots = [
        thread
        for thread in threads
        if thread["id"] not in child_ids
    ]

    subagents = [
        thread
        for thread in threads
        if thread["id"] in child_ids
    ]

    reviews = [
        thread
        for thread in threads
        if thread["model"] == "codex-auto-review"
    ]

    print()
    print("CODEX USAGE — INSPECT")
    print("=" * 50)
    print(f"Database:      {CODEX_DB}")
    print(f"Threads:       {len(threads):,}")
    print(f"Root threads:  {len(roots):,}")
    print(f"Subagents:     {len(subagents):,}")
    print(f"Spawn edges:   {len(edges):,}")
    print(f"Auto reviews:  {len(reviews):,}")
    print()
    print("Reading rollout token events...")
    print()

    usage_events = 0
    total_tokens = 0
    input_tokens = 0
    cached_tokens = 0
    output_tokens = 0
    reasoning_tokens = 0

    missing_rollouts = 0

    for thread in threads:
        rollout_path = thread["rollout_path"]

        if not rollout_path or not Path(
            rollout_path
        ).expanduser().is_file():
            missing_rollouts += 1
            continue

        for event in iter_usage_events(rollout_path):
            usage_events += 1

            total_tokens += event.total_tokens
            input_tokens += event.input_tokens
            cached_tokens += event.cached_input_tokens
            output_tokens += event.output_tokens
            reasoning_tokens += event.reasoning_output_tokens

    uncached_tokens = input_tokens - cached_tokens

    cache_hit = (
        cached_tokens / input_tokens * 100
        if input_tokens
        else 0
    )

    print("TOKEN EVENTS — ALL CODEX")
    print("=" * 50)
    print(f"Usage events:      {usage_events:,}")
    print(f"Missing rollouts:  {missing_rollouts:,}")
    print(f"Total tokens:      {total_tokens:,}")
    print(f"Input tokens:      {input_tokens:,}")
    print(f"Cached input:      {cached_tokens:,}")
    print(f"Uncached input:    {uncached_tokens:,}")
    print(f"Output tokens:     {output_tokens:,}")
    print(f"Reasoning tokens:  {reasoning_tokens:,}")
    print(f"Cache hit:         {cache_hit:.2f}%")
    print()

if __name__ == "__main__":
    main()