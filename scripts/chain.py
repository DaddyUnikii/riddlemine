"""Tiny JSON-RPC + signing helpers for BSC testnet scripts (no web3 needed)."""
import json, os, time, urllib.request
from eth_account import Account
from eth_utils import keccak, to_checksum_address

RPC = os.environ.get("RPC", "https://bsc-testnet-rpc.publicnode.com")
CHAIN_ID = 97


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for attempt in range(6):
        try:
            req = urllib.request.Request(RPC, body, {"content-type": "application/json", "user-agent": "curl/8"})
            out = json.load(urllib.request.urlopen(req, timeout=30))
        except Exception:
            time.sleep(2 + attempt * 2)
            continue
        if "error" in out:
            raise RuntimeError(out["error"])
        return out["result"]
    raise RuntimeError("rpc unreachable")


def word(x):
    if isinstance(x, str):
        x = int(x, 16)
    return x.to_bytes(32, "big")


def selector(sig):
    return keccak(text=sig)[:4]


def call(to, sig, *args):
    data = "0x" + (selector(sig) + b"".join(word(a) for a in args)).hex()
    return rpc("eth_call", [{"to": to, "data": data}, "latest"])


def receipt(h):
    for _ in range(90):
        r = rpc("eth_getTransactionReceipt", [h])
        if r:
            if r["status"] != "0x1":
                raise RuntimeError(f"tx failed {h}")
            return r
        time.sleep(2)
    raise RuntimeError(f"tx not mined {h}")


class Signer:
    def __init__(self, key):
        self.acct = Account.from_key(key)
        self.address = self.acct.address
        self.nonce = int(rpc("eth_getTransactionCount", [self.address, "pending"]), 16)

    def send(self, to, data=b"", value=0, gas=None, wait=True):
        price = int(rpc("eth_gasPrice", []), 16)
        tx = {"chainId": CHAIN_ID, "nonce": self.nonce, "gasPrice": price, "value": value, "data": data}
        if to:
            tx["to"] = to
        if gas is None:
            est = {"from": self.address, "data": "0x" + data.hex(), "value": hex(value)}
            if to:
                est["to"] = to
            gas = int(int(rpc("eth_estimateGas", [est]), 16) * 1.3)
        tx["gas"] = gas
        raw = self.acct.sign_transaction(tx).raw_transaction
        h = rpc("eth_sendRawTransaction", ["0x" + raw.hex()])
        self.nonce += 1
        if not wait:
            return h
        return receipt(h)


    def tx(self, to, sig, *args, value=0, gas=None, wait=True):
        return self.send(to, selector(sig) + b"".join(word(a) for a in args), value, gas, wait)

    def deploy(self, bin_path, *args):
        code = bytes.fromhex(open(bin_path).read().strip().removeprefix("0x"))
        r = self.send(None, code + b"".join(word(a) for a in args))
        return to_checksum_address(r["contractAddress"])
