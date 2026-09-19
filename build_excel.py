from __future__ import annotations

import unicodedata
from pathlib import Path

import yaml

from codex_usage.analytics import aggregate_usage
from codex_usage.db import (
    connect_readonly,
    get_spawn_edges,
    get_threads,
)
from codex_usage.excel import build_workbook
from codex_usage.pricing import calculate_cost
from codex_usage.rollouts import iter_usage_events
from codex_usage.tasks import build_task_rows


BASE_DIR = Path(__file__).parent


def normalize_path(value: str) -> str:
    return unicodedata.normalize(
        "NFC",
        str(Path(value).expanduser()),
    )


def load_config() -> dict:
    with (BASE_DIR / "config.yaml").open(
        "r",
        encoding="utf-8",
    ) as file:
        return yaml.safe_load(file)


def friendly_project_name(
    cwd: str,
    aliases: dict[str, str],
) -> str:
    """
    Return a friendly project name.

    Known paths use aliases from config.yaml.
    Other normal project folders use their folder name.
    Unusual/system paths keep their full cwd to avoid
    accidentally merging unrelated projects.
    """

    normalized = normalize_path(cwd)

    if normalized in aliases:
        return aliases[normalized]

    path = Path(normalized)

    if "/Documents/Projects/" in normalized:
        return path.name

    return normalized


def main() -> None:
    config = load_config()

    database = Path(
        config["codex"]["database"]
    ).expanduser()

    output = (
        BASE_DIR
        / config["output"]["excel"]
    )

    usd_rub = float(
        config["pricing"]["usd_rub"]
    )

    aliases = {
        normalize_path(path): name
        for path, name in (
            config.get("project_aliases") or {}
        ).items()
    }

    # -----------------------------
    # READ CODEX DATABASE
    # -----------------------------

    connection = connect_readonly(database)

    try:
        threads = get_threads(connection)
        edges = get_spawn_edges(connection)
    finally:
        connection.close()

    # -----------------------------
    # PROJECT DISCOVERY
    # -----------------------------
    #
    # Every cwd found in Codex is included.
    # Aliases only rename known paths.

    project_map = {
        normalize_path(thread["cwd"]):
            friendly_project_name(
                thread["cwd"],
                aliases,
            )
        for thread in threads
    }

    # -----------------------------
    # READ TOKEN EVENTS
    # -----------------------------

    records = []

    for thread in threads:
        rollout_path = thread["rollout_path"]

        if not rollout_path:
            continue

        for event in iter_usage_events(
            rollout_path
        ):
            records.append(
                (thread, event)
            )

    # -----------------------------
    # AGGREGATE
    # -----------------------------

    buckets = aggregate_usage(
        records,
        edges,
        timezone_name=config["timezone"],
    )

    model_rows = []

    unpriced_models = set()

    for key, bucket in buckets.items():
        (
            day,
            cwd,
            role,
            model,
            effort,
        ) = key

        project = project_map.get(
            normalize_path(cwd),
            friendly_project_name(
                cwd,
                aliases,
            ),
        )

        cost = calculate_cost(
            model=model,
            input_tokens=bucket.input_tokens,
            cached_tokens=bucket.cached_input_tokens,
            output_tokens=bucket.output_tokens,
            usd_rub=usd_rub,
        )

        if not cost["priced"]:
            unpriced_models.add(model)

        model_rows.append({
            "date": day,
            "project": project,
            "cwd": normalize_path(cwd),

            "role": role,
            "model": model,
            "effort": effort,

            "calls": bucket.calls,

            "total": bucket.total_tokens,
            "input": bucket.input_tokens,
            "cached": bucket.cached_input_tokens,
            "uncached": bucket.uncached_input_tokens,
            "output": bucket.output_tokens,
            "reasoning": bucket.reasoning_tokens,

            "cache_hit": bucket.cache_hit,

            "priced": cost["priced"],
            "cost_usd": cost["cost_usd"],
            "cost_rub": cost["cost_rub"],
        })

    model_rows.sort(
        key=lambda row: (
            row["date"],
            row["project"],
            row["role"],
            row["model"],
            row["effort"],
        )
    )

    # -----------------------------
    # TASKS
    # -----------------------------

    task_rows = build_task_rows(
        threads=threads,
        edges=edges,
        project_map=project_map,
        normalize_path=normalize_path,
        usd_rub=usd_rub,
        timezone_name=config["timezone"],
    )

    # -----------------------------
    # BUILD EXCEL
    # -----------------------------

    result = build_workbook(
        model_rows=model_rows,
        task_rows=task_rows,
        output_path=output,
    )

    # -----------------------------
    # SUMMARY
    # -----------------------------

    projects = sorted(
        set(project_map.values())
    )

    priced_tokens = sum(
        row["total"]
        for row in model_rows
        if row["priced"]
    )

    unpriced_tokens = sum(
        row["total"]
        for row in model_rows
        if not row["priced"]
    )

    total_cost_usd = sum(
        row["cost_usd"]
        for row in model_rows
    )

    total_cost_rub = sum(
        row["cost_rub"]
        for row in model_rows
    )

    print()
    print("CODEX USAGE — EXCEL")
    print("=" * 60)

    print(
        f"Threads:           "
        f"{len(threads):,}"
    )

    print(
        f"Projects / cwd:    "
        f"{len(projects):,}"
    )

    print(
        f"Models rows:       "
        f"{len(model_rows):,}"
    )

    print(
        f"Tasks:             "
        f"{len(task_rows):,}"
    )

    print(
        f"USD/RUB:           "
        f"{usd_rub:.2f}"
    )

    print()
    print("API EQUIVALENT")
    print("-" * 60)

    print(
        f"Priced tokens:     "
        f"{priced_tokens:,}"
    )

    print(
        f"Unpriced tokens:   "
        f"{unpriced_tokens:,}"
    )

    print(
        f"Base-rate USD:     "
        f"${total_cost_usd:,.2f}"
    )

    print(
        f"Base-rate RUB:     "
        f"{total_cost_rub:,.2f} ₽"
    )

    print()
    print(
        f"Output:            "
        f"{result}"
    )

    if unpriced_models:
        print()
        print("UNPRICED MODELS")
        print("-" * 60)

        for model in sorted(
            unpriced_models
        ):
            print(f"- {model}")

    print()


if __name__ == "__main__":
    main()