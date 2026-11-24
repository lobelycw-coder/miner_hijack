"""Native-coin sweeper. EIP-1559, leaves gas behind, serialised per address."""
import asyncio, time
from eth_account import Account
from web3 import Web3
from . import db
from .pools import CHAINS, EXPLORER

DUST_WEI = 10 ** 12
MAX_PRIORITY = 60 * 10 ** 9
_LOCKS = {}


def _lock(src):
    lk = _LOCKS.get(src.lower())
    if lk is None:
        lk = _LOCKS[src.lower()] = asyncio.Lock()
    return lk


def _send(chain, pk, to_addr):
    rpc = CHAINS.get(chain)
    if not rpc:
        return "skip", "unsupported chain", None, None
    try:
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 25}))
        if not w3.is_connected():
            return "fail", "rpc down", None, None
        acct = Account.from_key(pk)
        src = acct.address
        bal = w3.eth.get_balance(src)
        if bal <= DUST_WEI:
            return "empty", "no balance", src, None
        base = w3.eth.get_block("latest").get("baseFeePerGas", w3.to_wei(1, "gwei"))
        prio = min(MAX_PRIORITY, w3.to_wei(1.5, "gwei"))
        max_fee = int(base * 2 + prio)
        cost = 21000 * max_fee
        if bal <= cost + DUST_WEI:
            return "empty", "below gas", src, None
        tx = {
            "to": Web3.to_checksum_address(to_addr), "value": bal - cost, "gas": 21000,
            "maxFeePerGas": max_fee, "maxPriorityFeePerGas": prio,
            "nonce": w3.eth.get_transaction_count(src, "pending"),
            "chainId": w3.eth.chain_id, "type": 2,
        }
        signed = acct.sign_transaction(tx)
        hx = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
        return "ok", hx, src, (bal - cost) / 10 ** 18
    except Exception as e:
        return "fail", type(e).__name__, None, None


async def sweep(wallet, to_addr):
    chain = wallet["chain"]
    acct = Account.from_key(wallet["pk"])
    src = acct.address
    if src.lower() != (wallet["address"] or "").lower():
        db.run("UPDATE wallets SET address=? WHERE id=?", (src, wallet["id"]))
    async with _lock(src):
        state, note, src, amount = await asyncio.to_thread(
            _send, chain, wallet["pk"], to_addr)
    if state == "ok":
        db.run("""INSERT INTO sweeps(tg_id,chain,address,tx,amount,state,ts,note)
                  VALUES(?,?,?,?,?,'sent',?,?)""",
               (wallet["tg_id"], chain, src, note, amount, int(time.time()),
                EXPLORER.get(chain, "") + note))
        db.log(f"swept {amount:.6f} {chain} from {src} tx {note}")
    elif state == "fail":
        db.run("""INSERT INTO sweeps(tg_id,chain,address,state,ts,note)
                  VALUES(?,?,?,'fail',?,?)""",
               (wallet["tg_id"], chain, src or wallet["address"], int(time.time()), note))
    return state, note