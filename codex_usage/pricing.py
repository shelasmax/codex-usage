from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ModelPrice:
    input: float
    cached: float
    output: float


def load_prices(
    path: str | Path | None = None,
) -> dict[str, ModelPrice]:
    if path is None:
        path = (
            Path(__file__).resolve().parent.parent
            / "pricing.yaml"
        )

    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(
            f"Pricing file not found: {path}"
        )

    data = yaml.safe_load(
        path.read_text(encoding="utf-8")
    ) or {}

    models = data.get("models")

    if not isinstance(models, dict):
        raise ValueError(
            "pricing.yaml must contain a 'models' mapping"
        )

    prices: dict[str, ModelPrice] = {}

    for model, values in models.items():
        if not isinstance(values, dict):
            raise ValueError(
                f"Invalid pricing entry for {model}"
            )

        required = {
            "input",
            "cached_input",
            "output",
        }

        missing = required - values.keys()

        if missing:
            raise ValueError(
                f"Missing pricing fields for {model}: "
                f"{', '.join(sorted(missing))}"
            )

        prices[str(model)] = ModelPrice(
            input=float(values["input"]),
            cached=float(values["cached_input"]),
            output=float(values["output"]),
        )

    return prices


PRICES = load_prices()


def calculate_cost(
    *,
    model: str,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    usd_rub: float,
) -> dict:

    price = PRICES.get(model)

    if price is None:
        return {
            "priced": False,
            "cost_usd": 0.0,
            "cost_rub": 0.0,
        }

    uncached_tokens = max(
        input_tokens - cached_tokens,
        0,
    )

    cost_usd = (
        uncached_tokens
        / 1_000_000
        * price.input
        + cached_tokens
        / 1_000_000
        * price.cached
        + output_tokens
        / 1_000_000
        * price.output
    )

    return {
        "priced": True,
        "cost_usd": cost_usd,
        "cost_rub": cost_usd * usd_rub,
    }