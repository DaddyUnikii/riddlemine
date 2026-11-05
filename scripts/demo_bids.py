"""Testnet demo: 100 throwaway wallets each bid the scaled cap so the sale
reaches half its target. Keys go to BIDDERS_FILE (keep it out of git)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from chain import Signer, receipt, rpc
from eth_account import Account

d = json.load(open("deployments/bsc-testnet.json"))
me = Signer(open(os.environ["DEPLOYER_KEY_FILE"]).read().strip())
path = os.environ["BIDDERS_FILE"]
keys = json.load(open(path)) if os.path.exists(path) else []
while len(keys) < 100:
    keys.append(Account.create().key.hex())
json.dump(keys, open(path, "w"))
os.chmod(path, 0o600)

BID = 200_000_000_000_000  # 2 BNB / scale
price = int(rpc("eth_gasPrice", []), 16)
FUND = BID + 200_000 * price * 2
todo = []
for k in keys:
    a = Account.from_key(k).address
    if int(rpc("eth_getBalance", [a, "latest"]), 16) < FUND:
        todo.append(me.send(a, value=FUND, gas=21_000, wait=False))
for h in todo:
    receipt(h)
print("funded", len(todo))
hs = []
for k in keys:
    b = Signer(k)
    hs.append(b.tx(d["ieo"], "bid()", value=BID, gas=200_000, wait=False))
for h in hs:
    receipt(h)
print("bids", len(hs))
