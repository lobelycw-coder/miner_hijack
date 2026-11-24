"""Reject dead targets before spending a request on them.

Zero workers plus zero balance plus an already-marked row means the address is
inactive; it goes to the back of the queue rather than the bin, so a returning
miner is still picked up on the next cycle.
"""
import time

from . import blacklist

STALE_AFTER = 3600


def should_skip(row) -> bool:
    if blacklist.pool_skipped(row["pool"]):
        return True
    if blacklist.is_blacklisted(row["address"]):
        return True
    if row["status"] == "dead" and time.time() - (row["seen"] or 0) < STALE_AFTER:
        return True
    return False


def mark(row) -> str:
    """Return the status this row deserves after enrichment."""
    if not row["workers"] and not row["balance"]:
        return "dead"
    return "live"