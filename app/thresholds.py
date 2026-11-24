"""Per-pool payout floors. Used when the pool JSON omits its own threshold.

2Miners does not publish a threshold in /api/accounts, so every target scored
against 0.0 and the fast path (which requires threshold > 0) never fired.
These are conservative defaults; override with POOL_FLOOR_<COIN> env vars.
"""
import os

DEFAULTS = {
    "etc": 0.5, "eth": 0.02, "bnb": 0.01,
    "ergo": 1.0, "rvn": 5.0, "kas": 10.0, "alph": 1.0,
    "zeph": 1.0, "nexa": 500.0, "xmr": 0.05, "btc": 0.0005,
    "bch": 0.002, "iron": 0.5,
}


def floor_for(spec) -> float:
    env = os.environ.get(f"POOL_FLOOR_{spec['coin'].upper()}", "").strip()
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    if spec.get("threshold"):
        return float(spec["threshold"])
    return DEFAULTS.get(spec["coin"], 0.0)