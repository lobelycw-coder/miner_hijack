"""
Nonce-locked EVM sweeper.

One asyncio.Lock per (chain, address) makes fetch-nonce -> sign -> send
atomic per account, so parallel workers never race a nonce and never
strand a sweep on `replacement transaction underpriced`.

chain_id is asserted against the RPC before every sign. Dry-run returns
the fully-built tx without broadcasting.

Additive module. Edits nothing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.exceptions import ContractLogicError, TimeExhausted
from web3.middleware import ExtraDataToPOAMiddleware  # web3 v7 path

# --------------------------------------------------------------------------
# Chain registry
# --------------------------------------------------------------------------

CHAIN_IDS: Final[dict[str, int]] = {
    "eth": 1, "bnb": 56, "matic": 137, "base": 8453,
    "arbitrum": 42161, "optimism": 10, "avalanche": 43114,
}

# Chains that are EIP-1559 (type 2). Everything else -> legacy type 0.
EIP1559_CHAINS: Final[frozenset[str]] = frozenset(
    {"eth", "base", "arbitrum", "optimism", "matic"}
)

RPCS: Final[dict[str, tuple[str, ...]]] = {
    "eth": ("https://eth.llamarpc.com", "https://rpc.ankr.com/eth"),
    "bnb": ("https://bsc-dataseed.binance.org", "https://rpc.ankr.com/bsc"),
    "matic": ("https://polygon-rpc.com", "https://rpc.ankr.com/polygon"),
    "base": ("https://mainnet.base.org", "https://rpc.ankr.com/base"),
    "arbitrum": ("https://arb1.arbitrum.io/rpc", "https://rpc.ankr.com/arbitrum"),
    "optimism": ("https://mainnet.optimism.io", "https://rpc.ankr.com/optimism"),
    "avalanche": ("https://api.avax.network/ext/bc/C/rpc", "https://rpc.ankr.com/avalanche"),
}

MAX_FEE_MULTIPLIER: Final[float] = 1.35
PRIORITY_FEE_GWEI: Final[dict[str, Decimal]] = {
    "eth": Decimal("2"), "bnb": Decimal("1"), "matic": Decimal("30"),
    "base": Decimal("0.01"), "arbitrum": Decimal("0.01"),
    "optimism": Decimal("0.001"), "avalanche": Decimal("25"),
}

SWEEP_ALL: Final = Decimal("-1")

# --------------------------------------------------------------------------
# Result envelope
# --------------------------------------------------------------------------

@dataclass(slots=True)
class SweepResult:
    chain: str
    address: str
    to: str
    amount_wei: int
    nonce: int
    gas: int
    max_fee_per_gas: int
    tx_hash: str | None = None
    dry_run: bool = False
    error: str | None = None
    raw_tx: str | None = None          # not secret; debugging only
    _meta: dict = field(default_factory=dict, repr=False)

    @property
    def ok(self) -> bool:
        return self.error is None and (self.dry_run or self.tx_hash is not None)

# --------------------------------------------------------------------------
# Lock registry
# --------------------------------------------------------------------------

_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_LOCKS_GUARD = asyncio.Lock()


async def _lock_for(chain: str, address: str) -> asyncio.Lock:
    key = (chain.lower(), address.lower())
    async with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _LOCKS[key] = lock
        return lock

# --------------------------------------------------------------------------
# RPC with chain-id assertion
# --------------------------------------------------------------------------

async def _connect(chain: str) -> AsyncWeb3:
    if chain not in CHAIN_IDS:
        raise ValueError(f"unsupported chain: {chain}")
    expected = CHAIN_IDS[chain]
    last_err: Exception | None = None
    for url in RPCS[chain]:
        try:
            w3 = AsyncWeb3(AsyncHTTPProvider(url, request_kwargs={"timeout": 15}))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            actual = await w3.eth.chain_id
            if actual != expected:
                raise ValueError(f"{url} chain_id {actual} != {expected}")
            return w3
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise RuntimeError(f"no healthy RPC for {chain}: {last_err}")

# --------------------------------------------------------------------------
# Fees
# --------------------------------------------------------------------------

async def _fees(w3: AsyncWeb3, chain: str) -> tuple[int, int]:
    """(max_fee_or_gas_price, priority) wei. Legacy chains -> (gas_price, gas_price)."""
    if chain not in EIP1559_CHAINS:
        gp = await w3.eth.gas_price
        return int(gp), int(gp)
    block = await w3.eth.get_block("latest")
    base = getattr(block, "baseFeePerGas", None)
    priority = w3.to_wei(PRIORITY_FEE_GWEI.get(chain, Decimal("1")), "gwei")
    if base is None:
        gp = await w3.eth.gas_price
        return int(gp), int(gp)
    return int(base * MAX_FEE_MULTIPLIER) + int(priority), int(priority)

# --------------------------------------------------------------------------
# Amount
# --------------------------------------------------------------------------

async def _resolve_amount(w3: AsyncWeb3, address: str, amount: Decimal,
                          gas: int, max_fee: int) -> int:
    if amount != SWEEP_ALL:
        return int(amount)
    balance = await w3.eth.get_balance(address)
    reserve = int(gas * max_fee * 1.10)
    out = balance - reserve
    if out <= 0:
        raise ValueError(f"balance {balance} wei < reserve {reserve} wei")
    return out

# --------------------------------------------------------------------------
# Public
# --------------------------------------------------------------------------

async def sweep(chain: str, private_key: str, to: