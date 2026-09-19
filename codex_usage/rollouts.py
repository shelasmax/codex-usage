from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class UsageEvent:
    timestamp: datetime
    thread_id: str
    turn_id: str | None
    response_id: str | None

    input_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    total_tokens: int


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def make_usage_event(
    *,
    timestamp_raw: str,
    thread_id: str,
    turn_id: str | None,
    response_id: str | None,
    usage: dict,
) -> UsageEvent:
    input_tokens = int(usage.get("input_tokens") or 0)
    cached_input_tokens = int(
        usage.get("cached_input_tokens") or 0
    )

    return UsageEvent(
        timestamp=parse_timestamp(timestamp_raw),
        thread_id=thread_id,
        turn_id=turn_id,
        response_id=response_id,

        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,

        uncached_input_tokens=max(
            input_tokens - cached_input_tokens,
            0,
        ),

        output_tokens=int(
            usage.get("output_tokens") or 0
        ),

        reasoning_output_tokens=int(
            usage.get("reasoning_output_tokens") or 0
        ),

        total_tokens=int(
            usage.get("total_tokens") or 0
        ),
    )


def load_jsonl(path: Path) -> list[dict]:
    events = []

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

    return events


def iter_usage_events(
    rollout_path: str | Path,
) -> Iterator[UsageEvent]:
    """
    Read per-response token usage from Codex rollout JSONL.

    Supports two telemetry generations:

    CURRENT:
        type = token_usage_record
        payload.usage

    LEGACY:
        type = event_msg
        payload.type = token_count
        payload.info.last_token_usage

    IMPORTANT:
    If a rollout contains current token_usage_record events,
    legacy token_count events are ignored completely.

    This prevents double counting in rollouts that contain both
    telemetry formats.

    Cumulative total_token_usage and thread_token_usage are
    intentionally never summed.
    """

    path = Path(rollout_path).expanduser()

    if not path.is_file():
        return

    events = load_jsonl(path)

    # -----------------------------
    # CURRENT TELEMETRY
    # -----------------------------

    current_events = [
        event
        for event in events
        if event.get("type") == "token_usage_record"
        and (event.get("payload") or {}).get("usage")
    ]

    if current_events:
        for event in current_events:
            payload = event.get("payload") or {}
            usage = payload.get("usage") or {}
            timestamp_raw = event.get("timestamp")

            if not timestamp_raw:
                continue

            yield make_usage_event(
                timestamp_raw=timestamp_raw,
                thread_id=str(
                    payload.get("thread_id") or ""
                ),
                turn_id=payload.get("turn_id"),
                response_id=payload.get("response_id"),
                usage=usage,
            )

        return

    # -----------------------------
    # LEGACY TELEMETRY
    # -----------------------------
    #
    # Legacy token_count may repeat last_token_usage.
    # total_token_usage is used only as a monotonic
    # deduplication marker.
    #
    # We emit last_token_usage only when cumulative
    # total_tokens advances.

    previous_total = None

    for event in events:
        if event.get("type") != "event_msg":
            continue

        payload = event.get("payload") or {}

        if payload.get("type") != "token_count":
            continue

        info = payload.get("info") or {}

        cumulative = info.get("total_token_usage")
        usage = info.get("last_token_usage")

        if not cumulative or not usage:
            continue

        timestamp_raw = event.get("timestamp")

        if not timestamp_raw:
            continue

        cumulative_total = int(
            cumulative.get("total_tokens") or 0
        )

        if previous_total is not None:
            # Same cumulative value means repeated telemetry.
            if cumulative_total == previous_total:
                continue

            # Counter moved backwards: session accounting
            # was reset/rebased. Accept the current response
            # as the first response of the new segment.
            if cumulative_total < previous_total:
                previous_total = cumulative_total

                yield make_usage_event(
                    timestamp_raw=timestamp_raw,
                    thread_id="",
                    turn_id=None,
                    response_id=None,
                    usage=usage,
                )
                continue

        previous_total = cumulative_total

        yield make_usage_event(
            timestamp_raw=timestamp_raw,
            thread_id="",
            turn_id=None,
            response_id=None,
            usage=usage,
        )