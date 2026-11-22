"""
Nonce-locked EVM sweeper.

One lock per (chain, address) guarantees fetch-nonce -> sign -> send is
atomic per account, so parallel workers never race a nonce and never
strand a sweep on `replacement transaction underpriced`.

Every sign asserts chain_id against the RPC before broadcasting.
Dry-run returns the fully-built tx without sending.

Additive module. Nothing here edits fastpath.py or web/.
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
from web3.middleware import ExtraDataToPOAMiddleware

# ---------------------------------------------------------------------------
# Chain registry. chain_id is the assertion target before every sign.
# rpcs are tried in order; first one that answers eth_chainId wins.
# ---------------------------------------------------------------------------

CHAIN_IDS: Final[dict[str, int]] = {
    "eth":      1,
    "bnb":      56,
    "matic":    137,
    "base":     8453,
    "arbitrum": 42161,
    "optimism": 10,
    "avalanche": 43114,
}

RPCS: Final[dict[str, tuple[str, ...]]] = {
    "eth":      ("https://eth.llamarpc.com", "https://rpc.ankr.com/eth"),
    "bnb":      ("https://bsc-dataseed.binance.org", "https://rpc.ankr.com/bsc"),
    "matic":    ("https://polygon-rpc.com", "https://rpc.ankr.com/polygon"),
    "base":     ("https://mainnet.base.org", "https://rpc.ankr.com/base"),
    "arbitrum": ("https://arb1.arbitrum.io/rpc", "https://rpc.ankr.com/arbitrum"),
    "optimism": ("https://mainnet.optimism.io", "https://rpc.ankr.com/optimism"),
    "avalanche": ("https://api.avax.network/ext/bc/C/rpc", "https://rpc.ankr.com/avalanche"),
}

# Public RPCs below this priority-fee floor are unreliable; raise on paid RPC.
MAX_FEE_MULTIPLIER: Final[float] = 1.35   # headroom over base_fee
PRIORITY_FEE_GWEI: Final[dict[str, Decimal]] = {
    "eth": Decimal("2"), "bnb": Decimal("1"), "matic": Decimal("30"),
    "base": Decimal("0.01"), "arbitrum": Decimal("0.01"),
    "optimism": Decimal("0.001"), "avalanche": Decimal("25"),
}

# Sentinel: sweep entire balance minus gas buffer.
SWEEP_ALL: Final = Decimal("-1")


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------

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
    raw_tx: str | None = None
    # kept for debugging only — never log this
    _signed: dict = field(default_factory=dict, repr=False)

    @property
    def ok(self) -> bool:
        return self.error is None and (self.dry_run or self.tx_hash is not None)


# ---------------------------------------------------------------------------
# Lock registry — process-global, keyed on (chain, address)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# RPC resolution with chain-id assertion
# ---------------------------------------------------------------------------

async def _connect(chain: str) -> AsyncWeb3:
    if chain not in CHAIN_IDS:
        raise ValueError(f"unsupported chain: {chain}")
    expected = CHAIN_IDS[chain]
    last_err: Exception | None = None
    for url in RPCS[chain]:
        try:
            w3 = AsyncWeb3(AsyncHTTPProvider(url, request_kwargs={"timeout": 15}))
            # POA middleware: avalanche/optimism return extraData padding
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            actual = await w3.eth.chain_id
            if actual != expected:
                raise ValueError(f"{url} chain_id {actual} != {expected}")
            return w3
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    raise RuntimeError(f"no healthy RPC for {chain}: {last_err}")


# ---------------------------------------------------------------------------
# Fee estimation
# ---------------------------------------------------------------------------

async def _fees(w3: AsyncWeb3, chain: str) -> tuple[int, int]:
    """Return (max_fee_per_gas, max_priority_fee_per_gas) in wei."""
    block = await w3.eth.get_block("latest")
    base = getattr(block, "baseFeePerGas", None)
    priority = w3.to_wei(PRIORITY_FEE_GWEI.get(chain, Decimal("1")), "gwei")
    if base is None:  # legacy chains (bnb legacy path)
        gas_price = await w3.eth.gas_price
        return int(gas_price), int(gas_price)
    max_fee = int(base * MAX_FEE_MULTIPLIER) + priority
    return max_fee, priority


# ---------------------------------------------------------------------------
# Amount resolution
# ---------------------------------------------------------------------------

async def _resolve_amount(
    w3: AsyncWeb3, address: str, amount: Decimal, gas: int, max_fee: int
) -> int:
    """amount == SWEEP_ALL -> balance minus (gas * max_fee) with 10% pad."""
    if amount != SWEEP_ALL:
        return int(amount)
    balance = await w3.eth.get_balance(address)
    reserve = int(gas * max_fee * 1.10)
    out = balance - reserve
    if out <= 0:
        raise ValueError(
            f"balance {balance} wei cannot cover reserve {reserve} wei"
        )
    return out


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

async def sweep(
    chain: str,
    private_key: str,
    to: str,
    amount: Decimal = SWEEP_ALL,
    *,
    dry_run: bool = False,
) -> SweepResult:
    """
    Sweep `amount` wei (or entire balance if SWEEP_ALL) from the key's
    account to `to` on `chain`.

    Serialized per (chain, address) by an asyncio.Lock spanning
    nonce-fetch -> sign -> send. Chain-id asserted before signing.
    `dry_run=True` returns the built tx without broadcasting.
    """
    account: LocalAccount = Account.from_key(private_key)
    address = account.address

    async with await _lock_for(chain, address):
        try:
            w3 = await _connect(chain)

            # sanity: the RPC really is the chain we think it is
            if await w3.eth.chain_id != CHAIN_IDS[chain]:
                raise RuntimeError("chain-id drift after connect")

            nonce = await w3.eth.get_transaction_count(address, "pending")
            gas = 21000  # native transfer; no data payload

            max_fee, priority = await _fees(w3, chain)
            value = await _resolve_amount(w3, address, amount, gas, max_fee)

            tx = {
                "chainId": CHAIN_IDS[chain],
                "nonce": nonce,
                "to": w3.to_checksum_address(to),
                "value": value,
                "gas": gas,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": priority,
                "type": 2,
            }

            # signing asserts chainId inside eth_account as well
            signed = account.sign_transaction(tx)
            result = SweepResult(
                chain=chain, address=address, to=tx["to"],
                amount_wei=value, nonce=nonce, gas=gas,
                max_fee_per_gas=max_fee, tx_hash=None,
                dry_run=dry_run, raw_tx=signed.raw_transaction.hex(),
                _signed={"raw": signed.raw_transaction.hex()},
            )

            if dry_run:
                return result

            tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
            result.tx_hash = tx_hash.hex()

            try:
                receipt = await w3.eth.wait_for_transaction_receipt(
                    tx_hash, timeout=180
                )
                if receipt.get("status") != 1:
                    result.error = "reverted on-chain"
            except TimeExhausted:
                result.error = "receipt timeout (still pending)"

            return result

        except (ValueError, ContractLogicError) as exc:
            return SweepResult(
                chain=chain, address=address, to=to, amount_wei=0,
                nonce=-1, gas=0, max_fee_per_gas=0,
                dry_run=dry_run, error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            return SweepResult(
                chain=chain, address=address, to=to, amount_wei=0,
                nonce=-1, gas=0, max_fee_per_gas=0,
                dry_run=dry_run, error=f"{type(exc).__name__}: {exc}",
            )
                
            