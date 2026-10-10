from .base import Context, Strategy
from .maker_band import MakerBand
from .sma_cross import SmaCross

STRATEGIES: dict[str, type[Strategy]] = {
    SmaCross.name: SmaCross,
    MakerBand.name: MakerBand,
}


def build_strategy(name: str, params: dict[str, str] | None = None) -> Strategy:
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy {name!r}; choose from {sorted(STRATEGIES)}")
    kwargs = {k: float(v) for k, v in (params or {}).items()}
    return STRATEGIES[name](**kwargs)


__all__ = ["Context", "Strategy", "STRATEGIES", "build_strategy"]
