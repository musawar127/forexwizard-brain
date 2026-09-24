"""Phase 5.6: instrument metadata — XAU/USD contract specifications.

Position sizing needs broker-specific contract metadata:
  - contract_size (oz per 1.0 lot)
  - tick_size (smallest price increment, in price units)
  - tick_value (USD per tick per 1.0 lot)

Different brokers offer XAU/USD with different specs. Common examples:
  - Standard: 100 oz contract, 0.01 tick, $1/tick
  - Mini:     10 oz contract,  0.01 tick, $0.10/tick
  - Micro:    1 oz contract,   0.01 tick, $0.01/tick

If the operator has not configured broker specs via environment
variables, the position sizer returns POSITION_SIZE_UNAVAILABLE
rather than guessing a tick value.
"""
from __future__ import annotations

import os
from typing import Optional


# Default contract spec is INTENTIONALLY EMPTY — we do not silently
# guess a broker's tick value. Operators must opt in by setting env vars.
_DEFAULT_CONTRACT_SIZE: Optional[float] = None
_DEFAULT_TICK_SIZE: Optional[float] = None
_DEFAULT_TICK_VALUE: Optional[float] = None


def _env_float(name: str) -> Optional[float]:
    v = os.environ.get(name)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_instrument_meta(instrument: str) -> dict | None:
    """Return the contract spec dict for an instrument, or None if unknown.

    The dict shape is:
        {
          "instrument": "XAU/USD",
          "contract_size": 100.0,  # oz per 1.0 lot
          "tick_size":     0.01,   # smallest price increment
          "tick_value":    1.0,    # USD per tick per 1.0 lot
          "source": "env: XAUUSD_CONTRACT_SIZE / XAUUSD_TICK_SIZE / XAUUSD_TICK_VALUE"
        }

    Returns None when any of the three required fields are missing —
    the caller must then return POSITION_SIZE_UNAVAILABLE.
    """
    if instrument in ("XAU/USD", "XAUUSD", "XAUUSD_SPOT"):
        cs = _env_float("XAUUSD_CONTRACT_SIZE") or _DEFAULT_CONTRACT_SIZE
        ts = _env_float("XAUUSD_TICK_SIZE") or _DEFAULT_TICK_SIZE
        tv = _env_float("XAUUSD_TICK_VALUE") or _DEFAULT_TICK_VALUE
        if cs is None or ts is None or tv is None or cs <= 0 or ts <= 0 or tv <= 0:
            return None
        return {
            "instrument": "XAU/USD",
            "contract_size": float(cs),
            "tick_size": float(ts),
            "tick_value": float(tv),
            "source": "env: XAUUSD_CONTRACT_SIZE / XAUUSD_TICK_SIZE / XAUUSD_TICK_VALUE",
        }
    # Unknown instrument — caller should return POSITION_SIZE_UNAVAILABLE.
    return None


def list_known_instruments() -> list[str]:
    """Return the list of instruments we have metadata for (after env load)."""
    out: list[str] = []
    if get_instrument_meta("XAU/USD") is not None:
        out.append("XAU/USD")
    return out
