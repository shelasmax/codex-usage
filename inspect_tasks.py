import unicodedata
from pathlib import Path

import yaml

from codex_usage.db import (
    connect_readonly,
    get_spawn_edges,
    get_threads,
)
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
    
    usd_rub = float(
        config["pricing"]["usd_rub"]
    )

    project_map = {
        normalize_path(project["path"]): project["name"]
        for project in config["projects"]
    }

    database = Path(
        config["codex"]["database"]
    ).expanduser()

    connection = connect_readonly(database)

    try:
        threads = get_threads(connection)
        edges = get_spawn_edges(connection)
    finally:
        connection.close()

    rows = build_task_rows(
        threads=threads,
        edges=edges,
        project_map=project_map,
        normalize_path=normalize_path,
        usd_rub=usd_rub,
        timezone_name=config["timezone"],
    )

    print()
    print("TOP TASKS BY FULL TOKEN USAGE")
    print("=" * 130)

    for index, row in enumerate(rows[:25], start=1):
        title = row["title"][:45]

        print(
            f"{index:2}. "
            f"{row['project']:11} "
            f"{row['root_model']:17} "
            f"{row['root_effort']:7} "
            f"direct={fmt(row['direct_tokens']):>8} "
            f"sub={fmt(row['subagent_tokens']):>8} "
            f"review={fmt(row['review_tokens']):>7} "
            f"FULL={fmt(row['full_tokens']):>8} "
            f"children={row['children']:3} "
            f"days={row['active_days']:3} "
            f"{title}"
        )

    print()
    print(f"Tasks: {len(rows):,}")
    print()


if __name__ == "__main__":
    main()