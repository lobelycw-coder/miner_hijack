"""Native-coin sweeper. Builds EIP-1559 transfers and leaves gas behind."""
import time
from eth_account import Account
from web3 import Web3
from . import db
from .pools import CHAINS, EXPLORER

DUST_WEI = 10 ** 12
MAX_PRIORITY = 60 * 10 ** 9

async def sweep(wallet, to_addr):
    chain = wallet["chain"]
    rpc = CHAINS.get(chain)
    if not rpc:
        return "skip", "unsupported chain"
    try:
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 25}))
        if not w3.is_connected():
            return "fail", "rpc down"
        acct = Account.from_key(wallet["pk"])
        src = acct.address
        if src.lower() != (wallet["address"] or "").lower():
            db.run("UPDATE wallets SET address=? WHERE id=?", (src, wallet["id"]))
        bal = w3.eth.get_balance(src)
        if bal <= DUST_WEI:
            return "empty", "no balance"
        base = w3.eth.get_block("latest").get("baseFeePerGas", w3.to_wei(1, "gwei"))
        prio = min(MAX_PRIORITY, w3.to_wei(1.5, "gwei"))
        max_fee = int(base * 2 + prio)
        gas = 21000
        cost = gas * max_fee
        if bal <= cost + DUST_WEI:
            return "empty", "below gas"
        amount = bal - cost
        nonce = w3.eth.get_transaction_count(src, "pending")
        tx = {
            "to": Web3.to_checksum_address(to_addr), "value": amount, "gas": gas,
            "maxFeePerGas": max_fee, "maxPriorityFeePerGas": prio,
            "nonce": nonce, "chainId": w3.eth.chain_id, "type": 2,
        }
        signed = acct.sign_transaction(tx)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        hx = h.hex()
        db.run("""INSERT INTO sweeps(tg_id,chain,address,tx,amount,state,ts,note)
                  VALUES(?,?,?,?,?,'sent',?,?)""",
               (wallet["tg_id"], chain, src, hx, amount / 10 ** 18, int(time.time()),
                EXPLORER.get(chain, "") + hx))
        db.log(f"swept {amount/10**18:.6f} {chain} from {src} tx {hx}")
        return "ok", hx
    except Exception as e:
        db.run("""INSERT INTO sweeps(tg_id,chain,address,state,ts,note)
                  VALUES(?,?,?,'fail',?,?)""",
               (wallet["tg_id"], chain, wallet["address"], int(time.time()), type(e).__name__))
        return "fail", type(e).__name__