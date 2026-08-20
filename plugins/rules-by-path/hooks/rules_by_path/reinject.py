"""The re-injection budget: how many times one rule may be sent again within a
single session before the budget silences it, regardless of how far the
context has moved on since the last delivery.

A module of its own rather than another function in `config.py`: `config.py`
was already at the file's 400-line ceiling, and this one config key is
self-contained enough to stand alone — its safe range does not depend on
which layer sets it, unlike every other numeric setting there."""

from .constants import (MAX_CONFIGURABLE_REINJECT_BUDGET,
                        MAX_REINJECTIONS_PER_RULE, warn)


def sanitize_reinject_budget(raw, source):
    """The value a `reinject_budget` config key resolves to, or None when
    unusable. Clamped to [0, MAX_CONFIGURABLE_REINJECT_BUDGET] in every layer
    alike: unlike `rule_size`, there is no direction in which raising this
    number is safe to leave unclamped even for a trusted layer — a session
    repeating one rule without limit is exactly the failure this budget
    exists to prevent."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        warn(f"{source}: 'reinject_budget' must be a whole number; ignored")
        return None
    clamped = max(0, min(value, MAX_CONFIGURABLE_REINJECT_BUDGET))
    if clamped != value:
        warn(f"{source}: 'reinject_budget' of {value} is outside "
             f"0-{MAX_CONFIGURABLE_REINJECT_BUDGET}; using {clamped}")
    return clamped


def reinject_budget(config):
    """How many times a rule may be re-injected this session before is_due()
    refuses it, regardless of distance covered. Falls back to the shipped
    constant when no layer configures one."""
    value = (config or {}).get("reinject_budget")
    return value if value is not None else MAX_REINJECTIONS_PER_RULE
